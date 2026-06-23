# GPU 利用率 + 容器名查询列（DEVICE bot）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当用户在群里纯 AT DEVICE bot（不带参数）时，同步采集集群真实 GPU 利用率与占卡容器名，在 query 表格里多显示「利用率」「容器名」两列；带参 query 保持原表格、不触发 SSH。

**Architecture:** 新增包内 Python 采集器 `xpu_collector.py`，通过复用现有 `id_ed25519` 密钥的 SSH（`BatchMode=yes`）并发执行 `xpu-smi` 并解析利用率与容器名，结果带 60s TTL 模块级缓存。`device_bot.query` 仅在 `node_key is None`（纯 AT）路径采集并把结果传给 `build_device_query`；该函数据 `xpu_usage` 是否为 None 决定渲染 5 列还是 7 列表格。利用率/容器名为节点级数据，只在每个节点的首行填充。

**Tech Stack:** Python 3, `subprocess`, `concurrent.futures.ThreadPoolExecutor`, pytest（mock subprocess），既有 i18n / Config / query_render 机制。

---

## File Structure

| 文件 | 职责 | 操作 |
|---|---|---|
| `python/lockbot/core/xpu_collector.py` | SSH 采集 + 解析 + TTL 缓存，导出 `NodeUsage` 与 `collect_node_usage` | 创建 |
| `python/lockbot/core/config.py` | 新增 `SSH_USER` / `SSH_CMD_TIMEOUT` / `XPU_USAGE_TTL` 三个 schema 键 | 修改 |
| `python/lockbot/core/query_render.py` | `build_device_query` 增加 `xpu_usage` 参数，7 列渲染 | 修改 |
| `python/lockbot/core/device_bot.py` | `query` 在纯 AT 路径组装 node→ip 并采集 | 修改 |
| `python/lockbot/core/i18n/zh.py` | 改名 5 列表头 + 新增 7 列表头键 | 修改 |
| `python/lockbot/core/i18n/en.py` | 补全 5 列 + 7 列表头键 | 修改 |
| `tests/core/test_xpu_collector.py` | 采集器解析/降级/TTL 单测 | 创建 |
| `tests/core/test_device_query_render.py` | `build_device_query` 5/7 列渲染单测 | 创建 |

---

## Task 1: 新增配置项

**Files:**
- Modify: `python/lockbot/core/config.py:97-102`（在 `QUERY_TIP` 之后、`_CONFIG_SCHEMA` 闭合 `}` 之前插入）
- Test: `tests/core/test_config.py`（若不存在则创建）

- [ ] **Step 1: 写失败测试**

创建/追加 `tests/core/test_config.py`：

```python
from lockbot.core.config import Config


def test_xpu_collector_config_defaults():
    config = Config({})
    assert config.get_val("SSH_USER") == "v_qiujie04"
    assert config.get_val("SSH_CMD_TIMEOUT") == 15
    assert config.get_val("XPU_USAGE_TTL") == 60


def test_xpu_collector_config_override():
    config = Config({"SSH_USER": "alice", "SSH_CMD_TIMEOUT": 5, "XPU_USAGE_TTL": 30})
    assert config.get_val("SSH_USER") == "alice"
    assert config.get_val("SSH_CMD_TIMEOUT") == 5
    assert config.get_val("XPU_USAGE_TTL") == 30
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/core/test_config.py::test_xpu_collector_config_defaults -v`
Expected: FAIL — `get_val("SSH_USER")` 返回 `None`。

- [ ] **Step 3: 实现 — 在 `_CONFIG_SCHEMA` 末尾（`QUERY_TIP` 项之后）插入三键**

`python/lockbot/core/config.py`，在 `"QUERY_TIP": {...},` 这一项之后、字典闭合 `}` 之前加入：

```python
    "SSH_USER": {
        "default": "v_qiujie04",
        "description": "SSH username for xpu-smi collection on target nodes",
        "env": True,
    },
    "SSH_CMD_TIMEOUT": {
        "default": 15,
        "description": "Per-node SSH command timeout in seconds for xpu-smi collection",
        "env": True,
    },
    "XPU_USAGE_TTL": {
        "default": 60,
        "description": "TTL in seconds for cached GPU utilization/container results",
        "env": True,
    },
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/core/test_config.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 5: 提交**

```bash
git add python/lockbot/core/config.py tests/core/test_config.py
git commit -m "feat: add SSH_USER/SSH_CMD_TIMEOUT/XPU_USAGE_TTL config keys"
```

---

## Task 2: 采集器解析逻辑（`_parse_xpu_output`）

先实现纯函数级别的解析（不碰 SSH），便于单测。

**Files:**
- Create: `python/lockbot/core/xpu_collector.py`
- Test: `tests/core/test_xpu_collector.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/core/test_xpu_collector.py`：

```python
from lockbot.core.xpu_collector import NodeUsage, _parse_util, _parse_pid


def test_parse_util_averages_column():
    # xpu-smi -m 输出：利用率在第 20 列（1-indexed）
    line = " ".join(["x"] * 17 + ["100", "y", "80"])  # col18=100(mem) col20=80(util)
    out = "\n".join([line, line])  # 两张卡，均 80
    assert _parse_util(out) == 80.0


def test_parse_util_empty_returns_none():
    assert _parse_util("") is None


def test_parse_pid_finds_first_busy_process():
    out = "header\nfoo  N/A  N/A   12345  python\nbar N/A N/A 67890 train"
    assert _parse_pid(out) == "12345"


def test_parse_pid_none_when_no_match():
    assert _parse_pid("No running processes found") is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/core/test_xpu_collector.py -v`
Expected: FAIL — `ModuleNotFoundError: lockbot.core.xpu_collector`。

- [ ] **Step 3: 实现解析函数**

创建 `python/lockbot/core/xpu_collector.py`：

```python
"""Collect real GPU utilization and occupying container name via SSH (xpu-smi)."""

import re
import subprocess
import time
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor

NodeUsage = namedtuple("NodeUsage", ["util", "container"])  # util: float|None, container: str

_FAILED = NodeUsage(util=None, container="")

# node_key -> (fetched_at_epoch, NodeUsage)
_cache: dict[str, tuple[float, NodeUsage]] = {}

_PID_RE = re.compile(r"N/A\s+N/A\s+(\d+)")


def _parse_pid(xpu_output: str) -> str | None:
    """Return the first busy process pid from `xpu-smi`, or None when free/unparsable."""
    if "No running processes found" in xpu_output:
        return None
    m = _PID_RE.search(xpu_output)
    return m.group(1) if m else None


def _parse_util(xpu_m_output: str) -> float | None:
    """Average utilization across cards from `xpu-smi -m` (col 20, 1-indexed)."""
    utils = []
    for line in xpu_m_output.splitlines():
        cols = line.split()
        if len(cols) < 20:
            continue
        try:
            utils.append(float(cols[19]))
        except ValueError:
            continue
    if not utils:
        return None
    return round(sum(utils) / len(utils), 2)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/core/test_xpu_collector.py -v`
Expected: PASS（4 passed）。

- [ ] **Step 5: 提交**

```bash
git add python/lockbot/core/xpu_collector.py tests/core/test_xpu_collector.py
git commit -m "feat: xpu_collector output parsing helpers"
```

---

## Task 3: 单节点 SSH 采集（`_collect_one`）

**Files:**
- Modify: `python/lockbot/core/xpu_collector.py`
- Test: `tests/core/test_xpu_collector.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/core/test_xpu_collector.py`：

```python
from unittest import mock


def test_collect_one_unreachable_returns_failed():
    from lockbot.core import xpu_collector
    with mock.patch.object(xpu_collector, "_ping", return_value=False):
        assert xpu_collector._collect_one("10.0.0.1", "alice", 5) == xpu_collector._FAILED


def test_collect_one_free_node_has_util_no_container():
    from lockbot.core import xpu_collector
    with mock.patch.object(xpu_collector, "_ping", return_value=True), \
         mock.patch.object(xpu_collector, "_ssh_ok", return_value=True), \
         mock.patch.object(xpu_collector, "_ssh_run") as run:
        # first call: xpu-smi (free), second: xpu-smi -m (util=0)
        run.side_effect = [
            "No running processes found",
            " ".join(["x"] * 17 + ["0", "y", "0"]),
        ]
        usage = xpu_collector._collect_one("10.0.0.1", "alice", 5)
    assert usage.util == 0.0
    assert usage.container == ""


def test_collect_one_busy_resolves_container():
    from lockbot.core import xpu_collector
    with mock.patch.object(xpu_collector, "_ping", return_value=True), \
         mock.patch.object(xpu_collector, "_ssh_ok", return_value=True), \
         mock.patch.object(xpu_collector, "_ssh_run") as run:
        run.side_effect = [
            "foo N/A N/A 12345 python",                          # xpu-smi
            " ".join(["x"] * 17 + ["100", "y", "82"]),           # xpu-smi -m
            "12:devices:/docker/abcdef0123456789",               # /proc/<pid>/cgroup
            "abcdef0 my_container",                               # docker ps
        ]
        usage = xpu_collector._collect_one("10.0.0.1", "alice", 5)
    assert usage.util == 82.0
    assert usage.container == "my_container"


def test_collect_one_timeout_returns_failed():
    from lockbot.core import xpu_collector
    with mock.patch.object(xpu_collector, "_ping", return_value=True), \
         mock.patch.object(xpu_collector, "_ssh_ok", return_value=True), \
         mock.patch.object(xpu_collector, "_ssh_run", side_effect=subprocess.TimeoutExpired("ssh", 5)):
        assert xpu_collector._collect_one("10.0.0.1", "alice", 5) == xpu_collector._FAILED
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/core/test_xpu_collector.py -k collect_one -v`
Expected: FAIL — `_ping` / `_ssh_ok` / `_ssh_run` / `_collect_one` 未定义。

- [ ] **Step 3: 实现 SSH 辅助 + 单节点采集**

在 `xpu_collector.py` 末尾追加：

```python
_SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
]


def _ping(ip: str) -> bool:
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", "1", ip],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return r.returncode == 0
    except Exception:
        return False


def _ssh_ok(ip: str, user: str) -> bool:
    try:
        r = subprocess.run(
            ["ssh", *_SSH_OPTS, "-o", "ConnectTimeout=2", f"{user}@{ip}", "exit"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
        )
        return r.returncode == 0
    except Exception:
        return False


def _ssh_run(ip: str, user: str, remote_cmd: str, timeout: int) -> str:
    out = subprocess.check_output(
        ["ssh", *_SSH_OPTS, f"{user}@{ip}", remote_cmd],
        stderr=subprocess.STDOUT, timeout=timeout, encoding="utf-8",
    )
    return out


def _resolve_container(ip: str, user: str, pid: str, timeout: int) -> str:
    try:
        cgroup = _ssh_run(ip, user, f"cat /proc/{pid}/cgroup 2>/dev/null", timeout)
    except Exception:
        return ""
    m = re.search(r"docker[-/]?([0-9a-f]+)", cgroup)
    if not m:
        return ""
    short = m.group(1)[:7]
    try:
        ps = _ssh_run(ip, user, "docker ps --format '{{.ID}} {{.Names}}'", timeout)
    except Exception:
        return ""
    for line in ps.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith(short):
            return parts[1]
    return ""


def _collect_one(ip: str, user: str, timeout: int) -> NodeUsage:
    if not _ping(ip) or not _ssh_ok(ip, user):
        return _FAILED
    try:
        smi = _ssh_run(ip, user, "xpu-smi", timeout)
        smi_m = _ssh_run(ip, user, "xpu-smi -m", timeout)
    except Exception:
        return _FAILED
    util = _parse_util(smi_m)
    pid = _parse_pid(smi)
    container = _resolve_container(ip, user, pid, timeout) if pid else ""
    return NodeUsage(util=util, container=container)
```

> 注：`subprocess` 已在文件顶部导入；测试用 `subprocess.TimeoutExpired` 触发 `_ssh_run` 异常，被 `_collect_one` 的 `except Exception` 捕获并降级为 `_FAILED`。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/core/test_xpu_collector.py -k collect_one -v`
Expected: PASS（4 passed）。

- [ ] **Step 5: 提交**

```bash
git add python/lockbot/core/xpu_collector.py tests/core/test_xpu_collector.py
git commit -m "feat: per-node SSH xpu-smi collection with container resolution"
```

---

## Task 4: 并发采集 + TTL 缓存（`collect_node_usage`）

**Files:**
- Modify: `python/lockbot/core/xpu_collector.py`
- Test: `tests/core/test_xpu_collector.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/core/test_xpu_collector.py`：

```python
class _Cfg:
    def __init__(self, ttl=60, user="alice", timeout=5):
        self._d = {"XPU_USAGE_TTL": ttl, "SSH_USER": user, "SSH_CMD_TIMEOUT": timeout}

    def get_val(self, k, default=None):
        return self._d.get(k, default)


def test_collect_node_usage_maps_keys():
    from lockbot.core import xpu_collector
    xpu_collector._cache.clear()
    with mock.patch.object(xpu_collector, "_collect_one",
                           return_value=NodeUsage(util=50.0, container="c")):
        res = xpu_collector.collect_node_usage({"node1": "10.0.0.1"}, _Cfg())
    assert res["node1"] == NodeUsage(util=50.0, container="c")


def test_collect_node_usage_uses_cache_within_ttl():
    from lockbot.core import xpu_collector
    xpu_collector._cache.clear()
    with mock.patch.object(xpu_collector, "_collect_one",
                           return_value=NodeUsage(util=1.0, container="")) as co:
        xpu_collector.collect_node_usage({"node1": "10.0.0.1"}, _Cfg(ttl=60))
        xpu_collector.collect_node_usage({"node1": "10.0.0.1"}, _Cfg(ttl=60))
    assert co.call_count == 1  # second call served from cache


def test_collect_node_usage_refetches_after_ttl():
    from lockbot.core import xpu_collector
    xpu_collector._cache.clear()
    with mock.patch.object(xpu_collector, "_collect_one",
                           return_value=NodeUsage(util=1.0, container="")) as co, \
         mock.patch.object(xpu_collector.time, "time", side_effect=[100.0, 100.0, 1000.0, 1000.0]):
        xpu_collector.collect_node_usage({"node1": "10.0.0.1"}, _Cfg(ttl=60))
        xpu_collector.collect_node_usage({"node1": "10.0.0.1"}, _Cfg(ttl=60))
    assert co.call_count == 2  # cache expired, refetched
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/core/test_xpu_collector.py -k collect_node_usage -v`
Expected: FAIL — `collect_node_usage` 未定义。

- [ ] **Step 3: 实现并发 + 缓存**

在 `xpu_collector.py` 末尾追加：

```python
def collect_node_usage(node_ips: dict[str, str], config) -> dict[str, NodeUsage]:
    """Collect {node_key: NodeUsage} for the given node->ip map, with TTL caching.

    Failures (unreachable/timeout/parse error) degrade to NodeUsage(None, "").
    """
    ttl = config.get_val("XPU_USAGE_TTL", 60)
    user = config.get_val("SSH_USER", "v_qiujie04")
    timeout = config.get_val("SSH_CMD_TIMEOUT", 15)

    now = time.time()
    result: dict[str, NodeUsage] = {}
    to_fetch: dict[str, str] = {}

    for node_key, ip in node_ips.items():
        cached = _cache.get(node_key)
        if cached and now - cached[0] < ttl:
            result[node_key] = cached[1]
        else:
            to_fetch[node_key] = ip

    if to_fetch:
        with ThreadPoolExecutor(max_workers=min(16, len(to_fetch))) as ex:
            futures = {ex.submit(_collect_one, ip, user, timeout): nk
                       for nk, ip in to_fetch.items()}
            for fut, node_key in futures.items():
                try:
                    usage = fut.result()
                except Exception:
                    usage = _FAILED
                _cache[node_key] = (time.time(), usage)
                result[node_key] = usage

    return result
```

> 注：`time` 模块在文件顶部已导入；`test_collect_node_usage_refetches_after_ttl` 通过 `mock.patch.object(xpu_collector.time, "time", ...)` 替换其 `time.time` 来模拟 TTL 过期。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/core/test_xpu_collector.py -v`
Expected: PASS（全部）。

- [ ] **Step 5: 提交**

```bash
git add python/lockbot/core/xpu_collector.py tests/core/test_xpu_collector.py
git commit -m "feat: concurrent node usage collection with TTL cache"
```

---

## Task 5: i18n 表头改名 + 7 列变体

**Files:**
- Modify: `python/lockbot/core/i18n/zh.py:67`
- Modify: `python/lockbot/core/i18n/en.py:66-67`（`query.cluster_usage_title` 之后插入）
- Test: `tests/core/test_xpu_collector.py`（i18n 不单测，靠 Task 6 渲染测试覆盖）

- [ ] **Step 1: 修改 zh.py 表头并新增 7 列键**

把 `python/lockbot/core/i18n/zh.py:67` 这一行：

```python
    "query.table_header": "| IP地址 | 节点状态 | 卡状态 | 使用者 | 剩余时间 |\n| --- | --- | --- | --- | --- |\n",
```

替换为：

```python
    "query.table_header": "| IP | 节点状态 | 卡状态 | lock同学 | 剩余时间 |\n| --- | --- | --- | --- | --- |\n",
    "query.table_header_xpu": (
        "| IP | 节点状态 | 卡状态 | lock同学 | 剩余时间 | 利用率 | 容器名 |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
    ),
```

- [ ] **Step 2: 在 en.py 补全两键**

在 `python/lockbot/core/i18n/en.py` 的 `"query.cluster_usage_title": "ℹ️ Cluster Usage Details\n",` 这一行之后插入：

```python
    "query.table_header": "| IP | Node | Card | Locked by | Remaining |\n| --- | --- | --- | --- | --- |\n",
    "query.table_header_xpu": (
        "| IP | Node | Card | Locked by | Remaining | Util | Container |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
    ),
```

- [ ] **Step 3: 运行现有测试确认无回归**

Run: `pytest tests/core/ -v`
Expected: PASS（现有用例不依赖旧表头字面量；若有依赖，在 Task 6 修复）。

- [ ] **Step 4: 提交**

```bash
git add python/lockbot/core/i18n/zh.py python/lockbot/core/i18n/en.py
git commit -m "feat: rename query header columns and add 7-column xpu variant"
```

---

## Task 6: `build_device_query` 支持 `xpu_usage`（7 列渲染）

**Files:**
- Modify: `python/lockbot/core/query_render.py:52-112`
- Test: `tests/core/test_device_query_render.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/core/test_device_query_render.py`：

```python
from lockbot.core.config import Config
from lockbot.core.query_render import build_device_query
from lockbot.core.xpu_collector import NodeUsage


def _state():
    return {
        "node1": [
            {"dev_id": 0, "status": "idle", "dev_model": "a800", "current_users": []},
            {"dev_id": 1, "status": "idle", "dev_model": "a800", "current_users": []},
        ],
    }


def _config():
    return Config({
        "BOT_TYPE": "DEVICE",
        "CLUSTER_CONFIGS": {"node1": {"ip": "10.0.0.1", "devices": ["a800", "a800"]}},
        "QUERY_TIP": "",
    })


def test_param_query_renders_5_columns():
    out = build_device_query(_state(), "u1", _config(), node_filter="node1")
    assert "| IP | 节点状态 | 卡状态 | lock同学 | 剩余时间 |" in out
    assert "利用率" not in out


def test_bare_at_renders_7_columns():
    usage = {"node1": NodeUsage(util=82.0, container="my_ctr")}
    out = build_device_query(_state(), "u1", _config(), xpu_usage=usage)
    assert "| IP | 节点状态 | 卡状态 | lock同学 | 剩余时间 | 利用率 | 容器名 |" in out
    assert "82.0%" in out
    assert "my_ctr" in out


def test_bare_at_failed_node_shows_na():
    usage = {"node1": NodeUsage(util=None, container="")}
    out = build_device_query(_state(), "u1", _config(), xpu_usage=usage)
    assert "N/A" in out


def test_bare_at_util_and_container_first_row_only():
    usage = {"node1": NodeUsage(util=10.0, container="c")}
    out = build_device_query(_state(), "u1", _config(), xpu_usage=usage)
    # util "10.0%" appears exactly once (node first row), not on every device row
    assert out.count("10.0%") == 1
    assert out.count("| c |") == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/core/test_device_query_render.py -v`
Expected: FAIL — `build_device_query()` 不接受 `xpu_usage`；7 列断言不通过。

- [ ] **Step 3: 实现 7 列渲染**

修改 `python/lockbot/core/query_render.py` 的 `build_device_query`。

签名（第 52 行）改为：

```python
def build_device_query(bot_state, user_id, config, node_filter=None, xpu_usage=None):
    """Build full markdown query text for a DEVICE bot.

    When xpu_usage is provided (bare-AT path), renders 7 columns with
    per-node utilization and occupying container name; otherwise 5 columns.
    """
```

表头行（第 75 行）改为：

```python
    header_key = "query.table_header_xpu" if xpu_usage is not None else "query.table_header"
    lines.append(t(header_key, config=config))
```

行渲染循环（第 96-110 行）改为：

```python
        first_row = True
        for is_idle, fields in rows:
            dev_state = _DEV_FREE if is_idle else _DEV_BUSY
            dev_cell = f"{fields['dev']} {dev_state}"
            if is_idle:
                user_cell = "--"
                dur_cell = "--"
            else:
                mode = fields["mode"].strip("()")
                user_cell = f"{fields['user']}（{mode}）".strip()
                dur_cell = fields["dur"] or "--"
            node_cell = _node_label(cluster_configs, node_key) if first_row else ""
            node_status_cell = node_state if first_row else ""
            if xpu_usage is not None:
                if first_row:
                    usage = xpu_usage.get(node_key, NodeUsage(util=None, container=""))
                    util_cell = f"{usage.util}%" if usage.util is not None else "N/A"
                    container_cell = usage.container or ""
                else:
                    util_cell = ""
                    container_cell = ""
                lines.append(_md_row(
                    node_cell, node_status_cell, dev_cell, user_cell, dur_cell,
                    util_cell, container_cell,
                ))
            else:
                lines.append(_md_row(node_cell, node_status_cell, dev_cell, user_cell, dur_cell))
            first_row = False
```

在 `query_render.py` 顶部 import 区（第 13 行 `from lockbot.core.utils ...` 之后）追加：

```python
from lockbot.core.xpu_collector import NodeUsage
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/core/test_device_query_render.py -v`
Expected: PASS（4 passed）。

- [ ] **Step 5: 提交**

```bash
git add python/lockbot/core/query_render.py tests/core/test_device_query_render.py
git commit -m "feat: 7-column device query with util/container on node first row"
```

---

## Task 7: `device_bot.query` 在纯 AT 路径采集

**Files:**
- Modify: `python/lockbot/core/device_bot.py:155-161`（`query` 方法）
- Modify: `python/lockbot/core/device_bot.py`（新增 `_node_ips` 私有方法）
- Test: `tests/core/test_device_bot.py`（追加）

- [ ] **Step 1: 写失败测试**

追加到 `tests/core/test_device_bot.py`（若结构不同，按既有 fixture 风格放置；核心是 mock 采集器并断言「带参不调用、纯 AT 调用」）：

```python
from unittest import mock

from lockbot.core import xpu_collector


def _make_device_bot():
    from lockbot.core.bot_instance import BotInstance
    return BotInstance({
        "BOT_TYPE": "DEVICE",
        "BOT_NAME": "t_dev",
        "CLUSTER_CONFIGS": {"node1": {"ip": "10.0.0.1", "devices": ["a800"]}},
        "QUERY_TIP": "",
    }).bot


def test_query_bare_at_triggers_collection():
    bot = _make_device_bot()
    with mock.patch.object(xpu_collector, "collect_node_usage",
                           return_value={"node1": xpu_collector.NodeUsage(50.0, "")}) as c:
        bot.query("u1")  # node_key=None → bare AT
    c.assert_called_once()


def test_query_with_node_does_not_collect():
    bot = _make_device_bot()
    with mock.patch.object(xpu_collector, "collect_node_usage") as c:
        bot.query("u1", "node1")  # parameterized → no SSH
    c.assert_not_called()


def test_node_ips_extracts_only_configured_ips():
    bot = _make_device_bot()
    assert bot._node_ips() == {"node1": "10.0.0.1"}
```

> 注：`BotInstance` 的构造与 `.bot` 属性按 `python/lockbot/core/bot_instance.py` 实际签名调整；若已有 device bot fixture，直接复用。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/core/test_device_bot.py -k "bare_at or does_not_collect or node_ips" -v`
Expected: FAIL — `_node_ips` 未定义；`query` 未调用采集器。

- [ ] **Step 3: 实现 — 修改 query + 新增 _node_ips**

在 `python/lockbot/core/device_bot.py` 顶部 import 区（第 13 行 `from lockbot.core.query_render import build_device_query` 之后）追加：

```python
from lockbot.core.query_render import _get_ip
from lockbot.core.xpu_collector import collect_node_usage
```

把 `query`（第 155-161 行）替换为：

```python
    def query(self, user_id, node_key=None):
        """
        query current usage

        On a bare AT (node_key is None), synchronously collect real GPU
        utilization + occupying container name for all configured nodes and
        render a 7-column table. Parameterized queries keep the 5-column table.
        """
        with self._lock:
            if node_key is None:
                node_ips = self._node_ips()
                xpu_usage = collect_node_usage(node_ips, self.config) if node_ips else None
            else:
                xpu_usage = None
            content = build_device_query(
                self.state.bot_state, user_id, self.config,
                node_filter=node_key, xpu_usage=xpu_usage,
            )
            return self.adapter.build_reply(content, [user_id], markdown=True)

    def _node_ips(self):
        """Return {node_key: ip} for nodes that have a real IP configured."""
        cluster_configs = self.config.get_val("CLUSTER_CONFIGS") or {}
        result = {}
        for node_key in self.state.bot_state:
            ip = _get_ip(cluster_configs, node_key)
            if ip:
                result[node_key] = ip
        return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/core/test_device_bot.py -k "bare_at or does_not_collect or node_ips" -v`
Expected: PASS（3 passed）。

- [ ] **Step 5: 提交**

```bash
git add python/lockbot/core/device_bot.py tests/core/test_device_bot.py
git commit -m "feat: device query collects GPU usage on bare-AT path only"
```

---

## Task 8: 全量回归 + lint

**Files:** 无（验证任务）

- [ ] **Step 1: 全量测试**

Run: `pytest`
Expected: 全部 PASS。若旧用例硬编码了 `"IP地址"` / `"使用者"` 表头字面量，按本计划新表头（`IP` / `lock同学`）更新断言。

- [ ] **Step 2: lint + format**

Run: `ruff check python/ tests/ && ruff format --check python/ tests/`
Expected: 无错误。如有 import 顺序问题，`ruff check --fix python/ tests/ && ruff format python/ tests/` 后重跑。

- [ ] **Step 3: 提交（如 lint 有改动）**

```bash
git add -A
git commit -m "chore: lint/format for GPU util query feature"
```

---

## 决策记录（实现者须知）

- **空 IP 兜底**：DEVICE bot 若没有任何节点配了 IP（`_node_ips()` 为空），纯 AT 也保持 5 列、不发 SSH。这避免给未配 IP 的旧部署平白增加两列 `N/A`。（spec 未显式要求，但属合理的非破坏性默认；如用户希望「只要纯 AT 就强制 7 列」，把 Task 7 的 `if node_ips else None` 去掉即可。）
- **利用率格式**：`f"{usage.util}%"`，`_parse_util` 已 `round(...,2)`，例如 `0.0%` / `82.0%`。
- **容器名「失败」与「无容器」一致**：两者都显示空（`""`），与 spec 一致。
- **取代旧 spec**：本特性取代 `2026-06-17-xpu-smi-realtime-usage`（后台守护线程 + root SSH）方案；旧 spec/plan 文件暂保留不删。

---

## Self-Review

- **Spec 覆盖**：触发条件（Task 7）✓；TTL 60s 缓存（Task 1+4）✓；DEVICE-only（Task 6/7 仅改 device 路径）✓；node→IP via CLUSTER_CONFIGS（Task 7 `_node_ips`）✓；两列利用率在前容器名在后（Task 5/6 表头与渲染）✓；复用 id_ed25519 SSH（Task 3 `_SSH_OPTS`）✓；失败显示 N/A + 空容器（Task 6 测试）✓；表头改名 IP/lock同学（Task 5）✓；5/7 列切换（Task 6）✓。
- **占位符扫描**：无 TBD/TODO，所有代码步骤含完整代码。
- **类型/命名一致**：`NodeUsage(util, container)`、`collect_node_usage`、`_collect_one`、`_parse_util`、`_parse_pid`、`_node_ips`、`xpu_usage` 在各 Task 间一致。
