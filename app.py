from flask import Flask, render_template, request, redirect, url_for, flash
import requests
from bs4 import BeautifulSoup
import time
import os
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, JSON, Boolean  # Added Booleanfrom sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from models import db, Stock, History
from datetime import datetime
from flask_migrate import Migrate
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///stocks.db').replace("postgres://", "postgresql://")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.environ.get('SECRET_KEY', '11abe499f15247d1de9102f8d5e5f556')
db.init_app(app)
migrate = Migrate(app, db)

# Custom engine with retry and connection pooling
engine = create_engine(
    app.config['SQLALCHEMY_DATABASE_URI'],
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    connect_args={'sslmode': 'require'}
)
Base = declarative_base()

# Stock model
class Stock(Base):
    __tablename__ = 'stock'
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String)
    last_updated = Column(DateTime)
    current_score = Column(Float)
    breakdown = Column(JSON)
    is_favorite = Column(Boolean, default=False)
    net_profit_5y_cagr = Column(Float)
    div_yield = Column(Float)
    pe_ratio = Column(Float)
    roe = Column(Float)
    last_refreshed = Column(DateTime)

Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

with app.app_context():
    db.create_all()

def get_all_stock_codes():
    codes = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:142.0) Gecko/20100101 Firefox/142.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Content-Type': 'application/json'
    }
    body = {
        "dtDraw": 7,
        "start": 0,
        "order": [{"column": 1, "dir": "asc"}],
        "page": 0,
        "size": 500,
        "marketList": ["ACE", "ETF", "MAIN"],
        "sectorList": [],
        "subsectorList": [],
        "type": "",
        "stockType": ""
    }
    url = "https://klse.i3investor.com/wapi/web/stock/listing/datatables"
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if 'data' not in data or not data['data']:
                return []
            for row in data['data']:
                if len(row) < 2:
                    continue
                name_html = row[1]
                soup = BeautifulSoup(name_html, 'html.parser')
                a_tag = soup.find('a')
                if a_tag:
                    code = a_tag['href'].split('/')[-1]
                    short_name = a_tag.text.strip()
                    full_name = soup.get_text(separator=' ').strip().replace(short_name, '').replace(' ', '')
                    name = f"{short_name} - {full_name}"
                    if code and name:
                        codes.append((code, name))
            return list(set(codes))
        except requests.RequestException as e:
            print(f"API fetch error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                return []

@app.route('/')
def index():
    try:
        session = Session()
        stocks = session.query(Stock).order_by(Stock.is_favorite.desc(), Stock.current_score.desc()).all()
        session.close()
        return render_template('index.html', stocks=stocks)
    except Exception as e:
        print(f"Database error in index: {e}")
        return render_template('error.html', message="Database connection failed. Please try again later."), 500

@app.route('/refresh', methods=['POST'])
def refresh():
    try:
        session = Session()
        codes = get_all_stock_codes()
        today = datetime.utcnow().date()
        updated_count = 0
        for code, name in codes:
            logger.info(f"Processing stock: code={code}, name={name}")
            stock = session.query(Stock).filter_by(code=code).first()
            if not stock:
                stock = Stock(code=code, name=name)
                session.add(stock)
                session.commit()  # Immediate upsert for new stock
            else:
                # Update existing stock
                pass  # Will update below, commit after changes
            
            if stock.last_refreshed and stock.last_refreshed.date() == today:
                continue
            
            url = f"https://www.klsescreener.com/v2/stocks/view/{code}/all.json"
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    new_score, new_breakdown = calculate_score(data)
                    
                    if stock.current_score != new_score and stock.current_score != 0:
                        history = History(
                            stock_id=stock.id,
                            score=stock.current_score,
                            breakdown=stock.breakdown,
                            net_profit_5y_cagr=stock.net_profit_5y_cagr,
                            div_yield=stock.div_yield,
                            pe_ratio=stock.pe_ratio,
                            roe=stock.roe
                        )
                        session.add(history)
                    
                    stock.net_profit_5y_cagr = float(data.get('growth', {}).get('net_profit_5y_cagr', 0))
                    stock.div_yield = float(data.get('Stock', {}).get('DY', 0))
                    stock.pe_ratio = float(data.get('Stock', {}).get('PE', 999))
                    stock.roe = float(data.get('Stock', {}).get('ROE', 0))
                    stock.current_score = new_score
                    stock.breakdown = new_breakdown
                    stock.last_updated = datetime.utcnow()
                    stock.last_refreshed = datetime.utcnow()
                    session.commit()  # Immediate upsert for updates
                    updated_count += 1
                else:
                    flash(f"Failed to fetch {code}")
            except Exception as e:
                flash(f"Error on {code}: {e}")
                continue
        
        flash(f"Refresh complete! Updated {updated_count} stocks.")
    except Exception as e:
        flash(f"Database error: {e}")
        session.rollback()
    finally:
        session.close()
    return redirect(url_for('index'))

@app.route('/favorite/<code>', methods=['POST'])
def favorite(code):
    try:
        session = Session()
        stock = session.query(Stock).filter_by(code=code).first()
        if stock:
            stock.is_favorite = not stock.is_favorite
            session.commit()
            flash(f"{stock.name} favorite toggled!")
        session.close()
    except Exception as e:
        flash(f"Database error: {e}")
    return redirect(url_for('index'))

@app.route('/manual_refresh', methods=['GET', 'POST'])
def manual_refresh():
    db_session = Session()  # Move session creation outside try block
    try:
        if request.method == 'POST':
            code = request.form.get('stock_code', '').upper()
            if code:
                url = f"https://www.klsescreener.com/v2/stocks/view/{code}/all.json"
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                stock_data = resp.json()
                
                stock = db_session.query(Stock).filter_by(code=code).first()
                if not stock:
                    stock = Stock(code=code, name=stock_data.get('Stock', {}).get('name', code))
                    db_session.add(stock)
                    db_session.commit()
                
                score, breakdown = calculate_score(stock_data, stock)
                stock.current_score = score
                stock.breakdown = breakdown
                stock.last_updated = datetime.utcnow()
                db_session.commit()
                flash(f"Score for {stock.name} ({code}): {score}")
                return redirect(url_for('manual_refresh'))
            else:
                flash("Please enter a stock code.")
    except requests.RequestException as e:
        flash(f"Failed to fetch {code}: {e}")
    except Exception as e:
        flash(f"Error processing {code}: {e}")
    finally:
        db_session.close()  # Ensure session is always closed

    return render_template('manual_refresh.html')

def calculate_score(stock_data, stock=None):
    # Use stored values if available, else from JSON
    div_yield = float(stock.div_yield) if stock and stock.div_yield is not None else float(stock_data.get('Stock', {}).get('DY', 0))
    per = float(stock.pe_ratio) if stock and stock.pe_ratio is not None else float(stock_data.get('Stock', {}).get('PE', 999))
    roe = float(stock.roe) if stock and stock.roe is not None else float(stock_data.get('Stock', {}).get('ROE', 0))
    
    # Calculate or fetch growth
    growth = float(stock.net_profit_5y_cagr) if stock and stock.net_profit_5y_cagr is not None else float(stock_data.get('StockIndicator', {}).get('cagr_5y', 0))
    if growth == 0:  # Fallback to manual calculation if needed
        reports = stock_data.get('FinancialReport', [])
        if reports:
            from collections import defaultdict
            annual_profits = defaultdict(float)
            for report in reports:
                year = report['financial_year_end'][:4]  # Extract year
                profit = float(report.get('profit_loss', 0))
                annual_profits[year] += profit
            
            years = sorted(annual_profits.keys(), reverse=True)[:5]
            if len(years) >= 2:
                latest_year = years[0]
                earliest_year = years[-1]
                latest_profit = annual_profits[latest_year]
                earliest_profit = annual_profits[earliest_year]
                if earliest_profit <= 0 or latest_profit <= 0:  # Handle negative or zero profits
                    growth = 0
                else:
                    num_years = len(years) - 1
                    growth = ((latest_profit / earliest_profit) ** (1 / num_years) - 1) * 100
            else:
                growth = float(stock_data.get('StockIndicator', {}).get('cagr_3y', 0))  # Fallback to 3-year CAGR
    
    try:
        # Latest quarter for margin
        reports = stock_data.get('FinancialReport', [])
        if reports:
            latest = max(reports, key=lambda r: datetime.strptime(r['quarter_date_end'], '%Y-%m-%d'))
            profit = float(latest.get('profit_loss', 0))
            revenue = float(latest.get('revenue', 1))
            margin = (profit / revenue * 100) if revenue else 0
        else:
            margin = 0
        
        # Cash flow and profit positivity
        cash_positive = any(float(r.get('operating_cf', 0)) > 0 for r in reports[-4:])  # Last year approx
        profit_positive = profit >= 0
        
        # GDP (Growth, Dividend, PE)
        g_points = 50 if growth >= 15 else 40 if growth >= 10 else 30 if growth >= 6 else 20 if growth >= 1 else 0
        d_points = 20 if div_yield >= 7 else 15 if div_yield >= 5 else 10 if div_yield >= 3 else 5 if div_yield >= 1 else 0
        p_points = 30 if abs(per) <= 9 and per > 0 else 20 if abs(per) <= 15 and per > 0 else 10 if abs(per) <= 24 and per > 0 else 5 if per > 0 else 0
        gdp = g_points + d_points + p_points
        
        # PRC (Profit Margin, ROE, Cash)
        p_points_prc = 30 if margin >= 16 else 20 if margin >= 11 else 10 if margin >= 6 else 5 if margin >= 1 else 0
        r_points = 30 if roe >= 16 else 20 if roe >= 11 else 10 if roe >= 6 else 5 if roe >= 1 else 0
        c_points = -100 if not profit_positive else -50 if not cash_positive else 0
        prc = p_points_prc + r_points + c_points
        
        total = max(0, gdp + prc)  # Ensure score is not negative
        breakdown = {
            'G': g_points, 'D': d_points, 'P_GDP': p_points, 'GDP': gdp,
            'P_PRC': p_points_prc, 'R': r_points, 'C': c_points, 'PRC': prc,
            'W': total
        }
        return total, breakdown
    except Exception:
        return 0, {}

if __name__ == '__main__':
    app.run(debug=True)