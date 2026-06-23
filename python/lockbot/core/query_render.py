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
from lockbot.core.xpu_collector import NodeUsage

_NODE_FULL = '<font color="green">FREE</font>'
_NODE_BUSY = '<font color="red">BUSY</font>'
_NODE_PARTIAL = '<font color="orange">PARTIAL</font>'
_DEV_FREE = '<font color="green">FREE</font>'
_DEV_BUSY = '<font color="red">BUSY</font>'


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
    idle_nodes = 0
    idle_devs = 0
    for devs in bot_state.values():
        node_idle = sum(1 for d in devs if d["status"] == "idle")
        if node_idle == len(devs):
            idle_nodes += 1
        idle_devs += node_idle
    lines.append(t("query.idle_summary_device", config=config, idle_nodes=idle_nodes, idle_devs=idle_devs))

    # ── tip (right under the summary) ────────────────────────────────────
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
        entries.append((node_key, devs, rem, is_mine, order))

    for node_key, devs, _rem, _mine, _order in sorted(entries, key=_node_sort_key):
        node_state = _node_state_device(devs)
        grouped_usage = group_locked_devices(devs)
        shown = set()
        for _, dev_ids in grouped_usage:
            shown.update(dev_ids)
        idle_groups = group_idle_devices(devs, shown)
        rows = render_device_lines(devs, grouped_usage, idle_groups, config=config)
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
                    usage = xpu_usage.get(node_key, NodeUsage(util=None, mem=None, container=""))
                    if usage.util is None and usage.mem is None:
                        util_cell = "N/A"
                    else:
                        u = f"{usage.util}%" if usage.util is not None else "N/A"
                        m = f"{usage.mem}%" if usage.mem is not None else "N/A"
                        util_cell = f"{u}/{m}"
                    container_cell = usage.container or ""
                else:
                    util_cell = ""
                    container_cell = ""
                lines.append(
                    _md_row(
                        node_cell,
                        node_status_cell,
                        dev_cell,
                        user_cell,
                        dur_cell,
                        util_cell,
                        container_cell,
                    )
                )
            else:
                lines.append(_md_row(node_cell, node_status_cell, dev_cell, user_cell, dur_cell))
            first_row = False

    return "".join(lines)


def build_node_query(bot_state, user_id, config, node_filter=None):
    """Build full markdown query text for a NODE/QUEUE bot."""
    if node_filter is not None:
        bot_state = {k: v for k, v in bot_state.items() if k == node_filter}
    lines = [t("query.cluster_usage_title", config=config, timestamp=_now_str())]

    idle_nodes = sum(1 for ns in bot_state.values() if ns["status"] == "idle")
    lines.append(t("query.idle_summary_node", config=config, idle_nodes=idle_nodes))

    # ── tip (right under the summary) ────────────────────────────────────
    tip = config.get_val("QUERY_TIP") if config else ""
    if tip:
        lines.append(tip + "\n")

    lines.append(t("query.table_header", config=config))
    cluster_configs = config.get_val("CLUSTER_CONFIGS") if config else {}
    entries = []
    for order, (node_key, ns) in enumerate(bot_state.items()):
        rem = min_remaining(ns)
        is_mine = user_id is not None and any(u["user_id"] == user_id for u in ns.get("current_users", []))
        entries.append((node_key, ns, rem, is_mine, order))

    for node_key, ns, _rem, _mine, _order in sorted(entries, key=_node_sort_key):
        node_status_cell = _NODE_FULL if ns["status"] == "idle" else _NODE_BUSY
        node_label = _node_label(cluster_configs, node_key)
        if ns["status"] == "idle":
            lines.append(_md_row(node_label, node_status_cell, "--", "--", "--"))
        else:
            first_row = True
            for user_info in ns["current_users"]:
                mode_str = format_access_mode(ns["status"], config=config).strip("()")
                dur_str = format_duration(
                    remaining_duration(user_info["start_time"], user_info["duration"]), config=config
                )
                user_cell = f"{user_info['user_id']}（{mode_str}）"
                node_cell = node_label if first_row else ""
                node_st_cell = node_status_cell if first_row else ""
                lines.append(_md_row(node_cell, node_st_cell, "--", user_cell, dur_str or "--"))
                first_row = False

    return "".join(lines)


# ── helpers ───────────────────────────────────────────────────────────────


def _node_sort_key(entry):
    """Order: my locked nodes first, then idle (FREE), then PARTIAL
    (some cards free), then BUSY. Within a tier, by remaining duration asc.
    entry = (key, state, rem, is_mine, order).
    """
    _key, state, rem, is_mine, order = entry
    is_idle = rem is None
    if is_mine:
        rank = 0
    elif is_idle:
        rank = 1
    elif _is_device_partial(state):
        rank = 2
    else:
        rank = 3
    rem_val = rem if rem is not None else 0
    return (rank, rem_val, order)


def _node_state_device(devs):
    idle_count = sum(1 for d in devs if d["status"] == "idle")
    if idle_count == len(devs):
        return _NODE_FULL
    if idle_count == 0:
        return _NODE_BUSY
    return _NODE_PARTIAL


def _is_device_partial(state):
    """True if a DEVICE node has a mix of idle and locked devices.

    NODE/QUEUE nodes (dict state) are never PARTIAL.
    """
    if not isinstance(state, list):
        return False
    idle = sum(1 for d in state if d["status"] == "idle")
    return 0 < idle < len(state)
