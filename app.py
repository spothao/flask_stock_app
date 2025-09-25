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

# Global flag to control background refresh
refresh_running = False
refresh_stop_flag = False

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

def update_stock_data(session, code, name):
    """
    Update stock data from klsescreener API, compute score, and save to database.
    Returns tuple (success_boolean, message, updated_count_increment).
    """
    stock = session.query(Stock).filter_by(code=code).first()
    if not stock:
        stock = Stock(code=code, name=name)
        session.add(stock)
        session.commit()  # Immediate upsert for new stock
        logger.info(f"Created new stock entry for code: {code}, name: {stock.name}")
    else:
        logger.info(f"Found existing stock entry for code: {code}, name: {stock.name}")

    url = f"https://www.klsescreener.com/v2/stocks/view/{code}/all.json"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    logger.info(f"Attempting to fetch data for {code} from URL: {url}")
    logger.debug(f"Request headers: {headers}")

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        logger.info(f"Received response for {code} with status code: {resp.status_code}")
        logger.debug(f"Response headers: {dict(resp.headers)}")
        resp.raise_for_status()

        # Add random sleep between 100ms and 5 seconds
        sleep_time = random.uniform(0.1, 5)
        logger.debug(f"Sleeping for {sleep_time:.2f} seconds before processing {code}")
        time.sleep(sleep_time)

        if resp.status_code == 200:
            logger.info(f"Successfully fetched JSON data for {code}")
            stock_data = resp.json()
            logger.debug(f"Sample of stock_data: {dict(list(stock_data.items())[:3])}...")

            values = extract_values(stock_data)
            logger.debug(f"Extracted values: {values}")

            new_score, new_breakdown = compute_score(**values)
            logger.info(f"Computed new score for {code}: {new_score}")

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
                logger.info(f"Added history entry for {code} with previous score: {stock.current_score}")

            stock.growth_cagr = values['growth']
            stock.div_yield = values['div_yield']
            stock.pe_ratio = values['per']
            stock.roe = values['roe']
            stock.current_score = new_score
            stock.breakdown = new_breakdown
            stock.profit = values['margin']  # Updated to match extract_values
            stock.cash_positive = values['cash_positive']
            stock.last_updated = datetime.utcnow()
            stock.last_refreshed = datetime.utcnow()
            session.commit()  # Immediate upsert for updates
            logger.info(f"Updated stock {code} in database with score: {new_score}")
            return True, f"Score for {stock.name} ({code}): {new_score}", 1
        else:
            logger.warning(f"Unexpected status code {resp.status_code} for {code}")
            return False, f"Failed to fetch {code} - Unexpected status: {resp.status_code}", 0
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error for {code}: {e}, Response text: {e.response.text if e.response else 'No response'}")
        if e.response.status_code == 403:
            return False, f"Access denied for {code}. The site may block automated requests. Try later.", 0
        return False, f"Failed to fetch {code}: {e}", 0
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error for {code}: {e}, URL: {url}")
        return False, f"Network error for {code}: {e}", 0
    except ValueError as e:
        logger.error(f"JSON parsing error for {code}: {e}, Response text: {resp.text if 'resp' in locals() else 'No response'}")
        return False, f"Failed to parse data for {code}: {e}", 0
    except Exception as e:
        logger.error(f"Unexpected error processing {code}: {e}, Traceback: {traceback.format_exc()}")
        return False, f"Error processing {code}: {e}", 0

def background_refresh():
    global refresh_running, refresh_stop_flag
    session = Session()
    codes = get_all_stock_codes()
    today = datetime.utcnow().date()
    updated_count = 0
    processed_count = 0

    if not codes:
        logger.warning("No stock codes retrieved from get_all_stock_codes")
        flash("No stock codes available for refresh.")
        session.close()
        return

    for code, name in codes:
        if refresh_stop_flag:
            logger.info("Refresh stopped by user request")
            flash("Refresh process stopped by user.")
            break
        stock = session.query(Stock).filter_by(code=code).first()
        if stock and stock.last_refreshed and stock.last_refreshed.date() == today:
            logger.info(f"Skipping {code} as it was refreshed today")
            continue
        success, message, count = update_stock_data(session, code, name)
        updated_count += count
        processed_count += 1
        if not success:
            flash(message)
        
        # Log progress every 10 stocks
        if processed_count % 10 == 0:
            logger.info(f"Progress: Processed {processed_count}/{len(codes)} stocks, updated {updated_count}")

    if not refresh_stop_flag:
        flash(f"Refresh complete! Updated {updated_count} stocks.")
    refresh_running = False
    session.close()
    logger.info(f"Session closed after refresh, total updated: {updated_count}")

@app.route('/')
def index():
    try:
        session = Session()
        page = request.args.get('page', 1, type=int)
        per_page = 50
        offset = (page - 1) * per_page
        
        # Count total for pagination
        total_stocks = session.query(Stock).count()
        if total_stocks is None:
            total_stocks = 0
            logger.error("Total stocks count returned None, defaulting to 0")
        
        total_pages = (total_stocks + per_page - 1) // per_page if total_stocks else 1
        
        # Fetch paginated stocks, sorted by favorite desc, then score desc
        stocks = session.query(Stock).order_by(
            Stock.is_favorite.desc(), Stock.current_score.desc()
        ).offset(offset).limit(per_page).all() or []
        
        session.close()
        return render_template('index.html', stocks=stocks, current_page=page, total_pages=total_pages, total_stocks=total_stocks, refresh_running=refresh_running)
    except Exception as e:
        logger.error(f"Database error in index: {e}, Traceback: {traceback.format_exc()}")
        return render_template('error.html', message=f"Database error: {e}. Please try again later."), 500

@app.route('/start_refresh', methods=['POST'])
def start_refresh():
    global refresh_running, refresh_stop_flag
    if not refresh_running:
        refresh_stop_flag = False
        refresh_thread = threading.Thread(target=background_refresh)
        refresh_thread.daemon = True
        refresh_thread.start()
        flash("Refresh process started in background. Check back later or stop if needed.")
    else:
        flash("Refresh is already running.")
    return redirect(url_for('index'))

@app.route('/stop_refresh', methods=['POST'])
def stop_refresh():
    global refresh_stop_flag
    refresh_stop_flag = True
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
    """
    Retry refresh for stocks with current_score == 0 and empty breakdown.
    """
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
            success, message, count = update_stock_data(session, code, code)  # Use code as name for new stocks
            if not success:
                flash(message)
            else:
                flash(message)
        else:
            logger.warning("No stock code provided in form")
            flash("Please enter a stock code.")

    return render_template('manual_refresh.html')

if __name__ == '__main__':
    app.run(debug=True)