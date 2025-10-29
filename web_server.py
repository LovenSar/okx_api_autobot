from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
import logging
import threading
import sys
import os
from werkzeug.serving import WSGIRequestHandler

# 获取当前文件所在目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 导入主程序
import deepseekok2
from bot.okx import _with_rate_limit_retry
import json
import config
from bot import state as bot_state

# 辅助：根据当前符号返回前端展示用杠杆（每币种映射或全局回退）
def __get_leverage_for_symbol(sym):
    try:
        if not sym:
            return deepseekok2.TRADE_CONFIG['leverage']
        from bot.context import get_symbol_leverage
        return int(get_symbol_leverage(sym))
    except Exception:
        try:
            return int(deepseekok2.TRADE_CONFIG['leverage'])
        except Exception:
            return 10
from bot.utils import TRADES_LOG_PATH, PROFIT_CURVE_LOG_PATH

# 明确指定模板和静态文件路径
app = Flask(__name__, 
            template_folder=os.path.join(BASE_DIR, 'templates'),
            static_folder=os.path.join(BASE_DIR, 'static'))
CORS(app)

# 关闭默认的Werkzeug访问日志（保留>=WARNING级别的错误/告警）
logging.getLogger('werkzeug').setLevel(logging.ERROR)
app.logger.setLevel(logging.WARNING)


class _SilentRequestHandler(WSGIRequestHandler):
    def log_request(self, code='-', size='-'):
        pass

    def log(self, type, message, *args):
        pass

@app.route('/')
def index():
    """主页"""
    try:
        return render_template('index.html')
    except Exception as e:
        return f"<h1>模板加载错误</h1><p>{str(e)}</p><p>模板路径: {app.template_folder}</p>"

@app.route('/api/dashboard')
def get_dashboard_data():
    """获取仪表板数据"""
    try:
        data = {
            'account_info': deepseekok2.web_data['account_info'],
            'current_position': deepseekok2.web_data['current_position'],
            'current_price': deepseekok2.web_data['current_price'],
            'last_update': deepseekok2.web_data['last_update'],
            'performance': deepseekok2.web_data['performance'],
            'realized_profit_usdt': getattr(deepseekok2, 'realized_profit_usdt', 0.0),
            'config': {
                'symbol': deepseekok2.TRADE_CONFIG['symbol'],
                'symbols': deepseekok2.TRADE_CONFIG.get('symbols', [deepseekok2.TRADE_CONFIG['symbol']]),
                'leverage': __get_leverage_for_symbol(deepseekok2.TRADE_CONFIG.get('symbol')), 
                'timeframe': deepseekok2.TRADE_CONFIG['timeframe'],
                'test_mode': deepseekok2.TRADE_CONFIG['test_mode'],
                'min_trade_interval_seconds': int(config.TRADE_MIN_INTERVAL_SECONDS)
            }
        }
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/kline')
def get_kline_data():
    """获取K线数据"""
    try:
        return jsonify(deepseekok2.web_data['kline_data'])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/trades')
def get_trade_history():
    """获取交易历史"""
    try:
        # 支持 limit=all 返回全部记录；否则为正整数条数，默认100
        limit_param = request.args.get('limit', default='100')
        limit = None
        try:
            if isinstance(limit_param, str) and limit_param.lower() == 'all':
                limit = None
            else:
                limit = int(limit_param)
                limit = max(1, limit)
        except Exception:
            limit = 100

        items = []
        # 优先从 trades.jsonl 读取
        try:
            if os.path.exists(TRADES_LOG_PATH):
                if limit is None:
                    # 读取全部
                    with open(TRADES_LOG_PATH, 'r', encoding='utf-8') as f:
                        for line in f:
                            try:
                                items.append(json.loads(line))
                            except Exception:
                                continue
                else:
                    # 高效尾部读取指定条数
                    with open(TRADES_LOG_PATH, 'rb') as f:
                        f.seek(0, os.SEEK_END)
                        file_size = f.tell()
                        buffer = bytearray()
                        lines = []
                        block_size = 4096
                        pos = file_size
                        while pos > 0 and len(lines) <= limit:
                            read_size = block_size if pos >= block_size else pos
                            pos -= read_size
                            f.seek(pos)
                            chunk = f.read(read_size)
                            buffer[0:0] = chunk
                            while True:
                                newline_index = buffer.rfind(b'\n')
                                if newline_index == -1:
                                    break
                                line = buffer[newline_index+1:]
                                buffer = buffer[:newline_index]
                                if line.strip():
                                    lines.append(line)
                                if len(lines) >= limit:
                                    break
                        if len(lines) < limit and buffer.strip():
                            lines.append(buffer)
                    lines = list(reversed(lines))
                    for b in lines:
                        try:
                            items.append(json.loads(b.decode('utf-8')))
                        except Exception:
                            continue
        except Exception:
            items = []

        if not items:
            if limit is None:
                items = deepseekok2.web_data['trade_history'][:]
            else:
                items = deepseekok2.web_data['trade_history'][-limit:]

        return jsonify(items)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ai_decisions')
def get_ai_decisions():
    """获取AI决策历史"""
    try:
        # 支持通过文件尾部读取，limit 指定条数，默认100
        limit = request.args.get('limit', default=100, type=int)
        limit = max(1, min(limit, 10000))

        # 先尝试从JSONL文件读取
        log_path = getattr(deepseekok2, 'AI_DECISIONS_LOG_PATH', None)
        decisions = []

        if log_path and os.path.exists(log_path):
            try:
                # 高效尾部读取：从文件末尾向前读取直到满足limit
                with open(log_path, 'rb') as f:
                    f.seek(0, os.SEEK_END)
                    file_size = f.tell()
                    buffer = bytearray()
                    lines = []
                    block_size = 4096
                    pos = file_size
                    while pos > 0 and len(lines) <= limit:
                        read_size = block_size if pos >= block_size else pos
                        pos -= read_size
                        f.seek(pos)
                        chunk = f.read(read_size)
                        buffer[0:0] = chunk
                        # 按行切分
                        while True:
                            newline_index = buffer.rfind(b'\n')
                            if newline_index == -1:
                                break
                            line = buffer[newline_index+1:]
                            buffer = buffer[:newline_index]
                            if line.strip():
                                lines.append(line)
                            if len(lines) >= limit:
                                break
                    # 剩余缓冲作为第一行
                    if len(lines) < limit and buffer.strip():
                        lines.append(buffer)

                # 反转为时间正序
                lines = list(reversed(lines))
                for b in lines:
                    try:
                        decisions.append(json.loads(b.decode('utf-8')))
                    except Exception:
                        continue
            except Exception:
                decisions = []

        # 如果文件没有或失败，回退到内存数据
        if not decisions:
            data = deepseekok2.web_data['ai_decisions']
            decisions = data[-limit:]

        return jsonify(decisions)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/signals')
def get_signal_history():
    """获取信号历史统计"""
    try:
        # 历史聚合优先：从 ai_decisions.jsonl 统计，失败再回退内存
        limit = request.args.get('limit', default=1000, type=int)
        limit = max(1, min(limit, 20000))

        records = []
        log_path = getattr(deepseekok2, 'AI_DECISIONS_LOG_PATH', None)
        if log_path and os.path.exists(log_path):
            try:
                with open(log_path, 'rb') as f:
                    f.seek(0, os.SEEK_END)
                    file_size = f.tell()
                    buffer = bytearray()
                    lines = []
                    block_size = 4096
                    pos = file_size
                    while pos > 0 and len(lines) <= limit:
                        read_size = block_size if pos >= block_size else pos
                        pos -= read_size
                        f.seek(pos)
                        chunk = f.read(read_size)
                        buffer[0:0] = chunk
                        while True:
                            newline_index = buffer.rfind(b'\n')
                            if newline_index == -1:
                                break
                            line = buffer[newline_index+1:]
                            buffer = buffer[:newline_index]
                            if line.strip():
                                lines.append(line)
                            if len(lines) >= limit:
                                break
                    if len(lines) < limit and buffer.strip():
                        lines.append(buffer)
                lines = list(reversed(lines))
                for b in lines:
                    try:
                        records.append(json.loads(b.decode('utf-8')))
                    except Exception:
                        continue
            except Exception:
                records = []

        if not records:
            records = deepseekok2.signal_history[-limit:]

        signal_stats = {'BUY': 0, 'SELL': 0, 'HOLD': 0}
        confidence_stats = {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
        for s in records:
            st = s.get('signal', 'HOLD')
            cf = s.get('confidence', 'LOW')
            signal_stats[st] = signal_stats.get(st, 0) + 1
            confidence_stats[cf] = confidence_stats.get(cf, 0) + 1

        return jsonify({
            'signal_stats': signal_stats,
            'confidence_stats': confidence_stats,
            'total_signals': len(records),
            'recent_signals': records[-10:] if records else []
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/profit_curve')
def get_profit_curve():
    """获取收益曲线数据：优先从日志文件读取，支持 limit（默认1000 或 all），失败回退内存。"""
    try:
        limit_param = request.args.get('limit', default='1000')
        limit = None
        try:
            if isinstance(limit_param, str) and limit_param.lower() == 'all':
                limit = None
            else:
                limit = int(limit_param)
                limit = max(1, limit)
        except Exception:
            limit = 1000

        items = []
        try:
            if os.path.exists(PROFIT_CURVE_LOG_PATH):
                if limit is None:
                    with open(PROFIT_CURVE_LOG_PATH, 'r', encoding='utf-8') as f:
                        for line in f:
                            try:
                                items.append(json.loads(line))
                            except Exception:
                                continue
                else:
                    with open(PROFIT_CURVE_LOG_PATH, 'rb') as f:
                        f.seek(0, os.SEEK_END)
                        file_size = f.tell()
                        buffer = bytearray()
                        lines = []
                        block_size = 4096
                        pos = file_size
                        while pos > 0 and len(lines) <= limit:
                            read_size = block_size if pos >= block_size else pos
                            pos -= read_size
                            f.seek(pos)
                            chunk = f.read(read_size)
                            buffer[0:0] = chunk
                            while True:
                                newline_index = buffer.rfind(b'\n')
                                if newline_index == -1:
                                    break
                                line = buffer[newline_index+1:]
                                buffer = buffer[:newline_index]
                                if line.strip():
                                    lines.append(line)
                                if len(lines) >= limit:
                                    break
                        if len(lines) < limit and buffer.strip():
                            lines.append(buffer)
                    lines = list(reversed(lines))
                    for b in lines:
                        try:
                            items.append(json.loads(b.decode('utf-8')))
                        except Exception:
                            continue
        except Exception:
            items = []

        if not items:
            if limit is None:
                items = deepseekok2.web_data['profit_curve'][:]
            else:
                items = deepseekok2.web_data['profit_curve'][-limit:]

        return jsonify(items)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/performance')
def get_performance_aggregate():
    try:
        total_trades = 0
        wins = 0
        losses = 0
        realized = float(getattr(deepseekok2, 'realized_profit_usdt', 0.0))
        # 读取 trades.jsonl 聚合历史（如存在）
        try:
            if os.path.exists(TRADES_LOG_PATH):
                with open(TRADES_LOG_PATH, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            obj = json.loads(line)
                            total_trades += 1 if obj.get('signal') in ('BUY','SELL') else 0
                        except Exception:
                            continue
        except Exception:
            pass
        perf = bot_state.web_data.get('performance', {})
        wins = int(perf.get('wins', 0))
        losses = int(perf.get('losses', 0))
        equity = None
        try:
            equity = bot_state.web_data.get('account_info', {}).get('total_equity')
        except Exception:
            equity = None
        return jsonify({
            'realized_profit_usdt': realized,
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'win_rate': (wins / (wins + losses) * 100.0) if (wins + losses) > 0 else 0,
            'equity': equity,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ai_model_info')
def get_ai_model_info():
    """获取AI模型信息和连接状态"""
    try:
        return jsonify(deepseekok2.web_data['ai_model_info'])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/config')
def get_runtime_config():
    """提供前端所需的时间与轮询配置"""
    try:
        return jsonify({
            'timeframe': config.TIMEFRAME,
            'backend_decision_interval_seconds': float(config.BACKEND_DECISION_INTERVAL_SECONDS),
            'backend_realtime_update_interval_seconds': float(config.BACKEND_REALTIME_UPDATE_INTERVAL_SECONDS),
            'frontend_refresh_interval_ms': int(config.FRONTEND_REFRESH_INTERVAL_MS),
            'min_trade_interval_seconds': int(config.TRADE_MIN_INTERVAL_SECONDS)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/symbols')
def list_symbols():
    try:
        return jsonify({'symbols': deepseekok2.TRADE_CONFIG.get('symbols', [deepseekok2.TRADE_CONFIG['symbol']])})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/multi/overview')
def multi_overview():
    try:
        from bot.state import ensure_symbol_bucket
        results = []
        for sym in deepseekok2.TRADE_CONFIG.get('symbols', [deepseekok2.TRADE_CONFIG['symbol']]):
            try:
                bucket = ensure_symbol_bucket(sym)
            except Exception:
                bucket = {}
            price = None
            try:
                if bucket and bucket.get('last_price_data_cache'):
                    price = bucket['last_price_data_cache'].get('price')
            except Exception:
                price = None
            if price is None:
                try:
                    ohlcv = _with_rate_limit_retry(lambda: deepseekok2.exchange.fetch_ohlcv(sym, deepseekok2.TRADE_CONFIG['timeframe'], limit=1))
                    if ohlcv and len(ohlcv) > 0:
                        price = ohlcv[0][4]
                except Exception:
                    price = None
            position = None
            try:
                # 读取缓存，若超过节流间隔再尝试刷新
                pos_cached = bucket.get('last_position')
                pos_ts = float(bucket.get('last_position_ts') or 0)
                import time as _t
                should_refresh = (_t.time() - pos_ts) >= float(config.PRIVATE_UPDATE_INTERVAL_SECONDS)
                if pos_cached and not should_refresh:
                    position = pos_cached
                else:
                    positions = _with_rate_limit_retry(lambda: deepseekok2.exchange.fetch_positions([sym]))
                    for pos in positions:
                        if pos.get('symbol') == sym and float(pos.get('contracts') or 0) > 0:
                            position = {
                                'side': pos.get('side'),
                                'size': float(pos.get('contracts')),
                                'entry_price': float(pos.get('entryPrice') or 0),
                                'unrealized_pnl': float(pos.get('unrealizedPnl') or 0),
                                'leverage': float(pos.get('leverage') or 0),
                                'symbol': sym
                            }
                            break
                    # 写回缓存
                    try:
                        bucket['last_position'] = position
                        bucket['last_position_ts'] = _t.time()
                    except Exception:
                        pass
            except Exception:
                position = None
            try:
                last_ts = bucket.get('last_analysis_ts', 0)
            except Exception:
                last_ts = 0
            tpsl = (bucket.get('tpsl_expected') or {'tp': None, 'sl': None})
            # 杠杆（配置/回退）
            try:
                from bot.context import get_symbol_leverage as _get_lev
                lev_cfg = int(_get_lev(sym))
            except Exception:
                lev_cfg = None

            results.append({
                'symbol': sym,
                'price': price,
                'position': position,
                'timeframe': deepseekok2.TRADE_CONFIG['timeframe'],
                'last_update': bot_state.web_data.get('last_update'),
                'tpsl_expected': tpsl,
                'leverage_cfg': lev_cfg
            })
        return jsonify({'items': results})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 移除Web端TP/SL与平仓交互接口（只读展示，不允许直接操作）

# 取消平仓操作接口（保持后端安全性，仅展示）

@app.route('/api/test_ai')
def test_ai_connection():
    """手动测试AI连接"""
    try:
        result = deepseekok2.test_ai_connection()
        return jsonify({
            'success': result,
            'info': deepseekok2.web_data['ai_model_info']
        })
    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500

def initialize_data():
    """启动时立即初始化一次数据"""
    try:
        print("正在初始化数据...")
        
        # 测试AI连接
        print("\n🤖 测试AI模型连接...")
        deepseekok2.test_ai_connection()
        print()
        
        # 设置交易所（如果还没设置）
        try:
            # 测试一下exchange是否可用
            _with_rate_limit_retry(lambda: deepseekok2.exchange.fetch_balance())
        except:
            # 如果不可用，进行设置
            if not deepseekok2.setup_exchange():
                print("⚠️ 交易所初始化失败")
                return
        
        # 获取初始数据
        price_data = deepseekok2.get_btc_ohlcv_enhanced()
        if price_data:
            # 更新账户信息
            try:
                balance = _with_rate_limit_retry(lambda: deepseekok2.exchange.fetch_balance())
                deepseekok2.web_data['account_info'] = {
                    'usdt_balance': balance['USDT']['free'],
                    'total_equity': balance['USDT']['total']
                }
            except Exception as e:
                print(f"获取账户信息失败: {e}")
            
            # 更新基础数据
            deepseekok2.web_data['current_price'] = price_data['price']
            deepseekok2.web_data['current_position'] = deepseekok2.get_current_position()
            deepseekok2.web_data['kline_data'] = price_data['kline_data']
            from datetime import datetime as _dt
            deepseekok2.web_data['last_update'] = _dt.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # 更新性能数据
            if deepseekok2.web_data['current_position']:
                deepseekok2.web_data['performance']['total_profit'] = deepseekok2.web_data['current_position'].get('unrealized_pnl', 0)
            
            try:
                sym = deepseekok2.TRADE_CONFIG['symbol']
            except Exception:
                sym = 'BTC/USDT:USDT'
            print(f"✅ 初始化完成 - {sym} 价格: ${price_data['price']:,.2f}")
            try:
                # 初始化时确保设置一次该符号的杠杆
                from bot.context import get_symbol_leverage as _lev
                lev = int(_lev(sym))
                _with_rate_limit_retry(lambda: deepseekok2.exchange.set_leverage(lev, sym, {'mgnMode': 'cross'}))
                print(f"✅ 初始化杠杆: {sym} => {lev}x")
            except Exception as _e:
                print(f"⚠️ 初始化杠杆失败: {_e}")
            print(f"✅ K线数据: {len(price_data['kline_data'])}条")
        else:
            print("⚠️ 获取K线数据失败")
            
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        import traceback
        traceback.print_exc()

def run_trading_bot():
    """在独立线程中运行交易机器人"""
    deepseekok2.main()

def run_realtime_update():
    """后台线程：每0.5秒更新实时数据（每秒2次）"""
    import time
    print(f"📊 启动实时数据更新线程（间隔{config.BACKEND_REALTIME_UPDATE_INTERVAL_SECONDS}秒）...")
    
    while True:
        try:
            deepseekok2.update_realtime_data()
        except Exception as e:
            print(f"⚠️ 实时数据更新线程出错: {e}")
        
        time.sleep(float(config.BACKEND_REALTIME_UPDATE_INTERVAL_SECONDS))

if __name__ == '__main__':
    # 立即初始化数据
    print("\n" + "="*60)
    try:
        _sym = deepseekok2.TRADE_CONFIG['symbol']
    except Exception:
        _sym = 'BTC/USDT:USDT'
    print(f"🚀 启动{_sym}交易机器人Web监控...")
    print("="*60 + "\n")
    
    initialize_data()
    
    # 启动实时数据更新线程（每秒更新）
    realtime_thread = threading.Thread(target=run_realtime_update, daemon=True)
    realtime_thread.start()
    
    # 启动交易机器人线程（每分钟决策）
    bot_thread = threading.Thread(target=run_trading_bot, daemon=True)
    bot_thread.start()
    
    # 启动Web服务器
    PORT = 8080  # 使用8080端口避免冲突
    print("\n" + "="*60)
    print("🌐 Web管理界面启动成功！")
    print(f"📊 访问地址: http://localhost:{PORT}")
    print(f"⏰ AI决策频率: 每{config.BACKEND_DECISION_INTERVAL_SECONDS}秒分析一次")
    print(f"📈 数据更新: 每{config.BACKEND_REALTIME_UPDATE_INTERVAL_SECONDS}秒刷新")
    print(f"🌐 Web界面: 每{int(config.FRONTEND_REFRESH_INTERVAL_MS)/1000:.3g}秒自动刷新")
    print(f"🛡️  交易间隔: 最少间隔 {deepseekok2.MIN_TRADE_INTERVAL} 秒")
    print(f"📁 模板目录: {app.template_folder}")
    print(f"📁 静态目录: {app.static_folder}")
    print(f"📄 模板文件存在: {os.path.exists(os.path.join(app.template_folder, 'index.html'))}")
    print("="*60 + "\n")
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True, request_handler=_SilentRequestHandler)

