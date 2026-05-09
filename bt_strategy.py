# -*- coding: utf-8 -*-
"""
Backtrader策略 - MA策略V2版本 + ATR动态止损 + 周线共振过滤
整合指标计算、买入/卖出信号、止损止盈
支持周线共振过滤：日线买入信号需满足周线MA5>MA10>MA20
"""
import backtrader as bt
import pandas as pd
import numpy as np
from config import config


class WeeklyResonanceMixin:
    """
    周线共振混入类
    直接从当前bar往前追溯计算周线MA，不依赖缓存
    """

    def _is_weekly_trending(self):
        """
        直接从历史日线数据计算周线MA
        回溯最多20周（~100个交易日）的数据
        """
        try:
            # 当前bar索引
            bar_idx = len(self) - 1  # 0-based index in backtrader
            
            # 需要至少20周的数据（约100个交易日）
            if bar_idx < 80:
                return False
            
            # 收集周线数据：从当前往前追溯
            weekly_closes = []  # 每周最后一个收盘价
            seen_weeks = set()
            
            # 往前最多看100个bar
            for i in range(0, min(bar_idx + 1, 100)):
                try:
                    dt = self.data.datetime.date(-i)
                    close = self.data.close[-i]
                    
                    # ISO周码
                    iso = dt.isocalendar()
                    week_key = (iso[0], iso[1])  # (year, week)
                    
                    # 每周只取最后一个收盘价（i越小越近）
                    if week_key not in seen_weeks:
                        weekly_closes.append((week_key, close))
                        seen_weeks.add(week_key)
                except:
                    break
            
            if len(weekly_closes) < 20:
                return False
            
            # 按周码排序（从早到晚）
            weekly_closes.sort(key=lambda x: (x[0][0], x[0][1]))
            closes = [c for _, c in weekly_closes]
            
            if len(closes) < 20:
                return False
            
            # 计算周线MA
            wma5 = np.mean(closes[-5:])
            wma10 = np.mean(closes[-10:])
            wma20 = np.mean(closes[-20:])
            
            trending = wma5 > wma10  # 简化：MA5 > MA10 即可（原 MA5 > MA10 > MA20 太严格）
            
            return trending
            
        except Exception as e:
            self.log(f'[WEEKLY ERROR] {e}')
            return False


class MAV2Strategy(bt.Strategy, WeeklyResonanceMixin):
    """
    MA策略V2（止损-12% / MA10退出 / 止盈30%）
    
    使用config.py中的参数，支持以下指标：
    - MA5 / MA10 / MA20 / MA60
    - VOL5 / VOL60（成交量均线）
    - ATR（动态止损用）
    
    买入信号：
    1. 回踩MA5（最低触及MA5，收盘在MA5之上，阳线）
    2. 放量突破20日新高（量比>1.8，收盘创20日新高）
    3. 强势低吸（近5日跌幅>8%，今日收盘翻红）
    
    卖出信号（ATR模式）：
    1. ATR动态止损（entry_price - ATR * N）
    2. ATR动态止盈（分批：entry_price + ATR * 4减半，ATR * 6清仓）
    3. MA10拐头 / 跌破MA20（保底）
    4. 固定止损（保底）

    卖出信号（固定模式）：
    1. 固定止损 -12%
    2. 跌破MA20 或 MA10拐头
    3. 盈利+15%减仓一半
    4. 盈利+30%清仓
    
    周线共振模式（weekly_resonance=True）：
    日线买入信号必须同时满足周线MA5>MA10>MA20
    """

    params = (
        ('stop_loss', config.STOP_LOSS),
        ('profit_half', config.PROFIT_HALF),
        ('profit_full', config.PROFIT_FULL),
        ('ma_hold_days', config.MA_HOLD_DAYS),
        ('strong_pct', config.STRONG_COOLDOWN),
        ('breakout_vol', config.BREAKOUT_VOL),
        ('pullback_depth', config.PULLBACK_DEPTH),
        ('exit_by', 'ma20_break'),  # 'ma20_break' 或 'ma10_turn'
        # --- ATR动态止损参数 ---
        ('use_atr_stop', False),     # 是否启用ATR动态止损
        ('atr_stop_mult', 3.0),      # ATR * N 作为止损
        ('atr_profit_half', 4.0),   # ATR * N 作为减仓止盈
        ('atr_profit_full', 6.0),    # ATR * N 作为清仓止盈
        # --- 信号开关 ---
        ('enable_pullback_ma5', True),
        ('enable_breakout_high', True),
        ('enable_strong_buy', True),
        # --- 回踩MA5 额外过滤 ---
        ('pullback_vol', 1.5),       # 回踩MA5时要求vol_ratio > 此值
        # --- 放量突破 额外过滤 ---
        ('breakout_rise', 0.0),      # 突破时要求涨幅 > breakout_rise %
        # --- 强势低吸 额外过滤 ---
        ('strong_vol', 0.8),        # 强势低吸时要求vol_ratio > 此值
        # --- 周线共振过滤 ---
        ('weekly_resonance', False),  # 是否启用周线共振过滤
        # --- 大盘环境过滤 ---
        ('market_breadth', {}),       # {date_str: ma20_width_pct} 预计算的市场宽度
        ('market_filter_threshold', 40.0),  # MA20宽度阈值，低于此值禁止买入
        ('market_filter_enabled', False),   # 是否启用大盘环境过滤
    )

    def __init__(self):
        # 指标
        self.ma5  = bt.indicators.SMA(self.data.close, period=5)
        self.ma10 = bt.indicators.SMA(self.data.close, period=10)
        self.ma20 = bt.indicators.SMA(self.data.close, period=20)
        self.ma60 = bt.indicators.SMA(self.data.close, period=60)
        self.vol5  = bt.indicators.SMA(self.data.volume, period=5)
        self.vol60 = bt.indicators.SMA(self.data.volume, period=60)
        
        # 成交量比
        self.vol_ratio = self.data.volume / self.vol5
        
        # 20日新高
        self.highest = bt.indicators.Highest(self.data.high, period=20, plot=False)
        
        # ATR指标（用于动态止损）
        self.atr = bt.indicators.ATR(self.data, period=14)
        
        # 状态追踪
        self.order = None
        self.hold_days = 0
        self.half_sold = False
        self.entry_price = 0
        self.entry_date = None
        self.entry_atr = 0
        
        # 信号类型
        self.buy_signal = None
        
        # 追踪
        self.trades = []
        self._atr_stop_triggered = 0
        self._sell_reason_counts = {
            'atr_stop_loss': 0, 'atr_profit_half': 0, 'atr_profit_full': 0,
            'ma10_turn': 0, 'ma20_break': 0, 'fixed_stop_loss': 0,
            'stop_loss': 0, 'profit_half': 0, 'profit_full': 0,
        }
        
        # 周线共振统计
        self._weekly_filter_count = 0
        self._total_buy_check = 0
        
        # 大盘环境过滤统计
        self._market_filter_count = 0
        self._total_market_check = 0

    def log(self, txt, dt=None):
        dt = dt or self.data.datetime.date(0)
        # print(f'[{dt.isoformat()}] {txt}')

    def notify_order(self, order):
        """订单状态通知"""
        if order.status in [order.Submitted, order.Accepted]:
            return
        
        if order.status in [order.Completed]:
            if order.isbuy():
                self.log(f'BUY EXECUTED, price={order.executed.price:.2f}, size={order.executed.size}')
                self.entry_price = order.executed.price
                self.entry_date = self.data.datetime.date(0)
                self.hold_days = 0
                self.half_sold = False
                self.entry_atr = self.atr[0] if self.params.use_atr_stop else 0
            elif order.issell():
                self.log(f'SELL EXECUTED, price={order.executed.price:.2f}')
                pnl = (order.executed.price - self.entry_price) / self.entry_price * 100
                self.log(f'  PnL: {pnl:.2f}%')
                self.trades.append({
                    'entry_date': self.entry_date.isoformat() if self.entry_date else '',
                    'entry_price': self.entry_price,
                    'exit_date': self.data.datetime.date(0).isoformat(),
                    'exit_price': order.executed.price,
                    'hold_days': self.hold_days,
                    'pnl_pct': round(pnl, 2),
                    'buy_signal': self.buy_signal or 'unknown',
                    'sell_signal': getattr(self, '_last_sell_reason', 'unknown'),
                })
                # 统计卖出原因
                sell_reason = getattr(self, '_last_sell_reason', 'unknown')
                if sell_reason in self._sell_reason_counts:
                    self._sell_reason_counts[sell_reason] += 1
                self.entry_price = 0
                self.hold_days = 0
        
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.log('Order Canceled/Margin/Rejected')
        
        self.order = None

    def notify_trade(self, trade):
        pass

    def _check_buy_signal(self):
        """检查今日是否有买入信号"""
        close = self.data.close[0]
        low = self.data.low[0]
        open_p = self.data.open[0]
        high = self.data.high[0]
        vol_ratio = self.vol_ratio[0]
        ma5 = self.ma5[0]
        ma10 = self.ma10[0]
        ma20 = self.ma20[0]
        
        if ma20 <= 0 or close <= ma20:
            return None
        
        pct_5 = (close - self.data.close[-5]) / self.data.close[-5] * 100 if self.data.close[-5] > 0 else 0
        
        # 信号1：回踩MA5
        if self.params.enable_pullback_ma5:
            if low < ma5 < close and ma5 > 0 and open_p < ma5:
                depth = (ma5 - low) / ma5
                if depth < self.params.pullback_depth:
                    return 'pullback_ma5'
        
        # 信号2：放量突破20日新高
        if self.params.enable_breakout_high:
            rise_pct = (close - self.data.close[-1]) / self.data.close[-1] * 100 if self.data.close[-1] > 0 else 0
            is_new_high = 1 if high >= self.highest[-1] else 0
            if is_new_high and vol_ratio > self.params.breakout_vol and rise_pct >= self.params.breakout_rise:
                if close > ma5 and close > ma10:
                    return 'breakout_high'
        
        # 信号3：强势低吸
        if self.params.enable_strong_buy:
            if close > ma5 and ma5 > 0 and pct_5 < -self.params.strong_pct * 100:
                if close > open_p and vol_ratio > self.params.strong_vol:
                    return 'strong_buy'
        
        return None

    def _check_sell_signal(self, price):
        """检查是否需要卖出"""
        if self.entry_price <= 0:
            return None
        
        pnl = (price - self.entry_price) / self.entry_price
        current_atr = self.atr[0]
        
        # ===== ATR动态止损模式 =====
        if self.params.use_atr_stop and self.entry_atr > 0:
            atr_stop_price = self.entry_price - self.params.atr_stop_mult * self.entry_atr
            if price < atr_stop_price:
                self._last_sell_reason = 'atr_stop_loss'
                self._atr_stop_triggered += 1
                return 'sell'
            
            atr_profit_half_price = self.entry_price + self.params.atr_profit_half * self.entry_atr
            if price > atr_profit_half_price and not self.half_sold:
                self._last_sell_reason = 'atr_profit_half'
                self.half_sold = True
                return 'sell_half'
            
            atr_profit_full_price = self.entry_price + self.params.atr_profit_full * self.entry_atr
            if price > atr_profit_full_price:
                self._last_sell_reason = 'atr_profit_full'
                return 'sell'
            
            if self.hold_days >= self.params.ma_hold_days:
                if len(self) >= 2:
                    if self.ma10[-2] > self.ma10[-1] > 0 and price < self.ma10[-1]:
                        self._last_sell_reason = 'ma10_turn'
                        return 'sell'
            
            ma20 = self.ma20[0]
            if ma20 > 0 and price < ma20:
                self._last_sell_reason = 'ma20_break'
                return 'sell'
            
            if pnl <= -self.params.stop_loss:
                self._last_sell_reason = 'fixed_stop_loss'
                return 'sell'
        
        # ===== 固定止损模式 =====
        else:
            if pnl <= -self.params.stop_loss:
                self._last_sell_reason = 'stop_loss'
                return 'sell'
            
            if self.params.exit_by == 'ma20_break':
                ma20 = self.ma20[0]
                if ma20 > 0 and price < ma20:
                    self._last_sell_reason = 'ma20_break'
                    return 'sell'
                
                if pnl >= self.params.profit_half and not self.half_sold:
                    self._last_sell_reason = 'profit_half'
                    self.half_sold = True
                    return 'sell_half'
                
                if pnl >= self.params.profit_full:
                    self._last_sell_reason = 'profit_full'
                    return 'sell'
                
                if self.hold_days >= self.params.ma_hold_days:
                    if len(self) >= 2:
                        if self.ma10[-2] > self.ma10[-1] > 0 and price < self.ma10[-1]:
                            self._last_sell_reason = 'ma10_turn'
                            return 'sell'
            
            else:
                if self.hold_days >= self.params.ma_hold_days:
                    if len(self) >= 2:
                        if self.ma10[-2] > self.ma10[-1] > 0 and price < self.ma10[-1]:
                            self._last_sell_reason = 'ma10_turn'
                            return 'sell'
                
                ma20 = self.ma20[0]
                if ma20 > 0 and price < ma20:
                    self._last_sell_reason = 'ma20_break'
                    return 'sell'
                
                if pnl >= self.params.profit_half and not self.half_sold:
                    self._last_sell_reason = 'profit_half'
                    self.half_sold = True
                    return 'sell_half'
                
                if pnl >= self.params.profit_full:
                    self._last_sell_reason = 'profit_full'
                    return 'sell'
        
        return None

    def next(self):
        """每个K线调用一次"""
        if len(self) < 20:
            return
        
        if self.entry_price > 0:
            self.hold_days += 1
        
        if self.order is not None:
            return
        
        close = self.data.close[0]
        ma20 = self.ma20[0]
        
        # ========== 持仓中 ==========
        if self.entry_price > 0:
            signal = self._check_sell_signal(close)
            if signal == 'sell':
                self.order = self.close()
            elif signal == 'sell_half':
                self.order = self.close(size=self.position.size // 2)
            return
        
        # ========== 空仓中 ==========
        if ma20 <= 0 or close <= ma20:
            return
        
        # 检查买入信号
        buy_sig = self._check_buy_signal()
        if buy_sig is None:
            return
        
        # ===== 周线共振过滤 =====
        if self.params.weekly_resonance:
            self._total_buy_check += 1
            if not self._is_weekly_trending():
                self._weekly_filter_count += 1
                return  # 周线不满足，跳过
        
        # ===== 大盘环境过滤 =====
        if self.params.market_filter_enabled and self.params.market_breadth:
            try:
                dt = self.data.datetime.date(0)
                date_str = dt.isoformat()
                width = self.params.market_breadth.get(date_str, 100.0)
                self._total_market_check += 1
                if width < self.params.market_filter_threshold:
                    self._market_filter_count += 1
                    return  # 大盘弱势，禁止买入
            except:
                pass
        
        # 买入
        self.buy_signal = buy_sig
        self._last_sell_reason = 'unknown'
        self.order = self.buy()
        self.log(f'BUY SIGNAL: {buy_sig}, price≈{close:.2f}, weekly_ok={not self.params.weekly_resonance or self._is_weekly_trending()}')
