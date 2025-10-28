"""Bot package: 分层模块化交易机器人。

子模块：
- context: 环境、日志、AI与交易所客户端初始化
- state: 共享运行时状态（web_data、缓存、变量）
- okx: OKX订单、持仓、签名POST、价格步长与参数构建
- indicators: 技术指标与趋势分析
- sentiment: 外部情绪数据拉取与缓存
- utils: 通用工具（JSON解析、持久化、统计等）
- prompts: 提示词构建与LLM交互
- trade: 交易执行、风控与策略动作
"""


