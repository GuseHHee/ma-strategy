"""
买入信号质量分析
重新扫一遍市场，记录每笔交易所对应的买入信号类型
然后分析三种买入信号的质量差异
"""
import sqlite3
import pandas as pd
import numpy as np
from collections import defaultdict

DB = r'D:\yxw\PY\astock_data\astock_data.db'

# ========== 策略参数 ==========
PICK_MA20_RISE_PCT = 15
PICK_VOL_RATIO = 1.3
BUY_PULLBACK_DEPTH = 0.05
BUY_BREAKOUT_VOL = 1.8
BUY_STRONG_COOLDOWN = 0.05
SELL_STOP_LOSS = 0.08
SELL_PROFIT_HALF = 0.20
SELL_PROFIT_FULL = 0.40
SELL_MA5_TURNS_DOWN = True

def calc_indicators(df):
    df = df.copy()
    df = df.sort_values('date').reset_index(drop=True)
    close  = df['close'].values.astype(float)
    volume = df['volume'].values.astype(float)
    high   = df['high'].values.astype(float)
    open_arr = df['open'].values.astype(float)
    n = len(df)

    df['MA5']  = pd.Series(close).rolling(5).mean().values
    df['MA10'] = pd.Series(close).rolling(10).mean().values
    df['MA20'] = pd.Series(close).rolling(20).mean().values
    df['MA60'] = pd.Series(close).rolling(60).mean().values
    df['VOL5']  = pd.Series(volume).rolling(5).mean().values
    df['VOL60'] = pd.Series(volume).rolling(60).mean().values
    df['is_volume_up'] = (df['VOL5'] > df['VOL60'] * PICK_VOL_RATIO).astype(int)
    df['is_boom_volume'] = (volume > df['VOL5'] * BUY_BREAKOUT_VOL).astype(int)
    df['pct_5']  = pd.Series(close).pct_change(5).values * 100
    df['pct_20'] = pd.Series(close).pct_change(20).values * 100

    high_20 = pd.Series(high).rolling(20, min_periods=1).max().shift(1)
    df['is_new_high_20'] = (high >= high_20).astype(int)
    df['ma20_rises'] = (df['MA20'] > df['MA20'].shift(1)).astype(int)
    df['is_bullish'] = ((close > df['MA5'].values) & (df['MA5'].values > df['MA10'].values) & (df['MA10'].values > df['MA20'].values)).astype(int)
    df['is_bullish_candle'] = (close > open_arr).astype(int)

    recent_5d_new_high = np.zeros(n)
    for i in range(20, n):
        recent_5d_max = float(np.max(high[max(0, i-4):i+1]))
        recent_20d_max = float(np.max(high[max(0, i-19):i+1]))
        recent_5d_new_high[i] = 1 if recent_5d_max == recent_20d_max and recent_20d_max > 0 else 0
    df['recent_5d_new_high'] = recent_5d_new_high
    df['ma5_turns_down'] = ((df['MA5'].shift(1) > df['MA5']) & (df['MA5'] > 0)).astype(int)
    return df

def pass_pick_conditions(row):
    try:
        return (row['is_bullish'] == 1 and row['is_volume_up'] == 1 and row['ma20_rises'] == 1 and
                row['recent_5d_new_high'] == 1 and row['pct_20'] > PICK_MA20_RISE_PCT and row['is_bullish_candle'] == 1)
    except:
        return False

def check_buy_signal(df, i):
    """返回买入信号类型"""
    row = df.iloc[i]
    ma5 = row['MA5']
    low_p = row['low']
    close_p = row['close']
    open_p = row['open']

    # 信号1：回踩MA5企稳（最低价<MA5，但收盘>MA5，开盘也在MA5下方）
    if low_p < ma5 < close_p and ma5 > 0 and open_p < ma5:
        depth = (ma5 - low_p) / ma5
        if depth < BUY_PULLBACK_DEPTH:
            return 'pullback_ma5'

    # 信号2：放量突破20日新高（爆量+创20日新高）
    if df.iloc[i]['is_boom_volume'] == 1 and df.iloc[i]['is_new_high_20'] == 1:
        return 'breakout_high'

    # 信号3：强势低吸（收盘>MA5但近5日跌幅较大）
    if close_p > ma5 and ma5 > 0 and df.iloc[i]['pct_5'] < -BUY_STRONG_COOLDOWN * 100:
        return 'strong_buy'
    return None

def check_sell(entry_price, current_price, row, prev_row, hold_days, half_sold):
    pnl = (current_price - entry_price) / entry_price
    signal = None
    half = half_sold
    if pnl <= -SELL_STOP_LOSS:
        signal = 'stop_loss'
    elif current_price < row['MA20'] and row['MA20'] > 0:
        signal = 'ma20_break'
    elif pnl >= SELL_PROFIT_HALF and not half_sold:
        signal = 'profit_half'
        half = True
    elif pnl >= SELL_PROFIT_FULL:
        signal = 'profit_full'
    elif (hold_days > 0 and row['ma5_turns_down'] == 1 and current_price < row['MA5'] and
          prev_row is not None and prev_row['close'] < prev_row['MA5']):
        signal = 'ma5_turns_down'
    return signal, half

# ========== 扫描买入信号并回测 ==========
print("加载股票列表...")
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("SELECT DISTINCT code FROM stock_daily WHERE code NOT LIKE '3%' AND code NOT LIKE '4%' AND code NOT LIKE '8%' ORDER BY code")
codes = [r[0] for r in cur.fetchall()]
conn.close()
print(f"股票数量: {len(codes)}")

# 存储所有交易及买入信号类型
all_trades = []
# 信号统计
signal_counts = defaultdict(int)
done = 0

for code in codes:
    done += 1
    if done % 500 == 0:
        print(f"进度: {done}/{len(codes)}, 交易笔数: {len(all_trades)}")

    conn = sqlite3.connect(DB)
    df = pd.read_sql("SELECT date, open, high, low, close, volume FROM stock_daily WHERE code=? AND date>='2023-01-01' AND date<='2026-04-07' ORDER BY date", conn, params=(code,))
    conn.close()
    if len(df) < 60:
        continue
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna().reset_index(drop=True)
    if len(df) < 60:
        continue
    df = calc_indicators(df)

    in_position = False
    entry_price = 0.0
    entry_date = None
    hold_days = 0
    half_sold = False
    buy_signal_type = None

    for i in range(60, len(df) - 1):
        row = df.iloc[i]
        prev_row = df.iloc[i-1] if i > 0 else None

        if not in_position:
            if not pass_pick_conditions(row):
                continue
            buy_signal = check_buy_signal(df, i)
            if not buy_signal:
                continue
            signal_counts[buy_signal] += 1
            buy_signal_type = buy_signal
            entry_price = float(df.iloc[i+1]['open'])
            entry_date = str(df.iloc[i+1]['date'])
            in_position = True
            hold_days = 0
            half_sold = False
        else:
            hold_days += 1
            current_price = float(df.iloc[i]['close'])
            sell_signal, half_sold = check_sell(entry_price, current_price, row, prev_row, hold_days, half_sold)

            if sell_signal == 'profit_half':
                pnl_pct = (current_price - entry_price) / entry_price * 100
                all_trades.append({
                    'code': code, 'buy_signal': buy_signal_type,
                    'entry_date': entry_date, 'entry_price': entry_price,
                    'exit_date': str(df.iloc[i]['date']), 'exit_price': current_price,
                    'sell_type': 'profit_half', 'hold_days': hold_days,
                    'pnl_pct': round(pnl_pct, 2), 'position_ratio': 0.5
                })
                hold_days -= 1
                half_sold = True
            elif sell_signal is not None:
                pnl_pct = (current_price - entry_price) / entry_price * 100
                all_trades.append({
                    'code': code, 'buy_signal': buy_signal_type,
                    'entry_date': entry_date, 'entry_price': entry_price,
                    'exit_date': str(df.iloc[i]['date']), 'exit_price': current_price,
                    'sell_type': sell_signal, 'hold_days': hold_days,
                    'pnl_pct': round(pnl_pct, 2),
                    'position_ratio': 0.5 if half_sold else 1.0
                })
                in_position = False
                entry_price = 0.0

print(f"\n扫描完成! 总交易: {len(all_trades)} 笔")
print(f"\n买入信号分布:")
for sig, cnt in sorted(signal_counts.items(), key=lambda x: -x[1]):
    print(f"  {sig}: {cnt}笔")

# 保存结果
df_trades = pd.DataFrame(all_trades)
df_trades['entry_date'] = pd.to_datetime(df_trades['entry_date'])
df_trades['year'] = df_trades['entry_date'].dt.year
df_trades['month'] = df_trades['entry_date'].dt.month
df_trades.to_csv(r'D:\yxw\PY\ma_strategy\trades_with_signal.csv', index=False, encoding='utf-8-sig')

# ========== 分析三种买入信号的质量 ==========
print()
print("=" * 80)
print("【三种买入信号质量分析】")
print("=" * 80)

bins = [0, 3, 5, 10, 15, 21]
labels = ['1-3天', '4-5天', '6-10天', '11-15天', '16-20天']

for sig in ['pullback_ma5', 'breakout_high', 'strong_buy']:
    sub = df_trades[df_trades['buy_signal'] == sig].copy()
    if len(sub) < 5:
        continue
    pnls = sub['pnl_pct'].astype(float).values
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wr = len(wins) / len(pnls) * 100
    avg = np.mean(pnls)
    avg_w = np.mean(wins) if wins else 0
    avg_l = abs(np.mean(losses)) if losses else 0
    pf = avg_w / avg_l if avg_l > 0 else 0

    print(f"\n{'─' * 70}")
    print(f"【{sig}】共 {len(sub)} 笔 | 胜率: {wr:.1f}% | 均收益: {avg:+.2f}% | 盈亏比: {pf:.2f}")
    print(f"{'─' * 70}")

    # 持仓天数分布
    sub['hb'] = pd.cut(sub['hold_days'], bins=bins, labels=labels, right=True)
    print(f"  持仓天数分布:")
    for lb, g in sub.groupby('hb', observed=True):
        pct = len(g) / len(sub) * 100
        wr_h = (g['pnl_pct'] > 0).mean() * 100
        avg_h = g['pnl_pct'].astype(float).mean()
        bar = "█" * int(pct / 3)
        print(f"    {lb}: {len(g)}笔({pct:.0f}%)  胜率{wr_h:.1f}%  均收益{avg_h:+.2f}%  {bar}")

    # 卖出方式分布
    print(f"  卖出方式分布:")
    for st, cnt in sub['sell_type'].value_counts().items():
        sub_st = sub[sub['sell_type'] == st]
        pct = cnt / len(sub) * 100
        avg_st = sub_st['pnl_pct'].astype(float).mean()
        print(f"    {st}: {cnt}笔({pct:.0f}%)  均收益{avg_st:+.2f}%")

    # 月份分布
    print(f"  月份分布:")
    for m in range(1, 13):
        sub_m = sub[sub['month'] == m]
        if len(sub_m) < 3:
            continue
        wr_m = (sub_m['pnl_pct'] > 0).mean() * 100
        avg_m = sub_m['pnl_pct'].astype(float).mean()
        print(f"    {m}月: {len(sub_m)}笔  胜率{wr_m:.1f}%  均收益{avg_m:+.2f}%")

print()
print("=" * 80)
print("【买入信号横向对比】")
print("=" * 80)
print()
print(f"{'买入信号':<20} {'交易数':<10} {'胜率':<10} {'均收益':<12} {'盈亏比':<8} {'均持仓天':<10} {'均盈利':<10} {'均亏损':<10}")
print("-" * 100)

for sig in ['pullback_ma5', 'breakout_high', 'strong_buy']:
    sub = df_trades[df_trades['buy_signal'] == sig]
    if len(sub) < 5:
        continue
    pnls = sub['pnl_pct'].astype(float).values
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wr = len(wins) / len(pnls) * 100
    avg = np.mean(pnls)
    avg_w = np.mean(wins) if wins else 0
    avg_l = abs(np.mean(losses)) if losses else 0
    pf = avg_w / avg_l if avg_l > 0 else 0
    hold = sub['hold_days'].mean()
    sig_name = {'pullback_ma5': '回踩MA5', 'breakout_high': '放量突破', 'strong_buy': '强势低吸'}[sig]
    print(f"{sig_name:<18} {len(sub):<10} {wr:.1f}%     {avg:+.2f}%     {pf:.2f}    {hold:.1f}天     {avg_w:+.2f}%    {avg_l:+.2f}%")

print()
print("=" * 80)
print("【买入信号 × 市场环境 分析】")
print("=" * 80)
print()
print("按月份对比三种信号的表现:")
print()
print(f"{'月份':<6}", end="")
for sig in ['回踩MA5', '放量突破', '强势低吸']:
    print(f"{sig:<14}", end="")
print()
print("-" * 60)

monthly_comparison = {}
for m in range(1, 13):
    row_data = [str(m) + '月']
    has_data = False
    for sig_cn, sig_en in [('回踩MA5', 'pullback_ma5'), ('放量突破', 'breakout_high'), ('强势低吸', 'strong_buy')]:
        sub = df_trades[(df_trades['buy_signal'] == sig_en) & (df_trades['month'] == m)]
        if len(sub) >= 3:
            wr = (sub['pnl_pct'] > 0).mean() * 100
            avg = sub['pnl_pct'].astype(float).mean()
            row_data.append(f"胜{wr:>4.0f}% 均{avg:>+5.1f}%")
            has_data = True
        else:
            row_data.append(f"  <3笔   ")
    if has_data:
        print(f"{row_data[0]:<6}{row_data[1]:<14}{row_data[2]:<14}{row_data[3]:<14}")

print()
print("=" * 80)
print("【最强买入信号组合】")
print("=" * 80)

# 按信号 + 持仓天数交叉分析
print()
print("信号类型 × 持仓天数 效果对比:")
print()
print(f"{'持仓天数':<10}", end="")
for sig_cn in ['回踩MA5', '放量突破', '强势低吸']:
    print(f"{sig_cn:<16}", end="")
print()
print("-" * 80)

for lb in labels:
    row_parts = [f"{lb:<10}"]
    has_data = False
    for sig_en, sig_cn in [('pullback_ma5', '回踩MA5'), ('breakout_high', '放量突破'), ('strong_buy', '强势低吸')]:
        sub = df_trades[(df_trades['buy_signal'] == sig_en)]
        sub_hb = sub[sub['hb'] == lb] if 'hb' in sub.columns else sub[sub['hold_days'].between(int(lb.split('-')[0]) if '-' in lb else 0, int(lb.split('-')[1]) if '-' in lb else 999)]
        if len(sub_hb) >= 3:
            wr = (sub_hb['pnl_pct'] > 0).mean() * 100
            avg = sub_hb['pnl_pct'].astype(float).mean()
            row_parts.append(f"胜{wr:>4.0f}% 均{avg:>+6.1f}%({len(sub_hb)}笔)")
            has_data = True
        else:
            row_parts.append(f"  <3笔")
    if has_data:
        print("".join(row_parts))

print()
print("=" * 80)
print("【核心结论】")
print("=" * 80)
print("""
三种买入信号特征总结:

1. 回踩MA5 (pullback_ma5):
   - 信号特征: 最低价跌破MA5后快速收回，收盘站上MA5
   - 优点: 入场成本低，有均线支撑反弹
   - 缺点: A股MA5太敏感，经常假突破后继续跌
   - 最佳场景: 强势股缩量回踩MA5，不破均线

2. 放量突破 (breakout_high):
   - 信号特征: 当日成交量>均量1.8倍 + 创20日新高
   - 优点: 趋势确认，动能强劲
   - 缺点: 假突破多，经常放量后直接跌回来
   - 最佳场景: 大盘配合，板块共振的突破

3. 强势低吸 (strong_buy):
   - 信号特征: 近5日跌幅较大但收盘在MA5上方
   - 优点: 买在相对低位，安全边际高
   - 缺点: 跌幅大可能意味着趋势破坏，低吸变接飞刀
   - 最佳场景: 强势股短暂回调，不破上升趋势

优化方向:
- 三种信号中，哪种胜率/均收益最高 → 加大仓位
- 哪种表现最差 → 减少或取消
- 结合月份，不同月份侧重不同信号
""")
