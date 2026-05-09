# -*- coding: utf-8 -*-
"""
指标计算模块
计算MA、VOL、涨跌幅等技术指标
"""
import numpy as np
import pandas as pd

def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算所有技术指标
    
    Args:
        df: 必须包含 date, open, high, low, close, volume 列
    
    Returns:
        添加了技术指标列的DataFrame
    """
    df = df.copy()
    df = df.sort_values('date').reset_index(drop=True)
    
    close  = df['close'].values.astype(float)
    volume = df['volume'].values.astype(float)
    high   = df['high'].values.astype(float)
    open_arr = df['open'].values.astype(float)
    low    = df['low'].values.astype(float)
    n = len(df)
    
    # ===== 移动平均线 =====
    df['MA5']  = pd.Series(close).rolling(5).mean().values
    df['MA10'] = pd.Series(close).rolling(10).mean().values
    df['MA20'] = pd.Series(close).rolling(20).mean().values
    df['MA60'] = pd.Series(close).rolling(60).mean().values
    
    # ===== 成交量均线 =====
    df['VOL5']  = pd.Series(volume).rolling(5).mean().values
    df['VOL60'] = pd.Series(volume).rolling(60).mean().values
    
    # ===== 量比 =====
    df['vol_ratio'] = volume / df['VOL5'].values
    df.loc[df['VOL5'].isna(), 'vol_ratio'] = 0
    
    # ===== 放量信号 =====
    from config import config
    df['is_volume_up']  = (df['VOL5'] > df['VOL60'] * config.VOL_RATIO).astype(int)
    df['is_boom_volume'] = (volume > df['VOL5'] * config.BREAKOUT_VOL).astype(int)
    
    # ===== 涨跌幅 =====
    df['pct_chg']  = df['close'].pct_change() * 100
    df['pct_5']  = df['close'].pct_change(5) * 100
    df['pct_10'] = df['close'].pct_change(10) * 100
    df['pct_20'] = df['close'].pct_change(20) * 100
    
    # ===== 20日新高 =====
    high_20 = pd.Series(high).rolling(20, min_periods=1).max()
    df['is_new_high_20'] = (high >= high_20.shift(1)).astype(int)
    
    # ===== MA趋势 =====
    df['ma5_turns_down']  = ((df['MA5'].shift(1) > df['MA5'])  & (df['MA5']  > 0)).astype(int)
    df['ma10_turns_down'] = ((df['MA10'].shift(1) > df['MA10']) & (df['MA10'] > 0)).astype(int)
    df['ma20_turns_down'] = ((df['MA20'].shift(1) > df['MA20']) & (df['MA20'] > 0)).astype(int)
    df['ma20_rises']     = ((df['MA20'] > df['MA20'].shift(1)) & (df['MA20'] > 0)).astype(int)
    
    # ===== 均线多头排列 =====
    df['is_bullish'] = (
        (close > df['MA5'].values) &
        (df['MA5'].values > df['MA10'].values) &
        (df['MA10'].values > df['MA20'].values)
    ).astype(int)
    
    # ===== 阳线 =====
    df['is_bullish_candle'] = (close > open_arr).astype(int)
    
    # ===== 布林带 =====
    bb_std = pd.Series(close).rolling(20).std().values
    df['BB_upper'] = df['MA20'].values + 2 * bb_std
    df['BB_lower'] = df['MA20'].values - 2 * bb_std
    
    # ===== ATR（真实波幅）=====
    tr1 = high - low
    tr2 = abs(high - pd.Series(close).shift(1).values)
    tr3 = abs(low  - pd.Series(close).shift(1).values)
    tr = pd.DataFrame({'tr1': tr1, 'tr2': tr2, 'tr3': tr3}).max(axis=1).values
    df['ATR14'] = pd.Series(tr).rolling(14).mean().values
    
    # ===== MACD（12, 26, 9）=====
    ema12 = pd.Series(close).ewm(span=12, adjust=False).mean().values
    ema26 = pd.Series(close).ewm(span=26, adjust=False).mean().values
    df['MACD'] = ema12 - ema26
    df['MACD_signal'] = pd.Series(df['MACD']).ewm(span=9, adjust=False).mean().values
    df['MACD_hist'] = df['MACD'].values - df['MACD_signal'].values
    
    # ===== RSI（14日）=====
    delta = pd.Series(close).diff()
    gain = delta.clip(lower=0).rolling(14).mean().values
    loss = (-delta.clip(upper=0)).rolling(14).mean().values
    rs = gain / (loss + 1e-10)
    df['RSI'] = 100 - (100 / (rs + 1))
    
    return df
