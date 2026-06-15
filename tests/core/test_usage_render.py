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
