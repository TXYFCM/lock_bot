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
