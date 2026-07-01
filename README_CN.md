# lockbot

集群资源管理机器人，支持通过即时通讯平台（如百度如流）的对话命令锁定和释放 GPU 设备、集群节点和队列资源。

支持两种部署方式：FastAPI + Vue.js 前端的管理平台模式（推荐），以及单机 Flask 独立部署。

[English](README.md) | [在线演示](https://dynamicheart.github.io/lockbot/)

[![PyPI version](https://img.shields.io/pypi/v/lockbot?color=blue)](https://pypi.org/project/lockbot/)
[![Docker Image](https://img.shields.io/badge/ghcr.io-dynamicheart%2Flockbot-blue?logo=docker)](https://github.com/DynamicHeart/lockbot/pkgs/container/lockbot)

## 功能特性

- **设备锁机器人** — 按单个 GPU/设备维度锁定和释放
- **节点锁机器人** — 按整个集群节点维度锁定和释放
- **队列机器人** — 管理资源分配队列，支持预约和抢占
- **平台模式** — Web 管理界面（Vue 3 + Element Plus），支持多机器人管理、用户认证（JWT）、角色权限控制和实时日志
- **状态持久化** — 机器人状态重启后自动恢复（JSON 文件存储）
- **双语支持** — 界面和机器人回复均支持中英文

## 部署 — Linux 虚拟机（tmux）

本指南介绍如何在不使用 Docker 的情况下，将 LockBot 部署到 Linux 虚拟机，使用 tmux 保持服务在后台运行。适用于生产和开发环境。

### 环境要求

- **Python 3.10+** 和 pip
- **Node.js 20+** 和 npm（用于构建前端）
- **tmux**（用于后台进程管理）
- **git**（用于克隆代码仓库）

### 1. 克隆仓库

```bash
git clone https://github.com/DynamicHeart/lockbot.git
cd lockbot
```

### 2. 安装 Python 依赖

```bash
pip install -e .
```

如果不需要前端，也可以通过 PyPI 安装：

```bash
pip install lockbot
```

### 3. 构建前端

```bash
cd frontend
npm install
npm run build
cd ..
```

这会在 `frontend/dist/` 下生成静态文件，由 FastAPI 后端自动托管。Web 管理界面依赖前端构建产物；如果只需要 Bot API 可跳过此步。

### 4. 生成密钥

```bash
python3 tools/gen_keys.py
```

输出示例：

```
ENCRYPTION_KEY=gAAAAABn...
JWT_SECRET=a1b2c3d4e5f6...
```

保存这两个值，下一步会用到。

### 5. 设置环境变量

```bash
export JWT_SECRET="<gen_keys 输出的 JWT_SECRET>"     # 必填
export ENCRYPTION_KEY="<gen_keys 输出的 KEY>"         # 必填
export DATA_DIR="/opt/lockbot/data"                   # 数据持久化目录，默认 /data
export PYTHONPATH="$(pwd)/python"                     # 从源码运行时必填
export DEV_MODE="true"                                # 首次启动自动创建管理员用户
```

主要环境变量说明：

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `JWT_SECRET` | 是 | — | JWT 签名密钥 |
| `ENCRYPTION_KEY` | 是 | — | Fernet 加密密钥，用于加密敏感字段（机器人 Token、AES Key 等） |
| `DATA_DIR` | 否 | `/data` | SQLite 数据库和机器人状态文件的存储目录，建议定期备份 |
| `PYTHONPATH` | 源码安装时 | — | 从源码运行时必须包含 `python/` 目录 |
| `DEV_MODE` | 否 | `false` | 设为 `true` 时首次启动自动创建管理员和测试用户 |
| `ALLOW_REGISTER` | 否 | `false` | 设为 `true` 时允许公开注册 |
| `DATABASE_URL` | 否 | `sqlite:///{DATA_DIR}/lockbot.db` | 自定义数据库地址（支持 PostgreSQL、MySQL 等） |
| `FRONTEND_DIST` | 否 | `frontend/dist`（自动检测） | 前端构建产物路径 |
| `REDIS_URL` | 否 | （内存模式） | Redis 地址，用于分布式限流 |

### 6. tmux 启动

```bash
tmux new-session -d -s lockbot \
  "export JWT_SECRET='<你的密钥>' \
   && export ENCRYPTION_KEY='<你的密钥>' \
   && export DATA_DIR='/opt/lockbot/data' \
   && export PYTHONPATH='$(pwd)/python' \
   && export DEV_MODE='true' \
   && uvicorn lockbot.backend.app.main:app --host 0.0.0.0 --port 8000 2>&1 | tee /tmp/lockbot.log"
```

- `--host 0.0.0.0` 使服务可被网络中其他机器访问
- `--port 8000` 是默认端口，如被占用可更换
- `2>&1 | tee /tmp/lockbot.log` 将日志同时输出到 tmux 窗口和日志文件
- 生产环境请去掉 `--reload` 参数（热重载会增加开销，且不适合生产）

### 7. 验证启动

```bash
sleep 4 && tmux capture-pane -t lockbot -p | tail -20
```

看到以下日志表示启动成功：

```
Application startup complete.
```

如果上次关闭前有正在运行的机器人，还会看到：

```
Auto-recovered bot 1 (my-bot)
```

然后用浏览器访问 `http://<虚拟机IP>:8000` 即可打开 Web 管理界面。

**DEV 模式默认用户**（`DEV_MODE=true`）：

| 用户名 | 密码 | 角色 |
|---|---|---|
| `admin` | `admin123` | 超级管理员 |
| `manager` | `manager` | 管理员 |
| `user1` | `user1` | 普通用户 |
| `user2` | `user2` | 普通用户 |

> ⚠️ **生产环境请设置 `DEV_MODE=false`**，并通过第 8 步手动创建超级管理员。DEV 模式的测试用户仅用于开发调试。

### 8. 创建超级管理员（生产环境）

当 `DEV_MODE=false` 时不会自动创建用户，需要手动创建：

```bash
export DATA_DIR="/opt/lockbot/data"
python3 tools/create_super_admin.py --username admin --email admin@example.com
```

脚本会生成随机密码，请保存并在首次登录后修改：

```
✓ Super admin created successfully!
  Username: admin
  Email:    admin@example.com
  Password: <自动生成的密码>
  ⚠️  Please change the password after first login.
```

也可以直接指定密码（安全性较低）：

```bash
python3 tools/create_super_admin.py --username admin --email admin@example.com --password 你的密码
```

### 服务管理

**查看日志：**
```bash
# 实时跟踪日志文件
tail -f /tmp/lockbot.log

# 查看当前 tmux 输出
tmux capture-pane -t lockbot -p | tail -50
```

**进入 tmux 会话（查看实时输出）：**
```bash
tmux attach -t lockbot
# 按 Ctrl+B 再按 D 即可退出（不会停止服务）
```

> ⚠️ **进入 tmux 后不要按 Ctrl+C** — uvicorn 是会话中唯一的前台进程，杀死它会导致 tmux 会话直接销毁。如需停止服务请用下面的命令。

**停止服务：**
```bash
tmux kill-session -t lockbot
```

**代码更新后重启：**
```bash
# 1. 停掉旧会话
tmux kill-session -t lockbot 2>/dev/null

# 2. 拉取最新代码
git pull

# 3. 重新构建前端（如果前端文件有改动）
cd frontend && npm install && npm run build && cd ..

# 4. 重新启动
tmux new-session -d -s lockbot \
  "export JWT_SECRET='<你的密钥>' \
   && export ENCRYPTION_KEY='<你的密钥>' \
   && export DATA_DIR='/opt/lockbot/data' \
   && export PYTHONPATH='$(pwd)/python' \
   && uvicorn lockbot.backend.app.main:app --host 0.0.0.0 --port 8000 2>&1 | tee /tmp/lockbot.log"

# 5. 验证
sleep 4 && tmux capture-pane -t lockbot -p | tail -20
```

**开机自启（systemd 服务）：**

创建 `/etc/systemd/system/lockbot.service`：

```ini
[Unit]
Description=LockBot Platform
After=network.target

[Service]
Type=simple
User=lockbot
WorkingDirectory=/opt/lockbot
Environment="JWT_SECRET=<你的密钥>"
Environment="ENCRYPTION_KEY=<你的密钥>"
Environment="DATA_DIR=/opt/lockbot/data"
Environment="PYTHONPATH=/opt/lockbot/python"
Environment="PATH=/home/lockbot/.local/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/home/lockbot/.local/bin/uvicorn lockbot.backend.app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

然后：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lockbot
sudo journalctl -u lockbot -f    # 查看日志
```

### 可选：GPU 监控（SSH 免密登录）

DEVICE 机器人在配置 SSH 免密登录后，可实时显示每个 GPU 节点的利用率和容器名。

```bash
# 1. 在 lockbot 所在机器上生成 SSH 密钥（如果没有）
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""

# 2. 将公钥复制到每个 GPU 节点
ssh-copy-id -i ~/.ssh/id_ed25519.pub user@gpu-node-ip

# 3. 验证免密登录和 xpu-smi 可用
ssh -o BatchMode=yes user@gpu-node-ip "which xpu-smi"
```

然后在 Web 管理界面（Bot 设置 → 集群配置）或数据库中为每个节点配置 IP 地址。未配置 SSH 免密的节点，其 GPU 利用率列将显示为空白。

## 快速开始 — 开发模式

本地开发调试，支持热重载：

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 设置最小环境变量
export JWT_SECRET="dev-secret"
export ENCRYPTION_KEY="dev-key-32-bytes-long!!"
export DEV_MODE="true"

# 后端（自动重载）
uvicorn lockbot.backend.app.main:app --host 0.0.0.0 --port 8000 --reload

# 前端开发服务器（另一个终端）
cd frontend && npm install && npm run dev
```

打开 `http://localhost:8000` 访问完整应用（前端由后端托管），或使用 Vite 开发服务器进行前端热更新开发。

## Docker 部署

```bash
# 1. 生成 ENCRYPTION_KEY 和 JWT_SECRET
python3 tools/gen_keys.py

# 2. 拉取预构建镜像（或从源码构建）
docker pull ghcr.io/dynamicheart/lockbot:latest
# docker build -f docker/Dockerfile -t lockbot .

# 3. 运行（将密钥替换为生成的值）
docker run -d --name lockbot -p 8000:8000 \
  -e JWT_SECRET=your-secret \
  -e ENCRYPTION_KEY=your-fernet-key \
  -v lockbot-data:/data \
  ghcr.io/dynamicheart/lockbot:latest

# 4. 创建 super_admin（密码自动生成并打印）
docker exec -it lockbot python3 tools/create_super_admin.py --username admin --email admin@example.com
```

> **数据持久化**：所有数据（SQLite 数据库、机器人状态文件）统一存储在 `/data` 目录下，可通过 `DATA_DIR` 环境变量自定义。

## 机器人配置

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `BOT_TYPE` | `DEVICE`、`NODE` 或 `QUEUE` | （必填） |
| `BOT_NAME` | 机器人实例名称 | `demo_bot` |
| `CLUSTER_CONFIGS` | 集群布局（字典或列表） | `{}` |
| `TOKEN` | 机器人签名验证 Token | `""` |
| `AESKEY` | 消息解密 AES 密钥 | `""` |
| `WEBHOOK_URL` | 消息发送 Webhook URL | `""` |
| `PORT` | 服务监听端口 | `8090` |
| `DEFAULT_DURATION` | 默认锁定时长（秒） | `7200`（2小时） |
| `MAX_LOCK_DURATION` | 最大锁定时长（秒） | `-1`（不限制） |
| `EARLY_NOTIFY` | 锁定到期前通知 | `false` |

完整配置项参见 `python/lockbot/core/config.py`。

## 命令列表

| 命令 | 说明 |
|------|------|
| `lock <node> [时长]` | 独占锁定（如 `lock gpu0 3d`、`lock node1 30m`） |
| `slock <node> [时长]` | 共享锁定（多人可用） |
| `unlock <node>` / `free <node>` | 释放指定节点 |
| `unlock` / `free` | 释放你的所有资源 |
| `kickout <node>` | 强制释放（管理员） |
| `book <node> [时长]` | 队列模式：预约排队 |
| `take <node>` | 队列模式：抢占当前锁定 |
| `<node>` | 查询资源使用情况 |
| `help` | 显示使用指南 |

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 代码检查 + 格式化
ruff check python/ tests/
ruff format --check python/ tests/
```

## 独立模式

适用于单机器人部署，运行轻量级 Flask Webhook 服务。

**设备锁机器人**（按 GPU 锁定）：

```python
from lockbot.core.bot_instance import BotInstance
from lockbot.core.entry import create_app

instance = BotInstance("DEVICE", {
    "BOT_NAME": "my-gpu-bot",
    "WEBHOOK_URL": "https://your-webhook-url",
    "TOKEN": "your-bot-token",
    "AESKEY": "your-aes-key",
    "CLUSTER_CONFIGS": {
        "node0": ["A800", "A800", "H100"],
        "node1": ["A800", "H100"],
    },
})

app = create_app(bot=instance.bot, bot_name="my-gpu-bot", port=8000)
app.run(host="0.0.0.0", port=8000)
```

**节点锁 / 排队机器人**（按节点锁定或排队调度）：

```python
from lockbot.core.bot_instance import BotInstance
from lockbot.core.entry import create_app

instance = BotInstance("NODE", {       # 改为 "QUEUE" 即为排队模式
    "BOT_NAME": "my-node-bot",
    "WEBHOOK_URL": "https://your-webhook-url",
    "TOKEN": "your-bot-token",
    "AESKEY": "your-aes-key",
    "CLUSTER_CONFIGS": ["node0", "node1", "node2", "node3"],
})

app = create_app(bot=instance.bot, bot_name="my-node-bot", port=8000)
app.run(host="0.0.0.0", port=8000)
```

## 许可证

MIT
