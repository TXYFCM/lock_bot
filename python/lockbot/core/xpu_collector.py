"""Collect real GPU utilization and occupying container name via SSH (xpu-smi)."""

import re
import subprocess
import time
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor

NodeUsage = namedtuple("NodeUsage", ["util", "container"])  # util: float|None, container: str

_FAILED = NodeUsage(util=None, container="")

# node_key -> (fetched_at_epoch, NodeUsage)
_cache: dict[str, tuple[float, NodeUsage]] = {}

_PID_RE = re.compile(r"N/A\s+N/A\s+(\d+)")


def _parse_pid(xpu_output: str) -> str | None:
    """Return the first busy process pid from `xpu-smi`, or None when free/unparsable."""
    if "No running processes found" in xpu_output:
        return None
    m = _PID_RE.search(xpu_output)
    return m.group(1) if m else None


def _parse_util(xpu_m_output: str) -> float | None:
    """Average utilization across cards from `xpu-smi -m` (col 20, 1-indexed)."""
    utils = []
    for line in xpu_m_output.splitlines():
        cols = line.split()
        if len(cols) < 20:
            continue
        try:
            utils.append(float(cols[19]))
        except ValueError:
            continue
    if not utils:
        return None
    return round(sum(utils) / len(utils), 2)


_SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
]


def _ping(ip: str) -> bool:
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", "1", ip],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return r.returncode == 0
    except Exception:
        return False


def _ssh_ok(ip: str, user: str) -> bool:
    try:
        r = subprocess.run(
            ["ssh", *_SSH_OPTS, "-o", "ConnectTimeout=2", f"{user}@{ip}", "exit"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
        )
        return r.returncode == 0
    except Exception:
        return False


def _ssh_run(ip: str, user: str, remote_cmd: str, timeout: int) -> str:
    out = subprocess.check_output(
        ["ssh", *_SSH_OPTS, f"{user}@{ip}", remote_cmd],
        stderr=subprocess.STDOUT, timeout=timeout, encoding="utf-8",
    )
    return out


def _resolve_container(ip: str, user: str, pid: str, timeout: int) -> str:
    try:
        cgroup = _ssh_run(ip, user, f"cat /proc/{pid}/cgroup 2>/dev/null", timeout)
    except Exception:
        return ""
    m = re.search(r"docker[-/]?([0-9a-f]+)", cgroup)
    if not m:
        return ""
    short = m.group(1)[:7]
    try:
        ps = _ssh_run(ip, user, "docker ps --format '{{.ID}} {{.Names}}'", timeout)
    except Exception:
        return ""
    for line in ps.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith(short):
            return parts[1]
    return ""


def _collect_one(ip: str, user: str, timeout: int) -> NodeUsage:
    if not _ping(ip) or not _ssh_ok(ip, user):
        return _FAILED
    try:
        smi = _ssh_run(ip, user, "xpu-smi", timeout)
        smi_m = _ssh_run(ip, user, "xpu-smi -m", timeout)
    except Exception:
        return _FAILED
    util = _parse_util(smi_m)
    pid = _parse_pid(smi)
    container = _resolve_container(ip, user, pid, timeout) if pid else ""
    return NodeUsage(util=util, container=container)
