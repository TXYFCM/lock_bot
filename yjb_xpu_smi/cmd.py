import sys
import os
import subprocess
from shlex import quote
import shlex
import multiprocessing
from concurrent.futures import ThreadPoolExecutor, as_completed

# 彩色输出（可选）
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RESET = "\033[0m"

def get_self_ip():
    ip = subprocess.check_output(['hostname', '-i']).decode().strip()
    return ip


def get_ips():
    if "IP" in os.environ:
        path = os.path.join(os.path.dirname(__file__), os.environ["IP"])
    else:
        path = os.path.join(os.path.dirname(__file__), "iplist.txt")

    print(f"Reading ip list from {path}")
    with open(path, "r") as f:
        return [ip.strip().split(" ")[0] for ip in f.readlines() if ip.strip() and not ip.strip().startswith('#')]

def ping(ip):
    try:
        count_param = "-c" if os.name != 'nt' else "-n"
        result = subprocess.run(
            ["ping", count_param, "1", ip],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return result.returncode == 0
    except Exception:
        return False

def ssh_check(ip, timeout=2, total_timeout=3):
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", "BatchMode=yes",
                "-o", f"ConnectTimeout={timeout}",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                ip,
                "exit"
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=total_timeout
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False

def check_remote_host(ip):
    if not ping(ip):
        return ip, False, f"{RED}[{ip}] Unreachable (ping failed). Skipped.{RESET}"
    if not ssh_check(ip):
        return ip, False, f"{YELLOW}[{ip}] SSH unreachable. Skipped.{RESET}"
    return ip, True, f"{GREEN}[{ip}] Reachable.{RESET}"

def concat_cmd(cmd):
    return " ".join([quote(c) for c in cmd])


def execute_cmd(args):
    try:
        ip, cmd = args
        timeout = int(os.getenv('SSH_CMD_TIMEOUT', '15'))
        env = os.environ.copy()
        env["LANG"] = "C"
        env["LC_ALL"] = "C"
        # 执行命令并获取输出
        output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=timeout, encoding='utf-8', env=env)
        return output.strip()
    except subprocess.TimeoutExpired as e:
        return f"{RED}[{ip}] Execution exceeded {timeout}s. Cmd: {cmd}{RESET}"
    except subprocess.CalledProcessError as e:
        return f"{RED}[{ip}] Error {cmd}\n{e.output.strip()}{RESET}"


def sync_cmd(cmd):
    ips = get_ips()
    all_cmds = []
    cmd = "{0}".format(concat_cmd(cmd))

    # 首先并发检查所有远程 IP 状态
    results = {}
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(check_remote_host, ip): ip for ip in ips}
        # for future in as_completed(futures):
        for ip, future in zip(ips, futures):
            ip, ok, msg = future.result()
            if not ok:
                print(msg)
            if ok:
                exe_cmd = concat_cmd(['ssh', ip, cmd])
                results[ip] = exe_cmd

    if not results:
        print(f"{RED}No valid targets to run command.{RESET}")
        return

    all_cmds = list(results.values())
    num_process = min(len(all_cmds), int(os.getenv('MAX_PROCESS', len(all_cmds))))

    args = zip(ips, all_cmds)
    with multiprocessing.Pool(num_process) as pool:
        results = pool.map(execute_cmd, args)

    for output in results:
        print(output)


if __name__ == "__main__":
    sync_cmd(sys.argv[1:])

