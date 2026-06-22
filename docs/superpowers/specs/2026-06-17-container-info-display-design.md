# Lock Bot 容器信息展示：权限与部署方案

## 背景

Lock Bot 的 query 表格目前只展示锁状态，不反映节点上真实的 GPU 占用和容器情况。为在 lock 表格新增「实时使用率」列（利用率 % + 占用容器/工号），需要在**采集侧**拥有足够的权限，才能查到 XPU 显存资源和 docker 容器信息。

本文档整理：**采集容器信息需要哪些权限、为什么需要、如何配置**，以及部署侧的完整检查清单。

---

## 为什么必须 root 权限

### 1. 查 XPU 显存（`xpu-smi`）

`xpu-smi` 在某些发行版中要求 root 或特定设备组权限才能读取 KunLun 的显存占用信息。以非 root 普通用户执行时，`xpu-smi` 可能只返回空进程列表或报权限错误，**导致节点被误判为 FREE**。

### 2. 查 docker 容器（`docker ps`）

`docker ps` 默认只有 root 或 `docker` 组成员才能执行。若 SSH 用 `root` 登录，`docker ps` 直接可用；若用普通用户且未加入 `docker` 组，执行会报 `permission denied`，**无法获取容器名**。

### 3. 读 `/proc/<pid>/cgroup`

从进程 PID 反查容器 ID 依赖读取 `/proc/<pid>/cgroup`。普通用户只能读自己进程的 cgroup，其他用户（包括 root 以外进程）的 cgroup 文件会返回空或权限拒绝。**必须 root** 才能跨用户读取任意进程的 cgroup。

### 结论

> **部署机器必须以 `root` 身份免密 SSH 到所有被监控节点。** 这是能够同时查 XPU 显存、`docker ps`、`/proc/<pid>/cgroup` 的唯一可靠方式。

---

## 架构概述

```
部署机器（Lock Bot 容器）
  └─ 后台采集线程（MonitorCollector）
       └─ ssh root@<节点IP>  "xpu-smi + docker ps + /proc 读取"
            └─ 各被监控节点（root 免密）
```

- Lock Bot **以 docker 容器运行**，挂载宿主机的 `/root/.ssh`（只读）获取免密私钥。
- 采集线程通过 `subprocess` 调用系统 `ssh`，`BatchMode=yes`（禁止密码交互），完全依赖 `/root/.ssh` 里的免密 key。
- **数据库和 config 中不存任何密码或私钥**。

---

## 权限配置步骤

### 步骤一：在部署机器上生成 root SSH 密钥（如尚未存在）

```bash
sudo ssh-keygen -t rsa -b 4096 -f /root/.ssh/id_rsa -N ""
```

验证密钥存在：

```bash
sudo cat /root/.ssh/id_rsa.pub
```

### 步骤二：将 root 公钥分发到所有被监控节点

参考 `yjb_xpu_smi/auto_root_ssh.sh`，脚本逻辑如下：

1. 读取部署机器的 `/root/.ssh/id_rsa.pub`
2. 对 `iplist.txt` 中每台机器：
   - 通过当前用户 SSH 登录
   - `sudo su - root` 后将公钥写入 `/root/.ssh/authorized_keys`
   - 确保 sshd 配置开启了 `PermitRootLogin yes` 和 `PubkeyAuthentication yes`
   - `systemctl restart sshd`

> **注意**：此脚本以**当前登录用户**执行（非 root），依赖当前用户有该节点的 sudo 权限。如果一键脚本失败，需要手动逐台配置。

```bash
# 在 yjb_xpu_smi/ 目录下执行
chmod +x auto_root_ssh.sh
./auto_root_ssh.sh
```

### 步骤三：更新 known_hosts（root 执行）

避免 SSH 首次连接时的交互式确认提示（`StrictHostKeyChecking` 会阻塞 `BatchMode`）：

```bash
sudo bash yjb_xpu_smi/update_known_hosts.sh
```

脚本对 `iplist.txt` 中所有 IP 执行 `ssh-keyscan`，结果追加去重后写入 `/root/.ssh/known_hosts`。

### 步骤四：验证免密连通性

```bash
# 对每台节点测试 root 免密
sudo ssh -o BatchMode=yes -o ConnectTimeout=5 root@<节点IP> "xpu-smi && docker ps --format '{{.Names}}' | head -5"
```

预期：无密码提示，直接输出 `xpu-smi` 结果和容器列表。若仍提示密码，检查：

- 目标节点 `/root/.ssh/authorized_keys` 是否包含部署机器的公钥
- `sshd_config` 中 `PermitRootLogin` 是否为 `yes`
- `authorized_keys` 文件权限是否为 `600`，`/root/.ssh` 目录权限是否为 `700`

---

## 部署配置

### docker run 关键参数

Lock Bot 容器启动时必须挂载宿主机的 root SSH 目录（只读）：

```bash
docker run \
    --privileged \
    --name=lockbot \
    --restart=always \
    -dti \
    --net=host \
    --uts=host \
    --ipc=host \
    -v /root/.ssh/:/root/.ssh:ro \   # 挂载免密 key（只读）
    -v /path/to/lock_bot:/app \
    iregistry.baidu-int.com/xmlir/lockbot:latest \
    ...
```

> **重要**：不挂载 `/root/.ssh:ro`，容器内 `ssh` 命令找不到私钥，所有节点采集均失败。

### Lock Bot 配置项（`config_overrides`）

在 BotForm 高级配置中设置以下 key：

| Key | 说明 | 示例值 |
|-----|------|--------|
| `MONITOR_ENABLED` | 开启采集 | `true` |
| `MONITOR_INTERVAL` | 采集周期（秒） | `60` |
| `MONITOR_SSH_USER` | SSH 用户，必须是 root | `root` |
| `MONITOR_SSH_TIMEOUT` | 单节点超时（秒） | `15` |
| `MONITOR_STALE_SEC` | 缓存新鲜度阈值（秒） | `180` |
| `MONITOR_NODE_IPS` | 节点→IP 映射 | `{"node1": "10.x.x.1", "node2": "10.x.x.2"}` |

`MONITOR_NODE_IPS` 的 key 需与 `CLUSTER_CONFIGS` 中的节点 key 对应（DEVICE bot 对应 dev_id 上层的 node key，NODE bot 对应 node key）。

---

## 采集命令说明

默认 `MONITOR_CMD`（内置，无需手动配置）移植自 `yjb_xpu_smi/my_xpu_smi.sh`：

```bash
# emits: STATUS|MEM|UTIL|CONTAINER
out=$(xpu-smi 2>/dev/null)
if echo "$out" | grep -q "No running processes found"; then
  echo "FREE|0|0|"
else
  pid=$(echo "$out" | grep -E 'N/A  N/A\s*[0-9]+' | head -n1 | awk '{print $5}')
  cg=$(sed -E 's#.*/docker[-/]?([0-9a-f]+).*#\1#' /proc/$pid/cgroup 2>/dev/null | head -n1 | cut -c1-12)
  cname=$(docker ps -a --format '{{.ID}} {{.Names}}' 2>/dev/null | grep "$cg" | awk '{print $2}' | head -n1)
  m=$(xpu-smi -m 2>/dev/null)
  mem=$(echo "$m" | awk 'NR>1{s+=$18;n++} END{if(n)printf "%.0f",s/n; else print 0}')
  util=$(echo "$m" | awk 'NR>1{s+=$20;n++} END{if(n)printf "%.0f",s/n; else print 0}')
  echo "BUSY|$mem|$util|$cname"
fi
```

输出协议：`STATUS|MEM|UTIL|CONTAINER`（单行，`|` 分隔）。

| 字段 | 含义 | 示例 |
|------|------|------|
| STATUS | `FREE` / `BUSY` | `BUSY` |
| MEM | 节点平均显存（MiB） | `12345` |
| UTIL | 节点平均 XPU 利用率（%） | `87` |
| CONTAINER | 占用容器名（可空） | `liujie63_train` |

采集器按 `|` 分割，从 `CONTAINER` 字段提取首段（`_`/`-` 分隔）作为工号显示。

---

## 容错行为

| 场景 | 表格显示 |
|------|----------|
| `MONITOR_ENABLED=False`（默认） | 无新列，与原表格逐字符一致 |
| 节点未在 `MONITOR_NODE_IPS` 中配置 | 新列显示 `--` |
| SSH 不可达 / 超时 | 新列显示 `--` |
| 命令输出格式异常 | 新列显示 `--` |
| 数据超过 `MONITOR_STALE_SEC` 秒未更新 | 新列显示 `87%⚠`（加警示标记） |

---

## 安全说明

1. **MONITOR_CMD 只读**：默认命令仅执行 `xpu-smi`/`docker ps`/读 `/proc`，无任何写操作。自定义命令时需保证只读。
2. **不存凭据**：DB 和 config 均不含密码/私钥，鉴权完全依赖宿主机 `/root/.ssh` 免密。能采集的前提是已正确配置免密，本身就是一道权限闸。
3. **防命令注入**：IP 地址经格式校验后，以 `subprocess` 参数数组传递，不经 shell 字符串拼接。
4. **SSH 固定参数**：`BatchMode=yes`（禁密码交互）+ `ConnectTimeout` + 单节点超时，杜绝阻塞挂死。

---

## 部署检查清单

- [ ] 部署机器已生成 `/root/.ssh/id_rsa`（root 密钥对）
- [ ] 所有被监控节点的 `/root/.ssh/authorized_keys` 包含部署机器 root 公钥
- [ ] 所有被监控节点 `sshd_config` 开启 `PermitRootLogin yes`
- [ ] `/root/.ssh/known_hosts` 已包含所有被监控节点指纹（执行 `update_known_hosts.sh`）
- [ ] Lock Bot docker 容器挂载了 `-v /root/.ssh/:/root/.ssh:ro`
- [ ] 手动验证：`sudo ssh -o BatchMode=yes root@<节点IP> "xpu-smi"` 无密码提示直接输出
- [ ] `MONITOR_ENABLED=true` 和 `MONITOR_NODE_IPS` 已在 BotForm 高级配置中填写
- [ ] `MONITOR_SSH_USER=root`（默认值，确认未被覆盖为普通用户）
