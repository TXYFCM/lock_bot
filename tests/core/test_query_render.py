"""Tests for query_render node ordering."""

from lockbot.core.query_render import _is_device_partial, _node_sort_key


def _dev(status):
    return {"dev_id": 0, "status": status, "dev_model": "a800", "current_users": []}


def _device_node(idle_count, total):
    """A DEVICE node state (list) with idle_count idle devices out of total."""
    return [_dev("idle") for _ in range(idle_count)] + [_dev("exclusive") for _ in range(total - idle_count)]


def _order(entries):
    return [e[0] for e in sorted(entries, key=_node_sort_key)]


def test_partial_sorts_above_busy():
    """FREE -> PARTIAL -> BUSY (busy tier ordered by remaining asc)."""
    entries = [
        # (key, state, rem, is_mine, order)
        ("busy_8m", _device_node(0, 8), 8 * 60, False, 0),
        ("busy_44m", _device_node(0, 8), 44 * 60, False, 1),
        ("partial", _device_node(5, 8), 50 * 60, False, 2),
        ("free", _device_node(8, 8), None, False, 3),
    ]
    assert _order(entries) == ["free", "partial", "busy_8m", "busy_44m"]


def test_mine_first_then_free_partial_busy():
    """is_mine outranks everything, including a FREE node."""
    entries = [
        ("free", _device_node(8, 8), None, False, 0),
        ("partial", _device_node(3, 8), 50 * 60, False, 1),
        ("busy", _device_node(0, 8), 8 * 60, False, 2),
        ("mine", _device_node(0, 8), 99 * 60, True, 3),
    ]
    assert _order(entries) == ["mine", "free", "partial", "busy"]


def test_node_dict_state_never_partial():
    """NODE/QUEUE state is a dict; it must never be classified PARTIAL."""
    busy = {"status": "exclusive", "current_users": []}
    idle = {"status": "idle", "current_users": []}
    assert _is_device_partial(busy) is False
    assert _is_device_partial(idle) is False

    # A busy NODE node lands in the BUSY tier (rank 3), idle lands in FREE (rank 1).
    entries = [
        ("busy", busy, 10 * 60, False, 0),
        ("idle", idle, None, False, 1),
    ]
    assert _order(entries) == ["idle", "busy"]


def test_is_device_partial():
    assert _is_device_partial(_device_node(3, 8)) is True  # mixed
    assert _is_device_partial(_device_node(0, 8)) is False  # all busy
    assert _is_device_partial(_device_node(8, 8)) is False  # all idle
