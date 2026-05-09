# -*- coding: utf-8 -*-
"""
MA策略配置参数 - V2最优版本
所有可调整参数集中管理，不改代码也能调参
"""
from dataclasses import dataclass

@dataclass
class MAConfig:
    # ===== 选股条件 =====
    MA20_RISE_PCT: float = 15       # MA20 N日内涨幅（%）
    VOL_RATIO: float = 1.3           # 成交量/均量 比率阈值
    PULLBACK_DEPTH: float = 0.06    # 回踩MA5深度上限

    # ===== 买入信号 =====
    BREAKOUT_VOL: float = 2.3        # 放量突破：量比下限
    STRONG_COOLDOWN: float = 0.07    # 强势低吸：近N日跌幅上限

    # ===== 卖出信号 =====
    STOP_LOSS: float = 0.13          # 止损线（-13%）
    PROFIT_HALF: float = 0.15        # 减仓线（+15%）
    PROFIT_FULL: float = 0.30         # 清仓线（+30%）
    MA_EXIT: str = 'MA10'            # 均线退出：MA10
    MA_HOLD_DAYS: int = 9            # 持有满N天后才能用均线退出

    # ===== 选股质量过滤 =====
    MIN_PRICE: float = 3.0           # 最低股价过滤
    MAX_PRICE: float = 500.0         # 最高股价过滤

    # ===== 回测设置 =====
    DB_PATH: str = r'D:\yxw\PY\astock_data\astock_data.db'
    START_DATE: str = '2024-01-01'
    END_DATE: str = '2026-04-09'

    # ===== 信号扫描 =====
    SCAN_DAYS: int = 20             # 计算指标需要的历史数据天数


# 全局配置实例
config = MAConfig()
