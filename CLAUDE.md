# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run a single test file
pytest tests/core/test_device_bot.py

# Run a single test function
pytest tests/core/test_device_bot.py::test_lock_device -xvs

# Lint + format check
ruff check python/ tests/
ruff format --check python/ tests/

# Auto-fix lint issues
ruff check --fix python/ tests/
ruff format python/ tests/

# Run backend dev server
uvicorn lockbot.backend.app.main:app --host 0.0.0.0 --port 8000 --reload

# Run frontend dev server
cd frontend && npm install && npm run dev

# Docker build
docker build -f docker/Dockerfile -t lockbot .

# Generate encryption keys for deployment
python tools/gen_keys.py
```

## Architecture

**Two deployment modes share the same `python/lockbot/core/` library:**

### 1. Platform Mode (recommended)
- **Backend**: FastAPI app at `python/lockbot/backend/app/main.py` — lifespan creates DB tables, runs migrations, seeds dev users, starts `BotManager`
- **Frontend**: Vue 3 + Element Plus in `frontend/` — built via Vite, served as static files by FastAPI
- **BotManager** (`backend/app/bots/manager.py`): In-process multi-bot lifecycle manager. Uses a shared `BotScheduler` to drive all bot timer checks. Bots are identified by integer bot_id and receive webhook callbacks at `/api/bots/webhook/{bot_id}`
- **Auth**: JWT with roles (super_admin/admin/user), `must_change_password` flag, `token_version` for invalidation
- **DB**: SQLite via SQLAlchemy, auto-migrated on startup (see migration functions in `main.py`)
- **Rate limiting**: slowapi, disabled in tests via `conftest.py` mock

### 2. Standalone Mode (legacy/deprecated)
- Flask entry point at `python/lockbot/core/entry.py`
- Single bot per process, creates its own `BotScheduler`

### Core Library (`python/lockbot/core/`)

**Bot class hierarchy:**
- `BaseLockBot` (`base_bot.py`) — common infrastructure: config/state/lock/adapter, timer routine, help text, error formatting
- `DeviceBot` (`device_bot.py`) — per-GPU locking with exclusive/shared modes, device usage alert, command parsing via regex
- `NodeBot` (`node_bot.py`) — whole-node locking with exclusive/shared modes
- `QueueBot` (`queue_bot.py`) — extends `NodeBot`, adds `book`/`take` commands for queue scheduling with auto-promotion

**BotInstance** (`bot_instance.py`): Factory that wraps a bot + config + state + optional scheduler. Map: `"NODE" → NodeBot`, `"QUEUE" → QueueBot`, `"DEVICE" → DeviceBot`.

**BotState** (`base_bot.py.BotState`): Each bot class defines an inner `_state_class` with a `_loader` static method (e.g., `create_or_load_node_state`, `create_or_load_device_state`) that loads/creates state from JSON files.

**BotScheduler** (`scheduler.py`): Single daemon thread with a min-heap of `(fire_at, generation, bot_id)`. Replaces per-bot `threading.Timer`. On each tick, calls `bot._check_and_notify()` which checks lock expiry, sends notifications, then returns the next desired check interval. Tracks consecutive failures and fires `on_fatal_error` callback after 5 failures.

**Config** (`config.py`): Instance-level `Config(config_dict)` with `get_val()`/`set_val()`. Class-level methods (`Config.get()`, `Config.set()`) are deprecated. Paths like `STATE_FILENAME` are derived from `BOT_ID + DATA_DIR`. Schema defines defaults, descriptions, and whether env override is allowed.

**MessageAdapter** (`message_adapter.py`): Abstract base for IM platforms, with methods: `verify_request`, `decrypt_payload`, `extract_command`, `build_reply`, `send`. Only `InfoflowAdapter` (如流, in `platforms/infoflow.py`) is implemented. ROADMAP plans Slack/DingTalk/Feishu/WeChat adapters.

**Command routing** (`handler.py`): Parses incoming text → dispatches to bot methods (`lock`, `slock`, `unlock`, `kickout`, `book`, `take`, `query`). Unknown node names default to query. Empty input = query all.

**Query rendering** (`query_render.py`): Builds markdown tables for `/query` output. `build_device_query` for DEVICE bots (device-level rows), `build_node_query` for NODE/QUEUE bots (node-level rows). Sort order: my nodes → idle (FREE) → PARTIAL → BUSY, within each tier by remaining duration ascending.

**Usage rendering** (`usage_render.py`): Configurable line templating via `USAGE_LINE_TEMPLATE` / `USAGE_IDLE_TEMPLATE`, with `USAGE_SORT` (name/dur_asc/dur_desc) and `USAGE_GROUP` (none/idle_first/idle_last). `render_line()` gracefully falls back to a default template on format errors.

**I/O** (`io.py`): JSON-based state persistence. Each bot saves to `{DATA_DIR}/{bot_id}/bot_state.json`. Includes backward-compatible migrations for old field formats (`timestamp` → `start_time`, `in_use`/`is_shared` → `status`).

**I18n** (`i18n/`): `en.py` and `zh.py` dictionaries, looked up via `t(key, config=config)` where config provides `LANGUAGE`.

**`yjb_xpu_smi/`**: Standalone xpu-smi monitoring scripts (not part of the lockbot package).

### Backend API Structure (`python/lockbot/backend/app/`)

| Module | Purpose |
|--------|---------|
| `auth/` | Register, login, logout, JWT dependencies, role-based guards |
| `bots/` | CRUD, start/stop/restart lifecycle, webhook handler, encryption, BotManager |
| `admin/` | User management (super_admin only) |
| `settings/` | Global settings key-value store |
| `audit/` | Audit log recording and querying |

### Frontend (`frontend/src/`)

- **Router**: Login, Register, BotList, BotForm (create), BotDetail (edit), ProfileSettings, ForceChangePassword, admin/Users, NotFound
- **Stores**: `auth.js` (Pinia — user state, token, role checks), `bots.js` (bot list state)
- **Components**: `BotCard`, `BotForm/`, `LogViewer`, `StatusBadge`, `DemoChat`, `AuthFooter`

### Testing

- `tests/core/` — unit tests for bot logic, config, scheduler, query rendering, usage rendering
- `tests/backend/` — API integration tests using FastAPI `TestClient` with in-memory SQLite (StaticPool), rate limiter disabled, bot auto-start patched to `RuntimeError`
- `conftest.py` fixtures: `client` (TestClient with DB override), `auth_header` (JWT token), `admin_header` (admin JWT), `db_session` (raw SQLAlchemy session)
- Test config is set before importing backend modules: `DATABASE_URL = "sqlite://"`, `ALLOW_REGISTER = True`

### CI/CD (`.github/workflows/`)

- `ci.yml`: pytest + ruff on push/PR
- `publish.yml`: PyPI publish on tag push
- `docker.yml`: Build and push to ghcr.io on tag push
- `pages.yml`: Demo page deploy to GitHub Pages

### Pre-commit (`.pre-commit-config.yaml`)

Runs ruff (fix + format) on Python, ESLint + Prettier on frontend files.
