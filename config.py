import os

class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'postgresql://koyeb-adm:W1MrvpPw7DKg@ep-bold-breeze-a15rt6e6.ap-southeast-1.pg.koyeb.app/koyebdb')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
