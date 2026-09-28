# -*- coding: utf-8 -*-
"""全局配置：路径、缓存策略、模拟盘费率。所有魔法数字集中在此。"""
from pathlib import Path

from pydantic_settings import BaseSettings

# 项目根目录（backend/ 的上一级）
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT_DIR / "data"


class Settings(BaseSettings):
    # 存储
    database_path: Path = DATA_DIR / "app.db"

    # 行情快照内存缓存秒数：一次全市场快照服务所有标的的报价/搜索
    spot_mem_ttl: int = 60
    # 快照年龄超过该秒数视为过期数据（stale）
    spot_stale_after: int = 600
    # 快照网络尝试节流秒数：拉取失败后该时间内直接用缓存，不重复撞网络
    # （网络故障时避免每次请求都拖满上游超时；正常拉取成功不受影响）
    spot_retry_interval: int = 60

    # K 线默认拉取天数
    kline_default_days: int = 250
    # K 线缓存：缓存中最新日期不早于该天数阈值内的日期则不再请求网络
    kline_fresh_window_days: int = 3

    # 模拟盘
    initial_cash: float = 1_000_000.0
    # 场内交易费率
    commission_rate: float = 0.00025   # 佣金 万 2.5
    commission_min: float = 5.0        # 单笔最低佣金（元）
    stamp_tax_rate: float = 0.0005     # 卖出印花税 万 5
    # 场外基金费率
    fund_subscription_rate: float = 0.001    # 申购费 0.1%（一折后常见值）
    fund_redemption_rate: float = 0.005      # 赎回费基准（持有期阶梯在交易引擎中实现）

    class Config:
        env_prefix = "SRA_"


settings = Settings()
