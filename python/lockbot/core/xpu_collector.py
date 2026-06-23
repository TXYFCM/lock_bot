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
