import os
import logging
from dotenv import load_dotenv
from openai import OpenAI
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
logger.setLevel(logging.DEBUG)

# AI 客户端与模型
AI_PROVIDER = os.getenv('AI_PROVIDER', 'deepseek').lower()
if AI_PROVIDER == 'qwen':
    ai_client = OpenAI(
        api_key=os.getenv('DASHSCOPE_API_KEY'),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    AI_MODEL = "qwen-max"
else:
    ai_client = OpenAI(
        api_key=os.getenv('DEEPSEEK_API_KEY'),
        base_url="https://api.deepseek.com"
    )
    AI_MODEL = "deepseek-chat"

# 交易所
exchange = ccxt.okx({
    'options': {
        'defaultType': 'swap',
    },
    'apiKey': os.getenv('OKX_API_KEY'),
    'secret': os.getenv('OKX_SECRET'),
    'password': os.getenv('OKX_PASSWORD'),
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

