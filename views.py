from flask import Flask, render_template, request, redirect, url_for, flash
from config import Config
from models import db, Stock
import requests

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

@app.route('/')
def index():
    stocks = Stock.query.all()
    return render_template('index.html', stocks=stocks)

@app.route('/add', methods=['POST'])
def add_stock():
    tickers = request.form.get('tickers').split(',')
    for ticker in tickers:
        if not Stock.query.filter_by(ticker=ticker).first():
            new_stock = Stock(ticker=ticker)
            db.session.add(new_stock)
            db.session.commit()
    return redirect(url_for('index'))

@app.route('/refresh')
def refresh_stock():
    stocks = Stock.query.all()
    for stock in stocks:
        url = f'https://www.klsescreener.com/v2/stocks/view/{stock.ticker}/all.json'
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            stock.name = data['name']
            stock.price = data['price']
            stock.change = data['change']
            db.session.commit()
    return redirect(url_for('index'))

@app.route('/init_db')
def init_db():
    with app.app_context():
        db.create_all()
        flash("Database initialized successfully.", "success")
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
