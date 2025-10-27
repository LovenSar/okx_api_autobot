# 环境配置说明

## 📝 创建.env文件

在项目根目录创建 `.env` 文件：

```env
# ========================================
# BTC自动交易机器人配置文件
# ========================================

# ========== AI模型配置 ==========
# 可选值: deepseek 或 qwen
AI_PROVIDER=deepseek

# DeepSeek API密钥 (如果使用DeepSeek)
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx

# 阿里百炼API密钥 (如果使用Qwen)
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx

# ========== OKX交易所配置 ==========
OKX_API_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
OKX_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OKX_PASSWORD=xxxxxxxx
```

---

## 🤖 AI模型选择

### 选项1: DeepSeek (默认)

```env
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-your-deepseek-key
```

**特点：**
- ✅ 模型：deepseek-chat
- ✅ 成本：约0.14元/百万tokens
- ✅ 速度：快
- ✅ 中文理解：优秀
- ✅ 推荐：默认选择

**获取方式：**
1. 访问：https://platform.deepseek.com/
2. 注册并登录
3. 创建API Key
4. 充值使用

### 选项2: 阿里百炼Qwen

```env
AI_PROVIDER=qwen
DASHSCOPE_API_KEY=sk-your-dashscope-key
```

**特点：**
- ✅ 模型：qwen-max
- ✅ 成本：约0.12元/百万tokens
- ✅ 速度：较快
- ✅ 中文理解：优秀
- ✅ 推荐：国内用户

**获取方式：**
1. 访问：https://dashscope.console.aliyun.com/
2. 登录阿里云账号
3. 开通百炼服务
4. 创建API Key
5. 充值使用

---

## 🏦 OKX交易所配置

### 获取API密钥

1. **注册OKX**: https://www.okx.com/
2. **完成KYC**: 实名认证
3. **创建API**:
   - 进入：账户 → API管理
   - 创建API Key
   - 权限：勾选"交易"
   - 设置IP白名单（可选但推荐）
   - 记录：API Key、Secret Key、Passphrase

### 配置示例

```env
OKX_API_KEY=12345678-1234-1234-1234-123456789012
OKX_SECRET=ABCDEFGHIJKLMNOPQRSTUVWXYZ123456
OKX_PASSWORD=MyPassword123
```

⚠️ **安全警告**:
- 不要分享你的API密钥
- 不要将.env文件提交到Git
- 定期更换API密钥
- 建议设置IP白名单

---

## 🔄 切换AI模型

### 方法1: 修改.env文件

```bash
# 切换到DeepSeek
AI_PROVIDER=deepseek

# 切换到Qwen
AI_PROVIDER=qwen
```

### 方法2: 环境变量

**Windows PowerShell:**
```powershell
$env:AI_PROVIDER="qwen"
python web_server.py
```

**Linux/Mac:**
```bash
export AI_PROVIDER=qwen
python web_server.py
```

---

## 📊 模型性能对比

| 指标 | DeepSeek | 阿里百炼Qwen |
|------|----------|--------------|
| 模型名称 | deepseek-chat | qwen-max |
| 参数规模 | 大型 | 大型 |
| 推理速度 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| 中文能力 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 成本(元/百万tokens) | ~0.14 | ~0.12 |
| API稳定性 | 优秀 | 优秀 |
| 适用场景 | 全球用户 | 国内用户 |

---

## ✅ 配置检查清单

- [ ] 创建了.env文件
- [ ] 选择了AI模型(deepseek或qwen)
- [ ] 填写了对应的AI API密钥
- [ ] 填写了OKX API密钥
- [ ] 填写了OKX Secret
- [ ] 填写了OKX密码
- [ ] 确认.env文件不会被Git追踪
- [ ] 测试API连接正常

---

## 🐛 常见问题

### Q1: AI_PROVIDER设置错误

**错误提示**: 使用默认的DeepSeek模型

**解决方案**:
```env
# 正确写法（小写）
AI_PROVIDER=deepseek  # 或 qwen

# 错误写法
AI_PROVIDER=DeepSeek  # ❌
AI_PROVIDER=QWEN      # ❌
```

### Q2: API密钥格式错误

**DeepSeek密钥格式**: `sk-` 开头
**阿里百炼密钥格式**: `sk-` 开头

**检查方法**:
```bash
# Windows
type .env

# Linux/Mac
cat .env
```

### Q3: 如何测试配置

运行测试脚本：
```bash
python -c "from deepseekok2 import AI_PROVIDER, AI_MODEL; print(f'AI模型: {AI_PROVIDER.upper()} ({AI_MODEL})')"
```

---

## 📚 相关链接

- **DeepSeek官网**: https://www.deepseek.com/
- **DeepSeek平台**: https://platform.deepseek.com/
- **DeepSeek文档**: https://platform.deepseek.com/docs

- **阿里百炼**: https://dashscope.console.aliyun.com/
- **Qwen文档**: https://help.aliyun.com/zh/dashscope/

- **OKX官网**: https://www.okx.com/
- **OKX API文档**: https://www.okx.com/docs-v5/

---

**配置完成后，运行 `python web_server.py` 启动程序！** 🚀

