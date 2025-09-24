from flask import Flask, render_template, request, redirect, url_for, flash
import requests
from bs4 import BeautifulSoup
import time
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import db, Stock, History
from scoring import calculate_score
from datetime import datetime
from flask_migrate import Migrate

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
            stock = session.query(Stock).filter_by(code=code).first()
            if not stock:
                stock = Stock(code=code, name=name)
                session.add(stock)
            
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
                    session.commit()
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

if __name__ == '__main__':
    app.run(debug=True)