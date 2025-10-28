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

# 交易配置（保留与原一致的来源与键）
TRADE_CONFIG = {
    'symbol': 'BTC/USDT:USDT',
    'amount': 0.001,
    'leverage': 10,
    'timeframe': config.TIMEFRAME,
    'test_mode': False,
    'data_points': 96,
    'analysis_periods': {
        'short_term': 20,
        'medium_term': 50,
        'long_term': 96
    }
}


