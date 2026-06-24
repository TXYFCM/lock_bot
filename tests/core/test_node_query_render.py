"""Tests for build_node_query memory-based (NODE) vs legacy (QUEUE) rendering."""

from lockbot.core.config import Config
from lockbot.core.query_render import build_node_query
from lockbot.core.xpu_collector import NodeUsage


def _state():
    return {
        "n1": {"status": "idle", "current_users": [], "booking_list": []},
        "n2": {
            "status": "exclusive",
            "current_users": [{"user_id": "u1", "start_time": 0, "duration": 999999999999}],
            "booking_list": [],
        },
    }


def _config():
    return Config(
        {
            "BOT_TYPE": "NODE",
            "CLUSTER_CONFIGS": {"n1": "10.0.0.1", "n2": "10.0.0.2"},
            "QUERY_TIP": "",
            "MEM_BUSY_THRESHOLD": 10,
        }
    )


def test_node_memory_based_unlock_and_na():
    """memory_based with no xpu_usage: idle row shows null, status N/A (4 cols)."""
    out = build_node_query(_state(), None, _config(), memory_based=True, xpu_usage=None)
    assert "null" in out  # idle node lock column (green null)
    assert "N/A" in out  # no memory collected -> N/A badge
    assert "XPU%/MEM%" not in out  # xpu_usage is None -> 4 columns


def test_node_memory_based_seven_columns_and_status():
    """memory_based with xpu_usage: 7 cols, mem>threshold -> BUSY, mem<=threshold -> FREE."""
    xpu = {
        "n1": NodeUsage(util=5.0, mem=2.0, container=""),  # idle node, low mem -> FREE
        "n2": NodeUsage(util=90.0, mem=80.0, container="ctr2"),  # locked node, high mem -> BUSY
    }
    out = build_node_query(_state(), None, _config(), memory_based=True, xpu_usage=xpu)
    assert "XPU%/MEM%" in out
    assert "5.0%/2.0%" in out
    assert "90.0%/80.0%" in out
    assert "ctr2" in out
    assert "FREE" in out and "BUSY" in out
    # idle n1 still shows null even though decoupled status is FREE
    assert "null" in out


def test_node_status_decoupled_from_lock():
    """A locked node with low memory shows FREE; an idle node with high memory shows BUSY."""
    state = {
        "locked_low": {
            "status": "exclusive",
            "current_users": [{"user_id": "u1", "start_time": 0, "duration": 999999999999}],
            "booking_list": [],
        },
        "idle_high": {"status": "idle", "current_users": [], "booking_list": []},
    }
    cfg = Config(
        {
            "BOT_TYPE": "NODE",
            "CLUSTER_CONFIGS": {"locked_low": "10.0.0.1", "idle_high": "10.0.0.2"},
            "QUERY_TIP": "",
            "MEM_BUSY_THRESHOLD": 10,
        }
    )
    xpu = {
        "locked_low": NodeUsage(util=1.0, mem=2.0, container=""),
        "idle_high": NodeUsage(util=99.0, mem=95.0, container="big"),
    }
    out = build_node_query(state, None, cfg, memory_based=True, xpu_usage=xpu)
    rows = [ln for ln in out.splitlines() if ln.startswith("| ")]
    # find the data rows (skip header + separator)
    data_rows = [r for r in rows if "10.0.0" in r]
    locked_row = next(r for r in data_rows if "10.0.0.1" in r)
    idle_row = next(r for r in data_rows if "10.0.0.2" in r)
    assert "FREE" in locked_row  # locked but low mem
    assert "u1" in locked_row  # still shows lock holder, not null
    assert "BUSY" in idle_row  # idle but high mem
    assert "null" in idle_row  # decoupled lock column


def test_queue_legacy_mode_lock_based():
    """memory_based=False (QUEUE): idle->FREE, locked->BUSY, lock column '--', 4 cols."""
    out = build_node_query(_state(), None, _config(), memory_based=False, xpu_usage=None)
    assert "FREE" in out  # idle node
    assert "BUSY" in out  # locked node
    assert "null" not in out  # legacy uses '--' placeholder
    assert "XPU%/MEM%" not in out
