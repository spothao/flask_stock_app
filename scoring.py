def calculate_score(stock_data, stock=None):
    # Use stored values if available, else from JSON
    growth = float(stock.net_profit_5y_cagr) if stock and stock.net_profit_5y_cagr is not None else float(stock_data.get('growth', {}).get('net_profit_5y_cagr', 0))
    div_yield = float(stock.div_yield) if stock and stock.div_yield is not None else float(stock_data.get('Stock', {}).get('DY', 0))
    per = float(stock.pe_ratio) if stock and stock.pe_ratio is not None else float(stock_data.get('Stock', {}).get('PE', 999))
    roe = float(stock.roe) if stock and stock.roe is not None else float(stock_data.get('Stock', {}).get('ROE', 0))
    
    try:
        # Latest quarter for margin (still from JSON as it’s computed)
        reports = stock_data.get('FinancialReport', [])
        if reports:
            latest = max(reports, key=lambda r: r['current_quarter'])
            profit = float(latest.get('profit_loss', 0))
            revenue = float(latest.get('revenue', 1))
            margin = (profit / revenue * 100) if revenue else 0
        else:
            margin = 0
        
        # Cash flow: assume positive if operating_cf >0 in latest (simplified; check sign)
        cash_positive = any(float(r.get('operating_cf', 0)) > 0 for r in reports[-4:])  # Last year approx
        profit_positive = profit > 0
        
        # GDP
        g_points = 50 if growth >= 15 else 40 if growth >= 10 else 30 if growth >= 6 else 20 if growth >= 1 else 0
        d_points = 20 if div_yield >= 7 else 15 if div_yield >= 5 else 10 if div_yield >= 3 else 5 if div_yield >= 1 else 0
        p_points = 30 if per <= 9 else 20 if per <= 15 else 10 if per <= 24 else 5
        gdp = g_points + d_points + p_points
        
        # PRC
        p_points_prc = 30 if margin >= 16 else 20 if margin >= 11 else 10 if margin >= 6 else 5 if margin >= 1 else 0
        r_points = 30 if roe >= 16 else 20 if roe >= 11 else 10 if roe >= 6 else 5 if roe >= 1 else 0
        c_points = -100 if not profit_positive else -50 if not cash_positive else 0
        prc = p_points_prc + r_points + c_points
        
        total = gdp + prc
        breakdown = {
            'G': g_points, 'D': d_points, 'P_GDP': p_points, 'GDP': gdp,
            'P_PRC': p_points_prc, 'R': r_points, 'C': c_points, 'PRC': prc,
            'W': total
        }
        return total, breakdown
    except Exception:
        return 0, {}