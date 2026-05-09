# -*- coding: utf-8 -*-
"""
MACD + RSI 指标计算
"""
import pandas as pd
import numpy as np

def calc_macd(close, fast=12, slow=26, signal=9):
    """计算MACD"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    histogram = macd - signal_line
    return macd, signal_line, histogram

def calc_rsi(close, period=14):
    """计算RSI"""
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def add_macd_rsi(df):
    """给DataFrame添加MACD和RSI列"""
    close = df['close']
    df = df.copy()
    df['MACD'], df['MACD_signal'], df['MACD_hist'] = calc_macd(close)
    df['RSI'] = calc_rsi(close)
    return df
