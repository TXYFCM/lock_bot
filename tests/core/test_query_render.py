"""Tests for query_render node ordering (memory-based status + lock-aware sort)."""

from lockbot.core.query_render import _mem_category, _node_sort_key


def _entry(key, rem, is_mine, cat, order):
    # entry = (key, state, rem, is_mine, cat, order); state is unused by the sort.
    return (key, None, rem, is_mine, cat, order)


def _order(entries):
    return [e[0] for e in sorted(entries, key=_node_sort_key)]


def test_mem_category():
    assert _mem_category(None, 10) == "na"
    assert _mem_category(5, 10) == "free"
    assert _mem_category(10, 10) == "free"  # boundary: not > threshold
    assert _mem_category(10.1, 10) == "busy"
    assert _mem_category(80, 10) == "busy"


def test_unlocked_before_locked():
    """Primary key: no-lock (rem is None) sorts before locked, regardless of mem tier."""
    entries = [
        # (key, rem, is_mine, cat, order)
        _entry("locked_free", 100, False, "free", 0),
        _entry("unlocked_busy", None, False, "busy", 1),
    ]
    # unlocked+busy (rank 3) still beats locked+free (rank 4)
    assert _order(entries) == ["unlocked_busy", "locked_free"]


def test_mem_tier_within_lock_group():
    """Within the unlocked group: FREE < N/A < BUSY."""
    entries = [
        _entry("u_busy", None, False, "busy", 0),
        _entry("u_na", None, False, "na", 1),
        _entry("u_free", None, False, "free", 2),
    ]
    assert _order(entries) == ["u_free", "u_na", "u_busy"]


def test_mem_tier_within_locked_group():
    """Within the locked group: FREE < N/A < BUSY (ranks 4/5/6)."""
    entries = [
        _entry("l_busy", 100, False, "busy", 0),
        _entry("l_na", 100, False, "na", 1),
        _entry("l_free", 100, False, "free", 2),
    ]
    assert _order(entries) == ["l_free", "l_na", "l_busy"]


def test_mine_first():
    """is_mine outranks everything, even an unlocked+FREE node."""
    entries = [
        _entry("unlocked_free", None, False, "free", 0),
        _entry("mine", 100, True, "busy", 1),
    ]
    assert _order(entries) == ["mine", "unlocked_free"]


def test_full_seven_rank_order():
    """Full ordering across all seven ranks."""
    entries = [
        _entry("locked_busy", 100, False, "busy", 0),  # rank 6
        _entry("locked_na", 100, False, "na", 1),  # rank 5
        _entry("locked_free", 100, False, "free", 2),  # rank 4
        _entry("unlocked_busy", None, False, "busy", 3),  # rank 3
        _entry("unlocked_na", None, False, "na", 4),  # rank 2
        _entry("unlocked_free", None, False, "free", 5),  # rank 1
        _entry("mine", 100, True, "busy", 6),  # rank 0
    ]
    assert _order(entries) == [
        "mine",
        "unlocked_free",
        "unlocked_na",
        "unlocked_busy",
        "locked_free",
        "locked_na",
        "locked_busy",
    ]


def test_intra_rank_by_remaining_then_order():
    """Same rank: sort by remaining duration ascending, then insertion order."""
    entries = [
        _entry("l_44m", 44 * 60, False, "free", 0),
        _entry("l_8m", 8 * 60, False, "free", 1),
    ]
    assert _order(entries) == ["l_8m", "l_44m"]
