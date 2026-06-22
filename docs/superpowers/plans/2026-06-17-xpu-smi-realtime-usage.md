# 集群实时使用率采集与展示（xpu-smi）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一列「实时使用率」到 query 表格，数据由一个独立后台采集线程周期性 SSH 跑 `xpu-smi` 得到（节点平均利用率 + 真实占用者）。节点/设备可配可选 IP。仅 Platform 模式；SSH 靠宿主机免密 key，DB 不存凭据；默认关闭，关闭时输出与现状逐字符一致。

**Architecture:** 新建 `monitor.py`：`parse_monitor_output`（协议解析）、`occupant_from_container`（容器名→工号）、`MonitorCollector`（后台 daemon 线程 + 线程安全缓存 + 跨 IP 去重 + 并发 SSH）。`BotManager` 持有 collector，按 `MONITOR_ENABLED` 在 `start_bot`/`stop_bot` 注册/注销。`query_render.build_*_query` 新增 `monitor_snapshot` 参数，None 时走原路径（不变量）。新增 `MONITOR_*` config key。

**Tech Stack:** Python 3.10+，pytest，ruff（line-length 120）。SSH 走标准库 `subprocess` 调系统 `ssh`，无新第三方依赖。

**测试环境说明：** 本仓库 import `six` 等依赖。如本机无 pytest/six，先建临时 venv：`python -m venv .venv_test && .venv_test/bin/pip install -q pytest six pycryptodome flask requests`，用 `PYTHONPATH=python .venv_test/bin/python -m pytest ...` 运行，完成后 `rm -rf .venv_test`。下文命令统一写作 `pytest`，请按此环境替换。所有 SSH 在测试中一律 monkeypatch，**绝不真实联网**。

---

## File Structure

- **Create** `python/lockbot/core/monitor.py` — 协议解析 + 占用者反查 + MonitorCollector。
- **Create** `tests/core/test_monitor.py` — monitor 单元测试（SSH 全部 mock）。
- **Create** `tests/core/test_query_render_monitor.py` — 新列渲染测试 + 不变量。
- **Modify** `python/lockbot/core/config.py` — 新增 `MONITOR_*` config key。
- **Modify** `python/lockbot/core/query_render.py` — `build_*_query` 新增 `monitor_snapshot` 参数 + 新列。
- **Modify** `python/lockbot/core/i18n/zh.py` / `en.py` — 新增带监控列的表头文案。
- **Modify** `python/lockbot/core/device_bot.py` / `node_bot.py` — `query()` 注入 snapshot。
- **Modify** `python/lockbot/backend/app/bots/manager.py` — collector 生命周期。
- **Modify** `python/lockbot/backend/app/bots/schemas.py` — `MONITOR_*` 校验。
- **Modify** `tests/core/test_config.py` — `MONITOR_*` 默认值/覆盖断言。

---

## Task 1: 新增 MONITOR_* 配置项

**Files:**
- Modify: `python/lockbot/core/config.py` (在 `_CONFIG_SCHEMA` 末尾，`QUERY_TIP` 项之后)
- Test: `tests/core/test_config.py`

- [ ] **Step 1: Write the failing test**

在 `tests/core/test_config.py` 末尾追加：

```python
def test_monitor_defaults():
    """MONITOR_* config keys exist with monitoring-off defaults."""
    from lockbot.core.config import Config

    cfg = Config({})
    assert cfg.get_val("MONITOR_ENABLED") is False
    assert cfg.get_val("MONITOR_INTERVAL") == 60
    assert cfg.get_val("MONITOR_SSH_USER") == "root"
    assert cfg.get_val("MONITOR_SSH_TIMEOUT") == 15
    assert cfg.get_val("MONITOR_STALE_SEC") == 180
    assert cfg.get_val("MONITOR_NODE_IPS") == {}
    assert "STATUS|MEM|UTIL|CONTAINER" in cfg.get_val("MONITOR_CMD")


def test_monitor_override():
    """MONITOR_* keys are overridable via config_dict."""
    from lockbot.core.config import Config

    cfg = Config({"MONITOR_ENABLED": True, "MONITOR_NODE_IPS": {"n1": "10.0.0.1"}})
    assert cfg.get_val("MONITOR_ENABLED") is True
    assert cfg.get_val("MONITOR_NODE_IPS") == {"n1": "10.0.0.1"}
```

注意：`MONITOR_CMD` 默认值是 shell 命令，断言只检查其注释里含协议串 `STATUS|MEM|UTIL|CONTAINER`（在默认命令开头加一行注释 `# emits: STATUS|MEM|UTIL|CONTAINER`）。

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_config.py::test_monitor_defaults -v`
Expected: FAIL — `get_val("MONITOR_ENABLED")` returns `None`.

- [ ] **Step 3: Add the config keys**

在 `python/lockbot/core/config.py` 的 `_CONFIG_SCHEMA` 字典里，`"QUERY_TIP": {...}` 项之后、字典闭合 `}` 之前插入。`MONITOR_CMD` 默认值用 `_DEFAULT_MONITOR_CMD` 常量（定义在 `_CONFIG_SCHEMA` 之前）：

```python
_DEFAULT_MONITOR_CMD = (
    "# emits: STATUS|MEM|UTIL|CONTAINER\n"
    'out=$(xpu-smi 2>/dev/null)\n'
    'if echo "$out" | grep -q "No running processes found"; then\n'
    '  echo "FREE|0|0|"\n'
    "else\n"
    "  pid=$(echo \"$out\" | grep -E 'N/A  N/A\\s*[0-9]+' | head -n1 | awk '{print $5}')\n"
    "  cg=$(sed -E 's#.*/docker[-/]?([0-9a-f]+).*#\\1#' /proc/$pid/cgroup 2>/dev/null | head -n1 | cut -c1-12)\n"
    "  cname=$(docker ps -a --format '{{.ID}} {{.Names}}' 2>/dev/null | grep \"$cg\" | awk '{print $2}' | head -n1)\n"
    "  m=$(xpu-smi -m 2>/dev/null)\n"
    "  mem=$(echo \"$m\" | awk 'NR>1{s+=$18;n++} END{if(n)printf \"%.0f\",s/n; else print 0}')\n"
    "  util=$(echo \"$m\" | awk 'NR>1{s+=$20;n++} END{if(n)printf \"%.0f\",s/n; else print 0}')\n"
    '  echo "BUSY|$mem|$util|$cname"\n'
    "fi"
)
```

```python
    "MONITOR_ENABLED": {
        "default": False,
        "description": "Enable background xpu-smi realtime-usage collection",
        "env": False,
    },
    "MONITOR_INTERVAL": {
        "default": 60,
        "description": "Collection period in seconds",
        "env": False,
    },
    "MONITOR_CMD": {
        "default": _DEFAULT_MONITOR_CMD,
        "description": "Read-only remote command; must emit one line STATUS|MEM|UTIL|CONTAINER",
        "env": False,
    },
    "MONITOR_SSH_USER": {
        "default": "root",
        "description": "SSH login user for collection (no credentials stored; relies on host key)",
        "env": False,
    },
    "MONITOR_SSH_TIMEOUT": {
        "default": 15,
        "description": "Per-node SSH hard timeout in seconds",
        "env": False,
    },
    "MONITOR_STALE_SEC": {
        "default": 180,
        "description": "Cache freshness threshold; older snapshots are flagged stale",
        "env": False,
    },
    "MONITOR_NODE_IPS": {
        "default": {},
        "description": "Mapping {node_key: ip} of collection targets (parallel to CLUSTER_CONFIGS)",
        "env": False,
    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_config.py::test_monitor_defaults tests/core/test_config.py::test_monitor_override -v`
Expected: PASS (2 passed)。

注意：`MONITOR_NODE_IPS` 默认是 `{}`，`_normalize()` 只对 `CLUSTER_CONFIGS` 做 list→dict，不影响它。确认无副作用。

- [ ] **Step 5: Commit**

```bash
git add python/lockbot/core/config.py tests/core/test_config.py
git commit -m "feat: add MONITOR_* config keys for realtime usage collection"
```

---

## Task 2: monitor — parse_monitor_output（协议解析）

**Files:**
- Create: `python/lockbot/core/monitor.py`
- Test: `tests/core/test_monitor.py`

- [ ] **Step 1: Write the failing test**

创建 `tests/core/test_monitor.py`：

```python
def test_parse_busy_line():
    from lockbot.core.monitor import parse_monitor_output

    r = parse_monitor_output("BUSY|12345|87|zhangsan_train")
    assert r["status"] == "busy"
    assert r["mem"] == 12345.0
    assert r["util"] == 87.0
    assert r["container"] == "zhangsan_train"


def test_parse_free_line():
    from lockbot.core.monitor import parse_monitor_output

    r = parse_monitor_output("FREE|0|0|")
    assert r["status"] == "free"
    assert r["container"] == ""


def test_parse_bad_lines_become_error():
    from lockbot.core.monitor import parse_monitor_output

    for bad in ["", "garbage", "BUSY|onlytwo", "  \n  ", "command not found"]:
        r = parse_monitor_output(bad)
        assert r["status"] == "error"
        assert r["util"] is None


def test_parse_picks_protocol_line_among_noise():
    """Real ssh output may carry banner/warning lines; pick the protocol line."""
    from lockbot.core.monitor import parse_monitor_output

    out = "Warning: blah\nBUSY|100|50|abc_x\n"
    r = parse_monitor_output(out)
    assert r["status"] == "busy"
    assert r["util"] == 50.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_monitor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lockbot.core.monitor'`.

- [ ] **Step 3: Create monitor.py with parse_monitor_output**

创建 `python/lockbot/core/monitor.py`：

```python
"""Background realtime-usage collector (xpu-smi over SSH) and output parsing.

Platform mode only. SSH relies entirely on the host's passwordless key;
no credentials are ever read or stored here. The remote command must be
read-only and emit a single protocol line: STATUS|MEM|UTIL|CONTAINER.
"""

import logging

logger = logging.getLogger(__name__)


def _to_float(s):
    try:
        return float(str(s).strip())
    except (ValueError, TypeError):
        return None


def parse_monitor_output(raw):
    """Parse remote command stdout into a node monitor dict.

    Looks for a line shaped STATUS|MEM|UTIL|CONTAINER. Anything that does
    not match the 4-field protocol yields status='error'.
    """
    error = {"status": "error", "mem": None, "util": None, "container": ""}
    if not raw:
        return error
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) != 4:
            continue
        status_raw = parts[0].strip().upper()
        if status_raw not in ("BUSY", "FREE"):
            continue
        return {
            "status": "busy" if status_raw == "BUSY" else "free",
            "mem": _to_float(parts[1]),
            "util": _to_float(parts[2]),
            "container": parts[3].strip(),
        }
    return error
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_monitor.py -v`
Expected: PASS (4 passed)。

- [ ] **Step 5: Commit**

```bash
git add python/lockbot/core/monitor.py tests/core/test_monitor.py
git commit -m "feat: monitor.parse_monitor_output protocol parsing"
```

---

## Task 3: monitor — occupant_from_container（容器名→工号）

**Files:**
- Modify: `python/lockbot/core/monitor.py`
- Test: `tests/core/test_monitor.py`

依据 Doc 2 规范：容器名以工号为前缀。取首段，匹配工号模式则用工号，否则原样返回容器名。

- [ ] **Step 1: Write the failing test**

在 `tests/core/test_monitor.py` 追加：

```python
def test_occupant_extracts_id_prefix():
    from lockbot.core.monitor import occupant_from_container

    assert occupant_from_container("zhangsan_train") == "zhangsan"
    assert occupant_from_container("liujie63-job1") == "liujie63"
    assert occupant_from_container("wangwu") == "wangwu"


def test_occupant_empty_and_noncompliant():
    from lockbot.core.monitor import occupant_from_container

    assert occupant_from_container("") == ""
    assert occupant_from_container(None) == ""
    # no recognizable id prefix → return raw name
    assert occupant_from_container("123#$%") == "123#$%"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_monitor.py::test_occupant_extracts_id_prefix -v`
Expected: FAIL — `ImportError: cannot import name 'occupant_from_container'`.

- [ ] **Step 3: Add occupant_from_container**

在 `python/lockbot/core/monitor.py` 追加：

```python
import re

_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9]{1,30}$")


def occupant_from_container(container):
    """Derive the occupant (employee id) from a container name.

    Convention (Doc 2): container names are prefixed with the owner's id,
    separated by '_' or '-'. Returns the id if the first segment looks like
    one, else the raw container name. Empty input → "".
    """
    if not container:
        return ""
    first = re.split(r"[_\-]", container, maxsplit=1)[0]
    if _ID_RE.match(first):
        return first
    return container
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_monitor.py -v`
Expected: PASS (all)。

- [ ] **Step 5: Lint + commit**

```bash
ruff check python/lockbot/core/monitor.py
ruff format python/lockbot/core/monitor.py
git add python/lockbot/core/monitor.py tests/core/test_monitor.py
git commit -m "feat: monitor.occupant_from_container id extraction"
```

---

## Task 4: monitor — SSH 执行函数（可 mock 的单节点采集）

把单节点 SSH 执行抽成独立函数 `collect_one(ip, cmd, ssh_user, timeout)`，返回 NodeMonitor dict。测试一律 monkeypatch `subprocess.run`，**绝不真实联网**。

**Files:**
- Modify: `python/lockbot/core/monitor.py`
- Test: `tests/core/test_monitor.py`

- [ ] **Step 1: Write the failing test**

在 `tests/core/test_monitor.py` 追加：

```python
def test_collect_one_success(monkeypatch):
    import subprocess

    from lockbot.core import monitor

    class FakeProc:
        returncode = 0
        stdout = "BUSY|100|50|liujie63_x"
        stderr = ""

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["timeout"] = kwargs.get("timeout")
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = monitor.collect_one("10.0.0.1", "xpu-smi", "root", 15)
    assert r["status"] == "busy"
    assert r["util"] == 50.0
    assert r["occupant"] == "liujie63"
    assert "updated_at" in r
    # ssh invoked as a list (no shell string interpolation), with our IP
    assert captured["args"][0] == "ssh"
    assert "root@10.0.0.1" in captured["args"]
    assert captured["timeout"] == 15


def test_collect_one_timeout(monkeypatch):
    import subprocess

    from lockbot.core import monitor

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=15)

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = monitor.collect_one("10.0.0.1", "xpu-smi", "root", 15)
    assert r["status"] == "error"
    assert r["util"] is None


def test_collect_one_nonzero_exit(monkeypatch):
    import subprocess

    from lockbot.core import monitor

    class FakeProc:
        returncode = 255
        stdout = ""
        stderr = "Connection refused"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc())
    r = monitor.collect_one("10.0.0.1", "xpu-smi", "root", 15)
    assert r["status"] == "error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_monitor.py::test_collect_one_success -v`
Expected: FAIL — `ImportError: cannot import name 'collect_one'`.

- [ ] **Step 3: Add collect_one**

在 `python/lockbot/core/monitor.py` 顶部 import 补充 `import subprocess`、`import time`，并追加：

```python
_SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
]


def _node_error(reason):
    return {
        "status": "error",
        "mem": None,
        "util": None,
        "occupant": "",
        "updated_at": time.time(),
        "reason": reason,
    }


def collect_one(ip, cmd, ssh_user, timeout):
    """Collect one node's monitor data over SSH. Never raises.

    Uses the system ssh binary with BatchMode (no password prompt) and a
    hard timeout. Relies entirely on the host's passwordless key — no
    credential is passed. Returns a NodeMonitor dict (status busy/free/error).
    """
    args = [
        "ssh",
        *_SSH_OPTS,
        "-o", f"ConnectTimeout={int(timeout)}",
        f"{ssh_user}@{ip}",
        cmd,
    ]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        )
    except subprocess.TimeoutExpired:
        logger.warning("monitor: ssh to %s timed out after %ss", ip, timeout)
        return _node_error("timeout")
    except Exception as e:  # noqa: BLE001 — collection must never crash the thread
        logger.warning("monitor: ssh to %s failed: %s", ip, e)
        return _node_error("exec_error")

    if proc.returncode != 0:
        logger.warning("monitor: ssh to %s exited %d: %s", ip, proc.returncode, proc.stderr.strip()[:200])
        return _node_error("nonzero_exit")

    parsed = parse_monitor_output(proc.stdout)
    return {
        "status": parsed["status"],
        "mem": parsed["mem"],
        "util": parsed["util"],
        "occupant": occupant_from_container(parsed["container"]),
        "updated_at": time.time(),
    }
```

说明：`cmd` 作为单个参数传给 ssh（远端用登录 shell 执行），IP/user 走列表参数不做字符串插值，杜绝注入。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_monitor.py -v`
Expected: PASS (all)。

- [ ] **Step 5: Lint + commit**

```bash
ruff check python/lockbot/core/monitor.py
ruff format python/lockbot/core/monitor.py
git add python/lockbot/core/monitor.py tests/core/test_monitor.py
git commit -m "feat: monitor.collect_one single-node SSH collection"
```

---

## Task 5: monitor — MonitorCollector（缓存 + 注册 + 一轮采集）

先实现非线程部分：注册/注销、跨 IP 去重、`_collect_round`（同步跑一轮，便于测试）、`snapshot`。后台线程在 Task 6 加。

**Files:**
- Modify: `python/lockbot/core/monitor.py`
- Test: `tests/core/test_monitor.py`

每个 bot 注册自己的 `node_ips` 与采集参数。一轮采集：汇总所有 bot 的 (ip) 去重 → 并发 `collect_one` → 按 bot/node_key 回填缓存。

- [ ] **Step 1: Write the failing test**

在 `tests/core/test_monitor.py` 追加：

```python
def test_collector_register_and_round(monkeypatch):
    from lockbot.core import monitor

    calls = []

    def fake_collect_one(ip, cmd, ssh_user, timeout):
        calls.append(ip)
        return {"status": "busy", "mem": 1.0, "util": 42.0, "occupant": "u", "updated_at": 1000.0}

    monkeypatch.setattr(monitor, "collect_one", fake_collect_one)

    c = monitor.MonitorCollector()
    c.register(1, {"n1": "10.0.0.1", "n2": "10.0.0.2"}, cmd="x", ssh_user="root", ssh_timeout=5, interval=60)
    c._collect_round()
    snap = c.snapshot(1)
    assert snap["n1"]["util"] == 42.0
    assert snap["n2"]["util"] == 42.0
    assert set(calls) == {"10.0.0.1", "10.0.0.2"}


def test_collector_dedup_shared_ip(monkeypatch):
    """Two bots sharing one IP → that IP is collected once per round."""
    from lockbot.core import monitor

    calls = []
    monkeypatch.setattr(
        monitor, "collect_one",
        lambda ip, cmd, ssh_user, timeout: calls.append(ip)
        or {"status": "free", "mem": 0.0, "util": 0.0, "occupant": "", "updated_at": 1000.0},
    )

    c = monitor.MonitorCollector()
    c.register(1, {"n1": "10.0.0.9"}, cmd="x", ssh_user="root", ssh_timeout=5, interval=60)
    c.register(2, {"nA": "10.0.0.9"}, cmd="x", ssh_user="root", ssh_timeout=5, interval=60)
    c._collect_round()
    assert calls.count("10.0.0.9") == 1
    assert c.snapshot(1)["n1"]["util"] == 0.0
    assert c.snapshot(2)["nA"]["util"] == 0.0


def test_collector_unregister(monkeypatch):
    from lockbot.core import monitor

    monkeypatch.setattr(
        monitor, "collect_one",
        lambda *a, **k: {"status": "busy", "mem": 1.0, "util": 1.0, "occupant": "", "updated_at": 1.0},
    )
    c = monitor.MonitorCollector()
    c.register(1, {"n1": "1.1.1.1"}, cmd="x", ssh_user="root", ssh_timeout=5, interval=60)
    c.unregister(1)
    c._collect_round()
    assert c.snapshot(1) == {}


def test_snapshot_is_copy(monkeypatch):
    from lockbot.core import monitor

    monkeypatch.setattr(
        monitor, "collect_one",
        lambda *a, **k: {"status": "busy", "mem": 1.0, "util": 1.0, "occupant": "", "updated_at": 1.0},
    )
    c = monitor.MonitorCollector()
    c.register(1, {"n1": "1.1.1.1"}, cmd="x", ssh_user="root", ssh_timeout=5, interval=60)
    c._collect_round()
    snap = c.snapshot(1)
    snap["n1"]["util"] = 999
    assert c.snapshot(1)["n1"]["util"] == 1.0  # mutation didn't leak into cache
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_monitor.py::test_collector_register_and_round -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'MonitorCollector'`.

- [ ] **Step 3: Add MonitorCollector (no thread yet)**

在 `python/lockbot/core/monitor.py` 顶部补充 `import copy`、`import threading`、`from concurrent.futures import ThreadPoolExecutor`，并追加：

```python
_MAX_WORKERS = 16


class MonitorCollector:
    """Background xpu-smi collector with a thread-safe per-bot snapshot cache.

    Each enabled bot registers its node→IP map and SSH params. One round
    deduplicates by IP (a shared IP is collected once), runs collect_one
    concurrently, then fans results back out to each bot's node keys.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._regs = {}  # bot_id -> {node_ips, cmd, ssh_user, ssh_timeout, interval}
        self._cache = {}  # bot_id -> {node_key -> NodeMonitor}
        self._wake = threading.Event()
        self._alive = False
        self._thread = None

    def register(self, bot_id, node_ips, cmd, ssh_user, ssh_timeout, interval):
        with self._lock:
            self._regs[bot_id] = {
                "node_ips": dict(node_ips or {}),
                "cmd": cmd,
                "ssh_user": ssh_user,
                "ssh_timeout": ssh_timeout,
                "interval": interval,
            }
        self._wake.set()

    def unregister(self, bot_id):
        with self._lock:
            self._regs.pop(bot_id, None)
            self._cache.pop(bot_id, None)

    def snapshot(self, bot_id):
        """Return a deep copy of a bot's node→NodeMonitor map (consistent read)."""
        with self._lock:
            return copy.deepcopy(self._cache.get(bot_id, {}))

    def _collect_round(self):
        # 1. snapshot registrations under lock
        with self._lock:
            regs = {bid: dict(r) for bid, r in self._regs.items()}

        if not regs:
            return

        # 2. dedup IPs across all bots (ip -> (cmd, ssh_user, timeout) from first seen)
        ip_params = {}
        for r in regs.values():
            for ip in r["node_ips"].values():
                if ip and ip not in ip_params:
                    ip_params[ip] = (r["cmd"], r["ssh_user"], r["ssh_timeout"])

        # 3. concurrent collect, one result per IP
        results = {}
        if ip_params:
            with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(ip_params))) as ex:
                futs = {
                    ex.submit(collect_one, ip, cmd, user, to): ip
                    for ip, (cmd, user, to) in ip_params.items()
                }
                for fut in futs:
                    ip = futs[fut]
                    try:
                        results[ip] = fut.result()
                    except Exception as e:  # noqa: BLE001
                        logger.warning("monitor: collect failed for %s: %s", ip, e)
                        results[ip] = _node_error("future_error")

        # 4. fan results back to each bot's node keys
        with self._lock:
            for bot_id, r in regs.items():
                if bot_id not in self._regs:  # unregistered mid-round
                    continue
                node_map = {}
                for node_key, ip in r["node_ips"].items():
                    if ip in results:
                        node_map[node_key] = results[ip]
                self._cache[bot_id] = node_map
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_monitor.py -v`
Expected: PASS (all)。

- [ ] **Step 5: Lint + commit**

```bash
ruff check python/lockbot/core/monitor.py
ruff format python/lockbot/core/monitor.py
git add python/lockbot/core/monitor.py tests/core/test_monitor.py
git commit -m "feat: MonitorCollector cache, register, dedup round"
```

---

## Task 6: monitor — 后台线程 start/stop

参照 [scheduler.py](../../../python/lockbot/core/scheduler.py) 的线程模型：`daemon=True` 线程 + `Event` 唤醒 + 周期循环 + 异常不杀线程 + 可重建线程。

**Files:**
- Modify: `python/lockbot/core/monitor.py`
- Test: `tests/core/test_monitor.py`

- [ ] **Step 1: Write the failing test**

在 `tests/core/test_monitor.py` 追加：

```python
def test_start_runs_round_then_stop(monkeypatch):
    import time

    from lockbot.core import monitor

    rounds = []
    monkeypatch.setattr(
        monitor, "collect_one",
        lambda *a, **k: rounds.append(1)
        or {"status": "busy", "mem": 1.0, "util": 7.0, "occupant": "", "updated_at": 1.0},
    )

    c = monitor.MonitorCollector()
    c.register(1, {"n1": "1.1.1.1"}, cmd="x", ssh_user="root", ssh_timeout=1, interval=0.05)
    c.start()
    # let it run a couple of rounds
    deadline = time.time() + 2
    while not c.snapshot(1) and time.time() < deadline:
        time.sleep(0.01)
    c.stop()
    assert c.snapshot(1).get("n1", {}).get("util") == 7.0
    assert len(rounds) >= 1


def test_start_is_idempotent(monkeypatch):
    from lockbot.core import monitor

    monkeypatch.setattr(monitor, "collect_one", lambda *a, **k: monitor._node_error("x"))
    c = monitor.MonitorCollector()
    c.register(1, {"n1": "1.1.1.1"}, cmd="x", ssh_user="root", ssh_timeout=1, interval=60)
    c.start()
    t1 = c._thread
    c.start()  # second call no-op
    assert c._thread is t1
    c.stop()


def test_round_exception_does_not_kill_thread(monkeypatch):
    import time

    from lockbot.core import monitor

    state = {"n": 0}

    def flaky(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("boom")
        return {"status": "free", "mem": 0.0, "util": 0.0, "occupant": "", "updated_at": 1.0}

    monkeypatch.setattr(monitor, "collect_one", flaky)
    c = monitor.MonitorCollector()
    c.register(1, {"n1": "1.1.1.1"}, cmd="x", ssh_user="root", ssh_timeout=1, interval=0.05)
    c.start()
    deadline = time.time() + 2
    while state["n"] < 2 and time.time() < deadline:
        time.sleep(0.01)
    c.stop()
    assert state["n"] >= 2  # survived the first-round exception
```

注意：`collect_one` 内部已捕获自身异常，这里的 `flaky` 模拟的是 future 层冒泡，`_collect_round` 的 `try/except` 兜住；`_run` 再加一层兜底。`test_round_exception` 验证线程不死。

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_monitor.py::test_start_runs_round_then_stop -v`
Expected: FAIL — `AttributeError: 'MonitorCollector' object has no attribute 'start'`.

- [ ] **Step 3: Add start/stop/_run**

在 `MonitorCollector` 内追加方法：

```python
    def start(self):
        """Start the background daemon thread (idempotent)."""
        with self._lock:
            if self._alive:
                return
            self._alive = True
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True, name="MonitorCollector")
            thread = self._thread
        thread.start()

    def stop(self):
        """Stop the background thread; blocks until it exits."""
        with self._lock:
            self._alive = False
            thread = self._thread
        self._wake.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=10)

    def _next_interval(self):
        with self._lock:
            if not self._regs:
                return 60.0
            return max(1.0, min(r["interval"] for r in self._regs.values()))

    def _run(self):
        while self._alive:
            try:
                self._collect_round()
            except Exception:
                logger.exception("MonitorCollector: round failed, continuing")
            self._wake.clear()
            self._wake.wait(timeout=self._next_interval())
```

说明：`interval` 取所有 bot 中的最小值（最严格者主导周期）。测试里用 0.05s 小周期快速跑。`_next_interval` 下限 1s 防忙转——但测试 interval=0.05 会被 clamp 到 1s？需调整：测试期望快速多轮。

**修正**：把下限设为 0（或不 clamp 测试值）。改 `_next_interval` 为 `max(0.01, min(...))`，并在生产配置层（schemas 校验，Task 9）保证 `MONITOR_INTERVAL >= 5`。这样单元测试可用 0.05s，生产不会忙转。更新上面的 `_next_interval`：

```python
    def _next_interval(self):
        with self._lock:
            if not self._regs:
                return 60.0
            return max(0.01, min(r["interval"] for r in self._regs.values()))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_monitor.py -v`
Expected: PASS (all)。注意线程测试有 sleep，整体仍应秒级完成。

- [ ] **Step 5: Lint + commit**

```bash
ruff check python/lockbot/core/monitor.py
ruff format python/lockbot/core/monitor.py
git add python/lockbot/core/monitor.py tests/core/test_monitor.py
git commit -m "feat: MonitorCollector background thread start/stop"
```

---

## Task 7: i18n — 带监控列的表头

**Files:**
- Modify: `python/lockbot/core/i18n/zh.py` (line 67 附近)
- Modify: `python/lockbot/core/i18n/en.py` (对应表头)
- Test: (后续渲染任务覆盖；本任务仅数据)

- [ ] **Step 1: Add zh table_header_monitor**

在 `python/lockbot/core/i18n/zh.py` 的 `"query.table_header"` 之后新增一行（保持原 `table_header` 不变，确保不变量）：

```python
    "query.table_header_monitor": (
        "| IP地址 | 节点状态 | 实时使用率 | 卡状态 | 使用者 | 剩余时间 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
    ),
```

- [ ] **Step 2: Add en table_header_monitor**

在 `python/lockbot/core/i18n/en.py` 对应位置新增（与该文件现有 `query.table_header` 的列名风格一致；查阅文件后用其英文列名 + 插入 `Realtime Usage`）：

```python
    "query.table_header_monitor": (
        "| IP | Node | Realtime Usage | Device | User | Remaining |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
    ),
```

- [ ] **Step 3: Verify keys load**

Run: `PYTHONPATH=python python -c "from lockbot.core.i18n import t; print(t('query.table_header_monitor'))"`
Expected: 打印带「实时使用率」的 6 列表头。

- [ ] **Step 4: Commit**

```bash
git add python/lockbot/core/i18n/zh.py python/lockbot/core/i18n/en.py
git commit -m "feat: add monitor-column query table header i18n"
```

---

## Task 8: query_render — build_device_query / build_node_query 新增 monitor 列

`monitor_snapshot=None`（默认）→ 走原有渲染路径，输出逐字符不变（不变量）。非 None → 用 6 列表头，节点首行插入实时使用率单元格。

**Files:**
- Modify: `python/lockbot/core/query_render.py`
- Test: `tests/core/test_query_render_monitor.py`

- [ ] **Step 1: Write the failing test**

创建 `tests/core/test_query_render_monitor.py`：

```python
import time

import pytest

from lockbot.core.config import Config
from lockbot.core.query_render import build_device_query, build_node_query


@pytest.fixture
def cfg():
    return Config({})


def _busy(util, occ, age=0):
    return {"status": "busy", "mem": 1.0, "util": util, "occupant": occ, "updated_at": time.time() - age}


def _node_state_busy(uid="alice", dur=600):
    now = int(time.time())
    return {"status": "exclusive", "current_users": [{"user_id": uid, "start_time": now, "duration": dur}]}


def test_node_query_no_snapshot_unchanged(cfg):
    """monitor_snapshot=None → identical to legacy output (invariant)."""
    state = {"n1": _node_state_busy()}
    legacy = build_node_query(state, None, cfg)
    withnone = build_node_query(state, None, cfg, monitor_snapshot=None)
    assert legacy == withnone
    assert "实时使用率" not in legacy


def test_node_query_with_snapshot_adds_column(cfg):
    state = {"n1": _node_state_busy()}
    snap = {"n1": _busy(87, "zhangsan")}
    out = build_node_query(state, None, cfg, monitor_snapshot=snap)
    assert "实时使用率" in out
    assert "87" in out and "zhangsan" in out


def test_device_query_no_snapshot_unchanged(cfg):
    dev = lambda i, **k: {"dev_id": i, "status": "idle", "dev_model": "a800", "current_users": []}
    state = {"n1": [dev(i) for i in range(2)]}
    legacy = build_device_query(state, None, cfg)
    withnone = build_device_query(state, None, cfg, monitor_snapshot=None)
    assert legacy == withnone


def test_device_query_with_snapshot_adds_column(cfg):
    dev = lambda i: {"dev_id": i, "status": "idle", "dev_model": "a800", "current_users": []}
    state = {"n1": [dev(i) for i in range(2)]}
    snap = {"n1": _busy(50, "li4")}
    out = build_device_query(state, None, cfg, monitor_snapshot=snap)
    assert "实时使用率" in out
    assert "50" in out and "li4" in out


def test_monitor_cell_stale_and_error(cfg):
    state = {"n1": _node_state_busy(), "n2": _node_state_busy()}
    snap = {
        "n1": _busy(80, "u1", age=99999),          # stale
        "n2": {"status": "error", "mem": None, "util": None, "occupant": "", "updated_at": time.time()},
    }
    cfg2 = Config({"MONITOR_STALE_SEC": 180})
    out = build_node_query(state, None, cfg2, monitor_snapshot=snap)
    # stale marker present somewhere on n1's row; error node shows placeholder
    assert "⚠" in out
    assert "--" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_query_render_monitor.py::test_node_query_with_snapshot_adds_column -v`
Expected: FAIL — `build_node_query` 不接受 `monitor_snapshot` 参数 (TypeError)。

- [ ] **Step 3: Add monitor column to query_render.py**

在 `python/lockbot/core/query_render.py` 顶部加一个 helper（渲染监控单元格）：

```python
def _monitor_cell(snapshot, node_key, config):
    """Render the realtime-usage cell for a node. '--' when missing/error."""
    if not snapshot:
        return "--"
    nm = snapshot.get(node_key)
    if not nm or nm.get("status") == "error" or nm.get("util") is None:
        return "--"
    util = nm["util"]
    occ = nm.get("occupant") or ""
    cell = f"{util:.0f}% {occ}".strip()
    stale_sec = config.get_val("MONITOR_STALE_SEC") if config else 180
    if stale_sec and (time.time() - nm.get("updated_at", 0)) > stale_sec:
        cell += "⚠"
    return cell
```

`build_device_query` 改造：签名加 `monitor_snapshot=None`；表头按 snapshot 选择；在 `_md_row` 调用处，monitor 列只在节点首行有值（与 `node_cell`/`node_status_cell` 同步）。把现有：

```python
    lines.append(t("query.table_header", config=config))
```

改为：

```python
    header_key = "query.table_header_monitor" if monitor_snapshot else "query.table_header"
    lines.append(t(header_key, config=config))
```

并在生成每行处，插入 monitor 单元格。原 DEVICE 行：

```python
            node_cell = node_key if first_row else ""
            node_status_cell = node_state if first_row else ""
            lines.append(_md_row(node_cell, node_status_cell, dev_cell, user_cell, dur_cell))
```

改为：

```python
            node_cell = node_key if first_row else ""
            node_status_cell = node_state if first_row else ""
            if monitor_snapshot is not None:
                mon_cell = _monitor_cell(monitor_snapshot, node_key, config) if first_row else ""
                lines.append(_md_row(node_cell, node_status_cell, mon_cell, dev_cell, user_cell, dur_cell))
            else:
                lines.append(_md_row(node_cell, node_status_cell, dev_cell, user_cell, dur_cell))
            first_row = False
```

`build_node_query` 同样改：签名加 `monitor_snapshot=None`；表头切换；两处 `_md_row`（idle 行与 current_users 行）插入 monitor 单元格（仅首行有值）。idle 行：

```python
        if ns["status"] == "idle":
            if monitor_snapshot is not None:
                mon_cell = _monitor_cell(monitor_snapshot, node_key, config)
                lines.append(_md_row(node_key, node_status_cell, mon_cell, "--", "--", "--"))
            else:
                lines.append(_md_row(node_key, node_status_cell, "--", "--", "--"))
```

current_users 行：在 `node_cell`/`node_st_cell` 旁加 `mon_cell = _monitor_cell(...) if first_row else ""`，6 列 `_md_row`。

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_query_render_monitor.py -v`
Expected: PASS (all)。

- [ ] **Step 5: Lint + commit**

```bash
ruff check python/lockbot/core/query_render.py
ruff format python/lockbot/core/query_render.py
git add python/lockbot/core/query_render.py tests/core/test_query_render_monitor.py
git commit -m "feat: optional realtime-usage column in query_render"
```

---

## Task 9: schemas — MONITOR_* 校验

**Files:**
- Modify: `python/lockbot/backend/app/bots/schemas.py`
- Test: `tests/backend/.../test_bots_schemas.py`（沿用现有 schema 测试文件；若无则在 backend 测试目录新建）

- [ ] **Step 1: Write the failing test**

定位现有 config_overrides 校验测试（grep `_validate_config_overrides` 或 `config_overrides` in tests）。在其旁追加：

```python
def test_monitor_interval_bounds():
    from lockbot.backend.app.bots.schemas import _validate_config_overrides

    import pytest
    with pytest.raises(ValueError):
        _validate_config_overrides({"MONITOR_INTERVAL": 1})  # below min 5
    assert _validate_config_overrides({"MONITOR_INTERVAL": 60}) is not None


def test_monitor_node_ips_type():
    from lockbot.backend.app.bots.schemas import _validate_config_overrides

    import pytest
    with pytest.raises(ValueError):
        _validate_config_overrides({"MONITOR_NODE_IPS": {"n1": 123}})  # ip not str
    assert _validate_config_overrides({"MONITOR_NODE_IPS": {"n1": "10.0.0.1"}}) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ -k monitor_interval_bounds -v`
Expected: FAIL — 当前 `_validate_config_overrides` 不校验 MONITOR_*，不抛错。

- [ ] **Step 3: Extend _validate_config_overrides**

在 `python/lockbot/backend/app/bots/schemas.py` 的 `_CFG_RULES` 增加整数范围：

```python
    "MONITOR_INTERVAL": (5, 3600),
    "MONITOR_SSH_TIMEOUT": (1, 120),
    "MONITOR_STALE_SEC": (10, 86400),
```

在 `_validate_config_overrides` 末尾（`if errors` 之前）追加 `MONITOR_NODE_IPS` 类型校验：

```python
    node_ips = v.get("MONITOR_NODE_IPS")
    if node_ips is not None:
        if not isinstance(node_ips, dict) or any(
            not isinstance(k, str) or not isinstance(val, str) for k, val in node_ips.items()
        ):
            errors.append("MONITOR_NODE_IPS must be a mapping of {str: str}")
```

注意：`_CFG_RULES` 的循环已处理整数 min/max，新增三项会自动纳入校验，无需改循环逻辑。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ -k "monitor_interval_bounds or monitor_node_ips_type" -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add python/lockbot/backend/app/bots/schemas.py tests/
git commit -m "feat: validate MONITOR_* config overrides"
```

---

## Task 10: manager — collector 生命周期

**Files:**
- Modify: `python/lockbot/backend/app/bots/manager.py`
- Test: `tests/backend/.../test_manager.py`（沿用现有 manager 测试文件）

- [ ] **Step 1: Write the failing test**

在现有 manager 测试文件追加（用 monkeypatch 替换 collector 为 spy；BotInstance 真实启动需依赖，参考现有 manager 测试的 fixture 用法）：

```python
def test_start_bot_registers_monitor(monkeypatch):
    from lockbot.backend.app.bots import manager as mgr_mod

    events = []

    class SpyCollector:
        def register(self, *a, **k): events.append(("register", a, k))
        def unregister(self, *a, **k): events.append(("unregister", a))
        def start(self): events.append(("start",))
        def stop(self): events.append(("stop",))

    m = mgr_mod.BotManager()
    m._collector = SpyCollector()
    m.start_scheduler()
    cfg = {
        "BOT_TYPE": "NODE",
        "BOT_NAME": "t",
        "CLUSTER_CONFIGS": ["n1"],
        "MONITOR_ENABLED": True,
        "MONITOR_NODE_IPS": {"n1": "10.0.0.1"},
    }
    m.start_bot(99, cfg)
    assert any(e[0] == "register" for e in events)
    assert any(e[0] == "start" for e in events)
    m.stop_bot(99)
    assert any(e[0] == "unregister" for e in events)
    m.shutdown_all()
    assert any(e[0] == "stop" for e in events)


def test_start_bot_monitor_disabled_no_register(monkeypatch):
    from lockbot.backend.app.bots import manager as mgr_mod

    events = []

    class SpyCollector:
        def register(self, *a, **k): events.append("register")
        def unregister(self, *a, **k): pass
        def start(self): events.append("start")
        def stop(self): pass

    m = mgr_mod.BotManager()
    m._collector = SpyCollector()
    m.start_scheduler()
    m.start_bot(98, {"BOT_TYPE": "NODE", "BOT_NAME": "t", "CLUSTER_CONFIGS": ["n1"]})
    assert "register" not in events  # MONITOR_ENABLED defaults False
    m.stop_bot(98)
    m.shutdown_all()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ -k start_bot_registers_monitor -v`
Expected: FAIL — `BotManager` 无 `_collector` 行为 / `start_bot` 不调 register。

- [ ] **Step 3: Wire collector into BotManager**

在 `python/lockbot/backend/app/bots/manager.py`：

import 区加：

```python
from lockbot.core.config import Config
from lockbot.core.monitor import MonitorCollector
```

`__init__` 加：

```python
        self._collector = MonitorCollector()
```

`start_bot` 在 `logger.info(...)` 之前插入监控注册（用 `Config` 读取并归一）：

```python
        cfg = Config(config_dict)
        if cfg.get_val("MONITOR_ENABLED"):
            self._collector.register(
                bot_id,
                cfg.get_val("MONITOR_NODE_IPS") or {},
                cmd=cfg.get_val("MONITOR_CMD"),
                ssh_user=cfg.get_val("MONITOR_SSH_USER"),
                ssh_timeout=cfg.get_val("MONITOR_SSH_TIMEOUT"),
                interval=cfg.get_val("MONITOR_INTERVAL"),
            )
            self._collector.start()
```

`stop_bot` 末尾加：

```python
        self._collector.unregister(bot_id)
```

`shutdown_all` 开头加：

```python
        self._collector.stop()
```

注意：`restart_bot` 已串联 stop+start，自动覆盖 unregister/register，无需额外改。

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ -k "start_bot_registers_monitor or start_bot_monitor_disabled" -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add python/lockbot/backend/app/bots/manager.py tests/
git commit -m "feat: BotManager drives MonitorCollector lifecycle"
```

---

## Task 11: bot.query — 注入 snapshot 到渲染

让 `DeviceBot.query` / `NodeBot.query` 能拿到当前 bot 的 monitor snapshot 并传给 `build_*_query`。core 层不能直接 import backend，用**注入回调**：BotManager 在 `start_bot` 给 instance 设一个 `_monitor_snapshot_provider`，bot 调用它取 snapshot（无则 None）。

**Files:**
- Modify: `python/lockbot/backend/app/bots/manager.py` (设置 provider)
- Modify: `python/lockbot/core/device_bot.py` / `node_bot.py` (query 用 provider)
- Test: `tests/core/test_device_bot.py` / `test_node_bot.py`

- [ ] **Step 1: Write the failing test**

在 `tests/core/test_device_bot.py` 追加（bot fixture 复用现有）：

```python
def test_query_uses_monitor_provider(bot):
    """If a monitor snapshot provider is set, query renders the extra column."""
    bot._monitor_snapshot_provider = lambda: {
        k: {"status": "busy", "mem": 1.0, "util": 66.0, "occupant": "zhao", "updated_at": __import__("time").time()}
        for k in bot.state.bot_state
    }
    result = bot.query("user1")
    content = result["message"]["body"][0]["content"]
    assert "实时使用率" in content
    assert "66" in content and "zhao" in content


def test_query_without_provider_unchanged(bot):
    """No provider → no monitor column (invariant)."""
    result = bot.query("user1")
    content = result["message"]["body"][0]["content"]
    assert "实时使用率" not in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_device_bot.py::test_query_uses_monitor_provider -v`
Expected: FAIL — query 不读 provider，无新列。

- [ ] **Step 3: Add provider hook to bots and manager**

在 `DeviceBot`（`python/lockbot/core/device_bot.py`）的 `query` 内，构造 snapshot 后传入。查看现有 query（约 145-151 行）：

```python
        with self._lock:
            content = build_device_query(self.state.bot_state, user_id, self.config, node_filter=node_key)
```

改为：

```python
        with self._lock:
            snap = None
            provider = getattr(self, "_monitor_snapshot_provider", None)
            if provider is not None:
                try:
                    snap = provider()
                except Exception:
                    snap = None
            content = build_device_query(
                self.state.bot_state, user_id, self.config, node_filter=node_key, monitor_snapshot=snap
            )
```

`NodeBot.query`（`python/lockbot/core/node_bot.py`）做同样改造，调 `build_node_query(..., monitor_snapshot=snap)`。QueueBot 若也走 `build_node_query` 则一并改；若用自己的渲染，本期不加列（与 spec「节点级，QUEUE 暂不强制」一致——按实际 query 路径决定）。

在 `python/lockbot/backend/app/bots/manager.py` 的 `start_bot`，给 instance 设 provider（在监控注册之后）：

```python
            instance.bot._monitor_snapshot_provider = lambda bid=bot_id: self._collector.snapshot(bid)
```

仅当 `MONITOR_ENABLED` 时设置；否则保持未设置（provider 缺失 → None → 不变量）。

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_device_bot.py tests/core/test_node_bot.py -v`
Expected: PASS（含新测试与原有断言；无 provider 的原测试不受影响）。

- [ ] **Step 5: Lint + commit**

```bash
ruff check python/lockbot/core/device_bot.py python/lockbot/core/node_bot.py python/lockbot/backend/app/bots/manager.py
ruff format python/lockbot/core/device_bot.py python/lockbot/core/node_bot.py python/lockbot/backend/app/bots/manager.py
git add python/lockbot/core/device_bot.py python/lockbot/core/node_bot.py python/lockbot/backend/app/bots/manager.py tests/core/test_device_bot.py tests/core/test_node_bot.py
git commit -m "feat: inject monitor snapshot into bot query rendering"
```

---

## Task 12: 前端可选 IP 字段（BotForm）

**Files:**
- Modify: `frontend/src/views/BotForm.vue`

本任务为可选增强（spec：复用通用 config_overrides 也可）。若实施：在高级配置区加 `MONITOR_ENABLED` 开关 + 每节点 IP 输入，提交时写入 `config_overrides.MONITOR_ENABLED` 与 `config_overrides.MONITOR_NODE_IPS`。

- [ ] **Step 1: 阅读 BotForm.vue 现有高级配置区结构**

Read `frontend/src/views/BotForm.vue`，定位 `config_overrides` 的录入控件与提交逻辑。

- [ ] **Step 2: 加 MONITOR_ENABLED 开关 + 节点 IP 表**

在高级配置区按现有控件风格新增：一个开关绑定 `MONITOR_ENABLED`；一个随 `cluster_configs` 节点列表渲染的 IP 输入表，收集成 `{node_key: ip}` 写入 `MONITOR_NODE_IPS`。空 IP 的节点不写入。

- [ ] **Step 3: 前端构建校验**

Run: `cd frontend && npm run build`（或项目既有的 lint/build 命令）
Expected: 构建通过。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/BotForm.vue
git commit -m "feat: optional monitor IP config in BotForm"
```

---

## Task 13: 全量回归 + 清理

**Files:** 无新增；验证整体。

- [ ] **Step 1: Run monitor + render suites**

Run: `pytest tests/core/test_monitor.py tests/core/test_query_render_monitor.py -v`
Expected: 全绿。

- [ ] **Step 2: Run the whole core suite**

Run: `pytest tests/core/ -v`
Expected: 全绿。重点关注 test_query_render / test_device_bot / test_node_bot / test_queue_bot / test_config（默认关闭，不变量保证零回归）。

- [ ] **Step 3: Run the backend suite**

Run: `pytest tests/ -q`
Expected: 全绿（manager/schemas 新增项覆盖）。

- [ ] **Step 4: 不变量验证（逐字符）**

Run:

```bash
PYTHONPATH=python python -c "
from lockbot.core.config import Config
from lockbot.core.query_render import build_node_query, build_device_query
import time
now=int(time.time())
ns={'n1':{'status':'exclusive','current_users':[{'user_id':'a','start_time':now,'duration':600}]}}
assert build_node_query(ns,None,Config({})) == build_node_query(ns,None,Config({}),monitor_snapshot=None)
print('NODE invariant OK')
dev=lambda i:{'dev_id':i,'status':'idle','dev_model':'a800','current_users':[]}
ds={'n1':[dev(i) for i in range(2)]}
assert build_device_query(ds,None,Config({})) == build_device_query(ds,None,Config({}),monitor_snapshot=None)
print('DEVICE invariant OK')
"
```

Expected: 打印两行 OK（`monitor_snapshot=None` 与不传参逐字符一致）。

- [ ] **Step 5: Final lint**

Run: `ruff check python/ tests/ && ruff format --check python/ tests/`
Expected: All checks passed.

- [ ] **Step 6: Commit any cleanup**

```bash
git add -A
git commit -m "chore: realtime-usage monitor regression pass"
```

---

## Self-Review notes

- **Spec coverage:** 配置项(T1)、协议解析(T2)、占用者反查(T3)、单节点 SSH(T4)、Collector 缓存+去重(T5)、后台线程(T6)、i18n 表头(T7)、新列渲染+不变量(T8)、schemas 校验(T9)、manager 生命周期(T10)、query 注入(T11)、前端(T12)、回归+不变量(T13)——均有对应任务。
- **5 个决策落点：** ①后台线程→T5/T6；②命令可配+结构化解析→T1(MONITOR_CMD)/T2；③仅 Platform→T9/T10/T11 全在 backend+core query 路径，不碰 Standalone；④免密 key 不存凭据→T4 SSH 参数无密码、DB/schema 不含凭据字段；⑤利用率%+占用者→T3/T8。
- **不变量：** `MONITOR_ENABLED=False`（默认）→ manager 不 register/start（T10 测试），bot 无 provider（T11 测试），`build_*_query` 走 None 分支（T8 + T13 逐字符断言）。三层都验证，零回归。
- **安全：** SSH 列表参数无 shell 插值(T4)、BatchMode 禁交互、硬超时、env 收窄 PATH；MONITOR_CMD 只读由文档+默认命令保证；DB 不存凭据。
- **DEVICE 逐卡对齐：** 第一版节点级聚合，监控单元格挂节点首行（T8），不落单卡，规避卡序错位（spec 已记 YAGNI）。
- **线程安全：** snapshot 用 `deepcopy`(T5 `test_snapshot_is_copy`)，缓存读写持锁，跨 bot 去重在锁内取注册快照后于锁外并发 SSH，结果回填再持锁。
- **类型一致：** NodeMonitor dict 键 `status/mem/util/occupant/updated_at` 在 collect_one(T4)/collector(T5)/_monitor_cell(T8) 全程一致；`parse_monitor_output` 返回 `status/mem/util/container`（container 未提工号），collect_one 转成 `occupant`。
- **待执行时确认项：** ①QueueBot 的 query 实际渲染路径（是否复用 build_node_query），T11 按实际决定是否加列；②en.py 现有表头英文列名，T7 对齐其风格；③backend 测试文件的既有命名/fixture（T9/T10 追加位置）；④BotForm.vue 高级配置控件风格(T12)。

