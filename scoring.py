import json
import pandas as pd
import numpy as np
from datetime import datetime
from collections import defaultdict

def clean_float(value):
    """
    Safely convert value to float, handling None, commas, and invalid strings.
    """
    if value is None:
        return 0.0
    str_val = str(value).replace(',', '')  # Remove commas (e.g., '1,057.27' → '1057.27')
    try:
        return float(str_val)
    except (ValueError, TypeError):
        return 0.0

def extract_values(stock_data):
    """
    Extract necessary values from stock_data JSON for scoring.
    """
    # Extract div_yield, per, roe from Stock
    stock = stock_data.get('Stock', {})
    div_yield = clean_float(stock.get('DY', 0))
    per = clean_float(stock.get('PE', 999))  # Default high for invalid
    roe = clean_float(stock.get('ROE', 0))
    
    # Extract growth from StockIndicator or calculate manually
    indicators = stock_data.get('StockIndicator', {})
    growth = clean_float(indicators.get('cagr_5y', 0))
    if growth == 0:
        reports = stock_data.get('FinancialReport', [])
        if reports:
            annual_profits = defaultdict(float)
            for report in reports:
                year = report['financial_year_end'][:4]  # Extract year
                profit = clean_float(report.get('profit_loss', 0))
                annual_profits[year] += profit
            
            years = sorted(annual_profits.keys(), reverse=True)[:5]
            if len(years) >= 2:
                latest_year = years[0]
                earliest_year = years[-1]
                latest_profit = annual_profits[latest_year]
                earliest_profit = annual_profits[earliest_year]
                if earliest_profit <= 0 or latest_profit <= 0:
                    growth = 0
                else:
                    num_years = len(years) - 1
                    growth = ((latest_profit / earliest_profit) ** (1 / num_years) - 1) * 100
            else:
                growth = clean_float(indicators.get('cagr_3y', 0))
    
    # Extract profit, revenue for margin, and cash_flow from latest report
    profit = 0
    revenue = 1
    cash_positive = False
    reports = stock_data.get('FinancialReport', [])
    if reports:
        # Sort by date to get latest
        latest = max(reports, key=lambda r: datetime.strptime(r['quarter_date_end'], '%Y-%m-%d'))
        profit = clean_float(latest.get('profit_loss', 0))
        revenue = clean_float(latest.get('revenue', 1))
        # Check operating CF if available, else fallback to any positive profit in last 4 quarters
        if 'operating_cf' in latest:
            cash_positive = clean_float(latest.get('operating_cf', 0)) > 0
        else:
            cash_positive = any(clean_float(r.get('profit_loss', 0)) > 0 for r in reports[-4:])  # Last year approx
    
    margin = (profit / revenue * 100) if revenue else 0

    # Cash ratio from balance sheet
    bs = stock_data.get('stock_bs', {})
    total_cash = clean_float(bs.get('total_cash', 0))
    total_debt = clean_float(bs.get('total_debt', 0))
    total_equity = clean_float(bs.get('total_equity', 0))
    cash_ratio = (total_cash / total_equity * 100) if total_equity > 0 else 0
    cash_positive = cash_positive or (total_cash > total_debt)  # Enhanced check

    return {
        'growth': growth,
        'div_yield': div_yield,
        'per': per,
        'roe': roe,
        'margin': margin,  # Profit margin
        'profit': profit,
        'cash_positive': 1 if cash_positive else 0,
        'cash_ratio': cash_ratio
    }

def compute_score(growth, div_yield, per, roe, margin, profit, cash_positive, cash_ratio=0):
    """
    Compute score based on extracted values, aligned with W = G + D + P_PER + P_PM + R + C + adjustments.
    Blends user logic (GDP/PRC) with image thresholds (linear scaling).
    """
    profit_positive = profit >= 0
    
    # G: Growth (linear scale: 0 <5%, 50 >15%; from images, adjusted to user bands)
    g_points = max(0, min(50, (max(growth - 5, 0) / 10) * 50))  # Linear fallback to user's step: 50>=15,40>=10,etc.
    if growth >= 15:
        g_points = 50
    elif growth >= 10:
        g_points = 40
    elif growth >= 6:
        g_points = 30
    elif growth >= 1:
        g_points = 20
    else:
        g_points = 0
    
    # D: Dividend Yield (linear: 0 <1%, 20 >6%)
    d_points = max(0, min(20, (max(div_yield - 1, 0) / 5) * 20))  # Linear fallback to user's step
    if div_yield >= 7:
        d_points = 20
    elif div_yield >= 5:
        d_points = 15
    elif div_yield >= 3:
        d_points = 10
    elif div_yield >= 1:
        d_points = 5
    else:
        d_points = 0
    
    # P_PER: PER (inverse linear: 20 <10x, 0 >25x; handle negative as 0)
    p_per_points = 0 if per < 0 else max(0, min(20, 20 - (min((per - 10) / 15 * 20, 20))))
    # User's step: 30<=9,20<=15,10<=24,5>0
    if 0 < per <= 9:
        p_per_points = 30
    elif per <= 15:
        p_per_points = 20
    elif per <= 24:
        p_per_points = 10
    elif per > 0:
        p_per_points = 5
    else:
        p_per_points = 0
    
    gdp = g_points + d_points + p_per_points
    
    # P_PM: Profit Margin (linear: 0 <5%, 20 >20%; from images)
    p_pm_points = max(0, min(20, (max(margin - 5, 0) / 15) * 20))  # Linear fallback to user's step
    if margin >= 16:
        p_pm_points = 20
    elif margin >= 11:
        p_pm_points = 15
    elif margin >= 6:
        p_pm_points = 10
    elif margin >= 1:
        p_pm_points = 5
    else:
        p_pm_points = 0
    
    # R: ROE (linear: 0 <5%, 20 >15%; cap negative at 0)
    r_points = max(0, min(20, (max(roe - 5, 0) / 10) * 20)) if roe > 0 else 0
    # User's step: 40>=16,30>=11,20>=6,10>=1,0<1
    if roe >= 16:
        r_points = 40
    elif roe >= 11:
        r_points = 30
    elif roe >= 6:
        r_points = 20
    elif roe >= 1:
        r_points = 10
    else:
        r_points = 0
    
    # C: Cash Flow (binary-ish: 0 negative, 10-20 positive scaled by cash_ratio; enhanced from images/user)
    c_points = 0
    if cash_positive:
        c_points = 10 + min(10, (cash_ratio / 10))  # Scale with ratio
        if profit_positive:
            c_points += 10  # Bonus for positive profit (user logic)
            c_points = min(40, c_points)  # Cap at user's max
        else:
            c_points = min(20, c_points)
    else:
        c_points = 1 if profit_positive else 0  # User's minimal for non-positive
    
    prc = p_pm_points + r_points + c_points
    
    # Total W (GDP + PRC, max 0)
    total = max(0, gdp + prc)
    breakdown = {
        'G': g_points, 'D': d_points, 'P_PER': p_per_points, 'GDP': gdp,
        'P_PM': p_pm_points, 'R': r_points, 'C': c_points, 'PRC': prc,
        'W': total
    }
    return total, breakdown