# OKX BTC 自动交易机器人

基于 DeepSeek / Qwen + OKX 的全自动量化交易机器人，内置技术指标、情绪因子与风控，含 Web 面板实时监控。

---

## 快速开始

### 1) 环境
- Python 3.8+ 或 Docker 20.10+
- Windows / Linux / macOS

### 2) 安装（Python）
```bash
python -m venv venv
./venv/Scripts/activate   # Windows
source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

### 3) 配置 `.env`
```env
# AI: deepseek | qwen
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
# 若用 Qwen：
# DASHSCOPE_API_KEY=sk-xxx

# OKX API（需交易权限）
OKX_API_KEY=xxx
OKX_SECRET=xxx
OKX_PASSWORD=xxx
```

### 4) 运行
- Web 面板（推荐）：
```bash
python web_server.py
# 浏览器访问 http://localhost:8080
```
- 命令行策略：
```bash
python deepseekok2.py
```

### 5) Docker（可选）
```bash
docker-compose up -d
# http://localhost:8080
```

---

## 关键配置
- 主要交易参数：`bot/context.py` 中的 `TRADE_CONFIG`
- 运行频率/节流：见 `config.py`
- 切换测试/实盘：`TRADE_CONFIG['test_mode'] = True | False`

示例：
```python
TRADE_CONFIG = {
    'symbol': 'BTC/USDT:USDT',
    'amount': 0.001,
    'leverage': 10,
    'timeframe': config.TIMEFRAME,
    'test_mode': True,
    'data_points': 96,
}
```

首用建议：先开启测试模式、小金额、低杠杆。

---

## 功能概览
- 技术面：SMA/EMA/MACD/RSI/布林带、趋势/支撑阻力/量能
- 情绪面：CryptoOracle 指标（缓存与权重衰减）
- 决策权重：技术 60% + 情绪 30% + 风控 10%
- 风控：最小交易间隔、信心过滤、TP/SL 可选、保证金与持仓检查
- Web 面板：账户/持仓/收益曲线/K 线/AI 决策/交易记录/统计

---

## 目录结构
```
okx_api_autobot/
├── deepseekok2.py            # 主入口（编排，兼容 web_server）
├── web_server.py             # Web 服务
├── bot/
│   ├── context.py            # 日志、AI、OKX、TRADE_CONFIG
│   ├── state.py              # 运行态与缓存
│   ├── okx.py                # 下单/撤单/查询/签名
│   ├── indicators.py         # 技术指标与增强K线
│   ├── sentiment.py          # 情绪数据
│   ├── prompts.py            # 提示词与AI交互
│   ├── trade.py              # 执行与风控
│   └── utils.py              # 工具与持久化
├── data/
│   ├── ai_decisions.jsonl    # AI 决策日志
│   └── realized_pnl.json     # 已实现盈亏
├── templates/index.html
├── static/css/style.css
├── static/js/app.js
├── requirements.txt
├── config.py
└── docker-compose.yml
```

---

## 常见问题
- 8080 端口占用：改 `docker-compose.yml` 端口或变更 `web_server.py` 的 `PORT`
- AI 连接失败：检查 API Key/网络/余额，重启服务并查看控制台日志
- 数据为空：首轮执行需等待；或检查 `config` 的时间间隔设置

---

## 安全
- 秘钥放 `.env`，勿提交到 Git
- 切勿公网暴露 Web 面板；生产建议加认证
- 实盘前务必长时间回测与模拟

---

## 许可证与声明
- License: MIT
- 免责声明：本项目仅供学习研究，交易有风险，自负后果，不构成投资建议。


