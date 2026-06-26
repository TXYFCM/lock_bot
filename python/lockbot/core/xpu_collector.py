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
_SMI_BEGIN = "__LOCKBOT_XPU_SMI_BEGIN__"
_SMI_END = "__LOCKBOT_XPU_SMI_END__"
_SMI_M_BEGIN = "__LOCKBOT_XPU_SMI_M_BEGIN__"
_SMI_M_END = "__LOCKBOT_XPU_SMI_M_END__"
_CONTAINER_BEGIN = "__LOCKBOT_CONTAINER_BEGIN__"
_CONTAINER_END = "__LOCKBOT_CONTAINER_END__"


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


def _remote_probe_script() -> str:
    return f"""\
smi_output=$(xpu-smi 2>&1)
smi_rc=$?
smi_m_output=$(xpu-smi -m 2>&1)
smi_m_rc=$?
container=""
if [ "$smi_rc" -eq 0 ] && ! printf '%s\n' "$smi_output" | grep -q "No running processes found"; then
    pid=$(printf '%s\n' "$smi_output" | grep -E 'N/A[[:space:]]+N/A[[:space:]]+[0-9]+' | awk '{{print $NF+0, $5}}' | sort -rn | head -n 1 | awk '{{print $2}}')
    if [ -n "$pid" ] && [ -r "/proc/$pid/cgroup" ]; then
        cgroup_line=$(grep -E 'docker|containerd' "/proc/$pid/cgroup" 2>/dev/null | head -n 1)
        cid=$(printf '%s\n' "$cgroup_line" | sed -E 's#.*(docker[-/]?|containerd[-/]?)([0-9a-f]{{7,64}}).*#\\2#' | cut -c1-7)
        if [ -n "$cid" ]; then
            container=$(docker ps --format '{{{{.ID}}}} {{{{.Names}}}}' 2>/dev/null | awk -v cid="$cid" '$1 ~ "^" cid {{print $2; exit}}')
        fi
    fi
fi
printf '%s\n' '{_SMI_BEGIN}'
printf '%s\n' "$smi_output"
printf '%s\n' '{_SMI_END}'
printf '%s\n' '{_SMI_M_BEGIN}'
printf '%s\n' "$smi_m_output"
printf '%s\n' '{_SMI_M_END}'
printf '%s\n' '{_CONTAINER_BEGIN}'
printf '%s\n' "$container"
printf '%s\n' '{_CONTAINER_END}'
if [ "$smi_rc" -ne 0 ] || [ "$smi_m_rc" -ne 0 ]; then
    exit 1
fi
"""


def _ssh_collect(ip: str, user: str, timeout: int) -> str:
    proc = subprocess.run(
        ["ssh", *_SSH_OPTS, "-o", "ConnectTimeout=2", f"{user}@{ip}", "bash", "-s"],
        input=_remote_probe_script(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        encoding="utf-8",
        check=True,
    )
    return proc.stdout


def _extract_section(output: str, begin: str, end: str) -> str | None:
    begin_marker = begin + "\n"
    end_marker = "\n" + end
    start = output.find(begin_marker)
    if start == -1:
        return None
    start += len(begin_marker)
    finish = output.find(end_marker, start)
    if finish == -1:
        return None
    return output[start:finish]


def _split_remote_output(output: str) -> tuple[str, str, str] | None:
    smi = _extract_section(output, _SMI_BEGIN, _SMI_END)
    smi_m = _extract_section(output, _SMI_M_BEGIN, _SMI_M_END)
    container = _extract_section(output, _CONTAINER_BEGIN, _CONTAINER_END)
    if smi is None or smi_m is None or container is None:
        return None
    return smi, smi_m, container.strip()


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


def _collect_one(ip: str, user: str, timeout: int, container_mem_threshold: float = 0.02) -> NodeUsage:
    try:
        parts = _split_remote_output(_ssh_collect(ip, user, timeout))
    except Exception:
        return _FAILED
    if parts is None:
        return _FAILED
    _smi, smi_m, container = parts
    mem = _parse_mem(smi_m)
    if mem is not None and mem < container_mem_threshold:
        container = ""
    return NodeUsage(util=_parse_util(smi_m), mem=mem, container=container)


def collect_node_usage(node_ips: dict[str, str], config) -> dict[str, NodeUsage]:
    """Collect {node_key: NodeUsage} for the given node->ip map, with TTL caching.

    Failures (unreachable/timeout/parse error) degrade to NodeUsage(None, "").
    """
    ttl = config.get_val("XPU_USAGE_TTL", 60)
    user = config.get_val("SSH_USER", "v_qiujie04")
    timeout = config.get_val("SSH_CMD_TIMEOUT", 15)
    container_threshold = config.get_val("CONTAINER_MIN_MEM_PCT", 0.02)
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
            futures = {ex.submit(_collect_one, ip, user, timeout, container_threshold): nk for nk, ip in to_fetch.items()}
            for fut, node_key in futures.items():
                try:
                    usage = fut.result()
                except Exception:
                    usage = _FAILED
                _cache[node_key] = (time.time(), usage)
                result[node_key] = usage
    return result
