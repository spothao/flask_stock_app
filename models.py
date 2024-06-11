from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(10), unique=True, nullable=False)
    name = db.Column(db.String(100))
    price = db.Column(db.Float)
    change = db.Column(db.Float)

    def __repr__(self):
        return f'<Stock {self.ticker}>'
