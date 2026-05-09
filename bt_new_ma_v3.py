"""
MA策略新版 - 极速向量化回测 v3
核心优化：预计算所有入场信号的未来收益，避免逐条扫描
"""

import pandas as pd
import numpy as np
import sqlite3
import os
from itertools import product
from datetime import datetime

DB_PATH = r"D:\yxw\PY\astock_data\astock_data.db"
OUTPUT_DIR = r"D:\yxw\PY\openclaw-monitor\output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

DATA_START = "2023-01-01"
DATA_END = "2026-04-14"

# 网格参数（5×4×5 = 100组）
STOP_LOSS_VALUES = [0.08, 0.10, 0.12, 0.13, 0.15]
TAKE_PROFIT_VALUES = [0.15, 0.20, 0.25, 0.30]
HOLD_DAYS_VALUES = [5, 7, 9, 11, 15]
MAX_HOLD = max(HOLD_DAYS_VALUES)  # 15

BASELINE = {"STOP_LOSS": 0.13, "TAKE_PROFIT": 0.20, "HOLD_DAYS": 9}


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)


def load_and_prepare():
    """加载数据并预处理"""
    conn = sqlite3.connect(DB_PATH)
    query = f"""
        SELECT code, name, date, open, high, low, close, volume
        FROM stock_daily
        WHERE code NOT LIKE '688%'
          AND close <= 500
          AND date >= '{DATA_START}'
          AND date <= '{DATA_END}'
        ORDER BY code, date
    """
    df = pd.read_sql(query, conn, parse_dates=["date"])
    conn.close()
    log(f"Loaded {len(df):,} rows, {df['code'].nunique()} stocks")
    return df


def prepare_indicators(df):
    """计算所有指标"""
    df = df.sort_values(["code", "date"]).reset_index(drop=True)

    # MA
    df["ma5"] = df.groupby("code")["close"].transform(lambda x: x.rolling(5).mean())
    df["ma10"] = df.groupby("code")["close"].transform(lambda x: x.rolling(10).mean())
    df["vol_ma5"] = df.groupby("code")["volume"].transform(lambda x: x.rolling(5).mean())

    # Lag值
    for lag in [1, 2, 3, 4, 5]:
        df[f"close_lag{lag}"] = df.groupby("code")["close"].shift(lag)
        df[f"ma5_lag{lag}"] = df.groupby("code")["ma5"].shift(lag)
        df[f"ma10_lag{lag}"] = df.groupby("code")["ma10"].shift(lag)
        df[f"vol_ma5_lag{lag}"] = df.groupby("code")["vol_ma5"].shift(lag)

    df["vol_ratio"] = df["volume"] / df["vol_ma5_lag1"]
    df["vol_ratio"] = df["vol_ratio"].replace([np.inf, -np.inf], 0).fillna(0)

    # 入场信号：连续3天在MA5下方，然后重新站上
    df["below1"] = df["close_lag1"] < df["ma5_lag1"]
    df["below2"] = df["close_lag2"] < df["ma5_lag2"]
    df["below3"] = df["close_lag3"] < df["ma5_lag3"]
    df["crossed_above"] = (df["close"] > df["ma5"]) & (df["close_lag1"] <= df["ma5_lag1"])

    cond = (
        df["crossed_above"] &
        df["below1"] & df["below2"] & df["below3"] &
        (df["vol_ratio"] >= 1.3)
    )
    df["is_entry"] = cond

    # MA5死叉（未来日期，用lag检查：当天MA5<=MA10 且 前一天MA5>MA10）
    df["ma5_death_cross"] = (
        (df["ma5_lag1"] > df["ma10_lag1"]) &
        (df["ma5"] <= df["ma10"])
    )

    return df


def precompute_future_prices(df, max_hold=MAX_HOLD):
    """
    预计算：每个股票，每天 → 未来第1~max_hold天的收盘价
    返回一个dict: {(code, date): {hold_days: exit_price}}
    """
    log("Precomputing future exit prices...")
    n = len(df)

    # 建立索引：(code, date) -> row index
    df_indexed = df.set_index(["code", "date"])

    # 预分配未来价格数组
    codes = df["code"].values
    dates = df["date"].values
    closes = df["close"].values
    ma5_death = df["ma5_death_cross"].values

    future_cache = {}  # key: (code, date, hold_days) -> (exit_price, exit_reason)

    log(f"Iterating {n:,} rows to precompute future prices...")

    for idx in range(n):
        code = codes[idx]
        date = dates[idx]

        for hd in range(1, max_hold + 1):
            future_idx = idx + hd
            if future_idx >= n:
                break
            if codes[future_idx] != code:
                break

            exit_price = closes[future_idx]
            # 记录：未来第hd天的收盘价
            key = (code, date, hd)
            future_cache[key] = exit_price

        if idx > 0 and idx % 500000 == 0:
            log(f"  Processed {idx:,}/{n:,} ({100*idx/n:.0f}%)")

    log(f"Precomputed {len(future_cache):,} future price entries")
    return future_cache


def run_backtest_fast(entries_df, future_cache, stop_loss, take_profit, hold_days):
    """
    快速回测：用预计算的未来价格判断出场
    entries_df: 预筛选好的入场信号
    future_cache: 预计算的未来收盘价 {(code, date, hold_days): price}
    """
    trades = []
    n = len(entries_df)

    for i, (_, entry) in enumerate(entries_df.iterrows()):
        code = entry["code"]
        entry_date = entry["date"]
        entry_price = entry["close"]
        entry_vol_ratio = entry["vol_ratio"]

        exit_reason = None
        exit_price = entry_price
        hold_days_actual = 0

        # 逐天检查（最多hold_days天）
        for hd in range(1, hold_days + 1):
            key = (code, entry_date, hd)
            if key not in future_cache:
                break

            exit_price = future_cache[key]
            ret = (exit_price - entry_price) / entry_price
            hold_days_actual = hd

            # 止损（优先）
            if ret <= -stop_loss:
                exit_reason = "stop_loss"
                break

            # 持满HOLD_DAYS：止盈 or 到期
            if hd == hold_days:
                target = entry_price * (1 + take_profit)
                if exit_price >= target:
                    exit_reason = "take_profit"
                else:
                    exit_reason = "hold_expired"
                break

            # MA5死叉（持满2天后生效）
            if hd >= 2:
                # 检查第hd天是否有死叉
                death_key = (code, entry_date, hd)
                # ma5_death_cross在entry的日期（future）上检查
                # 用future_cache的索引方式
                pass

        # 如果没触发任何出场（数据不足）
        if exit_reason is None:
            key = (code, entry_date, hold_days)
            if key in future_cache:
                exit_price = future_cache[key]
                exit_reason = "end_of_data"
            else:
                exit_reason = "no_data"

        ret = (exit_price - entry_price) / entry_price

        trades.append({
            "code": code,
            "name": entry["name"],
            "signal_date": pd.Timestamp(entry_date).strftime("%Y-%m-%d"),
            "entry_price": round(entry_price, 3),
            "exit_price": round(exit_price, 3),
            "hold_days": hold_days_actual,
            "return": round(ret, 4),
            "exit_reason": exit_reason,
            "vol_ratio": round(entry_vol_ratio, 2),
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "hold_days_param": hold_days,
        })

        if i > 0 and i % 20000 == 0:
            log(f"    Processed {i:,}/{n:,} trades for SL={stop_loss} TP={take_profit} HD={hold_days}")

    return trades


def evaluate_trades(trades, year_start=2024, year_end=2025):
    if not trades:
        return None
    df = pd.DataFrame(trades)
    df["exit_date"] = pd.to_datetime(df["signal_date"]) + pd.to_timedelta(df["hold_days"], unit="D")
    df_eval = df[
        (df["exit_date"] >= f"{year_start}-01-01") &
        (df["exit_date"] <= f"{year_end}-12-31")
    ].copy()

    if len(df_eval) == 0:
        return None

    total_return = df_eval["return"].sum()
    wins = df_eval[df_eval["return"] > 0]
    losses = df_eval[df_eval["return"] <= 0]
    win_rate = len(wins) / len(df_eval) if len(df_eval) > 0 else 0
    avg_win = wins["return"].mean() if len(wins) > 0 else 0
    avg_loss = losses["return"].mean() if len(losses) > 0 else 0
    pl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    score = total_return * win_rate / max(np.sqrt(len(df_eval)), 1)

    return {
        "num_trades": len(df_eval),
        "total_return": round(total_return, 4),
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 4) if avg_win != 0 else 0,
        "avg_loss": round(avg_loss, 4) if avg_loss != 0 else 0,
        "profit_loss_ratio": round(pl_ratio, 4),
        "score": round(score, 6),
    }


def main():
    global STOP_LOSS_VALUES, TAKE_PROFIT_VALUES, HOLD_DAYS_VALUES

    t0 = datetime.now()
    log("=" * 50)
    log("STARTING: bt_new_ma_v3.py")
    log("=" * 50)

    df = load_and_prepare()
    log(f"Loading took: {datetime.now() - t0}")

    t1 = datetime.now()
    df = prepare_indicators(df)
    log(f"Indicators took: {datetime.now() - t1}")

    entries = df[df["is_entry"]].copy()
    log(f"Entry signals: {len(entries):,}")

    t2 = datetime.now()
    future_cache = precompute_future_prices(df, MAX_HOLD)
    log(f"Future prices took: {datetime.now() - t2}")

    # 保留必要列用于回测
    entry_cols = entries[["code", "name", "date", "close", "vol_ratio"]].copy()

    param_grid = list(product(STOP_LOSS_VALUES, TAKE_PROFIT_VALUES, HOLD_DAYS_VALUES))
    total = len(param_grid)
    log(f"Grid: {total} combos ({len(STOP_LOSS_VALUES)}x{len(TAKE_PROFIT_VALUES)}x{len(HOLD_DAYS_VALUES)})")
    log("Starting grid search...")

    all_results = []
    all_trades = []

    for idx, (sl, tp, hd) in enumerate(param_grid):
        t_start = datetime.now()
        trades = run_backtest_fast(entry_cols, future_cache, sl, tp, hd)
        t_end = datetime.now()

        stats = evaluate_trades(trades)
        if stats:
            stats["STOP_LOSS"] = sl
            stats["TAKE_PROFIT"] = tp
            stats["HOLD_DAYS"] = hd
            all_results.append(stats)
            for t in trades:
                t["score"] = stats["score"]
            all_trades.extend(trades)

        elapsed = (t_end - t_start).total_seconds()
        log(f"  [{idx+1:3d}/{total}] SL={sl} TP={tp} HD={hd}  trades={stats['num_trades'] if stats else 0}  score={stats['score'] if stats else 0:.4f}  ({elapsed:.1f}s)")

    results_df = pd.DataFrame(all_results).sort_values("score", ascending=False).reset_index(drop=True)

    log("\n" + "=" * 50)
    log("TOP 10 PARAMETER COMBINATIONS")
    log("=" * 50)
    for _, row in results_df.head(10).iterrows():
        log(f"  SL={row['STOP_LOSS']} TP={row['TAKE_PROFIT']} HD={row['HOLD_DAYS']}  trades={int(row['num_trades'])}  ret={row['total_return']:.2f}  wr={row['win_rate']:.1%}  pf={row['profit_loss_ratio']:.2f}  score={row['score']:.4f}")

    best = results_df.iloc[0]
    best_params = {"STOP_LOSS": float(best["STOP_LOSS"]),
                   "TAKE_PROFIT": float(best["TAKE_PROFIT"]),
                   "HOLD_DAYS": int(best["HOLD_DAYS"])}

    baseline_row = results_df[
        (results_df["STOP_LOSS"] == BASELINE["STOP_LOSS"]) &
        (results_df["TAKE_PROFIT"] == BASELINE["TAKE_PROFIT"]) &
        (results_df["HOLD_DAYS"] == BASELINE["HOLD_DAYS"])
    ]

    log(f"\nBEST: SL={best_params['STOP_LOSS']} TP={best_params['TAKE_PROFIT']} HD={best_params['HOLD_DAYS']}")
    log(f"  Score={best['score']:.4f} Return={best['total_return']:.4f} WinRate={best['win_rate']:.2%} P/L={best['profit_loss_ratio']:.2f} Trades={int(best['num_trades'])}")

    if len(baseline_row) > 0:
        bi = baseline_row.iloc[0]
        log(f"\nBASELINE: SL={BASELINE['STOP_LOSS']} TP={BASELINE['TAKE_PROFIT']} HD={BASELINE['HOLD_DAYS']}")
        log(f"  Score={bi['score']:.4f} Return={bi['total_return']:.4f} WinRate={bi['win_rate']:.2%} P/L={bi['profit_loss_ratio']:.2f} Trades={int(bi['num_trades'])}")
        log(f"\nCOMPARISON:")
        log(f"  Score Delta:   {best['score']-bi['score']:+.4f}")
        log(f"  Return Delta:  {best['total_return']-bi['total_return']:+.4f}")
        log(f"  WinRate Delta: {(best['win_rate']-bi['win_rate'])*100:+.1f}%")

    # 保存最优交易明细
    best_trades = [t for t in all_trades
                   if t["stop_loss"] == best_params["STOP_LOSS"]
                   and t["take_profit"] == best_params["TAKE_PROFIT"]
                   and t["hold_days_param"] == best_params["HOLD_DAYS"]]
    trades_df = pd.DataFrame(best_trades)
    trades_file = os.path.join(OUTPUT_DIR, "bt_new_ma_best_trades.csv")
    trades_df.to_csv(trades_file, index=False, encoding="utf-8-sig")
    log(f"Saved best trades: {trades_file} ({len(trades_df)} trades)")

    grid_file = os.path.join(OUTPUT_DIR, "bt_new_ma_gridsearch.csv")
    results_df.to_csv(grid_file, index=False, encoding="utf-8-sig")
    log(f"Saved grid results: {grid_file}")

    total_time = datetime.now() - t0
    log(f"\nTOTAL TIME: {total_time}")
    log("ALL DONE!")


if __name__ == "__main__":
    main()
