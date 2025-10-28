import os


def _get_float(env_name: str, default_value: float) -> float:
    value = os.getenv(env_name)
    if value is None or value == "":
        return float(default_value)
    try:
        return float(value)
    except ValueError:
        return float(default_value)


def _get_int(env_name: str, default_value: int) -> int:
    value = os.getenv(env_name)
    if value is None or value == "":
        return int(default_value)
    try:
        return int(float(value))
    except ValueError:
        return int(default_value)


# 交易与分析时间框架
TIMEFRAME = os.getenv("TIMEFRAME", "15m")

# 后端主循环（AI 决策调度）间隔（秒）
BACKEND_DECISION_INTERVAL_SECONDS = _get_float("BACKEND_DECISION_INTERVAL_SEC", 60)

# 后端实时数据刷新线程间隔（秒）
BACKEND_REALTIME_UPDATE_INTERVAL_SECONDS = _get_float("REALTIME_UPDATE_INTERVAL_SEC", 2)

# 前端页面自动刷新间隔（毫秒）
FRONTEND_REFRESH_INTERVAL_MS = _get_int("FRONTEND_REFRESH_INTERVAL_MS", 1000)

# 交易最小间隔（秒），用于防频繁交易（默认15分钟）
TRADE_MIN_INTERVAL_SECONDS = _get_int("MIN_TRADE_INTERVAL_SEC", 60)

# 私有接口（余额/持仓）刷新节流（秒）
PRIVATE_UPDATE_INTERVAL_SECONDS = _get_float("PRIVATE_UPDATE_INTERVAL_SEC", 5)

# 技术分析整体刷新间隔（秒）（默认15分钟）
ANALYSIS_UPDATE_INTERVAL_SECONDS = _get_float("ANALYSIS_UPDATE_INTERVAL_SEC", 60)

# 情绪数据缓存 TTL（秒）（默认15分钟）
SENTIMENT_CACHE_TTL_SECONDS = _get_float("SENTIMENT_TTL_SEC", 60)

# AI 决策最小调用间隔（秒）（默认100秒）
AI_DECISION_MIN_INTERVAL_SECONDS = _get_float("AI_DECISION_INTERVAL_SEC", 60)

# 反转下单所需的连续同向确认次数（含当前信号）
REVERSAL_CONFIRMATION_COUNT = _get_int("REVERSAL_CONFIRMATION_COUNT", 1)

# 突破翻转策略
# 是否启用价格突破立即翻转（1启用，0关闭）
BREAKOUT_ENABLED = _get_int("BREAKOUT_ENABLED", 1)
# 价格上破阻力阈值（百分比，如0.3表示0.3%）
BREAKOUT_UPPER_PCT = _get_float("BREAKOUT_UPPER_PCT", 0.3)
# 价格下破支撑阈值（百分比，如0.3表示0.3%）
BREAKOUT_LOWER_PCT = _get_float("BREAKOUT_LOWER_PCT", 0.3)
# 突破触发后的冷却时间（秒），避免高频重复翻转
BREAKOUT_COOLDOWN_SEC = _get_int("BREAKOUT_COOLDOWN_SEC", 60)

# 连涨加仓策略（同向加仓）
# 是否启用同向加仓（1启用，0关闭）
PYRAMID_ENABLED = _get_int("PYRAMID_ENABLED", 1)
# 同向加仓的最小间隔（秒），独立于最小交易间隔
PYRAMID_MIN_INTERVAL_SEC = _get_int("PYRAMID_MIN_INTERVAL_SEC", 60)
# 同向加仓的最大次数（每个方向）
PYRAMID_MAX_ADDS = _get_int("PYRAMID_MAX_ADDS", 3)
# 触发同向加仓所需的连续同向信号次数（含当前）
PYRAMID_CONSEC_SIGNALS_FOR_ADD = _get_int("PYRAMID_CONSEC_SIGNALS_FOR_ADD", 3)
# 单次同向加仓的下单BTC比例，相对基础 amount（如0.5表示50%）
PYRAMID_ADD_RATIO = _get_float("PYRAMID_ADD_RATIO", 0.5)


