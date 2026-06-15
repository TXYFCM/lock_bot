# 可配置的集群使用情况显示布局 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让管理员通过机器人高级配置（config_overrides）控制 query 输出的排序、空闲分组和单行模板，三种 bot（DEVICE/NODE/QUEUE）共用一套渲染引擎，默认即「紧凑 + 空闲置顶 + 按剩余时长升序」。

**Architecture:** 新建 `usage_render.py` 提供三个纯函数：`render_line`（模板渲染+换行strip+坏模板回退）、`sort_and_group`（排序与空闲分组）、`min_remaining`（节点最小剩余时长）。DEVICE/NODE/QUEUE 的 `_current_usage` 改为构造结构化字段字典后调用这套引擎。新增 4 个 config key 控制行为。

**Tech Stack:** Python 3.10+，pytest，ruff。无新依赖。

**测试环境说明：** 本仓库 import `six` 等依赖。如本机无 pytest/six，先建临时 venv：`python -m venv .venv_test && .venv_test/bin/pip install -q pytest six pycryptodome flask requests`，用 `PYTHONPATH=python .venv_test/bin/python -m pytest ...` 运行，完成后 `rm -rf .venv_test`。下文命令统一写作 `pytest`，请按此环境替换。

---

## File Structure

- **Create** `python/lockbot/core/usage_render.py` — 共享渲染引擎（render_line / sort_and_group / min_remaining）。三种 bot 共用。
- **Create** `tests/core/test_usage_render.py` — 引擎单元测试。
- **Modify** `python/lockbot/core/config.py` — 新增 4 个 `USAGE_*` config key。
- **Modify** `python/lockbot/core/device_usage_utils.py` — `render_device_lines` 产出字段字典；`get_current_usage` 改用引擎。
- **Modify** `python/lockbot/core/node_bot.py` — `_current_usage` 改用引擎。
- **Modify** `python/lockbot/core/queue_bot.py` — `_current_usage` 改用引擎，booking_list 保持独立渲染。
- **Modify** `python/lockbot/core/i18n/zh.py` / `en.py` — 移除 `device_usage.node_header`；`query.cluster_usage_title` 双 `\n` 改单 `\n`。
- **Modify** `tests/core/test_device_bot.py` — 更新依赖旧表头文案的断言。
- **Modify** `tests/core/test_device_usage_utils.py` — 适配 `render_device_lines` 新返回类型。

---

## Task 1: 新增 USAGE_* 配置项

**Files:**
- Modify: `python/lockbot/core/config.py` (在 `_CONFIG_SCHEMA` 末尾，`LANGUAGE` 项之后)
- Test: `tests/core/test_config.py`

- [ ] **Step 1: Write the failing test**

在 `tests/core/test_config.py` 末尾追加：

```python
def test_usage_layout_defaults():
    """USAGE_* config keys exist with compact-sort defaults."""
    from lockbot.core.config import Config

    cfg = Config({})
    assert cfg.get_val("USAGE_SORT") == "dur_asc"
    assert cfg.get_val("USAGE_GROUP") == "idle_first"
    assert cfg.get_val("USAGE_LINE_TEMPLATE") == "{node} {dev} {user}{mode} {dur}"
    assert cfg.get_val("USAGE_IDLE_TEMPLATE") == "{node} {dev} {status}"


def test_usage_layout_override():
    """USAGE_* keys are overridable via config_dict."""
    from lockbot.core.config import Config

    cfg = Config({"USAGE_SORT": "name", "USAGE_GROUP": "none"})
    assert cfg.get_val("USAGE_SORT") == "name"
    assert cfg.get_val("USAGE_GROUP") == "none"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_config.py::test_usage_layout_defaults -v`
Expected: FAIL — `get_val("USAGE_SORT")` returns `None`.

- [ ] **Step 3: Add the config keys**

在 `python/lockbot/core/config.py` 的 `_CONFIG_SCHEMA` 字典里，`"LANGUAGE": {...}` 项之后、字典闭合 `}` 之前插入：

```python
    "USAGE_SORT": {
        "default": "dur_asc",
        "description": "Usage display node sort: name / dur_asc / dur_desc",
        "env": False,
    },
    "USAGE_GROUP": {
        "default": "idle_first",
        "description": "Idle node grouping: none / idle_first / idle_last",
        "env": False,
    },
    "USAGE_LINE_TEMPLATE": {
        "default": "{node} {dev} {user}{mode} {dur}",
        "description": "str.format template for an occupied usage line",
        "env": False,
    },
    "USAGE_IDLE_TEMPLATE": {
        "default": "{node} {dev} {status}",
        "description": "str.format template for an idle usage line",
        "env": False,
    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_config.py::test_usage_layout_defaults tests/core/test_config.py::test_usage_layout_override -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add python/lockbot/core/config.py tests/core/test_config.py
git commit -m "feat: add USAGE_* config keys for layout control"
```

---

## Task 2: 渲染引擎 — min_remaining

**Files:**
- Create: `python/lockbot/core/usage_render.py`
- Test: `tests/core/test_usage_render.py`

- [ ] **Step 1: Write the failing test**

创建 `tests/core/test_usage_render.py`：

```python
import time

import pytest


@pytest.fixture(autouse=True)
def mock_time():
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("time.time", lambda: 1000000)
        yield


def _user(uid, dur, start=1000000):
    return {"user_id": uid, "start_time": start, "duration": dur}


def test_min_remaining_device_node():
    """Device node (list of devices): returns smallest remaining across all users."""
    from lockbot.core.usage_render import min_remaining

    node = [
        {"status": "exclusive", "current_users": [_user("a", 600)]},
        {"status": "exclusive", "current_users": [_user("b", 300)]},
        {"status": "idle", "current_users": []},
    ]
    assert min_remaining(node) == 300


def test_min_remaining_node_dict():
    """NODE/QUEUE node (single dict): returns smallest remaining across current_users."""
    from lockbot.core.usage_render import min_remaining

    node = {"status": "exclusive", "current_users": [_user("a", 600), _user("b", 900)]}
    assert min_remaining(node) == 600


def test_min_remaining_idle_returns_none():
    """Fully idle node returns None."""
    from lockbot.core.usage_render import min_remaining

    assert min_remaining([{"status": "idle", "current_users": []}]) is None
    assert min_remaining({"status": "idle", "current_users": [], "booking_list": []}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_usage_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lockbot.core.usage_render'`.

- [ ] **Step 3: Create usage_render.py with min_remaining**

创建 `python/lockbot/core/usage_render.py`：

```python
"""Shared usage-display rendering engine: sorting, grouping, and line templating.

Used by DeviceBot, NodeBot, and QueueBot so all three honor the same
USAGE_SORT / USAGE_GROUP / USAGE_*_TEMPLATE config knobs.
"""

import logging

from lockbot.core.utils import remaining_duration

logger = logging.getLogger(__name__)


def min_remaining(node_status):
    """Return the minimum remaining lock duration across a node's active users.

    Accepts either a DEVICE node (list of device dicts) or a NODE/QUEUE node
    (single dict). Returns None if the node has no active locks.
    """
    devices = node_status if isinstance(node_status, list) else [node_status]
    rem = None
    for dev in devices:
        if dev.get("status") != "idle":
            for user in dev.get("current_users", []):
                r = remaining_duration(user["start_time"], user["duration"])
                if rem is None or r < rem:
                    rem = r
    return rem
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_usage_render.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add python/lockbot/core/usage_render.py tests/core/test_usage_render.py
git commit -m "feat: usage_render.min_remaining"
```

---

## Task 3: 渲染引擎 — sort_and_group

**Files:**
- Modify: `python/lockbot/core/usage_render.py`
- Test: `tests/core/test_usage_render.py`

每个 `entry` 是 dict，约定字段：`order_index`（原始插入序，int）、`is_idle`（bool）、`min_remaining`（float|None）。`sort_and_group` 返回重排后的 entry 列表（稳定排序）。

- [ ] **Step 1: Write the failing test**

在 `tests/core/test_usage_render.py` 追加：

```python
def _entry(idx, is_idle, rem):
    return {"order_index": idx, "is_idle": is_idle, "min_remaining": rem}


def test_sort_name_keeps_original_order():
    from lockbot.core.usage_render import sort_and_group

    entries = [_entry(0, False, 600), _entry(1, True, None), _entry(2, False, 300)]
    out = sort_and_group(entries, "name", "none")
    assert [e["order_index"] for e in out] == [0, 1, 2]


def test_sort_dur_asc():
    from lockbot.core.usage_render import sort_and_group

    entries = [_entry(0, False, 600), _entry(1, False, 300), _entry(2, False, 900)]
    out = sort_and_group(entries, "dur_asc", "none")
    assert [e["order_index"] for e in out] == [1, 0, 2]


def test_sort_dur_desc():
    from lockbot.core.usage_render import sort_and_group

    entries = [_entry(0, False, 600), _entry(1, False, 300), _entry(2, False, 900)]
    out = sort_and_group(entries, "dur_desc", "none")
    assert [e["order_index"] for e in out] == [2, 0, 1]


def test_group_idle_first():
    """Idle nodes go to top; within each group dur_asc applies."""
    from lockbot.core.usage_render import sort_and_group

    entries = [_entry(0, False, 600), _entry(1, True, None), _entry(2, False, 300)]
    out = sort_and_group(entries, "dur_asc", "idle_first")
    assert [e["order_index"] for e in out] == [1, 2, 0]


def test_group_idle_last():
    from lockbot.core.usage_render import sort_and_group

    entries = [_entry(0, False, 600), _entry(1, True, None), _entry(2, False, 300)]
    out = sort_and_group(entries, "dur_asc", "idle_last")
    assert [e["order_index"] for e in out] == [2, 0, 1]


def test_unknown_sort_falls_back_to_name():
    from lockbot.core.usage_render import sort_and_group

    entries = [_entry(0, False, 600), _entry(1, False, 300)]
    out = sort_and_group(entries, "bogus", "none")
    assert [e["order_index"] for e in out] == [0, 1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_usage_render.py::test_sort_dur_asc -v`
Expected: FAIL — `ImportError: cannot import name 'sort_and_group'`.

- [ ] **Step 3: Add sort_and_group**

在 `python/lockbot/core/usage_render.py` 末尾追加：

```python
def sort_and_group(entries, sort_mode, group_mode):
    """Reorder node entries by sort_mode, then partition by group_mode.

    entry dict must contain: order_index (int), is_idle (bool),
    min_remaining (float|None). Sorting is stable; unknown modes fall back
    to insertion order / no grouping.
    """

    def sort_key(e):
        rem = e["min_remaining"]
        # idle nodes (rem is None) sort last among non-grouped dur sorts
        rem_val = rem if rem is not None else float("inf")
        if sort_mode == "dur_asc":
            return (rem_val, e["order_index"])
        if sort_mode == "dur_desc":
            return (-rem_val, e["order_index"])
        # "name" or unknown → original order
        return (e["order_index"],)

    ordered = sorted(entries, key=sort_key)

    if group_mode == "idle_first":
        return [e for e in ordered if e["is_idle"]] + [e for e in ordered if not e["is_idle"]]
    if group_mode == "idle_last":
        return [e for e in ordered if not e["is_idle"]] + [e for e in ordered if e["is_idle"]]
    # "none" or unknown → no grouping
    return ordered
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_usage_render.py -v`
Expected: PASS (all sort/group tests pass).

- [ ] **Step 5: Commit**

```bash
git add python/lockbot/core/usage_render.py tests/core/test_usage_render.py
git commit -m "feat: usage_render.sort_and_group"
```

---

## Task 4: 渲染引擎 — render_line（模板渲染+容错）

**Files:**
- Modify: `python/lockbot/core/usage_render.py`
- Test: `tests/core/test_usage_render.py`

- [ ] **Step 1: Write the failing test**

在 `tests/core/test_usage_render.py` 追加：

```python
def test_render_line_basic():
    from lockbot.core.usage_render import render_line

    fields = {"node": "node1", "dev": "dev0-7", "user": "alice", "mode": "(独占)", "dur": "2.7 小时"}
    out = render_line("{node} {dev} {user}{mode} {dur}", fields, "{node} {user}")
    assert out == "node1 dev0-7 alice(独占) 2.7 小时"


def test_render_line_strips_newlines():
    """Embedded \\n / \\r in template are removed before formatting."""
    from lockbot.core.usage_render import render_line

    fields = {"node": "n1", "user": "a"}
    out = render_line("{node}\n{user}\r", fields, "{node}")
    assert "\n" not in out and "\r" not in out
    assert out == "n1a"


def test_render_line_alignment_spec():
    """Python format spec :<N applies padding."""
    from lockbot.core.usage_render import render_line

    out = render_line("{dev:<8}|", {"dev": "dev0-7"}, "{dev}")
    assert out == "dev0-7  |"


def test_render_line_bad_template_falls_back():
    """Unknown placeholder or bad syntax → fallback template, no exception."""
    from lockbot.core.usage_render import render_line

    fields = {"node": "n1", "user": "a", "dev": "", "mode": "", "dur": "", "status": ""}
    # unknown placeholder {foo}
    out = render_line("{foo} {node}", fields, "{node} {user}")
    assert out == "n1 a"
    # broken syntax (unbalanced brace)
    out2 = render_line("{node", fields, "{node} {user}")
    assert out2 == "n1 a"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_usage_render.py::test_render_line_basic -v`
Expected: FAIL — `ImportError: cannot import name 'render_line'`.

- [ ] **Step 3: Add render_line**

在 `python/lockbot/core/usage_render.py` 末尾追加：

```python
def render_line(template, fields, fallback_template, *, bot_name=None):
    """Render one usage line from a str.format template.

    Newlines in the template are stripped (one template = one line). On a
    broken template (missing field / bad syntax) the fallback_template is
    used instead and a WARNING is logged — a misconfigured template must
    never break the whole usage output.
    """
    clean = template.replace("\r", "").replace("\n", "")
    try:
        return clean.format(**fields)
    except (KeyError, ValueError, IndexError) as e:
        logger.warning(
            "Bad usage template %r for bot %s (%s); using fallback",
            template,
            bot_name or "?",
            e,
        )
        return fallback_template.format(**fields)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_usage_render.py -v`
Expected: PASS (all render_line tests pass).

- [ ] **Step 5: Lint + commit**

```bash
ruff check python/lockbot/core/usage_render.py
ruff format python/lockbot/core/usage_render.py
git add python/lockbot/core/usage_render.py tests/core/test_usage_render.py
git commit -m "feat: usage_render.render_line with newline-strip and fallback"
```

---

## Task 5: i18n — 移除 node_header，标题改单换行

**Files:**
- Modify: `python/lockbot/core/i18n/zh.py:63,65`
- Modify: `python/lockbot/core/i18n/en.py:67,69`
- Test: (随后任务的集成测试覆盖；本任务仅做数据改动)

- [ ] **Step 1: Edit zh.py**

在 `python/lockbot/core/i18n/zh.py`：
- 将 `"query.cluster_usage_title": "ℹ️【集群使用详情】\n\n",` 改为 `"query.cluster_usage_title": "ℹ️【集群使用详情】\n",`
- 删除整行 `"device_usage.node_header": "{node_key}使用情况:\n",`

- [ ] **Step 2: Edit en.py**

在 `python/lockbot/core/i18n/en.py`：
- 将 `"query.cluster_usage_title": "ℹ️ Cluster Usage Details\n\n",` 改为 `"query.cluster_usage_title": "ℹ️ Cluster Usage Details\n",`
- 删除整行 `"device_usage.node_header": "{node_key} usage:\n",`

- [ ] **Step 3: Verify no remaining references**

Run: `grep -rn "node_header" python/ tests/`
Expected: 无输出（除本计划文档外）。若 `tests/` 或 `python/` 仍有引用，记录待后续任务处理。

- [ ] **Step 4: Commit**

```bash
git add python/lockbot/core/i18n/zh.py python/lockbot/core/i18n/en.py
git commit -m "refactor: drop device_usage.node_header, compact usage title"
```

---

## Task 6: DEVICE — render_device_lines 产出字段字典

把 `render_device_lines` 从「返回字符串列表」改为「返回 (is_idle, fields_dict) 列表」，由调用方决定模板与拼接。这解耦了字段提取与字符串拼装。

**Files:**
- Modify: `python/lockbot/core/device_usage_utils.py:113-157` (`render_device_lines`)
- Test: `tests/core/test_device_usage_utils.py`

- [ ] **Step 1: Update the existing tests to new return type**

`render_device_lines` 新返回类型为 `list[tuple[bool, dict]]`，dict 含键 `node`(始终 ""，节点名由上层填) / `dev` / `model` / `user` / `mode` / `dur` / `status`。更新 `tests/core/test_device_usage_utils.py` 中所有断言：把 `lines = render_device_lines(...)` 后对字符串的检查改为对 fields 的检查。

定义一个本地 helper 放在 `tests/core/test_device_usage_utils.py` 顶部（import 之后）：

```python
def _texts(rows):
    """Flatten render_device_lines rows into joined field strings for assertions."""
    out = []
    for _is_idle, f in rows:
        out.append(" ".join(str(f.get(k, "")) for k in ("dev", "model", "user", "mode", "dur", "status")))
    return out
```

然后将测试中形如 `lines = render_device_lines(node_status, locked, idle)` 之后的断言统一改为基于 `_texts(lines)`。例如：

```python
def test_group_locked_and_idle_and_render(make_device):
    node_status = [make_device(i, status="exclusive", user_id="张三", duration=600, dev_model="a800") for i in range(8)]
    locked = group_locked_devices(node_status)
    idle = group_idle_devices(node_status, exclude_indices={i for _, ids in locked for i in ids})
    rows = render_device_lines(node_status, locked, idle)
    texts = _texts(rows)
    assert any("张三" in t for t in texts)
    assert any("dev0-7" in t for t in texts)
    assert not any("a800" in t for t in texts)  # homogeneous → no model
```

对其余每个调用 `render_device_lines` 的测试（`test_mixed_users_and_models`、`test_shared_device_multiple_users`、`test_locked_devices_different_timestamps`、`test_idle_devices_split_if_non_continuous`、`test_mixed_lock_conditions`、`test_idle_devices_same_model_split`、`test_shared_device_multiple_users_with_continuous_devices`、`test_shared_device_multiple_users_with_modified_timestamp`、`test_mixed_shared_users_and_models`）做同样替换：`lines = render_device_lines(...)` → `rows = render_device_lines(...)`，`lines` → `_texts(rows)`。「空闲」断言改为检查 `status` 字段含 `空闲`（用 `_texts` 即可，因为 status 已并入）。`get_current_usage` 相关的两个测试（`test_get_current_usage_with_all_options`、`test_get_current_usage_hetero_warning`）**不改**（它们断言的是最终字符串，由 Task 7 保证）。

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_device_usage_utils.py -v`
Expected: FAIL — 多个测试因 `render_device_lines` 仍返回字符串而 `_texts` 解包失败（`too many values to unpack` 或 `'str' object has no attribute 'get'`）。

- [ ] **Step 3: Rewrite render_device_lines to return field dicts**

将 `python/lockbot/core/device_usage_utils.py` 的 `render_device_lines`（约 113-157 行）整体替换为：

```python
def render_device_lines(node_status, grouped_usage, idle_groups, config=None):
    """
    Generate (is_idle, fields) rows from locked and idle device groups.

    Each row is a tuple (is_idle: bool, fields: dict). fields keys:
    node (always "" — filled by caller), dev, model, user, mode, dur, status.
    """
    rows = []
    show_model = _is_heterogeneous(node_status)
    all_segments = []

    for key, dev_ids in grouped_usage:
        model, status, user_keys_sorted = key
        all_segments.append((dev_ids[0], "lock", (user_keys_sorted, status, dev_ids, model)))

    for group, model in idle_groups:
        all_segments.append((group[0], "idle", (group, model)))

    for _, tag, data in sorted(all_segments, key=lambda x: x[0]):
        if tag == "lock":
            user_keys_sorted, status, dev_ids, model = data
            for user_idx, (user_id, start_time, duration) in enumerate(user_keys_sorted):
                if len(dev_ids) > 1:
                    dev_range = f"dev{dev_ids[0]}-{dev_ids[-1]}"
                else:
                    dev_range = f"dev{dev_ids[0]}"
                dev_range = dev_range if user_idx == 0 else ""
                model_str = f"{model}" if show_model and user_idx == 0 else ""
                duration_str = format_duration(remaining_duration(start_time, duration), config=config)
                rows.append(
                    (
                        False,
                        {
                            "node": "",
                            "dev": dev_range,
                            "model": model_str,
                            "user": user_id,
                            "mode": format_access_mode(status, config=config),
                            "dur": duration_str,
                            "status": "",
                        },
                    )
                )
        elif tag == "idle":
            group, model = data
            if len(group) > 1:
                dev_range = f"dev{group[0]}-{group[-1]}"
            else:
                dev_range = f"dev{group[0]}"
            model_str = f"{model}" if show_model else ""
            rows.append(
                (
                    True,
                    {
                        "node": "",
                        "dev": dev_range,
                        "model": model_str,
                        "user": "",
                        "mode": "",
                        "dur": "",
                        "status": t("status.idle", config=config),
                    },
                )
            )
    return rows
```

注意：`format_usage_line` 函数不再被 `render_device_lines` 调用，但保留它（其它地方或测试可能引用），下一步检查。

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_device_usage_utils.py -v`
Expected: 除 `get_current_usage` 两个集成测试外全部 PASS。`get_current_usage` 两测试此时可能仍 PASS（旧 `get_current_usage` 还在用旧逻辑——见下步说明）。

实际上 `get_current_usage` 仍调用 `"\n".join(lines)`，而 `lines` 现在是 tuple 列表 → 会 FAIL。这是预期的，将在 Task 7 修复。本步只要求 `render_device_lines` 直接相关测试通过。若 `get_current_usage` 测试报错，记录并继续 Task 7。

- [ ] **Step 5: Commit**

```bash
git add python/lockbot/core/device_usage_utils.py tests/core/test_device_usage_utils.py
git commit -m "refactor: render_device_lines returns field dicts"
```

---

## Task 7: DEVICE — get_current_usage 接入引擎

**Files:**
- Modify: `python/lockbot/core/device_usage_utils.py:160-187` (`get_current_usage`)
- Modify: `tests/core/test_device_usage_utils.py` (新增集成断言)

- [ ] **Step 1: Write the failing integration test**

在 `tests/core/test_device_usage_utils.py` 末尾追加（依赖 `make_device`、`mock_time` fixture）：

```python
def test_get_current_usage_compact_and_sorted(make_device):
    """Default layout: no per-node header, single newline, idle first, dur_asc."""
    from lockbot.core.config import Config

    cfg = Config({})
    bot_state = {
        "node1": [make_device(i, status="exclusive", user_id="alice", duration=600) for i in range(2)],
        "node2": [make_device(i, status="exclusive", user_id="bob", duration=300) for i in range(2)],
        "idle1": [make_device(i, status="idle") for i in range(2)],
    }
    out = get_current_usage(None, bot_state, {}, config=cfg)
    # No legacy per-node header
    assert "使用情况" not in out
    # No blank lines between nodes
    assert "\n\n" not in out.rstrip("\n")
    lines = [ln for ln in out.split("\n") if ln.strip()]
    # idle first, then node2 (300s) before node1 (600s)
    assert lines[0].startswith("idle1")
    assert lines[1].startswith("node2")
    assert lines[2].startswith("node1")


def test_get_current_usage_custom_template(make_device):
    """Custom occupied template is honored."""
    from lockbot.core.config import Config

    cfg = Config({"USAGE_LINE_TEMPLATE": "[{node}] {user} {dur}", "USAGE_GROUP": "none", "USAGE_SORT": "name"})
    bot_state = {"node1": [make_device(0, status="exclusive", user_id="alice", duration=600)]}
    out = get_current_usage(None, bot_state, {}, config=cfg)
    assert "[node1] alice" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_device_usage_utils.py::test_get_current_usage_compact_and_sorted -v`
Expected: FAIL — 旧 `get_current_usage` 用 `"\n".join(lines)` 且 `lines` 现为 tuple，报错或断言不符。

- [ ] **Step 3: Rewrite get_current_usage**

将 `python/lockbot/core/device_usage_utils.py` 顶部 import 补充引擎：

```python
from lockbot.core.usage_render import min_remaining, render_line, sort_and_group
```

将 `get_current_usage`（约 160-187 行）整体替换为：

```python
def get_current_usage(node_filter, bot_state, monitor_status, config=None):
    """
    Render device usage. Layout controlled by USAGE_SORT / USAGE_GROUP /
    USAGE_LINE_TEMPLATE / USAGE_IDLE_TEMPLATE on the bot config.
    """
    line_tpl = config.get_val("USAGE_LINE_TEMPLATE") if config else "{node} {dev} {user}{mode} {dur}"
    idle_tpl = config.get_val("USAGE_IDLE_TEMPLATE") if config else "{node} {dev} {status}"
    sort_mode = config.get_val("USAGE_SORT") if config else "dur_asc"
    group_mode = config.get_val("USAGE_GROUP") if config else "idle_first"
    bot_name = config.get_val("BOT_NAME") if config else None
    fb_line = "{node} {dev} {user}{mode} {dur}"
    fb_idle = "{node} {dev} {status}"

    # Build one entry per node, each carrying its rendered field-dict rows.
    entries = []
    order = 0
    for node_key, node_status in bot_state.items():
        if not (
            node_filter is None
            or node_key == node_filter
            or (isinstance(node_filter, list) and node_key in node_filter)
        ):
            continue
        grouped_usage = group_locked_devices(node_status)
        shown_indices = set()
        for _, dev_ids in grouped_usage:
            shown_indices.update(dev_ids)
        idle_groups = group_idle_devices(node_status, shown_indices)
        device_rows = render_device_lines(node_status, grouped_usage, idle_groups, config=config)
        rem = min_remaining(node_status)
        entries.append(
            {
                "order_index": order,
                "is_idle": rem is None,
                "min_remaining": rem,
                "node_key": node_key,
                "rows": device_rows,
            }
        )
        order += 1

    ordered = sort_and_group(entries, sort_mode, group_mode)

    usage_info = ""
    for entry in ordered:
        node_key = entry["node_key"]
        first = True
        for is_idle, fields in entry["rows"]:
            fields = dict(fields)
            fields["node"] = node_key if first else " " * len(node_key)
            tpl, fb = (idle_tpl, fb_idle) if is_idle else (line_tpl, fb_line)
            usage_info += render_line(tpl, fields, fb, bot_name=bot_name).rstrip() + "\n"
            first = False

    if node_filter:
        keys = node_filter if isinstance(node_filter, list) else [node_filter]
        if any(_is_heterogeneous(bot_state.get(k, [])) for k in keys):
            usage_info += t("device_usage.hetero_warning", config=config, node_key=node_filter)

    return usage_info
```

说明：续行的 `{node}` 用等宽空格占位（保持设备列对齐），而非空串——与原多用户续行缩进语义一致。

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_device_usage_utils.py -v`
Expected: PASS (全部，含两个新集成测试与原 hetero/all_options 测试)。

- [ ] **Step 5: Lint + commit**

```bash
ruff check python/lockbot/core/device_usage_utils.py
ruff format python/lockbot/core/device_usage_utils.py
git add python/lockbot/core/device_usage_utils.py tests/core/test_device_usage_utils.py
git commit -m "feat: DEVICE usage honors USAGE_* layout config"
```

---

## Task 8: 修复 test_device_bot 旧表头断言

**Files:**
- Modify: `tests/core/test_device_bot.py:119-122` (`test_query`)

- [ ] **Step 1: Update the assertion**

将 `tests/core/test_device_bot.py` 的 `test_query` 替换为：

```python
def test_query(bot):
    """Test query."""
    result = bot.query("user1")
    content = result["message"]["body"][0]["content"]
    assert "message" in result and "集群使用详情" in content
    # Compact layout: node_key prefixes the device line (no separate header)
    assert "test dev" in content
```

- [ ] **Step 2: Run the full device_bot suite**

Run: `pytest tests/core/test_device_bot.py -v`
Expected: PASS (全部)。若其它测试断言了 `使用情况:` 旧表头，按相同方式改为检查 `集群使用详情` 与 `<node> dev` 前缀。

- [ ] **Step 3: Commit**

```bash
git add tests/core/test_device_bot.py
git commit -m "test: update device_bot query assertions for compact layout"
```

---

## Task 9: NODE — _current_usage 接入引擎

**Files:**
- Modify: `python/lockbot/core/node_bot.py:394-422` (`_current_usage`)
- Test: `tests/core/test_node_bot.py`

- [ ] **Step 1: Write the failing test**

在 `tests/core/test_node_bot.py` 末尾追加（查阅文件顶部 fixture，bot fixture 名通常为 `bot`；若不同请对应调整）：

```python
def test_node_usage_compact_sorted_default():
    """NODE usage: idle first, occupied by dur_asc, single newlines, no header."""
    import time

    from lockbot.core.node_bot import NodeBot

    cfg = {"BOT_NAME": "t", "CLUSTER_CONFIGS": ["n1", "n2", "n3"]}
    b = NodeBot(config_dict=cfg)
    now = int(time.time())
    b.state.bot_state = {
        "n1": {"status": "exclusive", "current_users": [{"user_id": "alice", "start_time": now, "duration": 600}], "booking_list": []},
        "n2": {"status": "exclusive", "current_users": [{"user_id": "bob", "start_time": now, "duration": 300}], "booking_list": []},
        "n3": {"status": "idle", "current_users": [], "booking_list": []},
    }
    out = b._current_usage()
    assert "使用情况" not in out
    lines = [ln for ln in out.split("\n") if ln.strip()]
    assert lines[0].startswith("n3")   # idle first
    assert lines[1].startswith("n2")   # 300s
    assert lines[2].startswith("n1")   # 600s
```

注意：`NodeBot(config_dict=...)` 会触发 `InfoflowAdapter` 等 import，需要 `six` 等依赖在测试 venv 中。

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_node_bot.py::test_node_usage_compact_sorted_default -v`
Expected: FAIL — 旧 `_current_usage` 带 header / 不排序，`lines[0]` 不以 `n3` 开头。

- [ ] **Step 3: Rewrite NodeBot._current_usage**

查看 `python/lockbot/core/node_bot.py` 顶部已 import 的符号（`format_access_mode`、`format_duration`、`remaining_duration`、`t`）。在文件顶部 import 区追加：

```python
from lockbot.core.usage_render import min_remaining, render_line, sort_and_group
```

将 `_current_usage`（约 394-422 行）整体替换为：

```python
    def _current_usage(self, node_filter=None):
        """Render NODE usage honoring USAGE_* layout config."""
        line_tpl = self.config.get_val("USAGE_LINE_TEMPLATE")
        idle_tpl = self.config.get_val("USAGE_IDLE_TEMPLATE")
        sort_mode = self.config.get_val("USAGE_SORT")
        group_mode = self.config.get_val("USAGE_GROUP")
        bot_name = self.config.get_val("BOT_NAME")
        fb_line = "{node} {dev} {user}{mode} {dur}"
        fb_idle = "{node} {dev} {status}"

        entries = []
        order = 0
        for node_key, node_status in self.state.bot_state.items():
            if node_filter is not None and node_key != node_filter:
                continue
            rem = min_remaining(node_status)
            rows = []
            if node_status["status"] == "idle":
                rows.append(
                    (
                        True,
                        {"node": "", "dev": "", "model": "", "user": "", "mode": "", "dur": "",
                         "status": t("status.idle", config=self.config)},
                    )
                )
            else:
                for user_idx, user_info in enumerate(node_status["current_users"]):
                    duration = format_duration(
                        remaining_duration(user_info["start_time"], user_info["duration"]), config=self.config
                    )
                    rows.append(
                        (
                            False,
                            {
                                "node": "",
                                "dev": "",
                                "model": "",
                                "user": user_info["user_id"] if user_idx == 0 or True else "",
                                "mode": format_access_mode(node_status["status"], config=self.config),
                                "dur": duration,
                                "status": "",
                            },
                        )
                    )
            entries.append(
                {"order_index": order, "is_idle": rem is None, "min_remaining": rem,
                 "node_key": node_key, "rows": rows}
            )
            order += 1

        ordered = sort_and_group(entries, sort_mode, group_mode)

        usage_info = ""
        for entry in ordered:
            node_key = entry["node_key"]
            first = True
            for is_idle, fields in entry["rows"]:
                fields = dict(fields)
                fields["node"] = node_key if first else " " * len(node_key)
                tpl, fb = (idle_tpl, fb_idle) if is_idle else (line_tpl, fb_line)
                usage_info += render_line(tpl, fields, fb, bot_name=bot_name).rstrip() + "\n"
                first = False
        return usage_info
```

说明：NODE 没有设备维度，`{dev}`/`{model}` 为空。每个 current_user 一行，续行 `{node}` 用等宽空格占位。

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_node_bot.py -v`
Expected: PASS（含新测试；原 `集群使用详情` 断言不受影响。若有测试断言节点间空行或旧格式，按紧凑布局调整）。

- [ ] **Step 5: Lint + commit**

```bash
ruff check python/lockbot/core/node_bot.py
ruff format python/lockbot/core/node_bot.py
git add python/lockbot/core/node_bot.py tests/core/test_node_bot.py
git commit -m "feat: NODE usage honors USAGE_* layout config"
```

---

## Task 10: QUEUE — _current_usage 接入引擎（保留 booking_list）

**Files:**
- Modify: `python/lockbot/core/queue_bot.py:465-521` (`_current_usage`)
- Test: `tests/core/test_queue_bot.py`

QueueBot 多了排队列表（`booking_list`）。模板只覆盖 current_users 行；排队列表沿用原 `label.queue_list` / `label.queue_item` i18n 逻辑，**接在该节点最后一行之后**。

- [ ] **Step 1: Write the failing test**

在 `tests/core/test_queue_bot.py` 末尾追加：

```python
def test_queue_usage_compact_and_booking_preserved():
    """QUEUE usage: compact+sorted, and booking_list still rendered after node."""
    import time

    from lockbot.core.queue_bot import QueueBot

    cfg = {"BOT_NAME": "t", "CLUSTER_CONFIGS": ["n1", "n2"]}
    b = QueueBot(config_dict=cfg)
    now = int(time.time())
    b.state.bot_state = {
        "n1": {
            "status": "exclusive",
            "current_users": [{"user_id": "alice", "start_time": now, "duration": 600}],
            "booking_list": [{"user_id": "carol", "start_time": now, "duration": 1200}],
        },
        "n2": {"status": "idle", "current_users": [], "booking_list": []},
    }
    out = b._current_usage()
    assert "使用情况" not in out
    assert "alice" in out
    # booking list rendered
    assert "carol" in out
    # idle node n2 comes first
    lines = [ln for ln in out.split("\n") if ln.strip()]
    assert lines[0].startswith("n2")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_queue_bot.py::test_queue_usage_compact_and_booking_preserved -v`
Expected: FAIL — 旧 `_current_usage` 不排序/带空行，`lines[0]` 不以 `n2` 开头。

- [ ] **Step 3: Rewrite QueueBot._current_usage**

在 `python/lockbot/core/queue_bot.py` 顶部 import 区追加：

```python
from lockbot.core.usage_render import min_remaining, render_line, sort_and_group
```

将 `_current_usage`（约 465-521 行）整体替换为：

```python
    def _current_usage(self, node_filter=None):
        """Render QUEUE usage honoring USAGE_* config; booking_list rendered per-node."""
        line_tpl = self.config.get_val("USAGE_LINE_TEMPLATE")
        idle_tpl = self.config.get_val("USAGE_IDLE_TEMPLATE")
        sort_mode = self.config.get_val("USAGE_SORT")
        group_mode = self.config.get_val("USAGE_GROUP")
        bot_name = self.config.get_val("BOT_NAME")
        fb_line = "{node} {dev} {user}{mode} {dur}"
        fb_idle = "{node} {dev} {status}"

        def _booking_text(node_status):
            booking_list = node_status.get("booking_list", [])
            if not booking_list:
                return ""
            text = t("label.queue_list", config=self.config)
            current_locked_time = 0
            for user_info in node_status.get("current_users", []):
                remain = remaining_duration(user_info["start_time"], user_info["duration"])
                if remain > current_locked_time:
                    current_locked_time = remain
            accumulated_wait = current_locked_time
            for idx, booking_user in enumerate(booking_list):
                text += t(
                    "label.queue_item",
                    config=self.config,
                    index=idx + 1,
                    user_id=booking_user["user_id"],
                    duration=format_duration(booking_user["duration"], config=self.config),
                    wait_time=format_duration(accumulated_wait, config=self.config),
                )
                accumulated_wait += booking_user["duration"]
            return text

        entries = []
        order = 0
        for node_key, node_status in self.state.bot_state.items():
            if node_filter is not None and node_key != node_filter:
                continue
            rem = min_remaining(node_status)
            rows = []
            if node_status["status"] == "idle":
                rows.append(
                    (
                        True,
                        {"node": "", "dev": "", "model": "", "user": "", "mode": "", "dur": "",
                         "status": t("status.idle", config=self.config)},
                    )
                )
            else:
                for user_info in node_status["current_users"]:
                    duration = format_duration(
                        remaining_duration(user_info["start_time"], user_info["duration"]), config=self.config
                    )
                    rows.append(
                        (
                            False,
                            {"node": "", "dev": "", "model": "", "user": user_info["user_id"],
                             "mode": "", "dur": duration, "status": ""},
                        )
                    )
            entries.append(
                {"order_index": order, "is_idle": rem is None, "min_remaining": rem,
                 "node_key": node_key, "rows": rows, "booking": _booking_text(node_status)}
            )
            order += 1

        ordered = sort_and_group(entries, sort_mode, group_mode)

        usage_info = ""
        for entry in ordered:
            node_key = entry["node_key"]
            first = True
            for is_idle, fields in entry["rows"]:
                fields = dict(fields)
                fields["node"] = node_key if first else " " * len(node_key)
                tpl, fb = (idle_tpl, fb_idle) if is_idle else (line_tpl, fb_line)
                usage_info += render_line(tpl, fields, fb, bot_name=bot_name).rstrip() + "\n"
                first = False
            if entry["booking"]:
                usage_info += entry["booking"]
        return usage_info
```

说明：QueueBot 原 current_users 行不带 access_mode 后缀（原代码 `f"{node_name} {uid}  {duration}"`），故 `mode` 留空，与原行为一致。`label.queue_item` 模板里已含换行，沿用原文案。

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_queue_bot.py -v`
Expected: PASS（含新测试与原有 `集群使用详情`/`空闲`/`排队` 相关断言。原 `test_queue_bot.py:222` 断言空闲节点含「空闲」、不含「排队」——idle 行模板含 `{status}`=空闲、无 booking，满足。原 906 行正则匹配 `资源已空闲...` 属其它路径不受影响）。

- [ ] **Step 5: Lint + commit**

```bash
ruff check python/lockbot/core/queue_bot.py
ruff format python/lockbot/core/queue_bot.py
git add python/lockbot/core/queue_bot.py tests/core/test_queue_bot.py
git commit -m "feat: QUEUE usage honors USAGE_* layout config, booking preserved"
```

---

## Task 11: 全量回归 + 清理

**Files:** 无新增；验证整体。

- [ ] **Step 1: Run the whole core suite**

Run: `pytest tests/core/ -v`
Expected: 全绿。重点关注 test_device_bot / test_node_bot / test_queue_bot / test_device_usage_utils / test_usage_render / test_config。

- [ ] **Step 2: Run the backend suite (webhook/bot lifecycle unaffected but verify)**

Run: `pytest tests/ -q`
Expected: 全绿（backend 测试不依赖显示布局，应不受影响）。

- [ ] **Step 3: Check for dead code**

Run: `grep -rn "format_usage_line\|node_header" python/ tests/`
Expected: `node_header` 无残留（除文档）。若 `format_usage_line` 已无任何引用，从 `device_usage_utils.py` 删除该函数并重跑 `pytest tests/core/test_device_usage_utils.py`；若仍被测试引用则保留。

- [ ] **Step 4: Final lint**

Run: `ruff check python/ tests/ && ruff format --check python/ tests/`
Expected: All checks passed.

- [ ] **Step 5: Manual smoke (optional but recommended)**

用一段脚本复现你最初的截图场景，确认 DEVICE 默认输出符合预期（紧凑、空闲置顶、时长升序、无表头无空行）。Run:

```bash
PYTHONPATH=python python -c "
from lockbot.core.i18n import t
from lockbot.core.config import Config
from lockbot.core.device_usage_utils import get_current_usage
import time
now=int(time.time())
def dev(i,user=None,dur=0,status='idle'):
    return {'dev_id':i,'status':status,'dev_model':'a800','current_users':([{'user_id':user,'start_time':now,'duration':dur}] if user else [])}
state={
 'node1':[dev(i,'jiaqinghao',int(4.5*3600),'exclusive') for i in range(8)],
 'node6':[dev(i,'guhangsong',int(6.0*3600),'exclusive') for i in range(8)],
 'node2':[dev(i,'anguo',int(2.7*3600),'exclusive') for i in range(8)],
 'bdc28':[dev(i) for i in range(8)],
}
print(t('query.cluster_usage_title') + get_current_usage(None, state, {}, config=Config({})))
"
```

Expected: 标题下空闲 `bdc28` 在首行，随后 node2(2.7)→node1(4.5)→node6(6.0)，无 `使用情况:` 表头，无空行。

- [ ] **Step 6: Commit any cleanup**

```bash
git add -A
git commit -m "chore: usage layout cleanup and regression pass"
```

---

## Self-Review notes

- **Spec coverage:** 配置项(T1)、min_remaining(T2)、sort_and_group(T3)、render_line+容错(T4)、i18n表头移除(T5)、DEVICE(T6-7)、NODE(T9)、QUEUE+booking(T10)、回归(T11)、默认值复现紧凑排序(T7 集成测试)、换行strip/对齐(T4)、坏模板回退(T4)——均有对应任务。
- **前端：** spec 明确无需改动（复用 config_overrides 通用输入），故无前端任务。
- **类型一致：** entry dict 字段（order_index/is_idle/min_remaining/node_key/rows[/booking]）在 T3/T7/T9/T10 一致；fields dict 键（node/dev/model/user/mode/dur/status）在 T6/T7/T9/T10 一致；引擎函数签名 `render_line(template, fields, fallback_template, *, bot_name)`、`sort_and_group(entries, sort_mode, group_mode)`、`min_remaining(node_status)` 全程一致。
- **NODE 续行 user 字段：** T9 中 `user_info["user_id"] if user_idx == 0 or True else ""` 等价于始终取 user_id（每个 current_user 自己一行，user 都应显示），写法保留以示意；执行时可简化为 `user_info["user_id"]`。
