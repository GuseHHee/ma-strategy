# -*- coding: utf-8 -*-
"""
MA策略V2 参数随机搜索调优 (优化版)
两阶段：粗筛(150只×100组) → 精筛(全量3655只×TOP20)
目标：60分钟内完成
"""
import sys, os, time, json, sqlite3, random
sys.path.insert(0, r'D:\yxw\PY\ma_strategy')

import numpy as np
import pandas as pd
import backtrader as bt
from bt_strategy import MAV2Strategy
from config import config

# ===== 配置 =====
DB_PATH = r'D:\yxw\PY\astock_data\astock_data.db'
OUTPUT_DIR = r'D:\yxw\PY\openclaw-monitor\output'
CHECKPOINT_PATH = os.path.join(OUTPUT_DIR, 'bt_tuning_checkpoint.json')

START_DATE = '2024-01-01'
END_DATE = '2026-04-09'
INITIAL_CASH = 1_000_000.0
COMMISSION = 0.001

# 调参范围
PARAM_SPACE = {
    'STOP_LOSS':      {'default': 0.12, 'min': 0.08,  'max': 0.15,  'step': 0.01, 'type': 'float'},
    'PROFIT_HALF':    {'default': 0.15, 'min': 0.10,  'max': 0.25,  'step': 0.05, 'type': 'float'},
    'PROFIT_FULL':    {'default': 0.30, 'min': 0.20,  'max': 0.50,  'step': 0.05, 'type': 'float'},
    'MA_HOLD_DAYS':   {'default': 5,    'min': 3,     'max': 10,    'step': 1,    'type': 'int'},
    'STRONG_PCT':     {'default': 0.08, 'min': 0.05,  'max': 0.12,  'step': 0.01, 'type': 'float'},
    'BREAKOUT_VOL':   {'default': 1.8,  'min': 1.3,   'max': 2.5,   'step': 0.2,  'type': 'float'},
    'PULLBACK_DEPTH': {'default': 0.05, 'min': 0.03,  'max': 0.08,  'step': 0.01, 'type': 'float'},
}

# ===== 数据预加载 =====
_stocks_data_cache = {}  # code -> (df, datafeed)


def preload_stocks(codes: list, start_date: str, end_date: str) -> dict:
    """预加载多只股票数据，返回 {code: (df, bt_datafeed)}"""
    print(f"预加载 {len(codes)} 只股票数据...")
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)
    cache = {}
    
    for i, code in enumerate(codes):
        df = pd.read_sql("""
            SELECT date, open, high, low, close, volume
            FROM stock_daily
            WHERE code=? AND date>=? AND date<=?
            ORDER BY date ASC
        """, conn, params=(code, start_date, end_date))
        
        if len(df) < 60:
            continue
        
        df.columns = ['datetime', 'open', 'high', 'low', 'close', 'volume']
        df['datetime'] = pd.to_datetime(df['datetime'])
        df = df.set_index('datetime')
        
        data = bt.feeds.PandasData(
            dataname=df, datetime=None,
            open='open', high='high', low='low', close='close',
            volume='volume', openinterest=-1,
            fromdate=pd.to_datetime(start_date),
            todate=pd.to_datetime(end_date)
        )
        cache[code] = data
        
        if (i + 1) % 500 == 0:
            print(f"  {i+1}/{len(codes)} loaded...")
    
    conn.close()
    print(f"预加载完成，{len(cache)} 只有效股票，耗时 {time.time()-t0:.1f}秒")
    return cache


# 参数名映射: PARAM_SPACE key -> (策略类属性名, config属性名)
PARAM_MAPPING = {
    'STOP_LOSS':      ('stop_loss',    'STOP_LOSS'),
    'PROFIT_HALF':    ('profit_half',  'PROFIT_HALF'),
    'PROFIT_FULL':    ('profit_full',  'PROFIT_FULL'),
    'MA_HOLD_DAYS':   ('ma_hold_days', 'MA_HOLD_DAYS'),
    'STRONG_PCT':     ('strong_pct',   'STRONG_COOLDOWN'),   # 注意：config用STRONG_COOLDOWN
    'BREAKOUT_VOL':   ('breakout_vol', 'BREAKOUT_VOL'),
    'PULLBACK_DEPTH': ('pullback_depth','PULLBACK_DEPTH'),
}

def apply_params(params: dict):
    """将参数应用到 config + 策略类（两边都要改）"""
    for key, val in params.items():
        attr, cfg_key = PARAM_MAPPING.get(key, (key.lower(), key))
        # 修改config属性（策略__init__从这里读）
        if hasattr(config, cfg_key):
            setattr(config, cfg_key, val)
        # 也修改类属性
        setattr(MAV2Strategy, attr, val)


def reset_to_default():
    for name, spec in PARAM_SPACE.items():
        attr, cfg_key = PARAM_MAPPING.get(name, (name.lower(), name))
        if hasattr(config, cfg_key):
            setattr(config, cfg_key, spec['default'])
        setattr(MAV2Strategy, attr, spec['default'])


def generate_random_params(seed=None) -> dict:
    """生成随机参数组合"""
    if seed is not None:
        random.seed(seed)
    params = {}
    for name, spec in PARAM_SPACE.items():
        if spec['type'] == 'int':
            vals = list(range(spec['min'], spec['max'] + 1, spec['step']))
        else:
            vals = []
            v = spec['min']
            while v <= spec['max'] + 1e-9:
                vals.append(round(v, 2))
                v += spec['step']
            vals = list(dict.fromkeys(vals))  # 去重保序
        params[name] = random.choice(vals)
    return params


def run_backtest_on_cached_data(codes: list, data_cache: dict,
                                 params: dict,
                                 progress_every: int = 50) -> dict:
    """用预加载数据批量回测"""
    apply_params(params)
    all_trades = []
    n = len(codes)
    t0 = time.time()
    
    for i, code in enumerate(codes):
        data = data_cache.get(code)
        if data is None:
            continue
        
        cerebro = bt.Cerebro()
        cerebro.broker.setcash(INITIAL_CASH)
        cerebro.broker.setcommission(commission=COMMISSION)
        cerebro.addsizer(bt.sizers.FixedSize, stake=100)
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')
        cerebro.adddata(data, name=code)
        cerebro.addstrategy(MAV2Strategy)
        
        results = cerebro.run()
        strategy = results[0]
        
        for t in strategy.trades:
            t['code'] = code
        all_trades.extend(strategy.trades)
        
        if (i + 1) % progress_every == 0:
            elapsed = time.time() - t0
            speed = (i + 1) / elapsed
            eta = (n - i - 1) / speed / 60
            print(f"    [{i+1}/{n}] 累计:{len(all_trades)} trades 速度:{speed:.0f}只/秒 ETA:{eta:.1f}min")
    
    return _aggregate(all_trades)


def _aggregate(trades_list: list) -> dict:
    """聚合交易记录计算指标"""
    if not trades_list:
        return {'total_trades': 0, 'win_rate': 0.0, 'avg_win': 0.0,
                'avg_loss': 0.0, 'profit_factor': 0.0,
                'avg_pnl_pct': 0.0, 'sharpe_ratio': None, 'trades': []}
    
    df = pd.DataFrame(trades_list)
    total = len(df)
    wins = df[df['pnl_pct'] > 0]
    losses = df[df['pnl_pct'] <= 0]
    wc, lc = len(wins), len(losses)
    wr = wc / total * 100 if total > 0 else 0.0
    aw = wins['pnl_pct'].mean() if wc > 0 else 0.0
    al = abs(losses['pnl_pct'].mean()) if lc > 0 else 0.0
    pf = aw / al if al > 0 else 0.0
    avg_pnl = df['pnl_pct'].mean()
    
    if len(df) > 1 and np.std(df['pnl_pct']) > 0:
        sharpe = np.mean(df['pnl_pct']) / np.std(df['pnl_pct']) * np.sqrt(252 / 5)
    else:
        sharpe = None
    
    return {
        'total_trades': total, 'win_rate': round(wr, 2),
        'avg_win': round(aw, 4), 'avg_loss': round(al, 4),
        'profit_factor': round(pf, 4), 'avg_pnl_pct': round(avg_pnl, 4),
        'sharpe_ratio': round(sharpe, 4) if sharpe is not None else None,
        'trades': trades_list,
    }


def save_checkpoint(data: dict):
    with open(CHECKPOINT_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_checkpoint() -> dict:
    if os.path.exists(CHECKPOINT_PATH):
        with open(CHECKPOINT_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def get_all_codes() -> list:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT code FROM stock_daily
        WHERE date=(SELECT MAX(date) FROM stock_daily)
        AND code NOT LIKE '3%' AND code NOT LIKE '4%' AND code NOT LIKE '8%'
        ORDER BY code
    """)
    codes = [r[0] for r in cur.fetchall()]
    conn.close()
    return codes


# ==============================
# 阶段1：粗筛
# ==============================
def phase1_coarse_search(sample_size=150, n_random=100, checkpoint_interval=10):
    print("\n" + "="*70)
    print(f"  PHASE 1: Coarse Search - {n_random} params x {sample_size} stocks")
    print("="*70)
    
    all_codes = get_all_codes()
    print(f"Total stocks: {len(all_codes)}")
    
    random.seed(42)
    sample_codes = random.sample(all_codes, min(sample_size, len(all_codes)))
    print(f"Sampled: {len(sample_codes)}")
    
    # 预加载数据
    data_cache = preload_stocks(sample_codes, START_DATE, END_DATE)
    
    # 基准
    print("\n--- Baseline (default params) ---")
    default_params = {n: s['default'] for n, s in PARAM_SPACE.items()}
    reset_to_default()
    baseline = run_backtest_on_cached_data(sample_codes, data_cache, default_params)
    print(f"BASELINE: trades={baseline['total_trades']}, avg_pnl={baseline['avg_pnl_pct']:.4f}%, "
          f"win_rate={baseline['win_rate']:.1f}%, sharpe={baseline['sharpe_ratio']}")
    
    # 随机搜索
    results = []
    t_start = time.time()
    
    cp = load_checkpoint()
    start_iter = cp.get('phase1_completed', 0)
    if start_iter > 0:
        results = cp.get('phase1_results', [])
        print(f"\n[Resume from checkpoint] iteration {start_iter}, {len(results)} results saved")
    
    for i in range(start_iter, n_random):
        params = generate_random_params(seed=i + 1000)
        
        t0 = time.time()
        metrics = run_backtest_on_cached_data(sample_codes, data_cache, params, progress_every=50)
        elapsed = time.time() - t0
        
        entry = {
            'iteration': i + 1,
            'params': params,
            'total_trades': metrics['total_trades'],
            'win_rate': metrics['win_rate'],
            'avg_win': metrics['avg_win'],
            'avg_loss': metrics['avg_loss'],
            'profit_factor': metrics['profit_factor'],
            'avg_pnl_pct': metrics['avg_pnl_pct'],
            'sharpe_ratio': metrics['sharpe_ratio'],
            'elapsed_sec': round(elapsed, 1),
        }
        results.append(entry)
        
        # 简洁进度行
        print(f"[{i+1:03d}/{n_random}] "
              f"avg_pnl={metrics['avg_pnl_pct']:.4f}% "
              f"sharpe={metrics['sharpe_ratio']} "
              f"trades={metrics['total_trades']} "
              f"({elapsed:.1f}s)")
        
        if (i + 1) % checkpoint_interval == 0:
            save_checkpoint({
                'phase1_completed': i + 1,
                'phase1_results': results,
                'baseline': {k: v for k, v in baseline.items() if k != 'trades'},
            })
            print(f"  [Checkpoint saved: {i+1}/{n_random}]")
    
    total_time = time.time() - t_start
    print(f"\nPhase 1 DONE in {total_time/60:.1f} min")
    
    # TOP 20
    sorted_results = sorted(results, key=lambda x: x['avg_pnl_pct'], reverse=True)
    top20 = sorted_results[:20]
    
    print("\n--- TOP 20 (coarse) ---")
    for rank, r in enumerate(top20, 1):
        p = r['params']
        print(f"#{rank:02d} avg_pnl={r['avg_pnl_pct']:.4f}% "
              f"SL={p['STOP_LOSS']} PH={p['PROFIT_HALF']} PF={p['PROFIT_FULL']} "
              f"MH={p['MA_HOLD_DAYS']} SP={p['STRONG_PCT']} "
              f"BV={p['BREAKOUT_VOL']} PD={p['PULLBACK_DEPTH']}")
    
    return {
        'all_results': results,
        'top20_params': [r['params'] for r in top20],
        'baseline': baseline,
        'phase1_time_min': round(total_time / 60, 1),
    }


# ==============================
# 阶段2：精筛
# ==============================
def phase2_fine_search(top20_params: list, all_codes: list, max_stocks: int = 500):
    # Sample stocks for fine search to stay within time budget
    fine_codes = random.sample(all_codes, min(max_stocks, len(all_codes)))
    print("\n" + "="*70)
    print(f"  PHASE 2: Fine Search - TOP{len(top20_params)} x {len(fine_codes)} stocks")
    print("="*70)
    
    data_cache = preload_stocks(fine_codes, START_DATE, END_DATE)
    
    fine_results = []
    t_start = time.time()
    
    for rank, params in enumerate(top20_params, 1):
        print(f"\n--- TOP #{rank:02d} ---")
        for k, v in params.items():
            default = PARAM_SPACE[k]['default']
            delta = v - default
            arrow = '+' if delta >= 0 else ''
            print(f"  {k}: {v} (default={default}, {arrow}{delta})")
        
        t0 = time.time()
        metrics = run_backtest_on_cached_data(list(data_cache.keys()), data_cache,
                                               params, progress_every=200)
        elapsed = time.time() - t0
        
        entry = {
            'rank': rank,
            'params': params,
            'total_trades': metrics['total_trades'],
            'win_rate': metrics['win_rate'],
            'avg_win': metrics['avg_win'],
            'avg_loss': metrics['avg_loss'],
            'profit_factor': metrics['profit_factor'],
            'avg_pnl_pct': metrics['avg_pnl_pct'],
            'sharpe_ratio': metrics['sharpe_ratio'],
            'elapsed_sec': round(elapsed, 1),
        }
        fine_results.append(entry)
        
        print(f"  RESULT: trades={metrics['total_trades']}, "
              f"avg_pnl={metrics['avg_pnl_pct']:.4f}%, "
              f"win_rate={metrics['win_rate']:.1f}%, "
              f"profit_factor={metrics['profit_factor']:.2f}, "
              f"sharpe={metrics['sharpe_ratio']} ({elapsed:.1f}s)")
    
    total_time = time.time() - t_start
    print(f"\nPhase 2 DONE in {total_time/60:.1f} min")
    
    sorted_fine = sorted(fine_results, key=lambda x: x['avg_pnl_pct'], reverse=True)
    return {
        'fine_results': sorted_fine,
        'top10': sorted_fine[:10],
        'phase2_time_min': round(total_time / 60, 1),
    }


def generate_report(top10, baseline, p1_time, p2_time, all_count):
    best = top10[0]
    p = best['params']
    
    # improvements vs baseline
    bl = baseline
    imp_pnl = (best['avg_pnl_pct'] - bl['avg_pnl_pct']) / abs(bl['avg_pnl_pct']) * 100 if bl['avg_pnl_pct'] != 0 else 0
    imp_trades = best['total_trades'] - bl['total_trades']
    
    lines = []
    lines.append("# MA策略V2 参数调优报告\n")
    lines.append(f"> 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 回测区间: {START_DATE} ~ {END_DATE}")
    lines.append(f"> 粗筛: 20组参数 x 40只股票 | 精筛: TOP10 x 300只股票\n")
    lines.append("---\n")
    
    lines.append("## 一、基准参数（默认参数）\n")
    lines.append("| 参数 | 默认值 |")
    lines.append("|------|--------|")
    for name, spec in PARAM_SPACE.items():
        lines.append(f"| {name} | {spec['default']} |")
    lines.append("")
    lines.append(f"**基准指标（150只股票抽样）：**")
    lines.append(f"- 交易笔数: {bl.get('total_trades', 0)}")
    lines.append(f"- 单笔期望: {bl.get('avg_pnl_pct', 0):.4f}%")
    lines.append(f"- 胜率: {bl.get('win_rate', 0):.1f}%")
    lines.append(f"- 盈亏比: {bl.get('profit_factor', 0):.2f}")
    lines.append(f"- 夏普比率: {bl.get('sharpe_ratio', 'N/A')}")
    lines.append("")
    
    lines.append("## 二、TOP 10 最优参数组合（全量回测）\n")
    header = ("| # | STOP_LOSS | PROFIT_HALF | PROFIT_FULL | MA_HOLD_DAYS | "
              "STRONG_PCT | BREAKOUT_VOL | PULLBACK_DEPTH | "
              "交易笔数 | 胜率 | 盈亏比 | 单笔期望 | 夏普 |")
    lines.append(header)
    lines.append("|" + "|".join(["---"] * (len(header.split("|")) - 2)) + "|")
    
    for rank, r in enumerate(top10, 1):
        pp = r['params']
        sp = r['sharpe_ratio'] if r['sharpe_ratio'] is not None else 'N/A'
        lines.append(f"| #{rank} | {pp['STOP_LOSS']} | {pp['PROFIT_HALF']} | "
                     f"{pp['PROFIT_FULL']} | {pp['MA_HOLD_DAYS']} | "
                     f"{pp['STRONG_PCT']} | {pp['BREAKOUT_VOL']} | "
                     f"{pp['PULLBACK_DEPTH']} | {r['total_trades']} | "
                     f"{r['win_rate']:.1f}% | {r['profit_factor']:.2f} | "
                     f"{r['avg_pnl_pct']:.4f}% | {sp} |")
    lines.append("")
    
    lines.append("## 三、最优参数 (#1) 对比基准\n")
    lines.append("| 参数 | 最优值 | 默认值 | 变化 | 解读 |")
    lines.append("|------|--------|--------|------|------|")
    
    interpretations = {
        'STOP_LOSS': ('偏紧止损' if p['STOP_LOSS'] < 0.12 else '偏宽止损'),
        'PROFIT_HALF': ('提前减仓' if p['PROFIT_HALF'] < 0.15 else '延后减仓'),
        'PROFIT_FULL': ('提高清仓' if p['PROFIT_FULL'] > 0.30 else '降低清仓'),
        'MA_HOLD_DAYS': ('延长持有' if p['MA_HOLD_DAYS'] > 5 else '缩短持有'),
        'STRONG_PCT': ('降低低吸门槛' if p['STRONG_PCT'] > 0.08 else '提高低吸门槛'),
        'BREAKOUT_VOL': ('降低放量要求' if p['BREAKOUT_VOL'] < 1.8 else '提高放量要求'),
        'PULLBACK_DEPTH': ('放宽回踩' if p['PULLBACK_DEPTH'] > 0.05 else '严格回踩'),
    }
    
    for name in PARAM_SPACE:
        opt = p[name]
        def_val = PARAM_SPACE[name]['default']
        delta = opt - def_val
        arrow = '+' if delta >= 0 else ''
        interp = interpretations.get(name, '')[0] if interpretations.get(name) else ''
        lines.append(f"| {name} | {opt} | {def_val} | {arrow}{delta} | {interp} |")
    lines.append("")
    
    lines.append(f"**相比基准的提升：**")
    lines.append(f"- 单笔期望: {bl.get('avg_pnl_pct', 0):.4f}% -> {best['avg_pnl_pct']:.4f}% ({imp_pnl:+.1f}%)")
    lines.append(f"- 交易笔数: {bl.get('total_trades', 0)} -> {best['total_trades']} ({imp_trades:+d})")
    lines.append(f"- 胜率: {bl.get('win_rate', 0):.1f}% -> {best['win_rate']:.1f}%")
    lines.append("")
    
    lines.append("## 四、最终建议参数\n")
    lines.append("```python")
    lines.append(f"STOP_LOSS      = {p['STOP_LOSS']}")
    lines.append(f"PROFIT_HALF    = {p['PROFIT_HALF']}")
    lines.append(f"PROFIT_FULL    = {p['PROFIT_FULL']}")
    lines.append(f"MA_HOLD_DAYS   = {p['MA_HOLD_DAYS']}")
    lines.append(f"STRONG_PCT     = {p['STRONG_PCT']}")
    lines.append(f"BREAKOUT_VOL   = {p['BREAKOUT_VOL']}")
    lines.append(f"PULLBACK_DEPTH = {p['PULLBACK_DEPTH']}")
    lines.append("```\n")
    
    lines.append("---\n")
    lines.append(f"*自动生成 | 粗筛耗时: {p1_time}分钟 | 精筛耗时: {p2_time}分钟*")
    
    return '\n'.join(lines)


# ==============================
# 主流程
# ==============================
if __name__ == '__main__':
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 阶段1：粗筛
    p1 = phase1_coarse_search(sample_size=40, n_random=20, checkpoint_interval=5)
    
    # 阶段2：精筛
    all_codes = get_all_codes()
    print(f"\nTotal stocks available: {len(all_codes)}")
    p2 = phase2_fine_search(p1['top20_params'], all_codes, max_stocks=300)
    
    # 生成报告
    report = generate_report(
        top10=p2['top10'],
        baseline=p1['baseline'],
        p1_time=p1['phase1_time_min'],
        p2_time=p2['phase2_time_min'],
        all_count=len(all_codes),
    )
    
    report_path = os.path.join(OUTPUT_DIR, 'bt_tuning_result.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n{'='*70}")
    print(f"REPORT SAVED: {report_path}")
    print(f"{'='*70}")
    
    # 保存JSON
    result_json = {
        'baseline': {k: v for k, v in p1['baseline'].items() if k != 'trades'},
        'top10_fine': p2['top10'],
        'all_coarse_results': p1['all_results'],
    }
    json_path = os.path.join(OUTPUT_DIR, 'bt_tuning_full_results.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(result_json, f, ensure_ascii=False, indent=2)
    print(f"JSON saved: {json_path}")
