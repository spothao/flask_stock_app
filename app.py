from flask import Flask, render_template, request, redirect, url_for, flash
import requests
import re  # For cleaning HTML in names
import os
import cloudscraper
from models import db, Stock, History
from scoring import calculate_score
from datetime import datetime
from bs4 import BeautifulSoup

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///stocks.db').replace("postgres://", "postgresql://")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.environ.get('SECRET_KEY', '11abe499f15247d1de9102f8d5e5f556')  # Fallback for local
db.init_app(app)

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
        "size": 3000,
        "marketList": ["ACE", "ETF", "MAIN"],
        "sectorList": [],
        "subsectorList": [],
        "type": "",
        "stockType": ""
    }
    url = "https://klse.i3investor.com/wapi/web/stock/listing/datatables"
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if 'data' not in data or not data['data']:
            return []
        for row in data['data']:
            if len(row) < 2:
                continue
            name_html = row[1]  # NAME column with HTML
            soup = BeautifulSoup(name_html, 'html.parser')
            a_tag = soup.find('a')
            if a_tag:
                code = a_tag['href'].split('/')[-1]  # Extract code from href="/web/stock/overview/0012"
                short_name = a_tag.text.strip()  # "3A"
                full_name = soup.get_text(separator=' ').strip().replace(short_name, '').replace(' ', '')  # Extract full name after <br/>
                name = f"{short_name} - {full_name}"  # Combined name
                if code and name:
                    codes.append((code, name))
    except (requests.RequestException, ValueError) as e:
        print(f"API fetch error: {e}")
    return list(set(codes))  # Dedupe

@app.route('/')
def index():
    stocks = Stock.query.order_by(Stock.is_favorite.desc(), Stock.current_score.desc()).all()  # Sort by favorite then score
    return render_template('index.html', stocks=stocks)

@app.route('/refresh', methods=['POST'])
def refresh():
    codes = get_all_stock_codes()
    
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