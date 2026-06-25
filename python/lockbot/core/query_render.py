"""Markdown table rendering for query output."""

import time
from datetime import datetime

from lockbot.core.device_usage_utils import (
    group_idle_devices,
    group_locked_devices,
    render_device_lines,
)
from lockbot.core.i18n import t
from lockbot.core.usage_render import min_remaining
from lockbot.core.utils import format_access_mode, format_duration, remaining_duration

# Status badges are derived from GPU memory utilization (decoupled from lock state):
#   mem  > MEM_BUSY_THRESHOLD  -> BUSY (red)
#   mem <= MEM_BUSY_THRESHOLD  -> FREE (green)
#   mem is None (not collected) -> N/A (gray)
_STATUS_FREE = '<font color="green">FREE</font>'
_STATUS_BUSY = '<font color="red">BUSY</font>'
_STATUS_NA = '<font color="gray">N/A</font>'
# lock同学 column when nobody holds a lock (decoupled from the status badge).
_UNLOCK = '<font color="green">UNLOCK</font>'
# NODE bot: lock同学 column when nobody holds a lock.
_NODE_UNLOCK = '<font color="green">null</font>'


def _get_ip(cluster_configs, node_key) -> str:
    """Extract IP from a cluster_configs entry, returning '' when no real IP is set.

    Supports new (DEVICE: {ip, devices}; NODE/QUEUE: ip_str) and old formats.
    """
    if not isinstance(cluster_configs, dict):
        return ""
    v = cluster_configs.get(node_key, "")
    if isinstance(v, dict):
        return v.get("ip", "") or ""
    # NODE/QUEUE old format normalized to {name: name}; treat name==value as no IP
    if isinstance(v, str):
        return "" if v == node_key else v
    return ""


def _node_label(cluster_configs, node_key) -> str:
    """Format a node label as 'name(ip)' if IP is set, else just 'name'."""
    ip = _get_ip(cluster_configs, node_key)
    return f"{node_key}({ip})" if ip else node_key


def _now_str():
    return datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")


def _md_row(*cells):
    return "| " + " | ".join(str(c) for c in cells) + " |\n"


def build_device_query(bot_state, user_id, config, node_filter=None, xpu_usage=None):
    """Build full markdown query text for a DEVICE bot."""
    if node_filter is not None:
        bot_state = {k: v for k, v in bot_state.items() if k == node_filter}
    # ── header ──────────────────────────────────────────────────────────
    lines = [t("query.cluster_usage_title", config=config, timestamp=_now_str())]

    # ── summary ─────────────────────────────────────────────────────────
    threshold = config.get_val("MEM_BUSY_THRESHOLD", 10) if config else 10
    unlocked_devs = sum(1 for devs in bot_state.values() for dev in devs if dev["status"] == "idle")
    free_devs = sum(
        len(devs) for node_key, devs in bot_state.items() if _mem_category(_node_mem(xpu_usage, node_key), threshold) == "free"
    )
    lines.append(
        t("query.idle_summary_device", config=config, unlocked_devs=unlocked_devs, free_devs=free_devs)
    )

    # ── tip (right under the summary) ────────────────────────────────────
    lines.append(t("query.status_tip", config=config))
    tip = config.get_val("QUERY_TIP") if config else ""
    if tip:
        lines.append(tip + "\n")

    # ── markdown table ───────────────────────────────────────────────────
    header_key = "query.table_header_xpu" if xpu_usage is not None else "query.table_header"
    lines.append(t(header_key, config=config))
    cluster_configs = config.get_val("CLUSTER_CONFIGS") if config else {}
    entries = []
    for order, (node_key, devs) in enumerate(bot_state.items()):
        rem = min_remaining(devs)
        is_mine = user_id is not None and any(
            u["user_id"] == user_id for d in devs if d.get("status") != "idle" for u in d.get("current_users", [])
        )
        cat = _mem_category(_node_mem(xpu_usage, node_key), threshold)
        entries.append((node_key, devs, rem, is_mine, cat, order))

    for node_key, devs, _rem, _mine, cat, _order in sorted(entries, key=_node_sort_key):
        status_badge = _STATUS_BADGE[cat]
        grouped_usage = group_locked_devices(devs)
        shown = set()
        for _, dev_ids in grouped_usage:
            shown.update(dev_ids)
        idle_groups = group_idle_devices(devs, shown)
        rows = render_device_lines(devs, grouped_usage, idle_groups, config=config)
        first_row = True
        for is_idle, fields in rows:
            dev_cell = fields["dev"]
            if is_idle:
                user_cell = _UNLOCK
                dur_cell = "--"
            else:
                mode = fields["mode"].strip("()")
                user_cell = f"{fields['user']}（{mode}）".strip()
                dur_cell = fields["dur"] or "--"
            node_cell = _node_label(cluster_configs, node_key) if first_row else ""
            node_status_cell = status_badge if first_row else ""
            cells = [node_cell, node_status_cell, dev_cell, user_cell, dur_cell]
            usage = xpu_usage.get(node_key) if xpu_usage is not None else None
            lines.append(_md_row(*_with_xpu(cells, usage, first_row=first_row, xpu_on=xpu_usage is not None)))
            first_row = False

    return "".join(lines)


def build_node_query(bot_state, user_id, config, node_filter=None, xpu_usage=None, memory_based=True):
    """Build full markdown query text for a NODE/QUEUE bot.

    memory_based=True (NODE): status badge is driven by GPU memory utilization
    and the lock column shows UNLOCK when free, mirroring DEVICE. When xpu_usage
    is provided a 7-column table is rendered.

    memory_based=False (QUEUE): legacy lock-based status (idle→FREE, locked→BUSY)
    with a '--' placeholder lock column; always 5 columns.
    """
    if node_filter is not None:
        bot_state = {k: v for k, v in bot_state.items() if k == node_filter}
    lines = [t("query.cluster_usage_title", config=config, timestamp=_now_str())]

    threshold = config.get_val("MEM_BUSY_THRESHOLD", 10) if config else 10
    unlocked_nodes = sum(1 for ns in bot_state.values() if ns["status"] == "idle")
    free_nodes = sum(
        1 for node_key in bot_state if _mem_category(_node_mem(xpu_usage, node_key), threshold) == "free"
    )
    lines.append(
        t("query.idle_summary_node", config=config, unlocked_nodes=unlocked_nodes, free_nodes=free_nodes)
    )

    # ── tip (right under the summary) ────────────────────────────────────
    lines.append(t("query.status_tip", config=config))
    tip = config.get_val("QUERY_TIP") if config else ""
    if tip:
        lines.append(tip + "\n")

    xpu_on = memory_based and xpu_usage is not None
    header_key = "query.table_header_node_xpu" if xpu_on else "query.table_header_node"
    lines.append(t(header_key, config=config))
    cluster_configs = config.get_val("CLUSTER_CONFIGS") if config else {}
    entries = []
    for order, (node_key, ns) in enumerate(bot_state.items()):
        rem = min_remaining(ns)
        is_mine = user_id is not None and any(u["user_id"] == user_id for u in ns.get("current_users", []))
        if memory_based:
            cat = _mem_category(_node_mem(xpu_usage, node_key), threshold)
        else:
            cat = "free" if ns["status"] == "idle" else "busy"
        entries.append((node_key, ns, rem, is_mine, cat, order))

    idle_lock_cell = _NODE_UNLOCK if memory_based else "--"
    for node_key, ns, _rem, _mine, cat, _order in sorted(entries, key=_node_sort_key):
        status_badge = _STATUS_BADGE[cat]
        node_label = _node_label(cluster_configs, node_key)
        usage = xpu_usage.get(node_key) if xpu_on else None
        if ns["status"] == "idle":
            cells = [node_label, idle_lock_cell, status_badge, "--"]
            lines.append(_md_row(*_with_xpu(cells, usage, first_row=True, xpu_on=xpu_on)))
        else:
            first_row = True
            for user_info in ns["current_users"]:
                mode_str = format_access_mode(ns["status"], config=config).strip("()")
                dur_str = format_duration(
                    remaining_duration(user_info["start_time"], user_info["duration"]), config=config
                )
                user_cell = f"{user_info['user_id']}（{mode_str}）"
                node_cell = node_label if first_row else ""
                node_st_cell = status_badge if first_row else ""
                cells = [node_cell, user_cell, node_st_cell, dur_str or "--"]
                lines.append(_md_row(*_with_xpu(cells, usage, first_row=first_row, xpu_on=xpu_on)))
                first_row = False

    return "".join(lines)


# ── helpers ───────────────────────────────────────────────────────────────


def _node_mem(xpu_usage, node_key):
    """Node-average memory % for node_key, or None when not collected."""
    if xpu_usage is None:
        return None
    usage = xpu_usage.get(node_key)
    return usage.mem if usage is not None else None


def _mem_category(mem, threshold):
    """Classify node-average memory into 'free' / 'busy' / 'na' (not collected)."""
    if mem is None:
        return "na"
    return "busy" if mem > threshold else "free"


_STATUS_BADGE = {"free": _STATUS_FREE, "busy": _STATUS_BUSY, "na": _STATUS_NA}
# Within a lock group, order memory tiers FREE < N/A < BUSY.
_CAT_RANK = {"free": 0, "na": 1, "busy": 2}


def _node_sort_key(entry):
    """Order nodes by (1) is_mine, (2) lock presence (unlocked first), then
    (3) memory tier within each lock group (FREE < N/A < BUSY). Within a tier,
    by remaining lock duration ascending.

    Resulting ranks:
        0 = @'d (is_mine)
        1/2/3 = unlocked + FREE / N/A / BUSY
        4/5/6 = locked  + FREE / N/A / BUSY
    entry = (key, state, rem, is_mine, cat, order).
    """
    _key, _state, rem, is_mine, cat, order = entry
    is_locked = rem is not None
    if is_mine:
        rank = 0
    else:
        rank = 1 + (3 if is_locked else 0) + _CAT_RANK[cat]
    rem_val = rem if rem is not None else 0
    return (rank, rem_val, order)


def _format_xpu_cells(usage):
    """Return (util_cell, container_cell) for a node's first row.

    usage is a NodeUsage or None. None / both-None mem+util -> 'N/A', ''.
    """
    if usage is None or (usage.util is None and usage.mem is None):
        return "N/A", ""
    u = f"{usage.util}%" if usage.util is not None else "N/A"
    m = f"{usage.mem}%" if usage.mem is not None else "N/A"
    return f"{u}/{m}", usage.container or ""


def _with_xpu(cells, usage, *, first_row, xpu_on):
    """Append the two XPU columns to a base 5-cell row when xpu_on is True."""
    if not xpu_on:
        return cells
    if first_row:
        util_cell, container_cell = _format_xpu_cells(usage)
    else:
        util_cell, container_cell = "", ""
    return [*cells, util_cell, container_cell]
