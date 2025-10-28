import os
import time
from datetime import datetime, timedelta
import requests

from .context import TRADE_CONFIG
from . import state

_cache = {'ts': 0.0, 'data': None}


def get_sentiment_indicators():
    try:
        now_ts = time.time()
        ttl = float(state.SENTIMENT_TTL or 0)
        if _cache['data'] is not None and (now_ts - _cache['ts'] < ttl):
            return _cache['data']

        API_URL = os.getenv('CRYPTO_ORACLE_API_URL', '').strip()
        API_KEY = os.getenv('CRYPTO_ORACLE_API_KEY', '').strip()
        if not API_URL or not API_KEY:
            print("⚠️ 情绪API配置缺失，请在.env设置 CRYPTO_ORACLE_API_URL / CRYPTO_ORACLE_API_KEY")
            return None

        end_time = datetime.now()
        start_time = end_time - timedelta(hours=4)

        # 动态选择情绪代币（基础币种）
        try:
            base_token = str(TRADE_CONFIG.get('symbol', 'BTC/USDT:USDT')).split('/')[0].upper()
        except Exception:
            base_token = 'BTC'

        request_body = {
            "apiKey": API_KEY,
            "endpoints": ["CO-A-02-01", "CO-A-02-02"],
            "startTime": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "endTime": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "timeType": TRADE_CONFIG['timeframe'],
            "token": [base_token]
        }

        headers = {"Content-Type": "application/json", "X-API-KEY": API_KEY}
        response = requests.post(API_URL, json=request_body, headers=headers)

        if response.status_code == 200:
            data = response.json()
            if data.get("code") == 200 and data.get("data"):
                time_periods = data["data"][0]["timePeriods"]
                for period in time_periods:
                    period_data = period.get("data", [])
                    sentiment = {}
                    valid_data_found = False
                    for item in period_data:
                        endpoint = item.get("endpoint")
                        value = item.get("value", "").strip()
                        if value:
                            try:
                                if endpoint in ["CO-A-02-01", "CO-A-02-02"]:
                                    sentiment[endpoint] = float(value)
                                    valid_data_found = True
                            except (ValueError, TypeError):
                                continue
                    if valid_data_found and "CO-A-02-01" in sentiment and "CO-A-02-02" in sentiment:
                        positive = sentiment['CO-A-02-01']
                        negative = sentiment['CO-A-02-02']
                        net_sentiment = positive - negative
                        data_delay = int((datetime.now() - datetime.strptime(
                            period['startTime'], '%Y-%m-%d %H:%M:%S')).total_seconds() // 60)
                        print(f"✅ 使用情绪数据时间: {period['startTime']} (延迟: {data_delay}分钟)")
                        data_obj = {
                            'positive_ratio': positive,
                            'negative_ratio': negative,
                            'net_sentiment': net_sentiment,
                            'data_time': period['startTime'],
                            'data_delay_minutes': data_delay
                        }
                        _cache['ts'] = now_ts
                        _cache['data'] = data_obj
                        return data_obj
        return None
    except Exception as e:
        print(f"情绪指标获取失败: {e}")
        return None


