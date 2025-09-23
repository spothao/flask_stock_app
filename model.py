from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)
    current_score = db.Column(db.Integer, default=0)
    breakdown = db.Column(db.JSON, default=dict)
    is_favorite = db.Column(db.Boolean, default=False)
    net_profit_5y_cagr = db.Column(db.Float, default=0.0)  # New field
    div_yield = db.Column(db.Float, default=0.0)          # New field
    pe_ratio = db.Column(db.Float, default=999.0)         # New field (PE)
    roe = db.Column(db.Float, default=0.0)                # New field

class History(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('stock.id'), nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    score = db.Column(db.Integer, nullable=False)
    breakdown = db.Column(db.JSON, nullable=False)
    net_profit_5y_cagr = db.Column(db.Float, default=0.0)  # New field
    div_yield = db.Column(db.Float, default=0.0)          # New field
    pe_ratio = db.Column(db.Float, default=999.0)         # New field
    roe = db.Column(db.Float, default=0.0)                # New field