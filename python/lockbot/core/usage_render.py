"""Shared usage-display rendering engine: sorting, grouping, and line templating.

Used by DeviceBot, NodeBot, and QueueBot so all three honor the same
USAGE_SORT / USAGE_GROUP / USAGE_*_TEMPLATE config knobs.
"""

import logging

from lockbot.core.utils import remaining_duration

logger = logging.getLogger(__name__)

DEFAULT_LINE_TEMPLATE = "{node} {dev} {model}{user}{mode} {dur}"
DEFAULT_IDLE_TEMPLATE = "{node} {dev} {model}{status}"


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
        is_idle_rank = 1 if rem is None else 0  # idle always after active
        rem_val = rem if rem is not None else 0
        if sort_mode == "dur_asc":
            return (is_idle_rank, rem_val, e["order_index"])
        if sort_mode == "dur_desc":
            return (is_idle_rank, -rem_val, e["order_index"])
        # "name" or unknown → original order
        return (e["order_index"],)

    ordered = sorted(entries, key=sort_key)

    if group_mode == "idle_first":
        return [e for e in ordered if e["is_idle"]] + [e for e in ordered if not e["is_idle"]]
    if group_mode == "idle_last":
        return [e for e in ordered if not e["is_idle"]] + [e for e in ordered if e["is_idle"]]
    # "none" or unknown → no grouping
    return ordered


def render_line(template, fields, fallback_template, *, bot_name=None):
    """Render one usage line from a str.format template.

    Newlines in the template are stripped (one template = one line). On a
    broken or non-string template (missing field / bad syntax / wrong type)
    the fallback_template is used instead and a WARNING is logged — a
    misconfigured template must never break the whole usage output.
    """
    try:
        if not isinstance(template, str):
            raise TypeError(f"template must be str, got {type(template).__name__}")
        clean = template.replace("\r", "").replace("\n", "")
        return clean.format(**fields)
    except (KeyError, ValueError, IndexError, AttributeError, TypeError) as e:
        logger.warning(
            "Bad usage template %r for bot %s (%s); using fallback",
            template,
            bot_name or "?",
            e,
        )
        return fallback_template.format(**fields)
