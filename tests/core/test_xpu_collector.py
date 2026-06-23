import subprocess
from unittest import mock

from lockbot.core.xpu_collector import NodeUsage, _parse_util, _parse_pid


def test_parse_util_averages_column():
    line = " ".join(["x"] * 17 + ["100", "y", "80"])  # col18=100(mem) col20=80(util)
    out = "\n".join([line, line])
    assert _parse_util(out) == 80.0


def test_parse_util_empty_returns_none():
    assert _parse_util("") is None


def test_parse_pid_finds_first_busy_process():
    out = "header\nfoo  N/A  N/A   12345  python\nbar N/A N/A 67890 train"
    assert _parse_pid(out) == "12345"


def test_parse_pid_none_when_no_match():
    assert _parse_pid("No running processes found") is None


def test_collect_one_unreachable_returns_failed():
    from lockbot.core import xpu_collector
    with mock.patch.object(xpu_collector, "_ping", return_value=False):
        assert xpu_collector._collect_one("10.0.0.1", "alice", 5) == xpu_collector._FAILED


def test_collect_one_free_node_has_util_no_container():
    from lockbot.core import xpu_collector
    with mock.patch.object(xpu_collector, "_ping", return_value=True), \
         mock.patch.object(xpu_collector, "_ssh_ok", return_value=True), \
         mock.patch.object(xpu_collector, "_ssh_run") as run:
        # first call: xpu-smi (free), second: xpu-smi -m (util=0)
        run.side_effect = [
            "No running processes found",
            " ".join(["x"] * 17 + ["0", "y", "0"]),
        ]
        usage = xpu_collector._collect_one("10.0.0.1", "alice", 5)
    assert usage.util == 0.0
    assert usage.container == ""


def test_collect_one_busy_resolves_container():
    from lockbot.core import xpu_collector
    with mock.patch.object(xpu_collector, "_ping", return_value=True), \
         mock.patch.object(xpu_collector, "_ssh_ok", return_value=True), \
         mock.patch.object(xpu_collector, "_ssh_run") as run:
        run.side_effect = [
            "foo N/A N/A 12345 python",                          # xpu-smi
            " ".join(["x"] * 17 + ["100", "y", "82"]),           # xpu-smi -m
            "12:devices:/docker/abcdef0123456789",               # /proc/<pid>/cgroup
            "abcdef0 my_container",                               # docker ps
        ]
        usage = xpu_collector._collect_one("10.0.0.1", "alice", 5)
    assert usage.util == 82.0
    assert usage.container == "my_container"


def test_collect_one_timeout_returns_failed():
    from lockbot.core import xpu_collector
    with mock.patch.object(xpu_collector, "_ping", return_value=True), \
         mock.patch.object(xpu_collector, "_ssh_ok", return_value=True), \
         mock.patch.object(xpu_collector, "_ssh_run", side_effect=subprocess.TimeoutExpired("ssh", 5)):
        assert xpu_collector._collect_one("10.0.0.1", "alice", 5) == xpu_collector._FAILED
