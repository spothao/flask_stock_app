# Deployment Guide for KLSE Stock Scorer

## Quick Deploy to Render (Recommended)

### Option 1: One-Click Deploy (Easiest)

1. Push your code to GitHub
2. Go to [Render Dashboard](https://dashboard.render.com/)
3. Click **New** → **Blueprint**
4. Connect your GitHub repository
5. Render will auto-detect `render.yaml` and create:
   - Web service (Flask app)
   - PostgreSQL database (free tier)
6. Wait for deployment (~2-5 minutes)
7. Access your app at `https://klse-stock-scorer.onrender.com`

### Option 2: Manual Setup

1. **Create PostgreSQL Database**
   - Go to Render → New → PostgreSQL
   - Select "Free" plan
   - Note the **Internal Database URL**

2. **Create Web Service**
   - Go to Render → New → Web Service
   - Connect GitHub repo
   - Set:
     - **Build Command**: `pip install -r requirements.txt && flask db upgrade`
     - **Start Command**: `gunicorn app:app`
   - Add Environment Variables:
     - `DATABASE_URL`: (paste Internal Database URL)
     - `SECRET_KEY`: (generate a random string)

3. Deploy!

---

## Local Development

```bash
# Clone the repository
git clone https://github.com/spothao/flask_stock_app.git
cd flask_stock_app

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run locally (uses SQLite)
python app.py

# Open http://localhost:5000
```

---

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `DATABASE_URL` | PostgreSQL connection string | Production only |
| `SECRET_KEY` | Flask secret key for sessions | Recommended |

---

## Notes

- **Free tier limitations**: Render free web services spin down after 15 minutes of inactivity. First request after spin-down takes ~30 seconds.
- **Database**: Free PostgreSQL has 1GB limit and 90-day retention without activity.
- **Background refresh**: Works well with Render's free tier - the refresh thread runs while the app is active.
