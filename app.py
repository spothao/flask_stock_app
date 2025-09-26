from flask import Flask, render_template, request, redirect, url_for, flash
import requests
from bs4 import BeautifulSoup
import time
import os
import random
import threading
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, JSON, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from models import db, Stock, History
from datetime import datetime
from flask_migrate import Migrate
import logging
import traceback
from scoring import extract_values, compute_score
from threading import Lock
from queue import Queue

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
Session = sessionmaker(bind=engine)

with app.app_context():
    db.create_all()

# Global state management with lock and queue for messages
refresh_lock = Lock()
refresh_running = False
refresh_stop_flag = False
refresh_message_queue = Queue()

def get_all_stock_codes():
    codes = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; YourApp/1.0; +https://yourapp.com)',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Content-Type': 'application/json'
    }
    url = "https://klse.i3investor.com/wapi/web/stock/listing/datatables"
    size = 500
    start = 0
    max_retries = 3

    while True:
        body = {
            "dtDraw": 7,
            "start": start,
            "order": [{"column": 1, "dir": "asc"}],
            "page": start // size,
            "size": size,
            "marketList": ["ACE", "ETF", "MAIN"],
            "sectorList": [],
            "subsectorList": [],
            "type": "",
            "stockType": ""
        }
        for attempt in range(max_retries):
            try:
                resp = requests.post(url, headers=headers, json=body, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                if 'data' not in data or not data['data']:
                    logger.info(f"No more data at start={start}, stopping pagination")
                    return list(set(codes))
                for row in data['data']:
                    if len(row) < 2: continue
                    name_html = row[1]
                    soup = BeautifulSoup(name_html, 'html.parser')
                    a_tag = soup.find('a')
                    if a_tag:
                        code = a_tag['href'].split('/')[-1]
                        # Skip warrant-like codes
                        if any(suffix in code for suffix in ['WA', 'WB', 'WD', 'WC']):
                            continue
                        short_name = a_tag.text.strip()
                        full_name = soup.get_text(separator=' ').strip().replace(short_name, '').replace(' ', '')
                        name = f"{short_name} - {full_name}"
                        if code and name: codes.append((code, name))
                total_records = data.get('recordsTotal', len(codes) + start)
                if start + len(data['data']) >= total_records: break
                start += size
                break
            except requests.RequestException as e:
                logger.warning(f"API fetch error at start={start} (attempt {attempt + 1}/3): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    break
        else: break
    logger.info(f"Retrieved {len(codes)} stock codes")
    return list(set(codes))

def update_stock_data(session, code, name, stock_data=None):
    stock = session.query(Stock).filter_by(code=code).first()
    if not stock:
        stock = Stock(code=code, name=name)
        session.add(stock)
        session.commit()
    logger.info(f"Found/created stock {code}")

    if not stock_data:
        url = f"https://www.klsescreener.com/v2/stocks/view/{code}/all.json"
        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; YourApp/1.0; +https://yourapp.com)'}, timeout=10)
                resp.raise_for_status()
                stock_data = resp.json()
                break
            except requests.JSONDecodeError as e:
                logger.error(f"JSON decode error for {code} (attempt {attempt + 1}/{max_retries}): {e}, Response: {resp.text}")
                if attempt == max_retries - 1:
                    return False, f"Failed to parse JSON for {code}", 0
                time.sleep(2 ** attempt)  # Exponential backoff
            except requests.RequestException as e:
                logger.error(f"Fetch error for {code} (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    return False, f"Failed to fetch {code}", 0
                time.sleep(2 ** attempt)

    values = extract_values(stock_data)
    new_score, new_breakdown = compute_score(**values)
    if stock.current_score != new_score and stock.current_score != 0:
        history = History(stock_id=stock.id, score=stock.current_score, breakdown=stock.breakdown,
                          growth_cagr=stock.growth_cagr, div_yield=stock.div_yield, pe_ratio=stock.pe_ratio,
                          roe=stock.roe, profit=stock.profit, cash_positive=stock.cash_positive)
        session.add(history)
    stock.growth_cagr = values['growth']
    stock.div_yield = values['div_yield']
    stock.pe_ratio = values['per']
    stock.roe = values['roe']
    stock.profit = values['margin']
    stock.cash_positive = values['cash_positive']
    stock.current_score = new_score
    stock.breakdown = new_breakdown
    stock.industry = stock_data.get('Sector', {}).get('name', 'Unknown')
    stock.market = stock_data.get('Sector', {}).get('Board', {}).get('name', 'Unknown')
    stock.last_updated = datetime.utcnow()
    stock.last_refreshed = datetime.utcnow()
    session.commit()
    logger.info(f"Updated {code} with score: {new_score}")
    return True, f"Updated {code} with score: {new_score}", 1

def background_refresh():
    global refresh_running, refresh_stop_flag
    with refresh_lock:
        refresh_running = True
        logger.info("Refresh process started, refresh_running set to True")
    session = Session()
    codes = get_all_stock_codes()
    today = datetime.utcnow().date()
    updated_count = 0
    if not codes:
        logger.warning("No stock codes retrieved from get_all_stock_codes")
        with refresh_lock:
            refresh_running = False
        session.close()
        return
    for code, name in codes:
        if refresh_stop_flag:
            logger.info("Refresh stopped by user request")
            break
        stock = session.query(Stock).filter_by(code=code).first()
        if stock and stock.last_refreshed and stock.last_refreshed.date() == today:
            logger.info(f"Skipping {code} as it was refreshed today")
            continue
        success, message, count = update_stock_data(session, code, name)
        updated_count += count
        if not success:
            refresh_message_queue.put(message)
    with refresh_lock:
        if not refresh_stop_flag:
            refresh_message_queue.put(f"Refresh complete! Updated {updated_count} stocks.")
        else:
            refresh_message_queue.put("Refresh process stopped by user.")
        refresh_running = False
        logger.info("Refresh process ended, refresh_running set to False")
    session.close()

@app.route('/')
def index():
    session = Session()
    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page
    query = session.query(Stock).order_by(Stock.is_favorite.desc(), Stock.current_score.desc())
    favorites_only = request.args.get('favorites_only', 'false').lower() == 'true'
    industry = request.args.get('industry')
    market = request.args.get('market')
    min_score = request.args.get('min_score', type=float)
    max_score = request.args.get('max_score', type=float)
    if favorites_only:
        query = query.filter(Stock.is_favorite == True)
    if industry:
        query = query.filter(Stock.industry == industry)
    if market:
        query = query.filter(Stock.market == market)
    if min_score is not None:
        query = query.filter(Stock.current_score >= min_score)
    if max_score is not None:
        query = query.filter(Stock.current_score <= max_score)
    total_stocks = query.count()
    stocks = query.offset(offset).limit(per_page).all()
    unique_industries = [i[0] for i in session.query(Stock.industry).distinct().all() if i[0]]
    unique_markets = [m[0] for m in session.query(Stock.market).distinct().all() if m[0]]
    session.close()
    while not refresh_message_queue.empty():
        flash(refresh_message_queue.get())
    return render_template('index.html', stocks=stocks, current_page=page, total_pages=(total_stocks + per_page - 1) // per_page if total_stocks else 1, total_stocks=total_stocks, unique_industries=unique_industries, unique_markets=unique_markets, favorites_only=favorites_only, industry=industry, market=market, min_score=min_score, max_score=max_score, refresh_running=refresh_running)

@app.route('/start_refresh', methods=['POST'])
def start_refresh():
    global refresh_running, refresh_stop_flag
    with refresh_lock:
        if not refresh_running:
            refresh_stop_flag = False
            refresh_thread = threading.Thread(target=background_refresh)
            refresh_thread.daemon = True
            refresh_thread.start()
            logger.info("Started refresh thread")
            flash("Refresh process started in background. Check back later or stop if needed.")
        else:
            flash("Refresh is already running.")
    return redirect(url_for('index'))

@app.route('/stop_refresh', methods=['POST'])
def stop_refresh():
    global refresh_stop_flag
    with refresh_lock:
        refresh_stop_flag = True
        logger.info("Stop refresh flag set to True")
    flash("Refresh process will stop after current stock.")
    return redirect(url_for('index'))

@app.route('/clear_all', methods=['POST'])
def clear_all():
    try:
        session = Session()
        session.query(History).delete()
        session.query(Stock).delete()
        session.commit()
        flash("All stock and history data cleared.")
    except Exception as e:
        session.rollback()
        flash(f"Error clearing data: {e}")
    finally:
        session.close()
    return redirect(url_for('index'))

@app.route('/clear_stock/<code>', methods=['POST'])
def clear_stock(code):
    try:
        session = Session()
        stock = session.query(Stock).filter_by(code=code).first()
        if stock:
            session.query(History).filter_by(stock_id=stock.id).delete()
            session.delete(stock)
            session.commit()
            flash(f"Stock {code} and its history cleared.")
        else:
            flash(f"Stock {code} not found.")
    except Exception as e:
        session.rollback()
        flash(f"Error clearing stock {code}: {e}")
    finally:
        session.close()
    return redirect(url_for('index'))

@app.route('/retry_failed', methods=['POST'])
def retry_failed():
    try:
        session = Session()
        failed_stocks = session.query(Stock).filter(
            Stock.current_score == 0,
            Stock.breakdown == {}
        ).all()
        if not failed_stocks:
            logger.info("No failed stocks found for retry.")
            flash("No failed stocks to retry.")
            session.close()
            return redirect(url_for('index'))
        logger.info(f"Starting retry for {len(failed_stocks)} failed stocks.")
        updated_count = 0
        for stock in failed_stocks:
            success, message, count = update_stock_data(session, stock.code, stock.name)
            updated_count += count
            if not success:
                flash(message)
        flash(f"Retry complete! Updated {updated_count} failed stocks.")
    except Exception as e:
        logger.error(f"Database error during retry_failed: {e}, Traceback: {traceback.format_exc()}")
        flash(f"Database error: {e}")
        session.rollback()
    finally:
        session.close()
        logger.info(f"Session closed after retry_failed, total updated: {updated_count}")
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
            success, message, count = update_stock_data(session, code, code)
            if not success:
                flash(message)
            else:
                flash(message)
        else:
            logger.warning("No stock code provided in form")
            flash("Please enter a stock code.")
    session.close()
    return render_template('manual_refresh.html')

if __name__ == '__main__':
    app.run(debug=True)