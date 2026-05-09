"""
MA策略交易记录多维度分析
找出高收益、高盈亏比的规律
"""
import pandas as pd
import numpy as np

df = pd.read_csv(r'D:\yxw\PY\openclaw-monitor\output\bt_combo_new_params.csv')
df['entry_date'] = pd.to_datetime(df['entry_date'], format='mixed')
# 兼容：sell_signal → sell_type
if 'sell_signal' in df.columns and 'sell_type' not in df.columns:
    df['sell_type'] = df['sell_signal']
df['year'] = df['entry_date'].dt.year
df['month'] = df['entry_date'].dt.month

def analyze_group(sub, label):
    """统计分析一组交易"""
    if len(sub) < 10:
        return None
    pnls = sub['pnl_pct'].values.astype(float)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wr = len(wins) / len(pnls) * 100
    avg = np.mean(pnls)
    avg_w = np.mean(wins) if wins else 0
    avg_l = abs(np.mean(losses)) if losses else 0
    pf = avg_w / avg_l if avg_l > 0 else 0
    total = np.sum(pnls)
    print(f"{label:<35} {len(pnls):>5}笔  胜率{wr:5.1f}%  均收益{avg:+6.2f}%  盈亏比{pf:5.2f}  总收益{total:+10.2f}%")
    return {'label': label, 'count': len(pnls), 'winrate': wr, 'avg': avg, 'pf': pf, 'total': total}

print("=" * 75)
print("【一、按卖出方式分析】")
print("=" * 75)
print(f"{'卖出方式':<35} {'交易数':>5}   {'胜率':>6}   {'均收益':>8}   {'盈亏比':>6}   {'总收益':>10}")
print("-" * 75)
for stype, g in df.groupby('sell_type'):
    sub = g.copy()
    # 盈利交易和亏损交易
    wins = sub[sub['pnl_pct'] > 0]
    losses = sub[sub['pnl_pct'] <= 0]
    wr = len(wins) / len(sub) * 100
    avg = sub['pnl_pct'].mean()
    avg_w = wins['pnl_pct'].mean() if len(wins) > 0 else 0
    avg_l = abs(losses['pnl_pct'].mean()) if len(losses) > 0 else 0
    pf = avg_w / avg_l if avg_l > 0 else 0
    total = sub['pnl_pct'].sum()
    print(f"{stype:<35} {len(sub):>5}  胜率{wr:5.1f}%  均收益{avg:+7.2f}%  盈亏比{pf:5.2f}  总收益{total:+10.2f}%")

print()
print("=" * 75)
print("【二、按持仓天数分析】")
print("=" * 75)
bins = [0, 3, 5, 10, 15, 21]
labels = ['1-3天', '4-5天', '6-10天', '11-15天', '16-20天']
df['hold_bin'] = pd.cut(df['hold_days'], bins=bins, labels=labels, right=True)
for lb, g in df.groupby('hold_bin', observed=True):
    sub = g.copy()
    wins = sub[sub['pnl_pct'] > 0]
    losses = sub[sub['pnl_pct'] <= 0]
    wr = len(wins) / len(sub) * 100
    avg = sub['pnl_pct'].mean()
    avg_w = wins['pnl_pct'].mean() if len(wins) > 0 else 0
    avg_l = abs(losses['pnl_pct'].mean()) if len(losses) > 0 else 0
    pf = avg_w / avg_l if avg_l > 0 else 0
    print(f"持仓{lb:<10} {len(sub):>5}笔  胜率{wr:5.1f}%  均收益{avg:+7.2f}%  盈亏比{pf:5.2f}  均盈利{avg_w:+6.2f}%  均亏损{avg_l:+6.2f}%")

print()
print("=" * 75)
print("【三、按月份分析（建仓月份）】")
print("=" * 75)
month_names = ['', '1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月']
for m in range(1, 13):
    sub = df[df['month'] == m].copy()
    if len(sub) < 10:
        continue
    wins = sub[sub['pnl_pct'] > 0]
    losses = sub[sub['pnl_pct'] <= 0]
    wr = len(wins) / len(sub) * 100
    avg = sub['pnl_pct'].mean()
    avg_w = wins['pnl_pct'].mean() if len(wins) > 0 else 0
    avg_l = abs(losses['pnl_pct'].mean()) if len(losses) > 0 else 0
    pf = avg_w / avg_l if avg_l > 0 else 0
    total = sub['pnl_pct'].sum()
    print(f"{month_names[m]:<6}  {len(sub):>5}笔  胜率{wr:5.1f}%  均收益{avg:+7.2f}%  盈亏比{pf:5.2f}  总收益{total:+10.2f}%")

print()
print("=" * 75)
print("【四、按年度分析】")
print("=" * 75)
for yr, g in df.groupby('year'):
    sub = g.copy()
    wins = sub[sub['pnl_pct'] > 0]
    losses = sub[sub['pnl_pct'] <= 0]
    wr = len(wins) / len(sub) * 100
    avg = sub['pnl_pct'].mean()
    avg_w = wins['pnl_pct'].mean() if len(wins) > 0 else 0
    avg_l = abs(losses['pnl_pct'].mean()) if len(losses) > 0 else 0
    pf = avg_w / avg_l if avg_l > 0 else 0
    total = sub['pnl_pct'].sum()
    print(f"{yr}年  {len(sub):>5}笔  胜率{wr:5.1f}%  均收益{avg:+7.2f}%  盈亏比{pf:5.2f}  总收益{total:+10.2f}%")

print()
print("=" * 75)
print("【五、高收益 vs 低收益 特征对比】")
print("=" * 75)
# 按收益分层
df['pnl_rank'] = pd.qcut(df['pnl_pct'], q=5, labels=['极差', '差', '中', '好', '极好'])
for rank, g in df.groupby('pnl_rank', observed=True):
    sub = g.copy()
    print(f"\n{rank}（共{len(sub)}笔，均收益范围: {sub['pnl_pct'].min():+.1f}% ~ {sub['pnl_pct'].max():+.1f}%）")
    # 卖出方式分布
    sell_dist = sub['sell_type'].value_counts(normalize=True) * 100
    print(f"  卖出方式: {dict(sell_dist.round(1))}")
    print(f"  持仓天数: 均值={sub['hold_days'].mean():.1f}天, 中位数={sub['hold_days'].median():.0f}天")
    # 月份分布
    month_dist = sub['month'].value_counts(normalize=True).sort_index() * 100
    top3_months = month_dist.head(3)
    print(f"  高频月份: {dict(top3_months.round(1))}")

print()
print("=" * 75)
print("【六、盈利交易 vs 亏损交易 关键差异】")
print("=" * 75)
wins_df = df[df['pnl_pct'] > 0]
loss_df = df[df['pnl_pct'] <= 0]
print(f"盈利交易: {len(wins_df)}笔  均持仓{wins_df['hold_days'].mean():.1f}天  均盈利{wins_df['pnl_pct'].mean():+.2f}%")
print(f"亏损交易: {len(loss_df)}笔  均持仓{loss_df['hold_days'].mean():.1f}天  均亏损{loss_df['pnl_pct'].mean():+.2f}%")
print()
print("盈利交易卖出方式:")
print(wins_df['sell_type'].value_counts())
print()
print("亏损交易卖出方式:")
print(loss_df['sell_type'].value_counts())

print()
print("=" * 75)
print("【七、不同卖出方式的盈亏对比】")
print("=" * 75)
for stype in df['sell_type'].unique():
    sub = df[df['sell_type'] == stype]
    w = sub[sub['pnl_pct'] > 0]
    l = sub[sub['pnl_pct'] <= 0]
    print(f"{stype:<20} 盈利{w['pnl_pct'].mean():+.2f}% ({len(w)}笔)  亏损{l['pnl_pct'].mean():+.2f}% ({len(l)}笔)  胜率{len(w)/len(sub)*100:.1f}%  均收益{sub['pnl_pct'].mean():+.2f}%")

print()
print("=" * 75)
print("【八、持仓天数 + 卖出方式 交叉分析】")
print("=" * 75)
# 找最佳组合
combo_stats = df.groupby(['hold_bin', 'sell_type'], observed=True).agg(
    count=('pnl_pct', 'count'),
    avg=('pnl_pct', 'mean'),
    pf=('pnl_pct', lambda x: x[x>0].mean()/abs(x[x<=0].mean()) if len(x[x<=0])>0 else 0)
).reset_index()
combo_stats = combo_stats[combo_stats['count'] >= 50].sort_values('avg', ascending=False)
print("Top15 最佳组合（持仓天数 + 卖出方式）:")
print(combo_stats.head(15).to_string(index=False))
