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
    assert "节点状态（XPU显存）" in out
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
    # Whole node locked by one user (one group) -> averaged node-level util/mem, util only on
    # first row. Container column lists per-card deduped containers (here both cards share "ctr").
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
    # Per-card container dedupes to a single "ctr" cell.
    assert out.count("| ctr |") == 1


def test_uniform_node_shows_single_max_mem_container():
    # Whole node by one user, two cards on DIFFERENT containers -> the single group's container
    # cell shows ONLY the highest-memory card's container (scenario 2/5: one container per row,
    # no <br> list). util/mem stay the node average (shown once).
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
                container="ctrA",
                per_card=[CardUsage(40.0, 50.0, "ctrA"), CardUsage(60.0, 70.0, "ctrB")],
            )
        },
    )
    # card1 (mem 70) > card0 (mem 50) -> show ctrB only; no <br> multi-container list.
    assert "| ctrB |" in out
    assert "ctrA<br>ctrB" not in out
    assert "<br>" not in out
    assert out.count("50.0%/60.0%") == 1


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
    # 卡状态: device range + GPU-mem status (u1 avg 70 > 10 → BUSY, u2 avg 3 <= 10 → FREE).
    assert "dev0-1 <font color=\"red\">BUSY</font>" in u1_row
    assert "dev2-3 <font color=\"green\">FREE</font>" in u2_row
    # per-group XPU%/MEM%
    assert "80.0%/70.0%" in u1_row
    assert "5.0%/3.0%" in u2_row
    # Node status: mixed per-card mem (70/70/3/3) → PARTIAL on both rows.
    assert "PARTIAL" in u1_row
    assert "PARTIAL" in u2_row
    # container ctrA (max-mem card) appears for u1's group; u2's group has no container -> "--"
    assert "ctrA" in u1_row
    assert "| -- |" in u2_row


def test_shared_lock_repeats_each_user():
    # Shared lock (slock): one card dev0 held by u1 + u2. Each shared user gets a FULL row
    # repeating dev0 + status badge + XPU + container (scenario 6).
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
        xpu_usage={"node1": NodeUsage(util=45.0, mem=55.0, container="shared_ctr")},
    )
    data_rows = [line for line in out.splitlines() if "u1" in line or "u2" in line]
    u1_row = next(r for r in data_rows if "u1" in r)
    u2_row = next(r for r in data_rows if "u2" in r)
    # No per_card → node N/A, card N/A. Both rows repeat dev0 + N/A + XPU + container.
    for row in (u1_row, u2_row):
        assert "dev0" in row
        assert "N/A" in row
        assert "45.0%/55.0%" in row
        assert "shared_ctr" in row


def test_mixed_lockers_both_rows_show_badge():
    # Two users each lock half the node (dev0-3 / dev4-7), both halves high-mem -> BOTH lock
    # rows show a BUSY badge (scenario 3).
    state = {
        "node1": [
            {
                "dev_id": i,
                "status": "exclusive",
                "dev_model": "a800",
                "current_users": [{"user_id": "u1" if i < 4 else "u2", "start_time": 0, "duration": 999999999999}],
            }
            for i in range(8)
        ]
    }
    cfg = Config(
        {
            "BOT_TYPE": "DEVICE",
            "CLUSTER_CONFIGS": {"node1": {"ip": "10.0.0.1", "devices": ["a800"] * 8}},
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
                util=70.0,
                mem=75.0,
                container="job_a",
                per_card=[CardUsage(70.0, 75.0, "job_a")] * 4 + [CardUsage(60.0, 65.0, "job_b")] * 4,
            )
        },
    )
    rows = [ln for ln in out.splitlines() if "u1" in ln or "u2" in ln]
    u1_row = next(r for r in rows if "u1" in r)
    u2_row = next(r for r in rows if "u2" in r)
    assert "BUSY" in u1_row
    assert "BUSY" in u2_row
    assert "job_a" in u1_row
    assert "job_b" in u2_row


def test_partial_lock_idle_shows_partial_and_dashes():
    # dev0-1 locked & high-mem (65% > 10), dev2-7 idle & 0% mem.
    # Node status: GPU-memory-based — some cards busy, some free → PARTIAL.
    # 卡状态: per-group GPU-mem — locked group BUSY, idle group FREE.
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
        + [{"dev_id": i, "status": "idle", "dev_model": "a800", "current_users": []} for i in range(2, 8)]
    }
    cfg = Config(
        {
            "BOT_TYPE": "DEVICE",
            "CLUSTER_CONFIGS": {"node1": {"ip": "10.0.0.1", "devices": ["a800"] * 8}},
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
                util=30.0,
                mem=32.0,
                container="job_a",
                per_card=[CardUsage(60.0, 65.0, "job_a")] * 2 + [CardUsage(0.0, 0.0, "")] * 6,
            )
        },
    )
    lock_row = next(ln for ln in out.splitlines() if "u1" in ln)
    idle_row = next(ln for ln in out.splitlines() if "null" in ln)
    # Node status is PARTIAL for all rows (GPU mem: some high, some low).
    assert "PARTIAL" in lock_row
    assert "PARTIAL" in idle_row
    # 卡状态: device range + lock status.
    assert 'dev0-1 <font color="red">BUSY</font>' in lock_row
    assert 'dev2-7 <font color="green">FREE</font>' in idle_row
    # idle group has no container -> "--"
    assert "| -- |" in idle_row


def test_single_group_mixed_cards_splits_card_status_and_xpu():
    # One user locks the whole node (a single render group dev0-7), but card0 is high-mem
    # while card1-7 are idle. Cards have only BUSY/FREE — so 卡状态 splits into contiguous
    # runs "dev0 BUSY dev1-7 FREE" within one cell, and XPU%/MEM% + 容器名 split to match.
    # Node status stays PARTIAL (mixed per-card mem).
    state = {
        "node1": [
            {
                "dev_id": i,
                "status": "exclusive",
                "dev_model": "a800",
                "current_users": [{"user_id": "u1", "start_time": 0, "duration": 999999999999}],
            }
            for i in range(8)
        ]
    }
    cfg = Config(
        {
            "BOT_TYPE": "DEVICE",
            "CLUSTER_CONFIGS": {"node1": {"ip": "10.0.0.1", "devices": ["a800"] * 8}},
            "QUERY_TIP": "",
            "MEM_BUSY_THRESHOLD": 10,
        }
    )
    out = build_device_query(
        state,
        None,
        cfg,
        node_filter="node1",
        xpu_usage={
            "node1": NodeUsage(
                util=20.0,
                mem=15.0,
                container="job_a",
                per_card=[CardUsage(90.0, 80.0, "job_a")] + [CardUsage(0.0, 0.0, "")] * 7,
            )
        },
    )
    row = next(ln for ln in out.splitlines() if "u1" in ln)
    # Node status: mixed -> PARTIAL (unchanged).
    assert "PARTIAL" in row
    # 卡状态: two contiguous runs in one cell.
    assert 'dev0 <font color="red">BUSY</font> dev1-7 <font color="green">FREE</font>' in row
    # XPU%/MEM%: per-run averages, positionally aligned with the card-status segments.
    assert "90.0%/80.0% 0.0%/0.0%" in row
    # 容器名: busy run's container, idle run has none -> "--".
    assert "| job_a -- |" in row
