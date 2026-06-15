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
