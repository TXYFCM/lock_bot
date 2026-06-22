# 集群实时使用率采集与展示（xpu-smi）

## 背景与目标

当前 query 表格的数据全部来自机器人**自身的锁状态**（谁 lock 了、剩多久），并不反映机器上 GPU 的**真实占用**。一台被 lock 的机器可能根本没在跑任务，一台空闲（未 lock）的机器也可能有人绕过机器人直接在用。运维无法从表格判断真实利用率。

`yjb_xpu_smi/` 已有一套**按需**方案：每次 AT 机器人时现场 SSH 到各节点跑 `xpu-smi`，解析显存/利用率，再 docker 反查占用容器。缺点是同步阻塞、AT 一次抖一次、节点多时延迟高。

目标：

1. 新增一列「**实时使用率**」到 query 表格，展示每个节点的真实 GPU 利用率 + 真实占用者。
2. 为节点/设备配置一个**可选的 IP 地址**，作为采集目标。
3. 用一个**独立后台采集线程** + 缓存，周期性 SSH 采集，与 webhook 的 `query()` 路径解耦——查询永远读缓存，绝不现场 SSH。

## 设计决策（已确认）

| # | 维度 | 决策 |
|---|------|------|
| 1 | 采集方式 | **独立后台守护线程 + 缓存**。webhook `query()` 只读缓存快照，不阻塞、不现场 SSH。 |
| 2 | xpu-smi 解析 | **命令可配 + 结构化解析**。远程命令由 `MONITOR_CMD` 配置，输出按固定协议结构化解析为 `{status, mem, util, container}`。 |
| 3 | 实现范围 | **仅 Platform 模式**（FastAPI + BotManager）。Standalone Flask 不动。 |
| 4 | SSH 鉴权 | **依赖宿主机免密 key**（容器挂载 `/root/.ssh:ro`）。数据库**不存任何凭据**。 |
| 5 | 新列内容 | **利用率% + 实际占用者**。占用者 = 容器名前缀→工号（Doc 2 反查法）。 |

## 配置项

复用现有 `config_overrides` 机制（[router.py `_build_config_dict`](../../../python/lockbot/backend/app/bots/router.py)）。管理员在网页为单个机器人设置，立即随该机器人采集与 query 生效。

在 [config.py `_CONFIG_SCHEMA`](../../../python/lockbot/core/config.py) 新增以下 key，均 `env=False`：

| Key | 作用 | 取值 | 默认值 |
|---|---|---|---|
| `MONITOR_ENABLED` | 采集总开关 | bool | `False` |
| `MONITOR_INTERVAL` | 采集周期（秒） | int | `60` |
| `MONITOR_CMD` | 远程只读命令 | str | 默认 xpu-smi 流水线（见下） |
| `MONITOR_SSH_USER` | SSH 登录用户 | str | `"root"` |
| `MONITOR_SSH_TIMEOUT` | 单节点 SSH 超时（秒） | int | `15` |
| `MONITOR_STALE_SEC` | 缓存新鲜度阈值（秒），超过判定为 stale | int | `180` |
| `MONITOR_NODE_IPS` | 节点→IP 映射 | dict `{node_key: ip}` | `{}` |

**默认 `MONITOR_ENABLED=False`**：不配置则功能完全关闭，query 表格与现状逐字符一致（不变量见下）。

`MONITOR_NODE_IPS` 是与 `CLUSTER_CONFIGS` **平行的独立映射**，不侵入现有 `CLUSTER_CONFIGS` 的严格类型校验（NODE/QUEUE→str、DEVICE→list）。某节点未在此 map 中 → 该节点新列显示 `--`（未配置采集）。

## 远程命令与解析协议

`MONITOR_CMD` 是一段**只读** shell 命令，在每个目标节点上远程执行，必须向 stdout 输出**单行固定格式**：

```
STATUS|MEM|UTIL|CONTAINER
```

| 字段 | 含义 | 示例 | 解析 |
|------|------|------|------|
| STATUS | 节点占用状态 | `BUSY` / `FREE` | 字符串 |
| MEM | 平均显存（MiB） | `12345` | float，失败→None |
| UTIL | 平均利用率（%） | `87` | float，失败→None |
| CONTAINER | 占用容器名（可空） | `zhangsan_train` | 字符串，可空 |

**默认 `MONITOR_CMD`**（移植自 [yjb_xpu_smi/my_xpu_smi.sh](../../../yjb_xpu_smi/my_xpu_smi.sh)，改为只读 + 固定分隔符输出）：

```bash
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

**结构化解析**：采集器按 `|` split 成 4 段，逐段转换；任何不符合协议的输出（空行、报错文本、段数不足）→ 该节点标记为 `error` 状态，新列显示 `--`，并记 WARNING 日志。管理员可换成 `nvidia-smi`/`npu-smi` 流水线，只要遵守 `STATUS|MEM|UTIL|CONTAINER` 协议即可。

## 占用者反查（容器名→工号）

依据 [Doc 2：查看 root 进程真正用户] 的方法，节点管理规范要求**所有容器名以使用者工号为前缀**。采集器对 `CONTAINER` 字段提取工号：

- 取容器名按分隔符（`_` / `-`）切分后的**首段**作为工号候选。
- 若首段匹配工号模式（字母+数字，长度合理）→ 作为占用者展示；否则原样展示容器名。
- `CONTAINER` 为空（FREE 节点）→ 占用者为空。

工号提取放在采集侧（`occupant_from_container`），渲染侧只消费已解析好的字符串，保持渲染层纯粹。

## 采集架构

把「数据采集」与「query 渲染」彻底解耦：采集线程后台跑，写缓存；query 读缓存。两者唯一接触点是**线程安全的缓存快照**。

### MonitorCollector（新建 `python/lockbot/core/monitor.py`）

```
class MonitorCollector:
    # 由 BotManager 持有，进程级单例式（每个启用监控的 bot 注册自己的节点→IP）
    register(bot_id, node_ips: dict, cmd, ssh_user, ssh_timeout, interval)
    unregister(bot_id)
    start()            # 启动后台 daemon 线程（幂等）
    stop()             # 停止线程
    snapshot(bot_id) -> dict[node_key, NodeMonitor]   # 线程安全读
```

- **单一后台 daemon 线程**，与 `BotScheduler` 同构（[scheduler.py](../../../python/lockbot/core/scheduler.py) 的线程模型）：`threading.Thread(daemon=True)` + `Event` 唤醒 + 周期轮询。
- **跨 bot 去重**：多个 bot 可能配同一个 IP。采集线程按 **IP 维度**去重采集（同一 IP 一个周期只 SSH 一次），再把结果按 node_key 分发回各 bot 的快照。
- **并发采集**：一个周期内对多个 IP 用 `ThreadPoolExecutor` 并发 SSH（移植 [cmd.py](../../../yjb_xpu_smi/cmd.py) 的 liveness 思路：先 ping/连通性，再执行），单节点 `MONITOR_SSH_TIMEOUT` 硬超时，单节点失败不影响其它节点。
- **SSH 调用**：`subprocess` 调 `ssh`，参数固定 `BatchMode=yes / ConnectTimeout / StrictHostKeyChecking=no / UserKnownHostsFile=/dev/null`（同 yjb_xpu_smi）。**不传任何密码/密钥**，完全依赖宿主机 `~/.ssh` 免密（决策 4）。

### 缓存与快照

```
NodeMonitor = {
    "status": "busy" | "free" | "error" | "unknown",
    "util": float | None,        # 利用率 %
    "mem": float | None,         # 平均显存 MiB
    "occupant": str,             # 工号 / 容器名 / ""
    "updated_at": float,         # epoch 秒，采集完成时间
}
```

- 缓存是 `dict[bot_id, dict[node_key, NodeMonitor]]`，所有读写持 `threading.Lock`。
- `snapshot(bot_id)` 返回**浅拷贝**，query 渲染拿到的是一致性快照，不会读到半更新状态。
- **新鲜度**：渲染时比较 `now - updated_at` 与 `MONITOR_STALE_SEC`；超阈值 → 新列追加 stale 标记（如 `87%⚠`），让用户知道数据可能过时；从未采集到 → `--`。

## 新列渲染

新列加在 query 表格，由 [query_render.py](../../../python/lockbot/core/query_render.py) 的 `build_device_query` / `build_node_query` 渲染。

- 两个 build 函数**新增参数 `monitor_snapshot=None`**（默认 None → 不显示新列，保证不变量）。
- 表头 [i18n table_header](../../../python/lockbot/core/i18n/zh.py) 当 snapshot 存在时使用**带新列的表头**；否则用现有表头。为避免 i18n 维护两份表头，渲染层动态拼接「实时使用率」列，或新增 `query.table_header_monitor` 一条文案。
- 新列单元格内容：`{util}% {occupant}`，stale 时加标记，error/未配置 → `--`。
- 新列挂在**节点首行**（与「节点状态」列同列对齐），续行留空——与现有 `node_cell`/`node_status_cell` 的 `first_row` 逻辑一致。

### DEVICE 逐卡对齐风险（重要）

`xpu-smi -m` 的每张卡顺序与 lockbot 的 `dev_id` 顺序**不保证一致**（与现有 `device_usage.hetero_warning` 警示同源：CUDA_VISIBLE_DEVICES 按算力编号、nvidia-smi 按 PCIe 编号）。因此：

- **第一版只做节点级聚合**（节点平均利用率 + 节点占用者），不尝试把利用率落到单张卡的行上。
- 节点级数字挂在节点首行，避免与 `dev_id` 的错位歧义。逐卡对齐留待后续（需要稳定的卡序映射，YAGNI）。

## Platform 集成

仅 Platform 模式（决策 3）：

1. **配置透传** [router.py `_build_config_dict`](../../../python/lockbot/backend/app/bots/router.py)：`MONITOR_*` 已在 `config_overrides` 里，随 `overrides` 自动并入 config，无需特殊处理；只需确认 `MONITOR_NODE_IPS` 不被 `_normalize_cluster_configs` 影响（它只动 `CLUSTER_CONFIGS`）。
2. **生命周期** [manager.py BotManager](../../../python/lockbot/backend/app/bots/manager.py)：
   - `start_bot`：若 `config.MONITOR_ENABLED` → `collector.register(bot_id, ...)` 并 `collector.start()`（幂等）。
   - `stop_bot`：`collector.unregister(bot_id)`。
   - `shutdown_all`：`collector.stop()`。
   - Collector 作为 `BotManager` 的成员（类比 `self._scheduler`），进程级单例。
3. **快照注入 query**：webhook → `device_bot.query()` / `node_bot.query()` 在持锁构造 query 文本时，从 collector 取 `snapshot(bot_id)` 传给 `build_*_query(..., monitor_snapshot=snap)`。bot 通过注入的回调拿 snapshot，避免 core 层直接依赖 backend。
4. **校验** [schemas.py](../../../python/lockbot/backend/app/bots/schemas.py)：`_validate_config_overrides` 增加 `MONITOR_INTERVAL`/`MONITOR_SSH_TIMEOUT`/`MONITOR_STALE_SEC` 的整数范围校验，`MONITOR_NODE_IPS` 校验为 `{str: str}`。
5. **前端** [BotForm.vue](../../../frontend/src/views/BotForm.vue)：在高级配置区为节点新增可选 IP 输入；`MONITOR_ENABLED` 开关。沿用通用 `config_overrides` 也可，无强制前端改动（与上一版 spec 同策略）。

## 安全

采集线程会以 `MONITOR_SSH_USER`（默认 `root`）SSH 到**所有配置 IP** 的节点并执行命令，blast radius 大，必须约束：

- **MONITOR_CMD 必须只读**。默认命令仅 `xpu-smi`/`docker ps`/读 `/proc`，无任何写操作。管理员自定义命令时需自行保证只读——文档明确警示。
- **不存凭据**（决策 4）：DB 与 config 都不含密码/私钥，鉴权 100% 靠宿主机免密 key。换言之，能采集的前提是宿主机已对目标节点免密——这本身就是一道权限闸。
- **SSH 参数固定**：`BatchMode=yes`（禁交互、禁密码提示）杜绝阻塞；`ConnectTimeout` + 单节点超时杜绝挂死。
- **命令注入防护**：node_ip 与命令拼接时严格用参数数组（`subprocess` 列表参数），不走 shell 字符串插值；IP 先做格式校验。

## 容错

| 场景 | 处理 |
|------|------|
| 某节点 SSH 超时/不可达 | 该节点 `status=error`，新列 `--`，记 WARNING；不影响其它节点与整张表 |
| 输出不符合协议 | 该节点 `status=error`，新列 `--`，记 WARNING |
| 采集线程内部异常 | 捕获并记日志，线程存活，下个周期重试（同 scheduler 的 `_run` 容错） |
| collector 未启动 / snapshot 为空 | query 走「无新列」分支，等价于功能关闭 |
| `MONITOR_ENABLED=False` | 完全不注册、不起线程、不加列 |

## 默认行为不变量

- `MONITOR_ENABLED=False`（默认）时：不创建采集线程、不 SSH、query 输出与当前 release **逐字符一致**（`build_*_query` 的 `monitor_snapshot=None` 分支等价于现有代码路径）。
- 启用监控但某 bot 无任何 IP 配置时：采集线程对该 bot 无目标，query 新列全 `--`，其余列不变。

## 测试方案

- **新增** `tests/core/test_monitor.py`：
  - 解析：合法 `BUSY|12345|87|zhangsan_x` → `{status:busy, mem:12345, util:87, occupant:zhangsan}`；`FREE|0|0|` → free；段数不足/空行/报错文本 → error。
  - 占用者：`occupant_from_container` 各种容器名 → 工号/原名/空。
  - 缓存：`register` + 注入 fake SSH（monkeypatch SSH 执行函数返回固定字符串）→ `snapshot` 返回正确结构。
  - 跨 bot 去重：两 bot 配同 IP，一个周期内该 IP 只被采集一次。
  - 新鲜度：`updated_at` 超 `MONITOR_STALE_SEC` → snapshot 标记 stale。
  - 超时/异常：fake SSH 抛超时 → 该节点 error，不冒泡。
- **新增** `tests/core/test_query_render_monitor.py`：
  - `build_device_query` / `build_node_query` 传 `monitor_snapshot` → 表头含「实时使用率」、节点首行含 `87% zhangsan`。
  - `monitor_snapshot=None` → 输出与无新列逐字符一致（不变量）。
  - stale 标记、error→`--`、未配置→`--`。
- **新增** `tests/core/test_config.py`：`MONITOR_*` 默认值与覆盖。
- **新增** backend：`manager` 在 `MONITOR_ENABLED` 下 `start_bot` 会 `register`+`start`，`stop_bot` 会 `unregister`（用 fake collector 或 monkeypatch 验证调用）。
- **回归**：现有 `test_query_render` / `test_device_bot` / `test_node_bot` / `test_queue_bot` 全绿（默认关闭，零影响）。
- **关键不变量**：`MONITOR_ENABLED=False` 时 query 逐字符复现当前输出。

## 网页前端

`MONITOR_*` 是通用 key-value 高级配置，经现有 `config_overrides` 入口即可填写，**前端无需改动**即可使用。可选增强（本 spec 不强制）：BotForm 高级配置区加 `MONITOR_ENABLED` 开关 + 节点 IP 表格输入控件。

## 不做（YAGNI）

- 不做 DEVICE 逐卡利用率落行（卡序映射不稳定，先节点级聚合）。
- 不做 Standalone Flask 模式支持（决策 3，仅 Platform）。
- 不在 DB 存任何 SSH 凭据（决策 4，靠宿主机免密 key）。
- 不做采集历史持久化 / 时序图表（只缓存最新快照）。
- 不做现场 SSH 兜底（query 永远读缓存，绝不阻塞 webhook）。
- 不做密码/密钥分发管理（不属于本机器人职责）。
