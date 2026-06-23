# GPU 利用率 + 容器名查询列（DEVICE bot）

日期：2026-06-23
状态：已确认设计，待用户复核

## 背景

如流（InfoFlow）DEVICE bot 的 `/query` 输出当前是一张 5 列 Markdown 表：

```
| IP地址 | 节点状态 | 卡状态 | 使用者 | 剩余时间 |
```

这些列只反映 bot 自己的「锁定状态」（谁锁了哪张卡、还剩多久），并不反映 GPU 的**真实硬件占用**。运维希望在用户查询时也能看到：

1. **真实 GPU 利用率**（来自 `xpu-smi`，节点级平均值）
2. **占卡的 Docker 容器名**（节点级，第一个 BUSY 进程对应的容器）

参考逻辑来自 `yjb_xpu_smi/my_xpu_smi.sh`：SSH 到目标机执行 `xpu-smi` 解析利用率，再通过 `/proc/<pid>/cgroup` + `docker ps` 解析容器名。

## 与既有设计的关系（重要）

仓库中已有两份相关 spec：

- `2026-06-17-xpu-smi-realtime-usage-design.md`
- `2026-06-17-container-info-display-design.md`

它们描述的是**后台守护线程 + 缓存**方案：用 root SSH、`MONITOR_*` 配置键、周期性轮询全集群，`query` 永远读缓存、绝不阻塞 webhook。

**本设计取代上述方案**，差异如下：

| 维度 | 旧方案（2026-06-17） | 本方案（2026-06-23） |
|---|---|---|
| 触发模型 | 后台线程周期轮询 | 用户**纯 AT 不带参**时同步采集 |
| 缓存 | 常驻缓存 | 短 TTL 缓存（默认 60s） |
| SSH 身份 | root | 复用现有 `id_ed25519`（`v_qiujie04`） |
| 范围 | DEVICE + NODE | 仅 DEVICE |
| 列 | 容器名 | 利用率 + 容器名 |
| query 阻塞 | 绝不阻塞，读缓存 | 纯 AT 路径可短暂阻塞（异步 webhook 回复，不受入站超时约束） |

选择本方案的原因：避免常驻 root SSH 轮询的部署/权限负担，复用已分发好的 `v_qiujie04` 密钥；如流 webhook 回复是异步 POST，不受入站请求超时限制，所以同步采集可接受。

## 触发条件

**仅当**用户在群里纯 AT 机器人、后面不带任何参数时触发（即「查询全部」路径，`node_key is None`）。

- 带参命令（`lock`/`slock`/`unlock`/`query 指定节点` 等）**不触发** SSH，保持原 5 列表格。
- 代码路径：`handler.py` → `device_bot.query(user_id)`，`node_key=None`。

## 数据粒度

`xpu-smi` 给出的是**节点级**数据（一个节点一个平均利用率、一个容器名）；而 DEVICE 表是**每卡一行**。因此利用率与容器名**只在该节点的第一行显示**，后续行留空——与现有 `node_cell` / `node_status_cell` 的 `first_row` 逻辑一致。

## 架构

### 新模块 `python/lockbot/core/xpu_collector.py`

```python
NodeUsage = namedtuple("NodeUsage", ["util", "container"])
# util: float | None  （N/A 时为 None）
# container: str       （无容器时为 ""）

def collect_node_usage(node_ips: dict[str, str], config) -> dict[str, NodeUsage]:
    ...
```

职责（在包内用 Python 重写 `my_xpu_smi.sh` 的远程命令，**不依赖** shell 脚本 / `cmd.py`）：

1. 对每个 `(node_key, ip)` 并发处理（`ThreadPoolExecutor`）。
2. 存活检查：先 `ping`，再 `BatchMode=yes` 的 SSH 探活。不可达跳过。
3. SSH 执行远程命令：
   - `xpu-smi`：无 "No running processes found" → 有占用；取第一个 `N/A N/A <pid>` 进程的 pid。
   - 解析容器：`/proc/<pid>/cgroup` grep `docker|containerd`，提取 hash 前 7 位，`docker ps --format '{{.ID}} {{.Names}}'` 匹配出容器名。
   - `xpu-smi -m`：第 18 列平均显存、第 20 列平均利用率（本设计只用利用率）。
4. SSH 参数：`BatchMode=yes`、`StrictHostKeyChecking=no`、`UserKnownHostsFile=/dev/null`。
5. 单节点超时 `SSH_CMD_TIMEOUT`（默认 15s）。
6. 任何失败（不可达 / 超时 / 解析失败）→ `NodeUsage(util=None, container="")`。

### TTL 缓存

模块级缓存 `_cache: dict[node_key, tuple[timestamp, NodeUsage]]`，TTL 由配置 `XPU_USAGE_TTL` 控制（默认 60s）。`collect_node_usage` 内部：命中未过期缓存直接返回，否则采集后写缓存。

### node → IP 映射

从 `CLUSTER_CONFIGS` 按节点名取 IP（DEVICE 格式为 `{ip, devices}`），复用 `query_render._get_ip` 的取值逻辑。新增内部方法在 device_bot 侧组装 `{node_key: ip}` 传给采集器（仅含有效 IP 的节点）。

## 集成点

### `device_bot.query`

```python
def query(self, ...):
    ...
    if node_key is None:
        node_ips = self._node_ips()                 # {node_key: ip}
        usage = collect_node_usage(node_ips, self.config)
    else:
        usage = None
    return build_device_query(..., xpu_usage=usage)
```

### `build_device_query`

新增可选参数 `xpu_usage=None`：

- **`None`（带参 query）**：5 列表头不变，不触发 SSH。
- **非 None（纯 AT）**：7 列表头：

  ```
  | IP地址 | 节点状态 | 利用率 | 卡状态 | 使用者 | 剩余时间 | 容器名 |
  ```

  利用率、容器名只在节点第一行填充：
  - 采集成功且有数值 → `"0.0%"` 这类数值；无容器 → 容器名留空。
  - 采集失败 / 不可达 → 利用率 `"N/A"`，容器名留空（与「正常采集但无 docker 占卡」显示一致）。

## 配置项

新增 config schema 键：

| 键 | 默认 | 说明 |
|---|---|---|
| `SSH_USER` | `v_qiujie04` | SSH 登录目标机用户名 |
| `SSH_CMD_TIMEOUT` | `15` | 单节点命令超时（秒） |
| `XPU_USAGE_TTL` | `60` | 利用率/容器名缓存 TTL（秒） |

## i18n

- `zh.py`：新增 7 列表头变体键（如 `query.table_header_xpu`）。
- `en.py`：当前缺 `query.table_header`，补全 5 列与 7 列两个变体（否则回退 zh）。

## 错误处理

- 采集器吞掉所有单节点异常，降级为 `NodeUsage(None, "")`，绝不让一台机器的故障影响整张表。
- 整个采集若抛异常（极端情况），`query` 兜底为 `xpu_usage` 视为空 dict，对应所有节点显示 `N/A`。

## 测试

- `tests/core/test_query_render.py`：
  - 带参 query → 5 列、无 `xpu_usage`。
  - 纯 AT → 7 列；利用率/容器名只在首行；失败节点显示 `N/A` + 空容器名；无 docker 节点显示数值 + 空容器名。
- `tests/core/test_xpu_collector.py`：
  - mock SSH/subprocess，验证解析逻辑、超时降级、TTL 命中/过期。

## 范围（YAGNI）

- 仅 DEVICE bot。
- 仅利用率 + 容器名两列（不加显存列）。
- 不做后台轮询、不做 NODE/QUEUE、不引入 root SSH。
