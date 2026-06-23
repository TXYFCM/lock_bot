import subprocess
from unittest import mock

from lockbot.core.xpu_collector import NodeUsage, _parse_mem, _parse_pid, _parse_util


def test_parse_util_averages_column():
    line = " ".join(["x"] * 17 + ["100", "200", "80"])  # col18=100(used) col19=200(total) col20=80(util)
    out = "\n".join([line, line])
    assert _parse_util(out) == 80.0


def test_parse_mem_averages_ratio():
    line1 = " ".join(["x"] * 17 + ["100", "200", "80"])  # used/total = 50%
    line2 = " ".join(["x"] * 17 + ["150", "200", "80"])  # used/total = 75%
    out = "\n".join([line1, line2])
    assert _parse_mem(out) == 62.5


def test_parse_mem_empty_returns_none():
    assert _parse_mem("") is None


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

    with (
        mock.patch.object(xpu_collector, "_ping", return_value=True),
        mock.patch.object(xpu_collector, "_ssh_ok", return_value=True),
        mock.patch.object(xpu_collector, "_ssh_run") as run,
    ):
        # first call: xpu-smi (free), second: xpu-smi -m (util=0)
        run.side_effect = [
            "No running processes found",
            " ".join(["x"] * 17 + ["0", "98304", "0"]),
        ]
        usage = xpu_collector._collect_one("10.0.0.1", "alice", 5)
    assert usage.util == 0.0
    assert usage.mem == 0.0
    assert usage.container == ""


def test_collect_one_busy_resolves_container():
    from lockbot.core import xpu_collector

    with (
        mock.patch.object(xpu_collector, "_ping", return_value=True),
        mock.patch.object(xpu_collector, "_ssh_ok", return_value=True),
        mock.patch.object(xpu_collector, "_ssh_run") as run,
    ):
        run.side_effect = [
            "foo N/A N/A 12345 python",  # xpu-smi
            " ".join(["x"] * 17 + ["100", "200", "82"]),  # xpu-smi -m
            "12:devices:/docker/abcdef0123456789",  # /proc/<pid>/cgroup
            "abcdef0 my_container",  # docker ps
        ]
        usage = xpu_collector._collect_one("10.0.0.1", "alice", 5)
    assert usage.util == 82.0
    assert usage.mem == 50.0
    assert usage.container == "my_container"


def test_collect_one_timeout_returns_failed():
    from lockbot.core import xpu_collector

    with (
        mock.patch.object(xpu_collector, "_ping", return_value=True),
        mock.patch.object(xpu_collector, "_ssh_ok", return_value=True),
        mock.patch.object(xpu_collector, "_ssh_run", side_effect=subprocess.TimeoutExpired("ssh", 5)),
    ):
        assert xpu_collector._collect_one("10.0.0.1", "alice", 5) == xpu_collector._FAILED


class _Cfg:
    def __init__(self, ttl=60, user="alice", timeout=5):
        self._d = {"XPU_USAGE_TTL": ttl, "SSH_USER": user, "SSH_CMD_TIMEOUT": timeout}

    def get_val(self, k, default=None):
        return self._d.get(k, default)


def test_collect_node_usage_maps_keys():
    from lockbot.core import xpu_collector

    xpu_collector._cache.clear()
    with mock.patch.object(xpu_collector, "_collect_one", return_value=NodeUsage(util=50.0, mem=30.0, container="c")):
        res = xpu_collector.collect_node_usage({"node1": "10.0.0.1"}, _Cfg())
    assert res["node1"] == NodeUsage(util=50.0, mem=30.0, container="c")


def test_collect_node_usage_uses_cache_within_ttl():
    from lockbot.core import xpu_collector

    xpu_collector._cache.clear()
    usage = NodeUsage(util=1.0, mem=2.0, container="")
    with mock.patch.object(xpu_collector, "_collect_one", return_value=usage) as co:
        xpu_collector.collect_node_usage({"node1": "10.0.0.1"}, _Cfg(ttl=60))
        xpu_collector.collect_node_usage({"node1": "10.0.0.1"}, _Cfg(ttl=60))
    assert co.call_count == 1


def test_collect_node_usage_refetches_after_ttl():
    from lockbot.core import xpu_collector

    xpu_collector._cache.clear()
    with (
        mock.patch.object(xpu_collector, "_collect_one", return_value=NodeUsage(util=1.0, mem=2.0, container="")) as co,
        mock.patch.object(xpu_collector.time, "time", side_effect=[100.0, 100.0, 1000.0, 1000.0]),
    ):
        xpu_collector.collect_node_usage({"node1": "10.0.0.1"}, _Cfg(ttl=60))
        xpu_collector.collect_node_usage({"node1": "10.0.0.1"}, _Cfg(ttl=60))
    assert co.call_count == 2
