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
    assert "未Lock卡数：2；当前Free卡数：0" in out
    assert "节点状态表示机器当前是否正在使用" in out
    assert "10" not in out.split("节点状态表示机器当前是否正在使用", 1)[1].split("| IP |", 1)[0]
    assert "XPU%/MEM%" in out
    assert "82.0%/50.0%" in out
    assert "my_ctr" in out


def test_device_summary_decouples_lock_and_free():
    state = {
        "locked_low": [
            {
                "dev_id": 0,
                "status": "exclusive",
                "dev_model": "a800",
                "current_users": [{"user_id": "u1", "start_time": 0, "duration": 999999999999}],
            },
            {"dev_id": 1, "status": "idle", "dev_model": "a800", "current_users": []},
        ],
        "idle_high": [
            {"dev_id": 0, "status": "idle", "dev_model": "a800", "current_users": []},
            {"dev_id": 1, "status": "idle", "dev_model": "a800", "current_users": []},
        ],
    }
    cfg = Config(
        {
            "BOT_TYPE": "DEVICE",
            "CLUSTER_CONFIGS": {
                "locked_low": {"ip": "10.0.0.1", "devices": ["a800", "a800"]},
                "idle_high": {"ip": "10.0.0.2", "devices": ["a800", "a800"]},
            },
            "QUERY_TIP": "",
            "MEM_BUSY_THRESHOLD": 10,
        }
    )
    xpu = {
        "locked_low": NodeUsage(util=1.0, mem=2.0, container=""),
        "idle_high": NodeUsage(util=99.0, mem=95.0, container="big"),
    }
    out = build_device_query(state, None, cfg, xpu_usage=xpu)
    assert "未Lock卡数：3；当前Free卡数：2" in out


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


def test_shared_lock_status_only_on_device_row():
    state = {
        "node1": [
            {
                "dev_id": 0,
                "status": "shared",
                "dev_model": "a800",
                "current_users": [
                    {"user_id": "u1", "start_time": 0, "duration": 999999999999},
                    {"user_id": "u2", "start_time": 0, "duration": 999999999999},
                ],
            }
        ]
    }
    out = build_device_query(
        state,
        None,
        _config(),
        node_filter="node1",
        xpu_usage={"node1": NodeUsage(util=10.0, mem=20.0, container="c")},
    )
    data_rows = [line for line in out.splitlines() if "u1" in line or "u2" in line]
    assert "dev0" in data_rows[0]
    assert data_rows[0].count("BUSY") == 1
    assert "dev0" not in data_rows[1]
    assert "BUSY" not in data_rows[1]
