from flask import Flask, jsonify, render_template
from flask_cors import CORS
import threading
import sys
import os

# 获取当前文件所在目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 导入主程序
import deepseekok2
import config

# 明确指定模板和静态文件路径
app = Flask(__name__, 
            template_folder=os.path.join(BASE_DIR, 'templates'),
            static_folder=os.path.join(BASE_DIR, 'static'))
CORS(app)

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
            'config': {
                'symbol': deepseekok2.TRADE_CONFIG['symbol'],
                'leverage': deepseekok2.TRADE_CONFIG['leverage'],
                'timeframe': deepseekok2.TRADE_CONFIG['timeframe'],
                'test_mode': deepseekok2.TRADE_CONFIG['test_mode']
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
        return jsonify(deepseekok2.web_data['trade_history'])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ai_decisions')
def get_ai_decisions():
    """获取AI决策历史"""
    try:
        return jsonify(deepseekok2.web_data['ai_decisions'])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/signals')
def get_signal_history():
    """获取信号历史统计"""
    try:
        signals = deepseekok2.signal_history
        
        # 统计信号分布
        signal_stats = {'BUY': 0, 'SELL': 0, 'HOLD': 0}
        confidence_stats = {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
        
        for signal in signals:
            signal_type = signal.get('signal', 'HOLD')
            confidence = signal.get('confidence', 'LOW')
            signal_stats[signal_type] = signal_stats.get(signal_type, 0) + 1
            confidence_stats[confidence] = confidence_stats.get(confidence, 0) + 1
        
        return jsonify({
            'signal_stats': signal_stats,
            'confidence_stats': confidence_stats,
            'total_signals': len(signals),
            'recent_signals': signals[-10:] if signals else []
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/profit_curve')
def get_profit_curve():
    """获取收益曲线数据"""
    try:
        return jsonify(deepseekok2.web_data['profit_curve'])
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
            deepseekok2.exchange.fetch_balance()
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
                balance = deepseekok2.exchange.fetch_balance()
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
            deepseekok2.web_data['last_update'] = deepseekok2.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # 更新性能数据
            if deepseekok2.web_data['current_position']:
                deepseekok2.web_data['performance']['total_profit'] = deepseekok2.web_data['current_position'].get('unrealized_pnl', 0)
            
            print(f"✅ 初始化完成 - BTC价格: ${price_data['price']:,.2f}")
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
    print("🚀 启动BTC交易机器人Web监控...")
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
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)

