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
