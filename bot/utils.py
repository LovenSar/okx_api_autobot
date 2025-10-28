import os
import json
import re
from datetime import datetime

from .context import logger
from . import state


def safe_json_parse(json_str):
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        try:
            if '```json' in json_str:
                start = json_str.find('```json') + 7
                end = json_str.find('```', start)
                if end != -1:
                    json_str = json_str[start:end].strip()
            elif '```' in json_str:
                start = json_str.find('```') + 3
                end = json_str.find('```', start)
                if end != -1:
                    json_str = json_str[start:end].strip()

            try:
                return json.loads(json_str)
            except Exception:
                pass

            json_str = json_str.replace("'", '"')
            json_str = re.sub(r'(\w+):', r'"\\1":', json_str)
            json_str = re.sub(r',\s*}', '}', json_str)
            json_str = re.sub(r',\s*]', ']', json_str)
            return json.loads(json_str)
        except json.JSONDecodeError:
            print(f"JSON解析失败，原始内容: {json_str[:200]}")
            return None


def extract_error_info(error):
    try:
        error_type = type(error).__name__
        code = None
        try:
            code = getattr(error, 'code', None) or getattr(error, 'status_code', None) or getattr(error, 'http_status', None)
            if code is None:
                resp = getattr(error, 'response', None)
                if resp is not None:
                    code = getattr(resp, 'status_code', None) or getattr(resp, 'status', None)
        except Exception:
            pass
        return error_type, code
    except Exception:
        return 'UnknownError', None


# 数据目录（项目根目录/data）
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_THIS_DIR)
DATA_DIR = os.path.join(_ROOT_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

AI_DECISIONS_LOG_PATH = os.path.join(DATA_DIR, 'ai_decisions.jsonl')
TRADES_LOG_PATH = os.path.join(DATA_DIR, 'trades.jsonl')
PROFIT_CURVE_LOG_PATH = os.path.join(DATA_DIR, 'profit_curve.jsonl')
REALIZED_PNL_PATH = os.path.join(DATA_DIR, 'realized_pnl.json')


def append_ai_decision_to_file(decision: dict) -> None:
    try:
        with open(AI_DECISIONS_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(decision, ensure_ascii=False) + '\n')
    except Exception:
        pass


def append_trade_to_file(trade: dict) -> None:
    try:
        with open(TRADES_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(trade, ensure_ascii=False) + '\n')
    except Exception:
        pass


def append_profit_point_to_file(point: dict) -> None:
    try:
        with open(PROFIT_CURVE_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(point, ensure_ascii=False) + '\n')
    except Exception:
        pass


def load_realized_pnl() -> None:
    try:
        if os.path.exists(REALIZED_PNL_PATH):
            with open(REALIZED_PNL_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                state.realized_profit_usdt = float(data.get('realized_profit_usdt', 0.0))
    except Exception:
        pass


def save_realized_pnl() -> None:
    try:
        with open(REALIZED_PNL_PATH, 'w', encoding='utf-8') as f:
            json.dump({'realized_profit_usdt': state.realized_profit_usdt}, f, ensure_ascii=False)
    except Exception:
        pass


def update_win_statistics(realized_pnl_usdt: float, size_contracts: float) -> None:
    try:
        from .okx import get_contract_size_btc
        ct_size_btc = get_contract_size_btc()
        btc_equiv = float(size_contracts) * float(ct_size_btc)
        threshold_usdt = btc_equiv * 1.5
        perf = state.web_data.get('performance', {})
        if realized_pnl_usdt - threshold_usdt > 0:
            perf['wins'] = perf.get('wins', 0) + 1
        else:
            perf['losses'] = perf.get('losses', 0) + 1
        total = perf.get('wins', 0) + perf.get('losses', 0)
        perf['total_trades'] = total
        perf['win_rate'] = (perf.get('wins', 0) / total * 100.0) if total > 0 else 0
        state.web_data['performance'] = perf
    except Exception:
        pass


