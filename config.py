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
BACKEND_REALTIME_UPDATE_INTERVAL_SECONDS = _get_float("REALTIME_UPDATE_INTERVAL_SEC", 1)

# 前端页面自动刷新间隔（毫秒）
FRONTEND_REFRESH_INTERVAL_MS = _get_int("FRONTEND_REFRESH_INTERVAL_MS", 1000)

# 交易最小间隔（秒），用于防频繁交易（默认15分钟）
TRADE_MIN_INTERVAL_SECONDS = _get_int("MIN_TRADE_INTERVAL_SEC", 15 * 60)

# 私有接口（余额/持仓）刷新节流（秒）
PRIVATE_UPDATE_INTERVAL_SECONDS = _get_float("PRIVATE_UPDATE_INTERVAL_SEC", 1)

# 技术分析整体刷新间隔（秒）（默认15分钟）
ANALYSIS_UPDATE_INTERVAL_SECONDS = _get_float("ANALYSIS_UPDATE_INTERVAL_SEC", 60)

# 情绪数据缓存 TTL（秒）（默认15分钟）
SENTIMENT_CACHE_TTL_SECONDS = _get_float("SENTIMENT_TTL_SEC", 60)

# AI 决策最小调用间隔（秒）（默认100秒）
AI_DECISION_MIN_INTERVAL_SECONDS = _get_float("AI_DECISION_INTERVAL_SEC", 60)


