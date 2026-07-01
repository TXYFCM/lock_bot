# lockbot

Cluster resource management bot for IM platforms (e.g., Baidu InfoFlow).

Lock and unlock GPU devices, cluster nodes, and queue slots via chat commands.
Supports both standalone Flask deployment and a full platform mode with FastAPI + Vue.js frontend.

[中文文档](README_CN.md) | [Live Demo](https://dynamicheart.github.io/lockbot/)

[![PyPI version](https://img.shields.io/pypi/v/lockbot?color=blue)](https://pypi.org/project/lockbot/)
[![Docker Image](https://img.shields.io/badge/ghcr.io-dynamicheart%2Flockbot-blue?logo=docker)](https://github.com/DynamicHeart/lockbot/pkgs/container/lockbot)

## Features

- **Device Lock Bot** — Lock/unlock individual GPUs or devices on a cluster
- **Node Lock Bot** — Lock/unlock entire cluster nodes
- **Queue Bot** — Manage a queue for resource allocation with booking and preemption
- **Platform Mode** — Web UI (Vue 3 + Element Plus) for managing multiple bots, user authentication (JWT), role-based access control, and real-time logs
- **State Persistence** — Bot state survives restarts (JSON file)
- **Bilingual** — English and Chinese UI and bot responses

## Deployment — Linux VM (tmux)

This guide walks through deploying LockBot on a Linux VM without Docker, using tmux to keep the server running in the background. Suitable for both production and development environments.

### Prerequisites

- **Python 3.10+** and pip
- **Node.js 20+** and npm (for building the frontend)
- **tmux** (for background process management)
- **git** (to clone the repository)

### 1. Clone the repository

```bash
git clone https://github.com/DynamicHeart/lockbot.git
cd lockbot
```

### 2. Install Python dependencies

```bash
pip install -e .
```

If you don't need the frontend, you can add `--no-build-isolation` or install from PyPI:

```bash
pip install lockbot
```

### 3. Build the frontend

```bash
cd frontend
npm install
npm run build
cd ..
```

This produces static files under `frontend/dist/` which the FastAPI backend serves automatically. The frontend is required for the Web UI; skip this step if you only need the bot API.

### 4. Generate secrets

```bash
python3 tools/gen_keys.py
```

Output example:

```
ENCRYPTION_KEY=gAAAAABn...
JWT_SECRET=a1b2c3d4e5f6...
```

Save both values — you'll use them in the next step.

### 5. Set environment variables

```bash
export JWT_SECRET="<output-from-gen-keys>"       # required
export ENCRYPTION_KEY="<output-from-gen-keys>"    # required
export DATA_DIR="/opt/lockbot/data"               # persistent data, default: /data
export PYTHONPATH="$(pwd)/python"                 # required when running from source
export DEV_MODE="true"                            # auto-creates dev users on first start
```

Key variables explained:

| Variable | Required | Default | Description |
|---|---|---|---|
| `JWT_SECRET` | Yes | — | Secret key for JWT token signing |
| `ENCRYPTION_KEY` | Yes | — | Fernet key for encrypting sensitive fields (bot tokens, AES keys) |
| `DATA_DIR` | No | `/data` | Directory for SQLite DB + bot state files. Should be backed up. |
| `PYTHONPATH` | Source install | — | Must include the `python/` directory when running from source |
| `DEV_MODE` | No | `false` | When `true`: auto-creates admin + test users on first startup |
| `ALLOW_REGISTER` | No | `false` | When `true`: enables public user registration |
| `DATABASE_URL` | No | `sqlite:///{DATA_DIR}/lockbot.db` | Custom database URL (supports PostgreSQL, MySQL, etc.) |
| `FRONTEND_DIST` | No | `frontend/dist` (auto-detected) | Path to built frontend static files |
| `REDIS_URL` | No | (in-memory) | Redis URL for distributed rate limiting |

### 6. Start with tmux

```bash
tmux new-session -d -s lockbot \
  "export JWT_SECRET='<your-secret>' \
   && export ENCRYPTION_KEY='<your-key>' \
   && export DATA_DIR='/opt/lockbot/data' \
   && export PYTHONPATH='$(pwd)/python' \
   && export DEV_MODE='true' \
   && uvicorn lockbot.backend.app.main:app --host 0.0.0.0 --port 8000 2>&1 | tee /tmp/lockbot.log"
```

- `--host 0.0.0.0` makes the service accessible from other machines on the network
- `--port 8000` is the default; change it if the port is already in use
- `2>&1 | tee /tmp/lockbot.log` writes logs to both the tmux pane and a log file
- Remove `--reload` for production (auto-restart on code changes is a dev convenience that adds overhead)

### 7. Verify startup

```bash
sleep 4 && tmux capture-pane -t lockbot -p | tail -20
```

Look for these log lines indicating a successful start:

```
Application startup complete.
```

If you had bots running before the last shutdown, you'll also see:

```
Auto-recovered bot 1 (my-bot)
```

Then open `http://<your-vm-ip>:8000` in a browser to access the Web UI.

**Dev mode default users** (`DEV_MODE=true`):

| Username | Password | Role |
|---|---|---|
| `admin` | `admin123` | super_admin |
| `manager` | `manager` | admin |
| `user1` | `user1` | user |
| `user2` | `user2` | user |

> ⚠️ **For production, set `DEV_MODE=false`** after first setup and create a super_admin manually (see step 8). The dev users are intended for testing only.

### 8. Create super_admin (production)

When `DEV_MODE=false`, no users are auto-created. Create the first admin manually:

```bash
export DATA_DIR="/opt/lockbot/data"
python3 tools/create_super_admin.py --username admin --email admin@example.com
```

The script generates a random password — save it and change it after first login:

```
✓ Super admin created successfully!
  Username: admin
  Email:    admin@example.com
  Password: <auto-generated>
  ⚠️  Please change the password after first login.
```

You can also specify a password directly (less secure):

```bash
python3 tools/create_super_admin.py --username admin --email admin@example.com --password mypassword
```

### Service Management

**View logs:**
```bash
# Follow the log file
tail -f /tmp/lockbot.log

# View current tmux output
tmux capture-pane -t lockbot -p | tail -50
```

**Attach to tmux session (watch live output):**
```bash
tmux attach -t lockbot
# Press Ctrl+B then D to detach (does NOT stop the service)
```

> ⚠️ **Do NOT press Ctrl+C while attached** — uvicorn is the session's only foreground process; killing it destroys the tmux session. Use the stop command below instead.

**Stop the service:**
```bash
tmux kill-session -t lockbot
```

**Restart after code changes:**
```bash
# 1. Stop the old session
tmux kill-session -t lockbot 2>/dev/null

# 2. Pull latest code
git pull

# 3. Rebuild frontend (if frontend files changed)
cd frontend && npm install && npm run build && cd ..

# 4. Start again
tmux new-session -d -s lockbot \
  "export JWT_SECRET='<your-secret>' \
   && export ENCRYPTION_KEY='<your-key>' \
   && export DATA_DIR='/opt/lockbot/data' \
   && export PYTHONPATH='$(pwd)/python' \
   && uvicorn lockbot.backend.app.main:app --host 0.0.0.0 --port 8000 2>&1 | tee /tmp/lockbot.log"

# 5. Verify
sleep 4 && tmux capture-pane -t lockbot -p | tail -20
```

**Run on boot (systemd service):**

Create `/etc/systemd/system/lockbot.service`:

```ini
[Unit]
Description=LockBot Platform
After=network.target

[Service]
Type=simple
User=lockbot
WorkingDirectory=/opt/lockbot
Environment="JWT_SECRET=<your-secret>"
Environment="ENCRYPTION_KEY=<your-key>"
Environment="DATA_DIR=/opt/lockbot/data"
Environment="PYTHONPATH=/opt/lockbot/python"
Environment="PATH=/home/lockbot/.local/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/home/lockbot/.local/bin/uvicorn lockbot.backend.app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lockbot
sudo journalctl -u lockbot -f    # view logs
```

### Optional: GPU monitoring via SSH

DEVICE bots can display real-time GPU utilization and container names per card when SSH access to GPU nodes is configured.

```bash
# 1. Generate an SSH key on the lockbot host (if you don't have one)
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""

# 2. Copy the public key to each GPU node
ssh-copy-id -i ~/.ssh/id_ed25519.pub user@gpu-node-ip

# 3. Verify passwordless login and xpu-smi availability
ssh -o BatchMode=yes user@gpu-node-ip "which xpu-smi"
```

Then add each node's IP via the Web UI (Bot Settings → Cluster Configs) or directly in the database. The GPU utilization column in `/query` output will remain blank for any node that doesn't have SSH access.

## Quick Start — Development

For local development with hot-reload:

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Set minimal env vars
export JWT_SECRET="dev-secret"
export ENCRYPTION_KEY="dev-key-32-bytes-long!!"
export DEV_MODE="true"

# Backend (with auto-reload)
uvicorn lockbot.backend.app.main:app --host 0.0.0.0 --port 8000 --reload

# Frontend dev server (another terminal)
cd frontend && npm install && npm run dev
```

Open `http://localhost:8000` for the full app (frontend served by backend), or use the Vite dev server for hot-module-replacement during frontend development.

## Docker

```bash
# 1. Generate ENCRYPTION_KEY and JWT_SECRET
python3 tools/gen_keys.py

# 2. Pull pre-built image (or build from source)
docker pull ghcr.io/dynamicheart/lockbot:latest
# docker build -f docker/Dockerfile -t lockbot .

# 3. Run (replace the keys with generated values)
docker run -d --name lockbot -p 8000:8000 \
  -e JWT_SECRET=your-secret \
  -e ENCRYPTION_KEY=your-fernet-key \
  -v lockbot-data:/data \
  ghcr.io/dynamicheart/lockbot:latest

# 4. Create super_admin (password auto-generated and printed)
docker exec -it lockbot python3 tools/create_super_admin.py --username admin --email admin@example.com
```

> **Data persistence**: All data (SQLite DB, bot state files) stored under `/data`. Override with `DATA_DIR` env var.

## Bot Configuration

| Key | Description | Default |
|---|---|---|
| `BOT_TYPE` | `DEVICE`, `NODE`, or `QUEUE` | (required) |
| `BOT_NAME` | Bot instance name | `demo_bot` |
| `CLUSTER_CONFIGS` | Cluster layout (dict or list) | `{}` |
| `TOKEN` | Bot signature verification token | `""` |
| `AESKEY` | Message decryption AES key | `""` |
| `WEBHOOK_URL` | Message webhook URL | `""` |
| `PORT` | Server listen port | `8090` |
| `DEFAULT_DURATION` | Default lock duration (seconds) | `7200` (2h) |
| `MAX_LOCK_DURATION` | Max lock duration (seconds) | `-1` (unlimited) |
| `EARLY_NOTIFY` | Notify before lock expiry | `false` |

See `python/lockbot/core/config.py` for the full configuration reference.

## Commands

| Command | Description |
|---------|-------------|
| `lock <node> [duration]` | Exclusive lock (e.g., `lock gpu0 3d`, `lock node1 30m`) |
| `slock <node> [duration]` | Shared lock (multiple users) |
| `unlock <node>` / `free <node>` | Release a specific node |
| `unlock` / `free` | Release all your nodes |
| `kickout <node>` | Force release (admin) |
| `book <node> [duration]` | Queue: book a node for later |
| `take <node>` | Queue: take the current lock |
| `<node>` | Query current usage |
| `help` | Show usage |

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint + format check
ruff check python/ tests/
ruff format --check python/ tests/
```

## Standalone Mode

Single-bot deployment with a lightweight Flask webhook server.

**Device Lock Bot** (per-GPU locking):

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

**Node Lock Bot / Queue Bot** (per-node locking or queue scheduling):

```python
from lockbot.core.bot_instance import BotInstance
from lockbot.core.entry import create_app

instance = BotInstance("NODE", {       # or "QUEUE" for queue scheduling
    "BOT_NAME": "my-node-bot",
    "WEBHOOK_URL": "https://your-webhook-url",
    "TOKEN": "your-bot-token",
    "AESKEY": "your-aes-key",
    "CLUSTER_CONFIGS": ["node0", "node1", "node2", "node3"],
})

app = create_app(bot=instance.bot, bot_name="my-node-bot", port=8000)
app.run(host="0.0.0.0", port=8000)
```

## License

MIT
