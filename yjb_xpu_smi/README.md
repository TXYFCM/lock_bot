# yjb_xpu_smi — 批量 GPU 节点监控

批量 SSH 到 `iplist.txt` 中的所有机器，执行 `xpu-smi`，汇总每台机器的 GPU 使用情况（FREE/BUSY、显存、利用率）和占用它的 Docker 容器名。

## 输出示例

```
10.206.192.88: BUSY | Mem: 91043.50 MiB | Util: 82.75 % | Container: shijinxiang_pd_20260520_4437
10.206.192.80: FREE | Mem: 0 MiB | Util: 0 % | Container:
```

---

## 为什么要分发 SSH 私钥

### 背景问题

这套脚本运行在普通 GPU 节点上（**不是 relay 机器**）。原本想通过 `baas login` 让 BILS 转发身份到目标机器完成认证，但实测发现：

- `baas login` 报成功，但 **BILS 无法把身份转发到目标 IP**，22 台目标机器全部 SSH 认证失败。
- 脚本用的是非交互式 `subprocess` SSH，**无法完成 BILS 的交互式认证流程**（无法输入密码 / 响应提示）。

### 解决思路：用 SSH 密钥认证绕过 BILS

目标机器的 SSH 服务支持 `publickey` 认证方式。只要把公钥写入目标机器的 `~/.ssh/authorized_keys`，后续连接就能用密钥免密登录，**完全绕过 BILS**。

关键点：**认证和 relay 无关**。密钥认证只看目标机器上有没有你的公钥，跟你从哪台机器发起连接、是不是 relay 没有任何关系。

真正的限制只有两个：

| 依赖 | 说明 |
|---|---|
| **持有私钥** | 运行脚本的机器上要有 `~/.ssh/id_ed25519` 私钥 |
| **网络可达** | 运行脚本的机器要能直接路由（ping 通）到 `10.206.x.x` 这些内网 IP |

只要同时满足这两点，在任意一台机器（无论是否 relay）都能查。换到别的机器跑时，记得把私钥一起复制过去，并保持权限 `600`。

---

## 一次性准备：分发公钥

### 1. 生成 SSH 密钥（如果还没有）

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" -C "lock_bot"
```

### 2. 分发公钥到所有目标机器

```bash
cd yjb_xpu_smi
for ip in $(cat iplist.txt); do
    echo "=== 分发到 $ip ==="
    ssh-copy-id -i ~/.ssh/id_ed25519.pub v_qiujie04@$ip
done
```

> ⚠️ 这一步**需要交互**：每台机器会要求输入密码 / 完成 BILS 认证。这是因为分发本身（首次登录）还没有密钥可用，必须走一次原有认证流程。这是**一次性操作**，分发完成后即可永久免密。

### 3. 验证密钥认证已生效（可选）

```bash
ssh -o BatchMode=yes -o ConnectTimeout=3 \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    v_qiujie04@10.206.192.88 'echo OK $(hostname)'
```

返回 `OK <主机名>` 说明密钥认证成功；返回 `Permission denied (publickey,password)` 说明该机器公钥还没分发好。

---

## 日常使用：执行监控脚本

```bash
cd yjb_xpu_smi
SSH_USER=v_qiujie04 bash my_xpu_smi.sh
```

- `SSH_USER` 指定登录目标机器的用户名（必填，否则会以当前用户名直连）。

### 工作流程

1. **读取 IP 列表** — 从 `iplist.txt` 读取所有目标 IP（`#` 开头的行会被跳过）。
2. **并发健康检查** — 对每台机器先 `ping`，再用 `BatchMode=yes` 的 SSH 探活。不可达 / SSH 失败的机器会被跳过并打印警告。
3. **并发执行命令** — 对可达机器并发 SSH 执行 `xpu-smi`，解析 GPU 状态、平均显存、平均利用率，以及占用 GPU 的 Docker 容器名。
4. **汇总输出** — 每台机器一行结果。

### 可调环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SSH_USER` | 空 | 登录目标机器的用户名 |
| `IP` | `iplist.txt` | 指定其它 IP 列表文件（相对脚本目录） |
| `SSH_CMD_TIMEOUT` | `15` | 单台机器命令执行超时（秒） |
| `MAX_PROCESS` | 目标机器数 | 并发执行命令的进程数上限 |

---

## 文件说明

| 文件 | 用途 |
|---|---|
| `my_xpu_smi.sh` | 入口脚本，定义远程执行的 `xpu-smi` 解析逻辑，调用 `cmd.py` |
| `cmd.py` | 批量 SSH 框架：读 IP、并发探活、并发执行、汇总输出 |
| `iplist.txt` | 目标机器 IP 列表，每行一个 |
| `update_known_hosts.sh` | 用 `ssh-keyscan` 预填 `~/.ssh/known_hosts`（可选） |
| `auto_root_ssh.sh` | 批量配置目标机器 **root** 免密（需 sudo，按需使用） |

### 关于输出 IP 的说明

`my_xpu_smi.sh` 通过 `XPU_TARGET_IP` 环境变量（由 `cmd.py` 在发起 SSH 时注入）显示**连接用的 IP**，保证输出与 `iplist.txt` 一致。若该变量缺失，则回退为目标机器 `hostname -I` 的第一个网卡 IP（在多网卡机器上可能与连接 IP 不同）。

---

## 多用户访问

密钥认证看的是**目标机器上「登录用户」的 `~/.ssh/authorized_keys` 里有没有对应公钥**。我们分发时跑的是 `ssh-copy-id ... v_qiujie04@$ip`，公钥只写进了目标机器上 `v_qiujie04` 用户的 authorized_keys，因此默认只有 `SSH_USER=v_qiujie04` 能免密登录。

换成其它情况都会被拒（`Permission denied (publickey,password)`）：

| 场景 | 能否访问 | 原因 |
|---|---|---|
| `SSH_USER=v_qiujie04` | ✅ | 公钥已在目标机 v_qiujie04 的 authorized_keys |
| 换成别的目标用户名（如 root） | ❌ | 该用户的 authorized_keys 里没有这把公钥 |
| 别人拿走脚本，但没有私钥 | ❌ | 认证靠私钥 `id_ed25519`，他没有就过不了 |
| 别人有自己的密钥，但没分发过 | ❌ | 目标机不认他的公钥 |

### 让其他用户也能访问

**方式 1：给新用户名重新分发公钥**（推荐，各自独立身份）

```bash
cd yjb_xpu_smi
for ip in $(cat iplist.txt); do
    ssh-copy-id -i ~/.ssh/id_ed25519.pub 新用户名@$ip
done
```

之后用 `SSH_USER=新用户名 bash my_xpu_smi.sh` 运行。

**方式 2：共享私钥**

把 `~/.ssh/id_ed25519` 私钥复制给对方（`chmod 600`），对方仍用 `SSH_USER=v_qiujie04` 运行。

> ⚠️ 私钥等同于身份凭证，方式 2 相当于共享你的身份，会以 `v_qiujie04` 的权限操作所有目标机器，谨慎使用。优先选方式 1。

---

## 常见问题

**Q：所有机器都 `Permission denied (publickey)`？**
公钥没分发好，重跑「分发公钥」步骤。

**Q：所有机器都 `Unreachable (ping failed)` / `SSH unreachable`？**
当前机器无法路由到目标内网段。确认你在能直达 `10.206.x.x` 的网络环境里。

**Q：换了一台机器跑就连不上？**
新机器上没有私钥。把 `~/.ssh/id_ed25519` 复制过去（`chmod 600`），并确认新机器网络可达目标 IP。
