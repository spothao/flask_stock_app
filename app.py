from flask import Flask, render_template, request, redirect, url_for, flash
import requests
from bs4 import BeautifulSoup
import time
import os
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, JSON, Boolean  # Added Booleanfrom sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from models import db, Stock, History
from datetime import datetime
from flask_migrate import Migrate
import logging
from scoring import extract_values, compute_score  # Import from new file

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
# Base = declarative_base()

# # Stock model
# class Stock(Base):
#     __tablename__ = 'stock'
#     id = Column(Integer, primary_key=True)
#     code = Column(String, unique=True, nullable=False)
#     name = Column(String)
#     last_updated = Column(DateTime)
#     current_score = Column(Float)
#     breakdown = Column(JSON)
#     is_favorite = Column(Boolean, default=False)
#     growth_cagr = Column(Float)
#     div_yield = Column(Float)
#     pe_ratio = Column(Float)
#     roe = Column(Float)
#     profit = Column(Float)
#     cash_positive = Column(Float)
#     last_refreshed = Column(DateTime)

# Base.metadata.create_all(engine)
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
                    stock_data = resp.json()
                    values = extract_values(stock_data)  # Extract values
                    new_score, new_breakdown = compute_score(**values)  # Compute score from extracted values
                    
                    if stock.current_score != new_score and stock.current_score != 0:
                        history = History(
                            stock_id=stock.id,
                            score=stock.current_score,
                            breakdown=stock.breakdown,
                            growth_cagr=stock.growth_cagr,
                            div_yield=stock.div_yield,
                            pe_ratio=stock.pe_ratio,
                            roe=stock.roe,
                            profit=stock.profit,
                            cash_positive=stock.cash_positive
                        )
                        session.add(history)
                    
                    stock.growth_cagr = values['growth']
                    stock.div_yield = values['div_yield']
                    stock.pe_ratio = values['per']
                    stock.roe = values['roe']
                    stock.current_score = new_score
                    stock.breakdown = new_breakdown
                    stock.profit = values['profit']
                    stock.cash_positive = values['cash_positive']
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
    session = Session()
    if request.method == 'POST':
        code = request.form.get('stock_code', '').upper()
        if code:
            stock = session.query(Stock).filter_by(code=code).first()
            if not stock:
                stock = Stock(code=code, name=code)
                session.add(stock)
                session.commit()  # Immediate upsert for new stock
            else:
                # Update existing stock
                pass  # Will update below, commit after changes
            
            url = f"https://www.klsescreener.com/v2/stocks/view/{code}/all.json"
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    stock_data = resp.json()
                    values = extract_values(stock_data)  # Extract values
                    new_score, new_breakdown = compute_score(**values)  # Compute score from extracted values
                    
                    if stock.current_score != new_score and stock.current_score != 0:
                        history = History(
                            stock_id=stock.id,
                            score=stock.current_score,
                            breakdown=stock.breakdown,
                            growth_cagr=stock.growth_cagr,
                            div_yield=stock.div_yield,
                            pe_ratio=stock.pe_ratio,
                            roe=stock.roe,
                            profit=stock.profit,
                            cash_positive=stock.cash_positive
                        )
                        session.add(history)
                    
                    stock.growth_cagr = values['growth']
                    stock.div_yield = values['div_yield']
                    stock.pe_ratio = values['per']
                    stock.roe = values['roe']
                    stock.current_score = new_score
                    stock.breakdown = new_breakdown
                    stock.profit = values['profit']
                    stock.cash_positive = values['cash_positive']
                    stock.last_updated = datetime.utcnow()
                    stock.last_refreshed = datetime.utcnow()
                    session.commit()  # Immediate upsert for updates
                    flash(f"Score for {stock.name} ({code}): {score}")
                    return redirect(url_for('manual_refresh'))
                else:
                    flash(f"Failed to fetch {code}")
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 403:
                    flash(f"Access denied for {code}. The site may block automated requests. Try a different stock or contact support.")
                else:
                    flash(f"Failed to fetch {code}: {e}")
            except requests.RequestException as e:
                flash(f"Network error for {code}: {e}")
            except Exception as e:
                flash(f"Error processing {code}: {e}")
            finally:
                session.close()
        else:
            flash("Please enter a stock code.")

    return render_template('manual_refresh.html')

if __name__ == '__main__':
    app.run(debug=True)