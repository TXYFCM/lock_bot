"""Collect real GPU utilization and occupying container name via SSH (xpu-smi)."""

import re
import subprocess
import time
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor

NodeUsage = namedtuple("NodeUsage", ["util", "mem", "container"])  # util/mem: float|None (%), container: str

_FAILED = NodeUsage(util=None, mem=None, container="")

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


def _parse_mem(xpu_m_output: str) -> float | None:
    """Average memory utilization across cards from `xpu-smi -m`.

    Per card: used MiB (col 18, cols[17]) / total MiB (col 19, cols[18]) * 100.
    """
    ratios = []
    for line in xpu_m_output.splitlines():
        cols = line.split()
        if len(cols) < 20:
            continue
        try:
            used = float(cols[17])
            total = float(cols[18])
        except ValueError:
            continue
        if total <= 0:
            continue
        ratios.append(used / total * 100)
    if not ratios:
        return None
    return round(sum(ratios) / len(ratios), 2)


_SSH_OPTS = [
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
]


def _ping(ip: str) -> bool:
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", "1", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return r.returncode == 0
    except Exception:
        return False


def _ssh_ok(ip: str, user: str) -> bool:
    try:
        r = subprocess.run(
            ["ssh", *_SSH_OPTS, "-o", "ConnectTimeout=2", f"{user}@{ip}", "exit"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return r.returncode == 0
    except Exception:
        return False


def _ssh_run(ip: str, user: str, remote_cmd: str, timeout: int) -> str:
    out = subprocess.check_output(
        ["ssh", *_SSH_OPTS, f"{user}@{ip}", remote_cmd],
        stderr=subprocess.STDOUT,
        timeout=timeout,
        encoding="utf-8",
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
    mem = _parse_mem(smi_m)
    pid = _parse_pid(smi)
    container = _resolve_container(ip, user, pid, timeout) if pid else ""
    return NodeUsage(util=util, mem=mem, container=container)


def collect_node_usage(node_ips: dict[str, str], config) -> dict[str, NodeUsage]:
    """Collect {node_key: NodeUsage} for the given node->ip map, with TTL caching.

    Failures (unreachable/timeout/parse error) degrade to NodeUsage(None, "").
    """
    ttl = config.get_val("XPU_USAGE_TTL", 60)
    user = config.get_val("SSH_USER", "v_qiujie04")
    timeout = config.get_val("SSH_CMD_TIMEOUT", 15)
    now = time.time()
    result: dict[str, NodeUsage] = {}
    to_fetch: dict[str, str] = {}
    for node_key, ip in node_ips.items():
        cached = _cache.get(node_key)
        if cached and now - cached[0] < ttl:
            result[node_key] = cached[1]
        else:
            to_fetch[node_key] = ip
    if to_fetch:
        with ThreadPoolExecutor(max_workers=min(16, len(to_fetch))) as ex:
            futures = {ex.submit(_collect_one, ip, user, timeout): nk for nk, ip in to_fetch.items()}
            for fut, node_key in futures.items():
                try:
                    usage = fut.result()
                except Exception:
                    usage = _FAILED
                _cache[node_key] = (time.time(), usage)
                result[node_key] = usage
    return result
