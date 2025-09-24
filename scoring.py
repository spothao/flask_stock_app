from datetime import datetime

def calculate_score(stock_data, stock=None):
    # Use stored values if available, else from JSON
    div_yield = float(stock.div_yield) if stock and stock.div_yield is not None else float(stock_data.get('Stock', {}).get('DY', 0))
    per = float(stock.pe_ratio) if stock and stock.pe_ratio is not None else float(stock_data.get('Stock', {}).get('PE', 999))
    roe = float(stock.roe) if stock and stock.roe is not None else float(stock_data.get('Stock', {}).get('ROE', 0))
    
    # Calculate or fetch growth
    growth = float(stock.net_profit_5y_cagr) if stock and stock.net_profit_5y_cagr is not None else float(stock_data.get('StockIndicator', {}).get('cagr_5y', 0))
    if growth == 0:  # Fallback to manual calculation if needed
        reports = stock_data.get('FinancialReport', [])
        if reports:
            from collections import defaultdict
            annual_profits = defaultdict(float)
            for report in reports:
                year = report['financial_year_end'][:4]  # Extract year
                profit = float(report.get('profit_loss', 0))
                annual_profits[year] += profit
            
            years = sorted(annual_profits.keys(), reverse=True)[:5]
            if len(years) >= 2:
                latest_year = years[0]
                earliest_year = years[-1]
                latest_profit = annual_profits[latest_year]
                earliest_profit = annual_profits[earliest_year]
                if earliest_profit <= 0 or latest_profit <= 0:  # Handle negative or zero profits
                    growth = 0
                else:
                    num_years = len(years) - 1
                    growth = ((latest_profit / earliest_profit) ** (1 / num_years) - 1) * 100
            else:
                growth = float(stock_data.get('StockIndicator', {}).get('cagr_3y', 0))  # Fallback to 3-year CAGR
    
    try:
        # Latest quarter for margin
        reports = stock_data.get('FinancialReport', [])
        if reports:
            latest = max(reports, key=lambda r: datetime.strptime(r['quarter_date_end'], '%Y-%m-%d'))
            profit = float(latest.get('profit_loss', 0))
            revenue = float(latest.get('revenue', 1))
            margin = (profit / revenue * 100) if revenue else 0
        else:
            margin = 0
        
        # Cash flow and profit positivity
        cash_positive = any(float(r.get('operating_cf', 0)) > 0 for r in reports[-4:])  # Last year approx
        profit_positive = profit >= 0
        
        # GDP (Growth, Dividend, PE)
        g_points = 50 if growth >= 15 else 40 if growth >= 10 else 30 if growth >= 6 else 20 if growth >= 1 else 0
        d_points = 20 if div_yield >= 7 else 15 if div_yield >= 5 else 10 if div_yield >= 3 else 5 if div_yield >= 1 else 0
        p_points = 30 if abs(per) <= 9 and per > 0 else 20 if abs(per) <= 15 and per > 0 else 10 if abs(per) <= 24 and per > 0 else 5 if per > 0 else 0
        gdp = g_points + d_points + p_points
        
        # PRC (Profit Margin, ROE, Cash)
        p_points_prc = 30 if margin >= 16 else 20 if margin >= 11 else 10 if margin >= 6 else 5 if margin >= 1 else 0
        r_points = 30 if roe >= 16 else 20 if roe >= 11 else 10 if roe >= 6 else 5 if roe >= 1 else 0
        c_points = -100 if not profit_positive else -50 if not cash_positive else 0
        prc = p_points_prc + r_points + c_points
        
        total = max(0, gdp + prc)  # Ensure score is not negative
        breakdown = {
            'G': g_points, 'D': d_points, 'P_GDP': p_points, 'GDP': gdp,
            'P_PRC': p_points_prc, 'R': r_points, 'C': c_points, 'PRC': prc,
            'W': total
        }
        return total, breakdown
    except Exception:
        return 0, {}