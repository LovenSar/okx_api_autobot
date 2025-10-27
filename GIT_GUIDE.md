# Git版本控制使用指南 📚

本指南帮助你正确使用Git管理BTC交易机器人项目。

---

## 🔐 重要安全提醒

### ⚠️ 绝对不要提交以下文件到GitHub：
- ✅ `.env` - 包含你的API密钥和密码
- ✅ 任何包含真实密钥的文件
- ✅ 交易日志和个人数据

这些文件已经在`.gitignore`中被配置忽略。

---

## 🚀 快速开始

### 1. 初始化Git仓库

```bash
# 进入项目目录
cd ds-main

# 初始化Git仓库
git init

# 添加远程仓库（替换为你的GitHub仓库地址）
git remote add origin https://github.com/你的用户名/你的仓库名.git
```

### 2. 配置Git用户信息

```bash
git config user.name "你的名字"
git config user.email "your.email@example.com"
```

### 3. 首次提交

```bash
# 查看状态
git status

# 添加所有文件（.gitignore会自动过滤敏感文件）
git add .

# 提交
git commit -m "Initial commit: BTC自动交易机器人"

# 推送到GitHub
git push -u origin main
```

---

## 📝 日常使用

### 查看状态
```bash
git status
```

### 添加修改
```bash
# 添加特定文件
git add 文件名.py

# 添加所有修改
git add .
```

### 提交更改
```bash
git commit -m "描述你的修改"
```

### 推送到远程
```bash
git push
```

### 拉取最新代码
```bash
git pull
```

---

## 🔍 检查是否误提交敏感文件

### 提交前检查
```bash
# 查看将要提交的文件
git status

# 查看具体修改内容
git diff
```

### 如果不小心添加了敏感文件
```bash
# 从暂存区移除（保留本地文件）
git reset HEAD .env

# 或者移除所有暂存的文件
git reset HEAD .
```

---

## 🛡️ 安全最佳实践

### ✅ 推荐做法

1. **使用.env文件存储密钥**
   ```bash
   # 复制模板
   cp .env_template .env
   
   # 编辑.env填入真实密钥
   notepad .env  # Windows
   nano .env     # Linux/Mac
   ```

2. **验证.gitignore生效**
   ```bash
   # .env应该不会出现在这个列表中
   git status
   ```

3. **定期检查提交历史**
   ```bash
   # 查看最近的提交
   git log --oneline -5
   ```

### ❌ 避免的做法

- ❌ 直接在代码中硬编码API密钥
- ❌ 提交包含真实密钥的配置文件
- ❌ 在公开仓库中存储敏感数据
- ❌ 忽略.gitignore文件的警告

---

## 🔧 常用Git命令

### 分支管理
```bash
# 创建新分支
git branch feature-name

# 切换分支
git checkout feature-name

# 创建并切换到新分支
git checkout -b feature-name

# 合并分支
git checkout main
git merge feature-name

# 删除分支
git branch -d feature-name
```

### 查看历史
```bash
# 查看提交历史
git log

# 简洁模式
git log --oneline

# 图形化显示
git log --graph --oneline --all
```

### 撤销操作
```bash
# 撤销工作区修改
git checkout -- 文件名

# 撤销暂存
git reset HEAD 文件名

# 修改最后一次提交
git commit --amend
```

---

## 🆘 如果不小心提交了敏感信息

### 立即采取的行动：

1. **从历史中删除敏感文件**
   ```bash
   # 使用BFG Repo-Cleaner或git filter-branch
   # 参考：https://docs.github.com/cn/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository
   ```

2. **立即更换泄露的API密钥**
   - 登录DeepSeek/阿里云/OKX平台
   - 删除旧密钥
   - 生成新密钥
   - 更新本地.env文件

3. **强制推送清理后的历史**
   ```bash
   git push --force
   ```

---

## 📋 .gitignore文件说明

本项目的`.gitignore`已经配置忽略：

- 🔐 `.env` 和其他配置文件
- 🐍 Python缓存文件（`__pycache__/`, `*.pyc`）
- 📦 虚拟环境（`venv/`, `env/`）
- 💻 IDE配置文件（`.vscode/`, `.idea/`）
- 🖥️ 操作系统文件（`.DS_Store`, `Thumbs.db`）
- 📊 日志和数据文件（`*.log`, `*.db`）
- 🧪 临时测试文件（`test_*.py`）

---

## 🤝 协作开发

### Fork工作流

1. **Fork仓库到你的GitHub账号**

2. **克隆你的Fork**
   ```bash
   git clone https://github.com/你的用户名/仓库名.git
   cd 仓库名
   ```

3. **添加上游仓库**
   ```bash
   git remote add upstream https://github.com/原作者/仓库名.git
   ```

4. **创建功能分支**
   ```bash
   git checkout -b feature/new-feature
   ```

5. **提交修改并推送**
   ```bash
   git add .
   git commit -m "Add new feature"
   git push origin feature/new-feature
   ```

6. **在GitHub上创建Pull Request**

### 保持Fork同步
```bash
# 获取上游更新
git fetch upstream

# 合并到本地main分支
git checkout main
git merge upstream/main

# 推送到你的Fork
git push origin main
```

---

## 📚 推荐阅读

- [Git官方文档](https://git-scm.com/doc)
- [GitHub文档](https://docs.github.com/cn)
- [保护敏感数据](https://docs.github.com/cn/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)
- [Git最佳实践](https://git-scm.com/book/zh/v2)

---

## 💡 提示

- 提交前总是运行 `git status` 检查
- 写清晰的提交信息
- 经常提交，保持小步快跑
- 定期推送到远程备份
- **永远不要提交API密钥！**

---

**祝你使用愉快！如有问题请查阅Git文档或寻求帮助。** 🚀

