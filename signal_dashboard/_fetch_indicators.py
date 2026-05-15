"""补充技术指标到 signal_info（api_reanalyze 和批量分析时缺失这些字段）"""

def _fetch_indicators(code: str, signal_date: str) -> dict:
    """从数据库计算技术指标，返回 dict"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT date, open, high, low, close, volume
        FROM stock_daily
        WHERE code=? AND date<=? AND close IS NOT NULL
        ORDER BY date DESC LIMIT 60
    """, (code, signal_date))
    rows = c.fetchall()
    conn.close()
    if len(rows) < 20:
        return {}

    prices = [float(r[4]) for r in rows]
    volumes = [float(r[5]) for r in rows]
    close = prices[0]

    ma5 = np.mean(prices[:5])
    ma10 = np.mean(prices[:10])
    ma20 = np.mean(prices[:20])
    vol5 = np.mean(volumes[:5])
    # vol_ratio: 当日成交量 / 5日均量（当日量是volumes[0]）
    vol_ratio = volumes[0] / vol5 if vol5 > 0 else 0
    rsi_val = _rsi(prices, 14) if len(prices) >= 15 else 50
    dist_ma5 = (close - ma5) / ma5 * 100 if ma5 > 0 else 0
    dist_ma20 = (close - ma20) / ma20 * 100 if ma20 > 0 else 0

    return {
        'ma5': round(ma5, 2),
        'ma10': round(ma10, 2),
        'ma20': round(ma20, 2),
        'vol_ratio': round(vol_ratio, 2),
        'rsi': round(rsi_val, 1),
        'dist_ma5': round(dist_ma5, 2),
        'dist_ma20': round(dist_ma20, 2),
    }