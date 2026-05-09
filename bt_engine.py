# -*- coding: utf-8 -*-
"""
Backtrader回测引擎 - 每股票单独Cerebro实例
避免多数据共用策略实例导致的指标混乱
"""
import backtrader as bt
import pandas as pd
import sqlite3
from bt_strategy import MAV2Strategy
from config import config


def run_backtest(
    codes: list = None,
    start_date: str = None,
    end_date: str = None,
    initial_cash: float = 100000.0,
    commission: float = 0.001,
    output: str = None,
    exit_by: str = 'ma20_break',
    weekly_resonance: bool = False,
    warmup_days: int = 90,
    use_atr_stop: bool = False,
    atr_stop_mult: float = 3.0,
    atr_profit_half: float = 4.0,
    atr_profit_full: float = 6.0,
    market_breadth: dict = None,
    market_filter_enabled: bool = False,
    market_filter_threshold: float = 40.0,
    # === 策略核心参数（支持外部传入覆盖） ===
    stop_loss: float = None,
    profit_half: float = None,
    profit_full: float = None,
    ma_hold_days: int = None,
    strong_pct: float = None,
    breakout_vol: float = None,
    pullback_depth: float = None,
    pullback_vol: float = None,
    breakout_rise: float = None,
    strong_vol: float = None,
    enable_pullback_ma5: bool = None,
    enable_breakout_high: bool = None,
    enable_strong_buy: bool = None,
):
    """
    运行回测 - 每个股票在独立的Cerebro实例中运行
    """
    start_date = start_date or config.START_DATE
    end_date = end_date or config.END_DATE
    
    # 计算预热开始日期
    import datetime
    warmup_start_dt = pd.to_datetime(start_date) - datetime.timedelta(days=warmup_days)
    
    # 连接数据库
    conn = sqlite3.connect(config.DB_PATH)
    
    if codes is None:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT code FROM stock_daily 
            WHERE date=(SELECT MAX(date) FROM stock_daily)
            AND code NOT LIKE '3%' AND code NOT LIKE '4%' AND code NOT LIKE '8%'
            ORDER BY code
        """)
        codes = [r[0] for r in cur.fetchall()]
    
    print(f"加载 {len(codes)} 只股票...")
    print(f"模式: {'周线共振过滤' if weekly_resonance else '无周线过滤'}")
    print(f"预热天数: {warmup_days}")

    # 聚合所有策略的交易记录
    all_trades = []
    total_stocks = 0
    processed_stocks = 0
    
    for code in codes:
        # 加载预热数据 + 回测数据
        df = pd.read_sql("""
            SELECT date, open, high, low, close, volume
            FROM stock_daily
            WHERE code=? AND date>=? AND date<=?
            ORDER BY date ASC
        """, conn, params=(code, warmup_start_dt.strftime('%Y-%m-%d'), end_date))
        
        if len(df) < 60:
            continue
        
        total_stocks += 1
        
        df.columns = ['datetime', 'open', 'high', 'low', 'close', 'volume']
        df['datetime'] = pd.to_datetime(df['datetime'])
        df = df.set_index('datetime')
        
        # 每个股票创建独立的Cerebro
        cerebro = bt.Cerebro()
        cerebro.broker.setcash(initial_cash * 100)  # 100x避免资金问题
        cerebro.broker.setcommission(commission=commission)
        cerebro.addsizer(bt.sizers.FixedSize, stake=100)
        
        # 添加分析器
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe', 
                           riskfreerate=0.03, annualize=True)
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
        cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')
        
        data = bt.feeds.PandasData(
            dataname=df,
            datetime=None,
            open='open',
            high='high',
            low='low',
            close='close',
            volume='volume',
            openinterest=-1,
            fromdate=pd.to_datetime(warmup_start_dt),
            todate=pd.to_datetime(end_date)
        )
        cerebro.adddata(data, name=code)
        
        # 构建策略参数
        strategy_kwargs = dict(
            exit_by=exit_by,
            weekly_resonance=weekly_resonance,
            use_atr_stop=use_atr_stop,
            atr_stop_mult=atr_stop_mult,
            atr_profit_half=atr_profit_half,
            atr_profit_full=atr_profit_full,
            market_breadth=market_breadth or {},
            market_filter_enabled=market_filter_enabled,
            market_filter_threshold=market_filter_threshold,
        )
        # 只传递非None的覆盖参数
        for param_name in ['stop_loss', 'profit_half', 'profit_full', 'ma_hold_days',
                           'strong_pct', 'breakout_vol', 'pullback_depth', 'pullback_vol',
                           'breakout_rise', 'strong_vol', 'enable_pullback_ma5',
                           'enable_breakout_high', 'enable_strong_buy']:
            param_val = locals().get(param_name)
            if param_val is not None:
                strategy_kwargs[param_name] = param_val
        
        cerebro.addstrategy(MAV2Strategy, **strategy_kwargs)
        
        # 运行
        results = cerebro.run()
        strategy = results[0]
        
        # 收集交易记录
        for t in strategy.trades:
            t['code'] = code
            all_trades.append(t)
        
        processed_stocks += 1
        if processed_stocks % 200 == 0:
            print(f"  已处理 {processed_stocks}/{total_stocks} 只股票, 当前累计 {len(all_trades)} 笔交易")
    
    conn.close()
    
    print(f"\n完成! 共 {processed_stocks} 只股票, {len(all_trades)} 笔交易")
    
    # 计算统计
    total_trades = len(all_trades)
    if total_trades == 0:
        print("警告: 没有交易记录!")
        result = {
            'start_date': start_date,
            'end_date': end_date,
            'mode': mode_name,
            'total_trades': 0,
            'win_rate': 0,
            'avg_win': 0,
            'avg_loss': 0,
            'profit_factor': 0,
            'total_return': 0,
            'sharpe_ratio': None,
            'max_drawdown_pct': 0,
            'trades': [],
            'sell_reason_counts': {},
            'atr_stop_triggered': 0,
        }
        if output:
            df_trades = pd.DataFrame(all_trades)
            df_trades.to_csv(output, index=False, encoding='utf-8-sig')
            print(f"交易记录已保存: {output}")
        return result
    
    df_trades = pd.DataFrame(all_trades)
    
    # 统计
    win_mask = df_trades['pnl_pct'] > 0
    win_trades = win_mask.sum()
    lose_trades = (~win_mask).sum()
    win_rate = win_trades / total_trades * 100
    avg_win = df_trades.loc[win_mask, 'pnl_pct'].mean() if win_trades > 0 else 0
    avg_loss = abs(df_trades.loc[~win_mask, 'pnl_pct'].mean()) if lose_trades > 0 else 0
    profit_factor = avg_win / avg_loss if avg_loss > 0 else 0
    
    # 总收益率（按每笔PnL简单求和 * 初始资金比例）
    total_pnl = df_trades['pnl_pct'].sum()
    total_return = total_pnl  # 每笔PnL是百分比
    
    # 卖出原因统计
    sell_counts = df_trades['sell_signal'].value_counts().to_dict()
    atr_stop_triggered = sell_counts.get('atr_stop_loss', 0)
    
    # 夏普和回撤（难以跨股票计算，用0）
    sharpe = None
    max_drawdown = 0
    
    # 模式名称
    if use_atr_stop:
        mode_name = f"V4混合ATR(SL*{atr_stop_mult},PH*{atr_profit_half},PF*{atr_profit_full})"
    elif weekly_resonance:
        mode_name = "周线共振过滤"
    else:
        mode_name = "V2固定止损"
    
    # 输出结果
    print()
    print("=" * 60)
    print(f"  Backtrader MA策略V2 回测报告 - {mode_name}")
    print("=" * 60)
    print(f"  回测区间  : {start_date} ~ {end_date}")
    print(f"  股票数量  : {processed_stocks}")
    print(f"  总交易次数: {total_trades}")
    print(f"  总收益率  : {total_return:.1f}%")
    print(f"  胜率       : {win_rate:.1f}%")
    print(f"  均盈利     : {avg_win:.2f}%")
    print(f"  均亏损     : {avg_loss:.2f}%")
    print(f"  盈亏比     : {profit_factor:.2f}")
    print("=" * 60)
    
    # 保存结果
    if output:
        df_trades.to_csv(output, index=False, encoding='utf-8-sig')
        print(f"\n交易记录已保存: {output} ({len(df_trades)}笔)")
    
    result = {
        'start_date': start_date,
        'end_date': end_date,
        'mode': mode_name,
        'total_trades': total_trades,
        'win_rate': win_rate,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': profit_factor,
        'total_return': total_return,
        'sharpe_ratio': sharpe,
        'max_drawdown_pct': max_drawdown,
        'trades': all_trades,
        'sell_reason_counts': sell_counts,
        'atr_stop_triggered': atr_stop_triggered,
        'total_stocks': processed_stocks,
    }
    
    return result


if __name__ == '__main__':
    import sys
    output_path = r'D:\yxw\PY\openclaw-monitor\output\bt_backtest_result.csv'
    result = run_backtest(
        start_date='2024-01-01',
        end_date='2026-04-09',
        output=output_path
    )
