from flask import Flask, render_template, request, redirect, url_for, flash
import requests
import re  # For cleaning HTML in names
import os
from models import db, Stock, History
from scoring import calculate_score
from datetime import datetime

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///stocks.db').replace("postgres://", "postgresql://")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

with app.app_context():
    db.create_all()

def clean_name(html_str):
    # Strip HTML tags (e.g., <div class='stock_change'...>Name</div> -> Name)
    return re.sub(r'<[^>]*>', '', html_str).strip()

def get_all_stock_codes():
    codes = []
    page = 1
    while True:
        url = f"https://www.bursamalaysia.com/api/v1/equities_prices/equities_prices?inMarket=stock&per_page=50&page={page}"
        try:
            resp = requests.get(url)
            data = resp.json()
            if not data or 'data' not in data:
                break
            page_data = data['data']
            total = int(data.get('recordsTotal', 0))
            for row in page_data:
                code = row.get('stock_id', '').strip()
                name_html = row.get('short_name', '')
                name = clean_name(name_html)
                if code and name and len(code) <= 10:  # Valid KLSE code
                    codes.append((code, name))
            records_fetched = len(page_data)
            if records_fetched == 0 or len(codes) >= total:
                break
            page += 1
        except Exception as e:
            print(f"API fetch error on page {page}: {e}")
            break
    return list(set(codes))  # Dedupe if any

# Fallback hardcoded list from sample JSON (top 5 for demo)
FALLBACK_CODES = [
    ('7079', 'TWL [S]'),
    ('0116', 'FOCUS'),
    ('6963', 'VS [S]'),
    ('7081', 'PHARMA [S]'),  # Assuming from truncated
    ('0366', 'ICENTS [S]'),
    # Add more from full JSON if needed
]

@app.route('/')
def index():
    stocks = Stock.query.order_by(Stock.is_favorite.desc(), Stock.current_score.desc()).all()  # Sort by favorite then score
    return render_template('index.html', stocks=stocks)

@app.route('/refresh', methods=['POST'])
def refresh():
    codes = get_all_stock_codes()
    if not codes:
        codes = FALLBACK_CODES
        flash("Using fallback list; API unavailable.")
    
    new_codes_added = 0
    for code, name in codes:
        stock = Stock.query.filter_by(code=code).first()
        if not stock:
            stock = Stock(code=code, name=name)
            db.session.add(stock)
            new_codes_added += 1
        
        url = f"https://www.klsescreener.com/v2/stocks/view/{code}/all.json"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                # Extract raw data points
                growth = float(data.get('growth', {}).get('net_profit_5y_cagr', 0))
                div_yield = float(data.get('Stock', {}).get('DY', 0))
                per = float(data.get('Stock', {}).get('PE', 999))
                roe = float(data.get('Stock', {}).get('ROE', 0))
                
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
                    db.session.add(history)
                
                stock.net_profit_5y_cagr = growth
                stock.div_yield = div_yield
                stock.pe_ratio = per
                stock.roe = roe
                stock.current_score = new_score
                stock.breakdown = new_breakdown
            else:
                flash(f"Failed to fetch details for {code}")
        except Exception as e:
            flash(f"Error updating {code}: {e}")
        
        stock.last_updated = datetime.utcnow()
    
    db.session.commit()
    flash(f"Refresh complete! Added {new_codes_added} new stocks.")
    return redirect(url_for('index'))

@app.route('/favorite/<code>', methods=['POST'])
def favorite(code):
    stock = Stock.query.filter_by(code=code).first()
    if stock:
        stock.is_favorite = not stock.is_favorite
        db.session.commit()
        flash(f"{stock.name} favorite toggled!")
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)