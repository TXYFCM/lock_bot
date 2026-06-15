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
