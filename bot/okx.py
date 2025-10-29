import os
import json
import logging
import time
import threading
import queue
import requests
import config
from datetime import datetime

from .context import exchange, TRADE_CONFIG, logger, get_symbol_leverage
from .state import ensure_symbol_bucket
from .state import ACCOUNT_POS_MODE


class _OkxRequestTask:
    def __init__(self, fn):
        self.fn = fn
        self.event = threading.Event()
        self.result = None
        self.exc = None


class OkxRequestDispatcher:
    """集中派发OKX请求：单线程顺序执行，请求速率限制为每秒N次。"""

    def __init__(self, max_rps: int = 5):
        self.max_rps = max(1, int(max_rps))
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="OkxRequestDispatcher", daemon=True)
        self._thread.start()

    def submit(self, fn):
        # 避免在派发线程内部再次提交导致死锁：直接执行
        if threading.current_thread() is self._thread:
            return fn()

        task = _OkxRequestTask(fn)
        self._queue.put(task)
        task.event.wait()
        if task.exc is not None:
            raise task.exc
        return task.result

    def _run(self):
        interval = 1.0 / float(self.max_rps)
        last_ts = 0.0
        while not self._stop.is_set():
            try:
                task = self._queue.get(timeout=0.1)
            except Exception:
                continue
            # 速率控制：确保相邻两次执行至少间隔 interval 秒
            now = time.monotonic()
            wait = interval - (now - last_ts)
            if wait > 0:
                try:
                    time.sleep(wait)
                except Exception:
                    pass
            try:
                task.result = task.fn()
            except Exception as e:
                task.exc = e
            finally:
                last_ts = time.monotonic()
                task.event.set()
                try:
                    self._queue.task_done()
                except Exception:
                    pass


def _get_max_rps_default():
    try:
        val = os.getenv('OKX_MAX_RPS')
        if val is not None and str(val).strip() != '':
            return max(1, int(val))
    except Exception:
        pass
    try:
        return max(1, int(getattr(config, 'OKX_MAX_RPS', 5)))
    except Exception:
        return 5


# 全局派发器：集中限流到每秒5次（可通过环境变量 OKX_MAX_RPS 或 config.OKX_MAX_RPS 覆盖）
REQUEST_DISPATCHER = OkxRequestDispatcher(max_rps=_get_max_rps_default())


def _with_rate_limit_retry(callable_fn, max_retries: int = None, wait_seconds: int = 30):
    """调用OKX/CCXT接口，若命中限频(50011或"Too Many Requests")则按固定间隔退避重试。

    参数:
    - max_retries: 最大重试次数（不含首次调用），默认3
    - wait_seconds: 每次退避等待秒数，默认30
    """
    attempt = 0
    while True:
        try:
            # 所有请求通过全局派发器集中限流与顺序执行
            resp = REQUEST_DISPATCHER.submit(callable_fn)
            try:
                if isinstance(resp, dict):
                    code = str(resp.get('code')) if 'code' in resp else None
                    msg = str(resp.get('msg') or '')
                    if code == '50011' or ('Too Many Requests' in msg):
                        if (max_retries is not None) and (attempt >= max_retries):
                            return resp
                        print(f'OKX限频(50011): 等待{wait_seconds}秒后重试... (第{attempt+1}次)')
                        time.sleep(wait_seconds)
                        attempt += 1
                        continue
            except Exception:
                pass
            return resp
        except Exception as e:
            em = str(e)
            if ('Too Many Requests' in em) or ('50011' in em):
                if (max_retries is not None) and (attempt >= max_retries):
                    raise
                print(f'OKX限频异常(50011): 等待{wait_seconds}秒后重试... (第{attempt+1}次)')
                time.sleep(wait_seconds)
                attempt += 1
                continue
            raise

def _get_okx_inst_id() -> str:
    try:
        market = exchange.market(TRADE_CONFIG['symbol'])
        if isinstance(market, dict):
            return market.get('id') or market.get('info', {}).get('instId')
    except Exception:
        pass
    return TRADE_CONFIG['symbol'].replace('/', '-').replace(':USDT', '-SWAP')
def _okx_public_get(path: str, params: dict = None) -> dict:
    try:
        host = None
        try:
            host = getattr(exchange, 'hostname', None) or exchange.urls.get('hostname')
        except Exception:
            host = None
        if not host or '{' in str(host):
            host = 'www.okx.com'
        base_url = f"https://{host}"
        url = f"{base_url}{path}"

        def _do_get():
            r = requests.get(url, params=params or {}, timeout=15)
            try:
                return r.json()
            except Exception:
                return {'code': str(r.status_code), 'msg': r.text}

        resp_json = _with_rate_limit_retry(_do_get)
        return resp_json
    except Exception as e:
        return {'code': 'error', 'msg': str(e)}


def get_open_interest_snapshot() -> dict:
    """获取未平仓合约（OI）快照。返回 {latest, ts}，失败返回空字典。"""
    try:
        inst_id = _get_okx_inst_id()
        resp = _okx_public_get('/api/v5/public/open-interest', {'instId': inst_id})
        code = str(resp.get('code')) if isinstance(resp, dict) else None
        if code == '0':
            data = (resp.get('data') or [])
            if data:
                d0 = data[0]
                oi = d0.get('oi') or d0.get('oiCcy')
                ts = d0.get('ts')
                try:
                    latest = float(oi)
                except Exception:
                    latest = None
                return {'latest': latest, 'ts': ts}
        return {}
    except Exception:
        return {}


def get_current_funding_rate() -> dict:
    """获取当前资金费率。返回 {rate, ts}，失败返回空字典。"""
    try:
        inst_id = _get_okx_inst_id()
        # 优先使用 CCXT，如失败则回退原生 REST
        try:
            fr = exchange.fetch_funding_rate(TRADE_CONFIG['symbol'])
            rate = fr.get('fundingRate') if isinstance(fr, dict) else None
            try:
                rate = float(rate)
            except Exception:
                rate = None
            ts = fr.get('timestamp') if isinstance(fr, dict) else None
            return {'rate': rate, 'ts': ts}
        except Exception:
            pass
        resp = _okx_public_get('/api/v5/public/funding-rate', {'instId': inst_id})
        code = str(resp.get('code')) if isinstance(resp, dict) else None
        if code == '0':
            data = (resp.get('data') or [])
            if data:
                d0 = data[0]
                rate = d0.get('fundingRate')
                ts = d0.get('fundingTime') or d0.get('ts')
                try:
                    rate = float(rate)
                except Exception:
                    rate = None
                return {'rate': rate, 'ts': ts}
        return {}
    except Exception:
        return {}



def _get_okx_price_tick_info():
    """从交易所元数据获取价格tick信息（步长与小数位）。"""
    try:
        market = exchange.market(TRADE_CONFIG['symbol'])
        info = market.get('info', {}) if isinstance(market, dict) else {}
        tick_sz_str = (info.get('tickSz') or info.get('tickSize') or '').strip()
        if tick_sz_str:
            try:
                step = float(tick_sz_str)
            except Exception:
                step = 0.1
            decimals = 0
            if '.' in tick_sz_str:
                decimals = len(tick_sz_str.split('.')[-1].rstrip('0'))
            return step, max(decimals, 0)
        precision = market.get('precision', {}) if isinstance(market, dict) else {}
        dec = precision.get('price')
        if isinstance(dec, int) and dec >= 0:
            step = 10 ** (-dec) if dec <= 8 else 1e-8
            return step, dec
    except Exception:
        pass
    return 0.1, 1


def _format_price_for_okx(px: float) -> str:
    try:
        step, decimals = _get_okx_price_tick_info()
        if step <= 0:
            step = 0.1
            decimals = 1
        rounded = round(round(float(px) / step) * step, max(decimals, 0))
        fmt = f"{{:.{max(decimals, 0)}f}}"
        return fmt.format(rounded)
    except Exception:
        try:
            return f"{float(px):.2f}"
        except Exception:
            return str(px)


def get_contract_size_btc() -> float:
    try:
        market = exchange.market(TRADE_CONFIG['symbol'])
        contract_size = market.get('contractSize') or float(market.get('info', {}).get('ctVal', 0))
        if not contract_size or contract_size <= 0:
            contract_size = 0.001
        return float(contract_size)
    except Exception:
        return 0.001


def btc_amount_to_okx_contracts(btc_amount: float) -> int:
    try:
        contract_size = get_contract_size_btc()
        contracts = int((btc_amount / contract_size) + 1e-9)
        return max(1, int(contracts))
    except Exception:
        return 1


def estimate_required_margin_usdt(contracts: int, mark_price_usdt: float, leverage: float) -> float:
    ct_size_btc = get_contract_size_btc()
    notional = float(contracts) * ct_size_btc * float(mark_price_usdt)
    return (notional / max(float(leverage), 1.0)) * 1.05


def compute_aggressive_limit_price(side: str, base_price: float, offset_ticks: int = None) -> float:
    try:
        step, decimals = _get_okx_price_tick_info()
        if step <= 0:
            step = 0.1
            decimals = 1
        if offset_ticks is None:
            try:
                offset_ticks = int(getattr(config, 'LIMIT_ORDER_OFFSET_TICKS', 2))
            except Exception:
                offset_ticks = 2
        s = (side or '').lower()
        if s == 'buy':
            raw = float(base_price) + step * max(offset_ticks, 0)
        else:
            raw = float(base_price) - step * max(offset_ticks, 0)
        rounded = round(round(raw / step) * step, max(decimals, 0))
        return float(rounded)
    except Exception:
        try:
            return float(base_price)
        except Exception:
            return base_price


def _okx_signed_post(path: str, body_obj) -> dict:
    try:
        host = None
        try:
            host = getattr(exchange, 'hostname', None) or exchange.urls.get('hostname')
        except Exception:
            host = None
        if not host or '{' in str(host):
            host = 'www.okx.com'
        base_url = f"https://{host}"

        api_key = os.getenv('OKX_API_KEY')
        secret = os.getenv('OKX_SECRET')
        passphrase = os.getenv('OKX_PASSWORD')
        if not api_key or not secret or not passphrase:
            raise RuntimeError('OKX API配置缺失')

        ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        method = 'POST'
        body_str = json.dumps(body_obj, separators=(',', ':'))
        import hmac
        import hashlib
        import base64
        prehash = f"{ts}{method}{path}{body_str}"
        sign = base64.b64encode(hmac.new(secret.encode('utf-8'), prehash.encode('utf-8'), hashlib.sha256).digest()).decode()

        headers = {
            'OK-ACCESS-KEY': api_key,
            'OK-ACCESS-SIGN': sign,
            'OK-ACCESS-TIMESTAMP': ts,
            'OK-ACCESS-PASSPHRASE': passphrase,
            'Content-Type': 'application/json'
        }

        url = f"{base_url}{path}"
        def _do_post():
            r = requests.post(url, headers=headers, data=body_str, timeout=15)
            try:
                return r.json()
            except Exception:
                return {'code': str(r.status_code), 'msg': r.text}

        resp_json = _with_rate_limit_retry(_do_post)
        return resp_json
    except Exception as e:
        return {'code': 'error', 'msg': str(e)}


def cancel_algo_orders(entries: list) -> dict:
    result = {'requested': 0, 'cancelled': [], 'errors': [], 'okx_code': None, 'ok': False}
    try:
        if not isinstance(entries, list) or len(entries) == 0:
            return result
        valid = []
        for e in entries:
            try:
                inst = (e.get('instId') or '').strip()
                aid = (e.get('algoId') or '').strip() if e.get('algoId') is not None else None
                cid = (e.get('algoClOrdId') or '').strip() if e.get('algoClOrdId') is not None else None
                if not inst:
                    continue
                if (not aid) and (not cid):
                    continue
                valid.append({'instId': inst, 'algoId': aid, 'algoClOrdId': cid})
            except Exception:
                continue
        if not valid:
            return result

        result['requested'] = len(valid)

        body = valid
        try:
            logger.debug(f"POST /api/v5/trade/cancel-algos body={body}")
            resp = _okx_signed_post('/api/v5/trade/cancel-algos', body)
            logger.debug(f"/api/v5/trade/cancel-algos response={resp}")
            code = str(resp.get('code')) if isinstance(resp, dict) else None
            result['okx_code'] = code
            if code == '0':
                data = resp.get('data') or []
                for item in data:
                    try:
                        if str(item.get('sCode')) == '0':
                            result['cancelled'].append(item.get('algoId') or item.get('algoClOrdId'))
                        else:
                            emsg = item.get('sMsg') or 'cancel failed'
                            result['errors'].append(f"{item.get('algoId') or item.get('algoClOrdId')}: {emsg}")
                    except Exception:
                        continue
                result['ok'] = (len(result['errors']) == 0 and len(result['cancelled']) == result['requested'])
            else:
                result['errors'].append(str(resp))
                result['ok'] = False
        except Exception as e:
            logger.debug(f"取消策略委托请求失败: {e}")
            result['errors'].append(str(e))

        return result
    except Exception as e:
        result['errors'].append(str(e))
        return result


def format_open_orders_for_prompt(orders: list, max_items: int = 10) -> str:
    try:
        if not orders:
            return "无"
        lines = []
        for idx, o in enumerate(orders[:max_items], start=1):
            side = (o.get('side') or '').upper()
            typ = (o.get('type') or '')
            px = o.get('price')
            sz = o.get('size')
            ps = o.get('posSide') or '-'
            lines.append(f"{idx}) {side} {typ} 价:{px} 数量:{sz} pos:{ps}")
        if len(orders) > max_items:
            lines.append(f"... 其余 {len(orders) - max_items} 条未显示")
        return "\n".join(lines)
    except Exception:
        return "无"


def format_algo_orders_for_prompt(orders: list, max_items: int = 10) -> str:
    try:
        if not orders:
            return "无"
        lines = []
        for idx, o in enumerate(orders[:max_items], start=1):
            side = (o.get('side') or '').upper()
            typ = (o.get('ordType') or '')
            tpt = o.get('tpTriggerPx')
            slt = o.get('slTriggerPx')
            ps = o.get('posSide') or '-'
            parts = []
            if tpt not in (None, ''):
                parts.append(f"TP@{tpt}")
            if slt not in (None, ''):
                parts.append(f"SL@{slt}")
            detail = ' '.join(parts) if parts else '-'
            lines.append(f"{idx}) {side} {typ} {detail} pos:{ps}")
        if len(orders) > max_items:
            lines.append(f"... 其余 {len(orders) - max_items} 条未显示")
        return "\n".join(lines)
    except Exception:
        return "无"


def _parse_price_value(val):
    try:
        import re as _re
        if val is None:
            return None
        if isinstance(val, (int, float)):
            v = float(val)
            return v if v > 0 else None
        s = str(val).strip()
        m = _re.search(r"-?\d+(?:\.\d+)?", s)
        if m:
            v = float(m.group(0))
            return v if v > 0 else None
    except Exception:
        return None
    return None


def prepare_tp_sl_params_for_order(signal_side: str, current_price: float, take_profit, stop_loss) -> dict:
    try:
        tp = _parse_price_value(take_profit)
        sl = _parse_price_value(stop_loss)
        if tp is None and sl is None:
            return {}
        step, _ = _get_okx_price_tick_info()
        if step <= 0:
            step = 0.1
        side = (signal_side or '').upper()
        params = {}
        if side == 'BUY':
            if tp is not None:
                params['tpTriggerPx'] = _format_price_for_okx(tp)
                params['tpOrdPx'] = _format_price_for_okx(tp - step)
                params['tpTriggerPxType'] = 'last'
            if sl is not None:
                params['slTriggerPx'] = _format_price_for_okx(sl)
                params['slOrdPx'] = _format_price_for_okx(sl - step)
                params['slTriggerPxType'] = 'last'
        elif side == 'SELL':
            if tp is not None:
                params['tpTriggerPx'] = _format_price_for_okx(tp)
                params['tpOrdPx'] = _format_price_for_okx(tp + step)
                params['tpTriggerPxType'] = 'last'
            if sl is not None:
                params['slTriggerPx'] = _format_price_for_okx(sl)
                params['slOrdPx'] = _format_price_for_okx(sl + step)
                params['slTriggerPxType'] = 'last'
        else:
            return {}
        return params
    except Exception:
        return {}


def _adjust_tp_sl_by_position_side(current_price: float, tp: float, sl: float, pos_side: str):
    try:
        step, _ = _get_okx_price_tick_info()
        if step <= 0:
            step = 0.1
        cp = float(current_price)
        tp_adj = tp
        sl_adj = sl
        if (pos_side or '').lower() == 'long':
            if tp_adj is not None and tp_adj <= cp:
                tp_adj = cp + step
            if sl_adj is not None and sl_adj >= cp:
                sl_adj = cp - step
        elif (pos_side or '').lower() == 'short':
            if tp_adj is not None and tp_adj >= cp:
                tp_adj = cp - step
            if sl_adj is not None and sl_adj <= cp:
                sl_adj = cp + step
        return tp_adj, sl_adj
    except Exception:
        return tp, sl


def get_open_orders_pending(limit: int = 20) -> list:
    try:
        inst_id = _get_okx_inst_id()
        params = {'instId': inst_id}
        resp = _with_rate_limit_retry(lambda: exchange.privateGetTradeOrdersPending(params))
        logger.debug(f"GET /api/v5/trade/orders-pending resp={resp}")
        data = resp.get('data') if isinstance(resp, dict) else None
        logger.debug(f"未成交普通订单 原始 data={data}")
        if not data:
            return []
        results = []
        for item in data[:max(1, int(limit))]:
            try:
                results.append({
                    'id': item.get('ordId') or item.get('clOrdId') or item.get('id'),
                    'side': (item.get('side') or '').lower(),
                    'type': (item.get('ordType') or '').lower(),
                    'price': item.get('px'),
                    'size': item.get('sz'),
                    'posSide': (item.get('posSide') or '').lower() if item.get('posSide') else None,
                    'state': (item.get('state') or '').lower() if item.get('state') else None,
                    'cTime': item.get('cTime')
                })
            except Exception:
                continue
        logger.debug(f"未成交普通订单 标准化 results={results}")
        return results
    except Exception:
        return []


def get_algo_orders_pending(limit: int = 20) -> list:
    try:
        inst_id = _get_okx_inst_id()
        params = {'instId': inst_id, 'ordType': 'conditional'}
        logger.debug(f"GET /api/v5/trade/orders-algo-pending params={params}")
        resp = _with_rate_limit_retry(lambda: exchange.privateGetTradeOrdersAlgoPending(params))
        logger.debug(f"GET /api/v5/trade/orders-algo-pending resp={resp}")
        data = resp.get('data') if isinstance(resp, dict) else None
        logger.debug(f"未成交策略订单 原始 data={data}")
        if not data:
            return []
        results = []
        for item in data[:max(1, int(limit))]:
            try:
                results.append({
                    'algoId': item.get('algoId') or item.get('algoID') or item.get('id'),
                    'side': (item.get('side') or '').lower(),
                    'ordType': (item.get('ordType') or '').lower(),
                    'tpTriggerPx': item.get('tpTriggerPx'),
                    'slTriggerPx': item.get('slTriggerPx'),
                    'tpOrdPx': item.get('tpOrdPx'),
                    'slOrdPx': item.get('slOrdPx'),
                    'posSide': (item.get('posSide') or '').lower() if item.get('posSide') else None,
                    'state': (item.get('state') or '').lower() if item.get('state') else None,
                    'cTime': item.get('cTime')
                })
            except Exception:
                continue
        logger.debug(f"未成交策略订单 标准化 results={results}")
        return results
    except Exception:
        return []


def verify_tp_sl_against_pending(expected_tp: float = None, expected_sl: float = None, side: str = None) -> dict:
    result = {'tp_match': None, 'sl_match': None}
    try:
        inst_id = _get_okx_inst_id()
        resp = _with_rate_limit_retry(lambda: exchange.privateGetTradeOrdersAlgoPending({'instId': inst_id, 'ordType': 'conditional'}))
        data = resp.get('data') if isinstance(resp, dict) else None
        if not data:
            return result
        step, _ = _get_okx_price_tick_info()
        tol = max(step, 0.1)
        for item in data:
            try:
                if item.get('instId') != inst_id:
                    continue
                ord_type = (item.get('ordType') or '').lower()
                if ord_type not in ('conditional', 'tpsl'):
                    continue
                if side and item.get('side') and item.get('side').lower() != side.lower():
                    continue
                tpx = item.get('tpTriggerPx')
                slx = item.get('slTriggerPx')
                tpf = float(tpx) if tpx not in (None, '') else None
                slf = float(slx) if slx not in (None, '') else None
                if expected_tp is not None and tpf is not None and result['tp_match'] is None:
                    result['tp_match'] = abs(tpf - float(expected_tp)) <= tol
                if expected_sl is not None and slf is not None and result['sl_match'] is None:
                    result['sl_match'] = abs(slf - float(expected_sl)) <= tol
            except Exception:
                continue
        return result
    except Exception:
        return result


def deduplicate_pending_tpsl(side: str = None) -> dict:
    summary = {'cancelled': [], 'groups': {'tp': {}, 'sl': {}}}
    try:
        inst_id = _get_okx_inst_id()
        resp = _with_rate_limit_retry(lambda: exchange.privateGetTradeOrdersAlgoPending({'instId': inst_id, 'ordType': 'conditional'}))
        data = resp.get('data') if isinstance(resp, dict) else None
        if not data:
            return summary

        step, decimals = _get_okx_price_tick_info()
        tol = max(step, 0.1)

        def _round_px(px):
            try:
                return f"{round(round(float(px) / step) * step, max(decimals, 0)):.{max(decimals, 0)}f}"
            except Exception:
                return str(px)

        for item in data:
            try:
                if item.get('instId') != inst_id:
                    continue
                ord_type = (item.get('ordType') or '').lower()
                if ord_type not in ('conditional', 'tpsl'):
                    continue
                if side and item.get('side') and item.get('side').lower() != side.lower():
                    continue
                algo_id = item.get('algoId') or item.get('algoID') or item.get('id')
                if not algo_id:
                    continue
                tpx = item.get('tpTriggerPx')
                slx = item.get('slTriggerPx')
                if tpx not in (None, ''):
                    key = _round_px(tpx)
                    summary['groups']['tp'].setdefault(key, []).append({'algoId': algo_id, 'cTime': item.get('cTime')})
                if slx not in (None, ''):
                    key = _round_px(slx)
                    summary['groups']['sl'].setdefault(key, []).append({'algoId': algo_id, 'cTime': item.get('cTime')})
            except Exception:
                continue

        cancel_ids = []
        for kind in ('tp', 'sl'):
            for key, arr in summary['groups'][kind].items():
                if len(arr) > 1:
                    try:
                        arr_sorted = sorted(arr, key=lambda x: int(x.get('cTime') or 0))
                    except Exception:
                        arr_sorted = arr
                    keep = arr_sorted[-1]['algoId'] if arr_sorted else None
                    for obj in arr_sorted[:-1]:
                        cancel_ids.append(obj['algoId'])

        if cancel_ids:
            try:
                entries = [{'algoId': cid, 'instId': inst_id} for cid in cancel_ids]
                res = cancel_algo_orders(entries)
                cancelled = res.get('cancelled', []) if isinstance(res, dict) else []
                summary['cancelled'] = cancelled if cancelled else cancel_ids
            except Exception:
                pass

        return summary
    except Exception:
        return summary


def cancel_existing_tpsl_for_position(pos_side: str = None) -> dict:
    result = {'cancelled': [], 'count': 0}
    try:
        if not pos_side:
            return result
        inst_id = _get_okx_inst_id()
        resp = _with_rate_limit_retry(lambda: exchange.privateGetTradeOrdersAlgoPending({'instId': inst_id, 'ordType': 'conditional'}))
        data = resp.get('data') if isinstance(resp, dict) else None
        if not data:
            return result

        order_side = 'sell' if (pos_side or '').lower() == 'long' else 'buy'
        ids = []
        for item in data:
            try:
                if item.get('instId') != inst_id:
                    continue
                ord_type = (item.get('ordType') or '').lower()
                if ord_type not in ('conditional', 'tpsl'):
                    continue
                if (item.get('side') or '').lower() != order_side:
                    continue
                algo_id = item.get('algoId') or item.get('algoID') or item.get('id')
                if algo_id:
                    ids.append(algo_id)
            except Exception:
                continue

        if ids:
            try:
                entries = [{'algoId': cid, 'instId': inst_id} for cid in ids]
                res = cancel_algo_orders(entries)
                cancelled = res.get('cancelled', []) if isinstance(res, dict) else []
                result['cancelled'] = cancelled if cancelled else ids
                result['count'] = len(result['cancelled'])
            except Exception:
                try:
                    _with_rate_limit_retry(lambda: exchange.privatePostTradeCancelAlgos({'algoId': ids, 'instId': inst_id}))
                    result['cancelled'] = ids
                    result['count'] = len(ids)
                except Exception:
                    pass
        return result
    except Exception:
        return result


def get_current_position():
    try:
        sym = TRADE_CONFIG['symbol']
        bucket = None
        try:
            bucket = ensure_symbol_bucket(sym)
        except Exception:
            bucket = None
        import time as _t
        # 优先用缓存，超过节流再刷新
        if bucket is not None:
            pos_cached = bucket.get('last_position')
            pos_ts = float(bucket.get('last_position_ts') or 0)
            if pos_cached and (_t.time() - pos_ts) < float(getattr(config, 'PRIVATE_UPDATE_INTERVAL_SECONDS', 60)):
                return pos_cached
        positions = _with_rate_limit_retry(lambda: exchange.fetch_positions([sym]))
        for pos in positions:
            if pos['symbol'] == sym:
                contracts = float(pos['contracts']) if pos['contracts'] else 0
                if contracts > 0:
                    cur = {
                        'side': pos['side'],
                        'size': contracts,
                        'entry_price': float(pos['entryPrice']) if pos['entryPrice'] else 0,
                        'unrealized_pnl': float(pos['unrealizedPnl']) if pos['unrealizedPnl'] else 0,
                        'leverage': float(pos['leverage']) if pos['leverage'] else float(get_symbol_leverage(sym)),
                        'symbol': pos['symbol']
                    }
                    if bucket is not None:
                        try:
                            bucket['last_position'] = cur
                            bucket['last_position_ts'] = _t.time()
                        except Exception:
                            pass
                    return cur
        if bucket is not None:
            try:
                bucket['last_position'] = None
                bucket['last_position_ts'] = _t.time()
            except Exception:
                pass
        return None
    except Exception as e:
        print(f"获取持仓失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_all_positions(symbols: list = None) -> list:
    """获取全部(或指定列表)合约的当前持仓，标准化输出。

    返回列表元素结构：
    {
        'symbol': 'BTC/USDT:USDT',
        'side': 'long'|'short',
        'size': float,            # 合约张数
        'entry_price': float,
        'unrealized_pnl': float,
        'leverage': float
    }
    """
    results = []
    try:
        syms = None
        try:
            if symbols and isinstance(symbols, list) and len(symbols) > 0:
                syms = symbols
        except Exception:
            syms = None
        try:
            pos_list = _with_rate_limit_retry(lambda: exchange.fetch_positions(syms))
        except Exception:
            pos_list = []
        for pos in (pos_list or []):
            try:
                contracts = float(pos.get('contracts') or 0)
                if contracts <= 0:
                    continue
                results.append({
                    'symbol': pos.get('symbol'),
                    'side': pos.get('side'),
                    'size': contracts,
                    'entry_price': float(pos.get('entryPrice') or 0),
                    'unrealized_pnl': float(pos.get('unrealizedPnl') or 0),
                    'leverage': float(pos.get('leverage') or get_symbol_leverage(pos.get('symbol')))
                })
            except Exception:
                continue
        return results
    except Exception:
        return results


def get_account_overview() -> dict:
    """获取账户概览（USDT自由余额与总权益）。失败返回空字典。"""
    try:
        bal = _with_rate_limit_retry(lambda: exchange.fetch_balance())
        return {
            'usdt_free': float((bal.get('USDT') or {}).get('free') or 0.0),
            'usdt_total': float((bal.get('USDT') or {}).get('total') or 0.0),
        }
    except Exception:
        return {}


def format_positions_overview_for_prompt(positions: list) -> str:
    """将多合约持仓概览格式化为多行文本。"""
    try:
        if not positions:
            return "无持仓"
        lines = []
        for idx, p in enumerate(positions, start=1):
            try:
                sym = p.get('symbol')
                side = (p.get('side') or '-').lower()
                size = p.get('size')
                ep = p.get('entry_price')
                upnl = p.get('unrealized_pnl')
                lev = p.get('leverage')
                lines.append(f"{idx}) {sym} {side} 张:{size} 入场:{ep} UPNL:{upnl:.2f} L:{lev}")
            except Exception:
                continue
        return "\n".join(lines) if lines else "无持仓"
    except Exception:
        return "无持仓"


def set_position_tp_sl_for_okx(tp_price: float = None, sl_price: float = None, pos_side: str = None) -> bool:
    try:
        if tp_price is None and sl_price is None:
            return False

        market = exchange.market(TRADE_CONFIG['symbol'])
        inst_id = None
        try:
            if isinstance(market, dict):
                inst_id = market.get('id') or market.get('info', {}).get('instId')
        except Exception:
            pass
        if not inst_id:
            inst_id = TRADE_CONFIG['symbol'].replace('/', '-').replace(':USDT', '-SWAP')

        try:
            order_side = None
            if not pos_side:
                try:
                    cur = get_current_position()
                    pos_side = cur.get('side') if cur else None
                except Exception:
                    pos_side = None
            if pos_side == 'long':
                order_side = 'sell'
            elif pos_side == 'short':
                order_side = 'buy'
            else:
                order_side = 'sell'
        except Exception:
            order_side = 'sell'

        try:
            if ACCOUNT_POS_MODE == 'long_short' and pos_side in ('long', 'short'):
                pass
        except Exception:
            pass

        base_params = {
            'instId': inst_id,
            'tdMode': 'cross',
            'side': order_side,
            'reduceOnly': 'true',
        }

        # 使用市场最小价格步长作为触发后委托价的微调
        step, _ = _get_okx_price_tick_info()
        if step <= 0:
            step = 0.1

        pos_size_contracts = None
        try:
            curpos = get_current_position()
            if curpos and curpos.get('size'):
                pos_size_contracts = int(float(curpos['size']))
        except Exception:
            pos_size_contracts = None
        if not pos_size_contracts or pos_size_contracts <= 0:
            print("当前无有效持仓，跳过TP/SL设置")
            return False
        base_params['sz'] = str(pos_size_contracts)

        ok_any = False
        if tp_price is not None:
            tp_req = dict(base_params)
            tp_req.update({
                'ordType': 'conditional',
                'tpTriggerPx': _format_price_for_okx(tp_price),
                'tpOrdPx': _format_price_for_okx(tp_price - step) if order_side == 'sell' else _format_price_for_okx(tp_price + step),
                'tpTriggerPxType': 'last',
            })
            try:
                if ACCOUNT_POS_MODE == 'long_short' and pos_side in ('long', 'short'):
                    tp_req['posSide'] = pos_side
            except Exception:
                pass
            try:
                _with_rate_limit_retry(lambda: exchange.privatePostTradeOrderAlgo({k: v for k, v in tp_req.items() if v is not None}))
                print(f"✓ 已提交持仓止盈设置: {tp_req}")
                ok_any = True
            except Exception as e:
                print(f"⚠️ 止盈设置失败: {e}")
        if sl_price is not None:
            sl_req = dict(base_params)
            sl_req.update({
                'ordType': 'conditional',
                'slTriggerPx': _format_price_for_okx(sl_price),
                'slOrdPx': _format_price_for_okx(sl_price - step) if order_side == 'sell' else _format_price_for_okx(sl_price + step),
                'slTriggerPxType': 'last',
            })
            try:
                if ACCOUNT_POS_MODE == 'long_short' and pos_side in ('long', 'short'):
                    sl_req['posSide'] = pos_side
            except Exception:
                pass
            try:
                _with_rate_limit_retry(lambda: exchange.privatePostTradeOrderAlgo({k: v for k, v in sl_req.items() if v is not None}))
                print(f"✓ 已提交持仓止损设置: {sl_req}")
                ok_any = True
            except Exception as e:
                print(f"⚠️ 止损设置失败: {e}")
        return ok_any
    except Exception as _:
        return False


def setup_exchange():
    try:
        # 为当前激活符号设置杠杆（按每币种杠杆）
        try:
            sym = TRADE_CONFIG.get('symbol')
        except Exception:
            sym = None
        if sym:
            lev = int(get_symbol_leverage(sym))
            _with_rate_limit_retry(lambda: exchange.set_leverage(lev, sym, {'mgnMode': 'cross'}))
            print(f"设置杠杆倍数: {sym} => {lev}x")

        balance = _with_rate_limit_retry(lambda: exchange.fetch_balance())
        usdt_balance = balance['USDT']['free']
        print(f"当前USDT余额: {usdt_balance:.2f}")

        try:
            cfg = _with_rate_limit_retry(lambda: exchange.privateGetAccountConfig())
            data = (cfg.get('data') or [{}])[0]
            pos_mode_api = (data.get('posMode') or '').lower()
            from .state import ACCOUNT_POS_MODE as _MODE
            if 'long' in pos_mode_api:
                _mode = 'long_short'
            else:
                _mode = 'net'
            # 直接写入 state
            from . import state as _state
            _state.ACCOUNT_POS_MODE = _mode
            print(f"账户持仓模式: {_mode}")
        except Exception as _:
            print("账户持仓模式获取失败，默认按净值模式处理")
            from . import state as _state
            _state.ACCOUNT_POS_MODE = 'net'

        return True
    except Exception as e:
        print(f"交易所设置失败: {e}")
        return False


