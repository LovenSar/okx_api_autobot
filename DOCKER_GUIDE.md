# Docker部署指南 🐳

本指南详细介绍如何使用Docker部署BTC自动交易机器人。

---

## 📋 目录

- [为什么选择Docker](#为什么选择docker)
- [前置要求](#前置要求)
- [快速开始](#快速开始)
- [详细配置](#详细配置)
- [维护与管理](#维护与管理)
- [故障排查](#故障排查)
- [安全建议](#安全建议)

---

## 🎯 为什么选择Docker

### 优势

✅ **环境隔离**: 不污染主机环境，所有依赖都在容器内  
✅ **一致性**: 跨平台统一运行环境  
✅ **易部署**: 无需手动配置Python环境和依赖  
✅ **易维护**: 一键启动、停止、重启、更新  
✅ **自动恢复**: 容器崩溃后自动重启  
✅ **资源控制**: 限制CPU和内存使用  
✅ **日志管理**: 自动轮转日志文件  

### 对比传统部署

| 特性 | Docker部署 | Python环境部署 |
|------|-----------|---------------|
| 环境配置 | ⭐️⭐️⭐️⭐️⭐️ 自动 | ⭐️⭐️ 需要手动 |
| 跨平台 | ⭐️⭐️⭐️⭐️⭐️ 完全一致 | ⭐️⭐️⭐️ 可能有差异 |
| 更新升级 | ⭐️⭐️⭐️⭐️⭐️ 一键完成 | ⭐️⭐️⭐️ 需要重装依赖 |
| 资源隔离 | ⭐️⭐️⭐️⭐️⭐️ 完全隔离 | ⭐️⭐️ 共享主机环境 |
| 学习成本 | ⭐️⭐️⭐️⭐️ 稍高 | ⭐️⭐️⭐️⭐️⭐️ 较低 |

---

## 🔧 前置要求

### 安装Docker

#### Windows

1. 下载并安装 [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop)
2. 系统要求：
   - Windows 10/11 专业版、企业版或教育版
   - 启用Hyper-V和容器功能
   - 至少4GB RAM（推荐8GB）
3. 安装完成后启动Docker Desktop

#### macOS

1. 下载并安装 [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop)
2. 系统要求：
   - macOS 11 Big Sur或更高版本
   - 至少4GB RAM（推荐8GB）
3. 安装完成后启动Docker Desktop

#### Linux (Ubuntu/Debian)

```bash
# 更新包索引
sudo apt-get update

# 安装依赖
sudo apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release

# 添加Docker官方GPG密钥
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

# 添加Docker仓库
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 安装Docker Engine
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 启动Docker服务
sudo systemctl start docker
sudo systemctl enable docker

# 将当前用户添加到docker组（避免每次使用sudo）
sudo usermod -aG docker $USER

# 重新登录以使组权限生效
```

#### 验证安装

```bash
# 查看Docker版本
docker --version
docker-compose --version

# 测试Docker运行
docker run hello-world
```

---

## 🚀 快速开始

### 第一步：准备配置文件

```bash
# 克隆项目（如果还没有）
git clone <your-repo-url>
cd ds-main

# 创建配置文件
cp .env.example .env

# 编辑配置文件（使用你喜欢的编辑器）
nano .env
# 或
vim .env
# 或 Windows记事本
notepad .env
```

**配置示例**：

```env
# OpenAI 兼容接口
OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
AI_MODEL=deepseek-reasoner

# OKX交易所API
OKX_API_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
OKX_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OKX_PASSWORD=xxxxxxxx
```

### 第二步：启动服务

#### 使用启动脚本（推荐新手）

**Windows**:
```bash
# 双击运行start_docker.bat
# 或在命令行执行：
start_docker.bat
```

**Linux/macOS**:
```bash
# 添加执行权限（首次）
chmod +x start_docker.sh

# 运行
./start_docker.sh
```

#### 使用命令行（高级用户）

```bash
# 构建并启动容器（后台运行）
docker-compose up -d

# 查看启动日志
docker-compose logs -f
```

### 第三步：访问Web界面

打开浏览器访问：http://localhost:8080

---

## ⚙️ 详细配置

### docker-compose.yml 配置说明

```yaml
version: '3.8'

services:
  btc-trading-bot:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: btc-trading-bot
    restart: unless-stopped          # 自动重启策略
    ports:
      - "8080:8080"                  # 端口映射
    volumes:
      - ./.env:/app/.env:ro          # 挂载配置（只读）
      - ./data:/app/data             # 数据持久化
    environment:
      - TZ=Asia/Shanghai             # 时区设置
    deploy:
      resources:
        limits:
          cpus: '1'                  # CPU限制
          memory: 1G                 # 内存限制
        reservations:
          cpus: '0.5'                # CPU预留
          memory: 512M               # 内存预留
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/api/dashboard"]
      interval: 30s                  # 检查间隔
      timeout: 10s                   # 超时时间
      retries: 3                     # 重试次数
      start_period: 40s              # 启动等待时间
    logging:
      driver: "json-file"
      options:
        max-size: "10m"              # 单个日志文件最大10MB
        max-file: "3"                # 保留3个日志文件
```

### 自定义配置

#### 修改端口

将8080改为其他端口（如8888）：

```yaml
ports:
  - "8888:8080"
```

#### 调整资源限制

```yaml
deploy:
  resources:
    limits:
      cpus: '2'        # 允许使用最多2个CPU核心
      memory: 2G       # 允许使用最多2GB内存
    reservations:
      cpus: '1'        # 保证至少1个CPU核心
      memory: 1G       # 保证至少1GB内存
```

#### 修改时区

```yaml
environment:
  - TZ=America/New_York    # 改为纽约时区
  # 其他常见时区：
  # - TZ=Europe/London     # 伦敦
  # - TZ=Asia/Tokyo        # 东京
  # - TZ=UTC               # UTC时间
```

#### 数据持久化

默认已配置数据持久化目录：

```yaml
volumes:
  - ./data:/app/data       # 将容器内/app/data映射到主机./data目录
```

这样即使容器被删除，数据也不会丢失。

---

## 🛠️ 维护与管理

### 常用命令

```bash
# 查看运行状态
docker-compose ps

# 启动服务
docker-compose start

# 停止服务
docker-compose stop

# 重启服务
docker-compose restart

# 停止并删除容器
docker-compose down

# 停止并删除容器、镜像、卷
docker-compose down --rmi all --volumes

# 查看日志（实时）
docker-compose logs -f

# 查看最近100行日志
docker-compose logs --tail=100

# 查看指定服务日志
docker-compose logs -f btc-trading-bot

# 进入容器内部（调试用）
docker-compose exec btc-trading-bot bash

# 查看容器资源使用情况
docker stats btc-trading-bot
```

### 更新升级

```bash
# 拉取最新代码
git pull

# 重新构建镜像
docker-compose build

# 重启服务
docker-compose up -d

# 或一键完成
docker-compose up -d --build
```

### 清理资源

```bash
# 删除停止的容器
docker container prune

# 删除未使用的镜像
docker image prune

# 删除未使用的卷
docker volume prune

# 清理所有未使用资源（慎用！）
docker system prune -a
```

---

## 🔍 故障排查

### 1. 容器无法启动

**检查Docker状态**：
```bash
docker info
```

**查看详细错误日志**：
```bash
docker-compose logs
```

**常见原因**：
- Docker Desktop未启动
- 端口被占用
- .env文件格式错误
- 内存不足

### 2. 端口被占用

**查找占用端口的进程**：

**Windows**:
```powershell
netstat -ano | findstr :8080
taskkill /PID <进程ID> /F
```

**Linux/macOS**:
```bash
lsof -i :8080
kill -9 <PID>
```

**或修改端口**：
编辑 `docker-compose.yml`，将 `8080:8080` 改为 `8888:8080`

### 3. 无法访问Web界面

**检查容器是否运行**：
```bash
docker-compose ps
```

**检查容器内服务**：
```bash
docker-compose exec btc-trading-bot curl http://localhost:8080
```

**检查防火墙**：
确保8080端口未被防火墙阻止

### 4. API连接失败

**进入容器测试网络**：
```bash
# 进入容器
docker-compose exec btc-trading-bot bash

# 测试网络连接（以配置的 OPENAI_BASE_URL 为准）
# 示例：
curl -I $OPENAI_BASE_URL

# 检查环境变量
env | grep -i api
```

**检查.env文件**：
```bash
# 查看容器内的环境变量
docker-compose exec btc-trading-bot env
```

### 5. 容器不断重启

**查看重启原因**：
```bash
docker-compose logs --tail=50
```

**常见原因**：
- 程序崩溃（检查Python错误）
- 配置错误（检查.env文件）
- 资源不足（增加内存限制）

**临时禁用自动重启调试**：
```yaml
restart: "no"  # 改为no
```

---

## 🔒 安全建议

### 1. 保护敏感信息

✅ **永远不要**将 `.env` 文件提交到Git  
✅ 使用 `.env` 文件存储API密钥  
✅ 容器内挂载 `.env` 为只读（`:ro`）  

```yaml
volumes:
  - ./.env:/app/.env:ro  # :ro表示只读
```

### 2. 限制容器权限

✅ 不要使用 `privileged: true`  
✅ 不要使用 `root` 用户运行（如需要，在Dockerfile中创建普通用户）  

### 3. 网络隔离

✅ 不要将Web服务暴露到公网  
✅ 如需远程访问，使用VPN或SSH隧道  
✅ 考虑添加认证（如HTTP Basic Auth或JWT）  

### 4. 定期更新

✅ 定期更新Docker镜像  
✅ 定期更新依赖包  
✅ 关注安全公告  

```bash
# 更新基础镜像
docker pull python:3.11-slim

# 重新构建
docker-compose build --no-cache
```

### 5. 备份重要数据

✅ 定期备份 `.env` 配置文件  
✅ 定期备份 `data` 目录  
✅ 考虑使用 Docker 卷进行数据持久化  

```bash
# 备份数据卷
docker run --rm --volumes-from btc-trading-bot \
  -v $(pwd):/backup \
  alpine tar czf /backup/backup.tar.gz /app/data
```

---

## 📊 监控与日志

### 查看资源使用

```bash
# 实时监控
docker stats btc-trading-bot

# 查看详细信息
docker inspect btc-trading-bot
```

### 日志管理

**查看日志**：
```bash
# 实时查看
docker-compose logs -f

# 查看最近N行
docker-compose logs --tail=100

# 查看特定时间段
docker-compose logs --since="2024-01-01T00:00:00"
docker-compose logs --since="1h"  # 最近1小时
```

**导出日志**：
```bash
docker-compose logs > logs.txt
```

**日志轮转**：
已在 `docker-compose.yml` 中配置：
```yaml
logging:
  driver: "json-file"
  options:
    max-size: "10m"   # 单个文件最大10MB
    max-file: "3"     # 保留3个文件
```

---

## 🎓 高级话题

### 使用Docker Hub镜像加速（中国用户）

编辑 Docker Desktop 设置或 `/etc/docker/daemon.json`：

```json
{
  "registry-mirrors": [
    "https://docker.mirrors.ustc.edu.cn",
    "https://hub-mirror.c.163.com",
    "https://mirror.baidubce.com"
  ]
}
```

重启Docker服务：
```bash
# Linux
sudo systemctl restart docker

# Windows/macOS
重启Docker Desktop
```

### 多实例部署

如需运行多个交易机器人实例（不同交易对）：

1. 复制项目目录
2. 修改 `docker-compose.yml` 中的容器名和端口
3. 分别启动

或使用Docker Compose的扩展功能（高级）。

### 使用环境变量覆盖配置

在 `docker-compose.yml` 中直接设置环境变量（不推荐，会暴露密钥）：

```yaml
environment:
  - OPENAI_BASE_URL=${OPENAI_BASE_URL}
  - OPENAI_API_KEY=${OPENAI_API_KEY}
  - AI_MODEL=${AI_MODEL}
```

---

## 📚 相关资源

- [Docker官方文档](https://docs.docker.com/)
- [Docker Compose文档](https://docs.docker.com/compose/)
- [Dockerfile最佳实践](https://docs.docker.com/develop/dev-best-practices/)
- [项目主README](README.md)

---

## ❓ 获得帮助

如遇到问题：

1. 查看本文档的[故障排查](#故障排查)部分
2. 查看容器日志：`docker-compose logs -f`
3. 检查主项目README的[常见问题](README.md#常见问题)
4. 在GitHub Issues提问

---

**祝您部署顺利！** 🎉

