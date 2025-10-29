import os
import logging
from dotenv import load_dotenv
from openai import OpenAI
import httpx
import ccxt
import config

load_dotenv()

# 日志（与原文件保持相同配置）
logger = logging.getLogger("okx_api_autobot")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
# 从环境变量控制日志级别，默认 INFO 以屏蔽 debug 噪音
_level_name = os.getenv('LOG_LEVEL', 'INFO').upper()
_level = getattr(logging, _level_name, logging.INFO)
logger.setLevel(_level)

# AI 客户端与模型（统一为 OpenAI 兼容接口：自定义 base_url 与 api_key）
AI_PROVIDER = os.getenv('AI_PROVIDER', 'openai_compatible').lower()

_AI_BASE_URL = "https://svip.xty.app/v1"

_AI_API_KEY = os.getenv('DEEPSEEK_API_KEY')

ai_client = OpenAI(
    api_key=_AI_API_KEY,
    base_url=_AI_BASE_URL,
    http_client=httpx.Client(
        base_url=_AI_BASE_URL,
        follow_redirects=True,
        timeout=httpx.Timeout(
            connect=float(os.getenv('AI_HTTP_CONNECT_TIMEOUT', '10')),
            read=float(os.getenv('AI_HTTP_READ_TIMEOUT', '120')),
            write=float(os.getenv('AI_HTTP_WRITE_TIMEOUT', '120')),
            pool=float(os.getenv('AI_HTTP_POOL_TIMEOUT', '60')),
        ),
    ),
)

# 统一模型名来源，优先使用 AI_MODEL，其次兼容旧变量
AI_MODEL = "deepseek-v3.1"

# 交易所
exchange = ccxt.okx({
    'options': {
        'defaultType': 'swap',
    },
    'apiKey': os.getenv('OKX_API_KEY'),
    'secret': os.getenv('OKX_SECRET'),
    'password': os.getenv('OKX_PASSWORD'),
    'enableRateLimit': True,
    'rateLimit': 100,
})

# 合约符号规范化为 ccxt 风格: "BTC/USDT:USDT"
def _normalize_symbol(sym: str) -> str:
    try:
        s = str(sym or '').strip()
        if not s:
            return 'BTC/USDT:USDT'
        u = s.upper()
        # 已是 ccxt 风格
        if '/' in u and ':USDT' in u:
            return u
        # OKX instId 风格 → ccxt 风格
        if '-USDT-SWAP' in u:
            base = u.split('-')[0]
            return f"{base}/USDT:USDT"
        # 简写现货风格（用于USDT本位永续）如 BTCUSDT → BTC/USDT:USDT
        if u.endswith('USDT') and '/' not in u:
            base = u[:-4]
            return f"{base}/USDT:USDT"
        # 兜底：直接返回
        return u
    except Exception:
        return 'BTC/USDT:USDT'

# 预置支持合约列表（可通过 env 覆盖所选）
SUPPORTED_SYMBOLS = [
    'BTC/USDT:USDT',
    'ETH/USDT:USDT',
    'SOL/USDT:USDT',
    'BNB/USDT:USDT',
    'DOGE/USDT:USDT',
    'XRP/USDT:USDT',
]

_env_symbol = os.getenv('SYMBOL') or os.getenv('TRADE_SYMBOL') or os.getenv('OKX_SYMBOL')
_selected_symbol = _normalize_symbol(_env_symbol) if _env_symbol else 'BTC/USDT:USDT'
if _selected_symbol not in SUPPORTED_SYMBOLS:
    SUPPORTED_SYMBOLS.append(_selected_symbol)

# 多合约列表（逗号/分号/空白分隔）
_env_symbols_raw = (
    os.getenv('SYMBOLS')
    or os.getenv('TRADE_SYMBOLS')
    or os.getenv('OKX_SYMBOLS')
)
if _env_symbols_raw:
    _symbols = []
    for part in _env_symbols_raw.replace(';', ',').split(','):
        item = part.strip()
        if not item:
            continue
        norm = _normalize_symbol(item)
        _symbols.append(norm)
        if norm not in SUPPORTED_SYMBOLS:
            SUPPORTED_SYMBOLS.append(norm)
    if not _symbols:
        _symbols = [_selected_symbol]
else:
    _symbols = [_selected_symbol]

# 交易配置（保留与原一致的来源与键）
TRADE_CONFIG = {
    'symbol': _selected_symbol,
    'symbols': _symbols,
    'amount': 0.001,
    'leverage': 10,
    'timeframe': config.TIMEFRAME,
    'test_mode': False,
    'data_points': 96,
    'supported_symbols': SUPPORTED_SYMBOLS,
    'analysis_periods': {
        'short_term': 20,
        'medium_term': 50,
        'long_term': 96
    }
}


# ========================
# 每币种杠杆配置与工具函数
# ========================

# 基线杠杆建议（可按需要调整）
_BASELINE_LEVERAGE_BY_BASE = {
    'BTC': 10,
    'ETH': 8,
    'BNB': 6,
    'SOL': 6,
    'XRP': 5,
    'DOGE': 5,
}

# 运行时覆盖的每币种杠杆表（key 为规范化合约符号，如 "BTC/USDT:USDT"）
SYMBOL_LEVERAGE_MAP = {}

def _get_base_from_symbol(sym: str) -> str:
    try:
        s = _normalize_symbol(sym)
        return s.split('/')[0]
    except Exception:
        return 'BTC'

def get_symbol_leverage(sym: str) -> int:
    """获取指定合约符号的杠杆，优先返回运行时覆盖，其次返回基线，最后回退到 TRADE_CONFIG['leverage']。"""
    try:
        s = _normalize_symbol(sym)
        if s in SYMBOL_LEVERAGE_MAP:
            return int(SYMBOL_LEVERAGE_MAP[s])
        base = _get_base_from_symbol(s)
        return int(_BASELINE_LEVERAGE_BY_BASE.get(base, TRADE_CONFIG['leverage']))
    except Exception:
        try:
            return int(TRADE_CONFIG['leverage'])
        except Exception:
            return 10

def set_symbol_leverage(sym: str, leverage: int) -> None:
    """设置运行时每币种杠杆（写入内存映射）。"""
    try:
        lev = int(leverage)
        lev = max(1, lev)
    except Exception:
        return
    s = _normalize_symbol(sym)
    SYMBOL_LEVERAGE_MAP[s] = lev

