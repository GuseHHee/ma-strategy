# -*- coding: utf-8 -*-
"""
信号生成模块
买入信号、卖出信号、选股条件过滤
"""
import pandas as pd
from typing import Optional
from config import config


def pass_pick_conditions(row: pd.Series) -> bool:
    """
    选股质量过滤
    提高信号质量，排除弱势股
    """
    # 排除ST、退市、股价异常
    if pd.isna(row.get('close', 0)) or row['close'] <= 0:
        return False
    if row['close'] < config.MIN_PRICE or row['close'] > config.MAX_PRICE:
        return False
    
    # MA20向上
    ma20 = row.get('MA20', 0)
    ma20_prev = row.get('MA20', 0)  # 如果有前一交易日数据
    if pd.isna(ma20) or ma20 <= 0:
        return False
    
    # 收盘在MA20之上
    if row['close'] < ma20:
        return False
    
    # 量比足够（流动性）
    vol_ratio = row.get('vol_ratio', 0)
    if vol_ratio < 0.5:  # 排除极度缩量
        return False
    
    # ===== MACD/RSI 多因子辅助过滤 =====
    macd = row.get('MACD', 0)
    macd_hist = row.get('MACD_hist', 0)
    rsi = row.get('RSI', 50)
    
    # MACD 金叉附近（histogram由负转正）
    macd_bullish = macd_hist > 0
    
    # RSI 不过热（20-70之间，不追高也不超卖）
    rsi_healthy = 20 < rsi < 70
    
    if not (macd_bullish and rsi_healthy):
        return False
    
    return True


def check_buy_signal(df: pd.DataFrame, i: int) -> Optional[str]:
    """
    检查买入信号
    
    Args:
        df: 已计算指标的数据
        i: 当前行索引（信号日）
    
    Returns:
        信号类型: 'pullback_ma5' / 'breakout_high' / 'strong_buy' / None
    """
    row = df.iloc[i]
    ma5  = row['MA5']
    ma10 = row['MA10']
    low_p = row['low']
    close_p = row['close']
    open_p = row['open']
    
    # 获取MACD和RSI
    macd = row.get('MACD', 0)
    macd_hist = row.get('MACD_hist', 0)
    rsi = row.get('RSI', 50)
    
    # ===== 信号1：回踩MA5企稳 =====
    # 最低价触及MA5但未有效跌破，收盘在MA5之上且阳线
    if (low_p < ma5 < close_p and ma5 > 0 and open_p < ma5):
        depth = (ma5 - low_p) / ma5
        if depth < config.PULLBACK_DEPTH:
            # MACD在0轴上方 + RSI健康 → 加分
            macd_bonus = macd > 0 and 20 < rsi < 70
            if macd_bonus:
                return 'pullback_ma5_macd'
            return 'pullback_ma5'
    
    # ===== 信号2：放量突破20日新高 =====
    if row.get('is_boom_volume', 0) == 1 and row.get('is_new_high_20', 0) == 1:
        if close_p > ma5 and close_p > ma10:
            if macd > 0 and 20 < rsi < 70:
                return 'breakout_high_macd'
            return 'breakout_high'
    
    # ===== 信号3：强势低吸 =====
    if (close_p > ma5 and ma5 > 0 and
            row.get('pct_5', 0) < -config.STRONG_COOLDOWN * 100):
        if close_p > open_p:  # 收盘翻红
            return 'strong_buy'
    
    return None


def check_sell_signal(
    entry_price: float,
    current_price: float,
    row: pd.Series,
    prev_row: pd.Series,
    hold_days: int,
    already_half_sold: bool
) -> tuple[Optional[str], bool]:
    """
    检查卖出信号
    
    Returns:
        (信号类型, 是否已减仓)
        信号类型: 'stop_loss' / 'ma20_break' / 'profit_half' / 'profit_full' / 'ma10_turns_down' / None
    """
    pnl = (current_price - entry_price) / entry_price
    signal = None
    half = already_half_sold
    
    # 1. 止损 -12%
    if pnl <= -config.STOP_LOSS:
        signal = 'stop_loss'
    
    # 2. 跌破MA20
    elif current_price < row['MA20'] and row['MA20'] > 0:
        signal = 'ma20_break'
    
    # 3. 盈利15%减仓一半
    elif pnl >= config.PROFIT_HALF and not already_half_sold:
        signal = 'profit_half'
        half = True
    
    # 4. 盈利30%清仓
    elif pnl >= config.PROFIT_FULL:
        signal = 'profit_full'
    
    # 5. MA10拐头（需持有满5天）
    elif (hold_days >= config.MA_HOLD_DAYS and
          row.get('ma10_turns_down', 0) == 1 and
          current_price < row['MA10'] and
          prev_row is not None and
          prev_row['close'] < prev_row['MA10']):
        signal = 'ma10_turns_down'
    
    return signal, half


def get_signal_name(signal: str) -> str:
    """信号中文名"""
    names = {
        'pullback_ma5': '回踩MA5',
        'pullback_ma5_macd': '回踩MA5(MACD加分)',
        'breakout_high': '放量突破',
        'breakout_high_macd': '放量突破(MACD加分)',
        'strong_buy': '强势低吸',
        'stop_loss': '止损',
        'ma20_break': '跌破MA20',
        'profit_half': '减仓一半',
        'profit_full': '止盈清仓',
        'ma10_turns_down': 'MA10拐头',
    }
    return names.get(signal, signal)
