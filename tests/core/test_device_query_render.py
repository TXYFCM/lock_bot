"""Tests for build_device_query 7-column (xpu_usage) rendering."""

from lockbot.core.config import Config
from lockbot.core.query_render import build_device_query
from lockbot.core.xpu_collector import NodeUsage


def _state():
    return {
        "node1": [
            {"dev_id": 0, "status": "idle", "dev_model": "a800", "current_users": []},
            {"dev_id": 1, "status": "idle", "dev_model": "a800", "current_users": []},
        ]
    }


def _config():
    return Config(
        {
            "BOT_TYPE": "DEVICE",
            "CLUSTER_CONFIGS": {"node1": {"ip": "10.0.0.1", "devices": ["a800", "a800"]}},
            "QUERY_TIP": "",
        }
    )


def test_param_query_is_five_columns():
    out = build_device_query(_state(), None, _config(), node_filter="node1")
    assert "XPU%/MEM%" not in out


def test_bare_at_is_seven_columns():
    out = build_device_query(
        _state(),
        None,
        _config(),
        node_filter="node1",
        xpu_usage={"node1": NodeUsage(util=82.0, mem=50.0, container="my_ctr")},
    )
    assert "XPU%/MEM%" in out
    assert "82.0%/50.0%" in out
    assert "my_ctr" in out


def test_failed_node_shows_na():
    out = build_device_query(
        _state(),
        None,
        _config(),
        node_filter="node1",
        xpu_usage={"node1": NodeUsage(util=None, mem=None, container="")},
    )
    assert "N/A" in out


def test_util_only_on_first_row():
    # Mixed node: one locked device + one idle device -> two rendered rows.
    state = {
        "node1": [
            {
                "dev_id": 0,
                "status": "exclusive",
                "dev_model": "a800",
                "current_users": [{"user_id": "u1", "start_time": 0, "duration": 999999999999}],
            },
            {"dev_id": 1, "status": "idle", "dev_model": "a800", "current_users": []},
        ]
    }
    out = build_device_query(
        state,
        None,
        _config(),
        node_filter="node1",
        xpu_usage={"node1": NodeUsage(util=10.0, mem=20.0, container="c")},
    )
    # Two data rows are rendered; util/container appear only on the first.
    assert out.count("10.0%/20.0%") == 1
    assert out.count("| c |") == 1
