# BTC自动交易机器人 🤖

基于DeepSeek or Qwen3-MAX AI + OKX交易所的智能加密货币交易机器人，结合技术指标分析和市场情绪数据，实现全自动化交易。
<img width="1892" height="844" alt="{8AA6FF7B-F3CA-4C76-A252-9CC7E1C11B7E}" src="https://github.com/user-attachments/assets/b9e0fcf8-0410-498c-9372-7a1ac2efb3f2" />
<img width="1882" height="904" alt="{1E1C1636-2F28-4A92-A8B9-1C2EF3B52FCE}" src="https://github.com/user-attachments/assets/b51b45ca-3c24-4e5c-8a5a-6c11f7b10e7c" />

# 感谢原作项目

## ✨ 核心特性

### 🧠 AI智能决策
- **DeepSeek AI分析**：深度学习模型分析市场趋势
- **技术指标**：SMA、EMA、MACD、RSI、布林带等多维度分析
- **市场情绪**：集成CryptoOracle情绪数据API
- **防频繁交易**：智能信号过滤，避免过度交易

### 📊 技术分析
- 移动平均线（5/20/50周期）
- MACD指标及信号线
- RSI相对强弱指数
- 布林带及位置分析
- 支撑/阻力位计算
- 成交量分析

### 🌐 Web监控面板
- ✅ **AI模型状态监控**：实时显示使用的AI模型和连接状态
- ✅ 实时账户信息和持仓展示
- ✅ 收益曲线图表：可视化账户权益、盈亏和收益率变化
- ✅ 专业K线图表（ECharts，支持缩放拖动）
- ✅ AI决策实时展示
- ✅ 交易记录完整追踪
- ✅ 盈亏统计可视化
- ✅ 信号分布分析图表
- ✅ 现代化深色主题UI
- ✅ 响应式设计支持移动端

### 🔒 风险管理
- 杠杆交易支持（可配置）
- 止损/止盈自动设置
- 保证金检查
- 信心等级过滤
- 持仓跟踪

---

## 🚀 快速开始

### 1. 环境要求

- **Python**: 3.8+ 或 **Docker**: 20.10+
- **操作系统**: Windows / Linux / macOS
- **依赖**: 见 `requirements.txt`

### 2. 部署方式

提供三种部署方式，任选其一：

#### 方式一：Docker部署 🐳 (推荐)

**优势**: 无需安装Python环境，一键启动，环境隔离

💡 **详细Docker部署指南**: 查看 [DOCKER_GUIDE.md](DOCKER_GUIDE.md) 获取完整Docker配置和故障排查说明

##### 2.1 前置要求
- 安装 [Docker](https://www.docker.com/get-started) 
- 安装 [Docker Compose](https://docs.docker.com/compose/install/)

##### 2.2 快速启动

```bash
# 1. 克隆项目
git clone <your-repo-url>
cd ds-main

# 2. 创建配置文件
cp .env.example .env
# 编辑.env文件，填入你的API密钥

# 3. 启动容器
docker-compose up -d

# 4. 查看日志
docker-compose logs -f

# 5. 停止服务
docker-compose down
```

##### 2.3 常用命令

```bash
# 查看运行状态
docker-compose ps

# 重启服务
docker-compose restart

# 更新镜像并重启
docker-compose pull
docker-compose up -d

# 进入容器调试
docker-compose exec btc-trading-bot bash

# 查看实时日志
docker-compose logs -f --tail=100
```

**访问地址**: http://localhost:8080

---

#### 方式二：Python虚拟环境 (Windows)

```bash
# 1. 创建虚拟环境
python -m venv venv

# 2. 激活虚拟环境
.\venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt
```

---

#### 方式三：Python虚拟环境 (Linux/macOS)

```bash
# 1. 创建虚拟环境
python3 -m venv venv

# 2. 激活虚拟环境
source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt
```

### 3. 配置文件

在项目根目录创建 `.env` 文件：

```env
# AI模型选择 (可选: deepseek 或 qwen)
AI_PROVIDER=deepseek

# DeepSeek API (默认)
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx

# 阿里百炼API (可选)
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx

# OKX交易所API
OKX_API_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
OKX_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OKX_PASSWORD=xxxxxxxx
```

💡 **详细配置说明**: 查看 [ENV_CONFIG.md](ENV_CONFIG.md) 获取完整配置指南

#### 获取API密钥

**AI模型（二选一）：**

1. **DeepSeek API** (默认): https://platform.deepseek.com/
   - 注册账号
   - 创建API Key
   - 充值（按使用量计费，约0.14元/百万tokens）
   - 模型：deepseek-chat

2. **阿里百炼 Qwen** (可选): https://dashscope.console.aliyun.com/
   - 注册阿里云账号
   - 开通百炼服务
   - 创建API Key
   - 模型：qwen-max
   - 设置 `AI_PROVIDER=qwen`

**交易所：**

3. **OKX API**: https://www.okx.com/
   - 注册账号并完成KYC
   - API管理 → 创建API
   - 权限：需要"交易"权限
   - **重要**：妥善保管密钥，不要泄露

### 4. 交易参数配置

编辑 `deepseekok2.py` 文件中的配置：

```python
TRADE_CONFIG = {
    'symbol': 'BTC/USDT:USDT',  # 交易对
    'amount': 0.01,             # 每次交易数量(BTC)
    'leverage': 10,             # 杠杆倍数
    'timeframe': '15m',         # K线周期
    'test_mode': False,         # True=模拟 False=实盘
    'data_points': 96,          # 分析数据点数
}
```

⚠️ **首次使用建议**：
- 设置 `test_mode: True` 进行模拟测试
- 使用小额 `amount` 开始
- 降低 `leverage` 杠杆倍数

---

## 📱 使用方法

### 方式一：Docker运行（推荐）🐳

#### 快速启动（推荐新手）

**Windows用户**:
```bash
# 双击运行
start_docker.bat

# 或命令行
.\start_docker.bat
```

**Linux/macOS用户**:
```bash
# 添加执行权限（首次）
chmod +x start_docker.sh

# 运行
./start_docker.sh
```

启动脚本会自动检查Docker环境、配置文件并启动服务。

#### 手动启动（高级用户）

```bash
# 启动服务
docker-compose up -d

# 查看运行日志
docker-compose logs -f

# 停止服务
docker-compose down

# 重启服务
docker-compose restart
```

**访问地址**: http://localhost:8080

**Docker优势**:
- ✅ 无需配置Python环境
- ✅ 自动重启（容器崩溃后自动恢复）
- ✅ 资源限制和健康检查
- ✅ 日志自动管理
- ✅ 一键更新升级
- ✅ 跨平台统一环境

---

### 方式二：Web界面监控（Python环境）

#### Windows
```bash
# 方法1：双击运行
start_web.bat

# 方法2：命令行
.\venv\Scripts\activate
python web_server.py
```

#### Linux/macOS
```bash
source venv/bin/activate
python web_server.py
```

**访问地址**: http://localhost:8080

**功能说明**:
- 🧠 实时显示AI模型名称和连接状态
- 📊 实时查看BTC价格和收益曲线
- 🤖 监控AI决策和信号分析
- 💰 查看账户余额和持仓情况
- 📈 追踪交易记录和盈亏
- 📉 分析信号分布统计

---

### 方式三：命令行运行

```bash
.\venv\Scripts\activate   # Windows
source venv/bin/activate  # Linux/Mac

python deepseekok2.py
```

**执行频率**: 每15分钟整点执行一次（00、15、30、45分）

---

## 🎯 交易策略说明

### 决策权重分配

1. **技术分析** (60%) - 主导
   - 均线排列和趋势判断
   - RSI超买超卖
   - MACD金叉死叉
   - 布林带位置

2. **市场情绪** (30%) - 辅助
   - CryptoOracle情绪指标
   - 用于验证技术信号
   - 延迟数据降低权重

3. **风险管理** (10%)
   - 持仓盈亏状况
   - 保证金充足度
   - 信心等级过滤

### 信号生成规则

- **BUY信号**: 强势上涨趋势 + 技术面支持
- **SELL信号**: 强势下跌趋势 + 技术面支持
- **HOLD信号**: 震荡无明确方向

### 防频繁交易机制

✅ 趋势持续性优先  
✅ 非高信心不反转  
✅ 低信心信号不执行  
✅ 信号连续性检查  

---

## 📊 Web监控面板详细说明

### 界面布局

#### 页面头部
- **AI模型监控**: 
  - 显示当前使用的AI模型（DeepSeek 或 阿里百炼 Qwen）
  - 实时连接状态指示灯：
    - 🟢 **已连接** - AI模型工作正常
    - 🔴 **连接失败** - 鼠标悬停查看错误详情
    - 🟡 **检测中** - 正在测试连接
  - 每10秒自动更新状态
  - 显示模型版本信息

#### 顶部卡片（4个）
1. **账户信息**: 余额、权益、杠杆
2. **当前价格**: BTC实时价格、涨跌幅
3. **持仓信息**: 多空方向、数量、盈亏
4. **绩效统计**: 总盈亏、胜率、交易次数

#### 中部区域
- **收益曲线图表**: 多维度收益可视化
  - 账户权益变化趋势
  - 累计盈亏金额
  - 收益率百分比
  - 盈亏平衡线参考
  - 最近200个数据点（约50小时）
  - 支持缩放、拖动、悬停查看详情

#### 底部区域
- **AI决策**: 
  - 最新信号（BUY/SELL/HOLD）
  - 信心等级（HIGH/MEDIUM/LOW）
  - 决策理由
  - 止损/止盈价格
  
- **交易记录**: 
  - 时间、信号、价格、数量
  - 最近100条记录

- **统计图表**:
  - 信号分布饼图
  - 信心等级分布

### 数据刷新

- **自动刷新**: 每10秒
- **手动刷新**: F5或Ctrl+F5
- **实时性**: 交易执行后立即更新
- **AI状态检测**: 
  - 启动时自动测试连接
  - 每次AI调用后更新状态
  - 失败时显示错误信息

---

## ⚙️ 高级配置

### Docker配置

#### 修改端口映射

编辑 `docker-compose.yml`:

```yaml
ports:
  - "8888:8080"  # 将本地8888端口映射到容器8080端口
```

#### 调整资源限制

编辑 `docker-compose.yml`:

```yaml
deploy:
  resources:
    limits:
      cpus: '2'      # 最多使用2个CPU核心
      memory: 2G     # 最多使用2GB内存
    reservations:
      cpus: '1'      # 保留1个CPU核心
      memory: 1G     # 保留1GB内存
```

#### 数据持久化

在 `docker-compose.yml` 中已配置：

```yaml
volumes:
  - ./data:/app/data  # 持久化数据目录
```

#### 环境变量配置

方式1：使用 `.env` 文件（推荐）
```bash
# 编辑 .env 文件
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx...
```

方式2：在 `docker-compose.yml` 中直接设置
```yaml
environment:
  - AI_PROVIDER=deepseek
  - DEEPSEEK_API_KEY=sk-xxx...
```

---

### 修改刷新频率

编辑 `static/js/app.js`:

```javascript
setInterval(updateData, 10000);  // 10秒
// 可改为: 5000(5秒) 或 30000(30秒)
```

**Docker用户**：修改后需重启容器
```bash
docker-compose restart
```

---

### 修改Web端口

#### Python环境

编辑 `web_server.py`:

```python
PORT = 8080  # 改为其他端口
```

#### Docker环境

编辑 `docker-compose.yml`:

```yaml
ports:
  - "8888:8080"  # 改为8888端口
```

---

### 切换测试/实盘模式

编辑 `deepseekok2.py`:

```python
'test_mode': True,   # True=模拟测试
'test_mode': False,  # False=实盘交易
```

**Docker用户**：修改后需重启容器
```bash
docker-compose restart
```

### 切换AI模型

编辑 `.env` 文件：

```env
# 使用DeepSeek (默认)
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxxxxxxx

# 或使用阿里百炼Qwen
AI_PROVIDER=qwen
DASHSCOPE_API_KEY=sk-xxxxxxxx
```

**模型对比：**

| 特性 | DeepSeek | 阿里百炼Qwen |
|------|----------|--------------|
| 模型 | deepseek-chat | qwen-max |
| 成本 | ~0.14元/百万tokens | ~0.12元/百万tokens |
| 速度 | 快 | 较快 |
| 中文理解 | 优秀 | 优秀 |
| 推荐场景 | 默认选择 | 国内用户 |

---

## 🔐 安全建议

### 环境配置
- ✅ 使用 `.env` 文件存储密钥
- ✅ 不要将 `.env` 提交到Git（已在.gitignore中配置）
- ✅ 定期更换API密钥
- ✅ 使用 `.env_template` 作为配置模板

### Git版本控制
- ✅ 已配置 `.gitignore` 保护敏感文件
- ✅ 提交前检查 `git status` 确认没有敏感文件
- ✅ 查看 [GIT_GUIDE.md](GIT_GUIDE.md) 了解详细使用说明
- ⚠️ 如果误提交了密钥，立即删除并更换新密钥

### Web服务
- ⚠️ 不要暴露到公网
- ⚠️ 仅在局域网或本地使用
- ⚠️ 生产环境建议添加认证

### 交易安全
- 🔴 首次必须用测试模式
- 🔴 小额资金开始测试
- 🔴 设置合理的止损位
- 🔴 定期检查运行状态

---

## 📝 文件说明

```
ds-main/
├── deepseekok2.py           # 主程序（交易机器人）
├── web_server.py            # Web服务器
├── requirements.txt         # Python依赖
├── .env                     # 配置文件（需自己创建，不会被Git追踪）
├── .env.example             # 配置模板（示例文件）
├── .gitignore              # Git忽略文件（保护敏感信息）
├── Dockerfile              # Docker镜像构建文件
├── docker-compose.yml      # Docker编排配置
├── .dockerignore           # Docker构建忽略文件
├── start_docker.bat        # Docker启动脚本（Windows）
├── start_docker.sh         # Docker启动脚本（Linux/macOS）
├── start_web.bat           # Python启动脚本（Windows）
├── README.md               # 本文件
├── DOCKER_GUIDE.md         # Docker部署详细指南
├── GIT_GUIDE.md            # Git使用指南
├── ENV_CONFIG.md           # 环境配置详解
├── templates/              # HTML模板
│   └── index.html         
└── static/                 # 静态资源
    ├── css/
    │   └── style.css
    └── js/
        └── app.js
```

---

## 🐛 常见问题

### Docker相关

#### 1. Docker容器无法启动
```
错误: Cannot connect to the Docker daemon
解决: 
  1. 确认Docker Desktop已启动
  2. Windows: 检查Hyper-V是否启用
  3. Linux: sudo systemctl start docker
```

#### 2. 端口8080被占用
```
错误: Bind for 0.0.0.0:8080 failed: port is already allocated
解决: 
  方法1: 停止占用端口的程序
  方法2: 修改docker-compose.yml中的端口映射
    ports:
      - "8888:8080"  # 改为8888端口
```

#### 3. .env文件未生效
```
原因: 文件路径或格式错误
解决:
  1. 确认.env文件在项目根目录
  2. 检查文件格式（无BOM，UTF-8编码）
  3. 重启容器: docker-compose restart
```

#### 4. 查看Docker容器日志
```bash
# 查看所有日志
docker-compose logs

# 实时查看最近100行
docker-compose logs -f --tail=100

# 查看特定服务
docker-compose logs btc-trading-bot
```

### 应用相关

#### 5. 端口被占用（非Docker）
```
错误: Address already in use
解决: 修改PORT或关闭占用进程
```

#### 6. AI模型连接失败
```
现象: 网页显示"🔴 连接失败"
原因: API密钥错误、网络问题或余额不足
解决: 
  1. 检查.env文件中的API密钥是否正确
  2. 确认网络可以访问对应API服务
  3. 检查API账户余额是否充足
  4. 鼠标悬停在"连接失败"上查看详细错误信息
  5. Docker用户: 检查容器内网络连接
     docker-compose exec btc-trading-bot ping -c 4 api.deepseek.com
```

#### 7. API调用失败
```
错误: DeepSeek/Qwen返回空响应
解决: 检查API密钥和网络连接，查看控制台日志
```

#### 8. 数据显示为空
```
原因: 等待15分钟整点首次执行
解决: 耐心等待或查看控制台日志
```

#### 9. 交易执行失败
```
原因: 保证金不足或API权限不足
解决: 充值USDT或检查API权限
```

#### 10. AI模型切换后状态未更新
```
原因: 缓存或未重启服务
解决: 
  1. 修改.env文件后需要重启服务
     Docker: docker-compose restart
     Python: 重启web_server.py
  2. 清除浏览器缓存后刷新页面
  3. 检查控制台日志确认使用的模型
```

---

## 📚 更多资源

### 项目文档
- **Docker部署指南**: [DOCKER_GUIDE.md](DOCKER_GUIDE.md) - Docker完整配置和故障排查
- **环境配置详解**: [ENV_CONFIG.md](ENV_CONFIG.md) - API密钥配置说明
- **Git使用指南**: [GIT_GUIDE.md](GIT_GUIDE.md) - 版本控制和安全提交

### 外部资源
- **DeepSeek文档**: https://platform.deepseek.com/docs
- **阿里百炼文档**: https://help.aliyun.com/zh/dashscope/
- **OKX API文档**: https://www.okx.com/docs-v5/
- **CCXT文档**: https://docs.ccxt.com/
- **Docker文档**: https://docs.docker.com/

---

## ⚠️ 免责声明

本项目仅供学习和研究使用。加密货币交易具有高风险，可能导致资金损失。使用本软件进行实盘交易的所有风险由使用者自行承担。

**重要提示**:
- 不构成投资建议
- 作者不对任何损失负责
- 请根据自身风险承受能力使用
- 建议在充分测试后再进行实盘交易

---

## 📄 许可证

本项目采用 MIT 许可证

---

**祝交易顺利！** 📈✨

如有问题，请查看控制台日志或检查配置文件。
打赏地址：0xddf4924195e872d34a72220d0e45d3020790da89
<img width="556" height="240" alt="{FC64115A-8CF4-4709-ADA7-2144940CC3FD}" src="https://github.com/user-attachments/assets/2a27ae0d-b73d-4f8a-bba4-b50252596e2f" />
