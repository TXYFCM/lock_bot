"""Tests for build_device_query 7-column (xpu_usage) rendering."""

from lockbot.core.config import Config
from lockbot.core.query_render import build_device_query
from lockbot.core.xpu_collector import CardUsage, NodeUsage


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
    # New column order: IP | lock同学 | 节点状态 | 卡状态 | 剩余时间
    assert "| IP | lock同学 | 节点状态 | 卡状态 | 剩余时间 |" in out


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
    assert "| IP | lock同学 | 节点状态 | 卡状态 | 剩余时间 | XPU%/MEM% | 容器名 |" in out
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


def test_util_per_group_when_mixed():
    # Mixed node: one locked device + one idle device -> two groups.
    # Under per-card semantics each group shows its own cards' average.
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
        xpu_usage={
            "node1": NodeUsage(
                util=20.0,
                mem=30.0,
                container="c",
                per_card=[CardUsage(10.0, 20.0, "c"), CardUsage(30.0, 40.0, "")],
            )
        },
    )
    # Each group's per-card average appears once (locked dev0 -> card0, idle dev1 -> card1).
    assert out.count("10.0%/20.0%") == 1
    assert out.count("30.0%/40.0%") == 1
    assert out.count("| c |") == 1


def test_single_owner_whole_node_averaged():
    # Whole node locked by one user (one group) -> averaged node-level cells, util only on first row.
    state = {
        "node1": [
            {
                "dev_id": 0,
                "status": "exclusive",
                "dev_model": "a800",
                "current_users": [{"user_id": "u1", "start_time": 0, "duration": 999999999999}],
            },
            {
                "dev_id": 1,
                "status": "exclusive",
                "dev_model": "a800",
                "current_users": [{"user_id": "u1", "start_time": 0, "duration": 999999999999}],
            },
        ]
    }
    out = build_device_query(
        state,
        None,
        _config(),
        node_filter="node1",
        xpu_usage={
            "node1": NodeUsage(
                util=50.0,
                mem=60.0,
                container="ctr",
                per_card=[CardUsage(10.0, 20.0, "ctr"), CardUsage(90.0, 100.0, "ctr")],
            )
        },
    )
    # Single group -> node average (50.0/60.0), shown once; per-card values do NOT appear.
    assert out.count("50.0%/60.0%") == 1
    assert "10.0%/20.0%" not in out
    assert out.count("| ctr |") == 1


def test_mixed_lockers_per_group_xpu():
    # dev0-1 locked by u1, dev2-3 locked by u2 -> two lock groups, each per-half average.
    state = {
        "node1": [
            {
                "dev_id": 0,
                "status": "exclusive",
                "dev_model": "a800",
                "current_users": [{"user_id": "u1", "start_time": 0, "duration": 999999999999}],
            },
            {
                "dev_id": 1,
                "status": "exclusive",
                "dev_model": "a800",
                "current_users": [{"user_id": "u1", "start_time": 0, "duration": 999999999999}],
            },
            {
                "dev_id": 2,
                "status": "exclusive",
                "dev_model": "a800",
                "current_users": [{"user_id": "u2", "start_time": 0, "duration": 999999999999}],
            },
            {
                "dev_id": 3,
                "status": "exclusive",
                "dev_model": "a800",
                "current_users": [{"user_id": "u2", "start_time": 0, "duration": 999999999999}],
            },
        ]
    }
    cfg = Config(
        {
            "BOT_TYPE": "DEVICE",
            "CLUSTER_CONFIGS": {"node1": {"ip": "10.0.0.1", "devices": ["a800"] * 4}},
            "QUERY_TIP": "",
        }
    )
    out = build_device_query(
        state,
        None,
        cfg,
        node_filter="node1",
        xpu_usage={
            "node1": NodeUsage(
                util=50.0,
                mem=50.0,
                container="ctrA",
                per_card=[
                    CardUsage(80.0, 70.0, "ctrA"),  # dev0
                    CardUsage(80.0, 70.0, "ctrA"),  # dev1 -> u1 avg 80/70
                    CardUsage(5.0, 3.0, ""),  # dev2
                    CardUsage(5.0, 3.0, ""),  # dev3 -> u2 avg 5/3
                ],
            )
        },
    )
    rows = [ln for ln in out.splitlines() if "u1" in ln or "u2" in ln]
    u1_row = next(r for r in rows if "u1" in r)
    u2_row = next(r for r in rows if "u2" in r)
    # lock同学 is column 2; dev range is column 4 (卡状态)
    assert "dev0-1" in u1_row
    assert "dev2-3" in u2_row
    # per-group XPU%/MEM%
    assert "80.0%/70.0%" in u1_row
    assert "5.0%/3.0%" in u2_row
    # node status badge only on the first data row (u1's), blank on u2's
    assert "</font>" in u1_row  # status badge present
    assert "BUSY" in u1_row or "FREE" in u1_row
    assert "BUSY" not in u2_row and "FREE" not in u2_row
    # container ctrA appears for u1's group only
    assert "ctrA" in u1_row


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
