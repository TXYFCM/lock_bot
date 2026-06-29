# 开发机集群 XPU 资源监控仪表盘

纯前端单页应用，实时展示百度百舸 GPU 集群的 XPU 使用率、显存占用率，以及 Lock Bot 平台的资源锁定状态。

**集群**：`wxtky02-p800-backup-8nic-vd`（45 节点 × 8 卡，node1~node51，排除 13/14/17/36）+ bdc9/19/28（仅展示 Lock Bot 占用，无 Monquery 监控数据）。

## 快速开始

```bash
cd /home/users/v_qiujie04/monitor
node proxy.js                    # 启动本地代理
# 浏览器打开 http://localhost:8900/index.html
```

在页面用 **Lock Bot 平台账号密码** 登录。Token 自动保存到 localStorage，4 小时有效期内刷新无需重登。

> **前置条件**：接入百度内网，能访问 Lock Bot（`10.206.192.17:8875`）和 Monquery（`api.mt.noah.baidu.com:8557`）。

## 功能

### 资源总览
- 顶部 6 张统计卡片：总节点 / LOCKED 节点 / BUSY 节点 / 总卡数 / LOCKER 卡数 / BUSY 卡数
- BUSY 节点数为非 FREE 节点总数（PARTIAL + BUSY），LOCKED 按 Lock Bot 活跃锁统计

### 状态栏 & 降级策略
- 🟢 绿色「数据正常」— Lock Bot + Monquery 双通道正常
- 🟡 黄色「监控数据获取失败」— Monquery 不可达，利用率显示 `--`，占用信息仍可见
- 🔴 红色「所有 Bot 离线」— Lock Bot 全挂，保留上次节点列表
- 双 API 均不可达且连续失败 ≥3 次 → 全页错误覆盖层，可手动重试或返回登录
- 自动刷新失败 → Toast 浮动通知（5 秒消失），不覆盖现有数据

### 节点列表
- **状态徽章**：FREE（绿）/ PARTIAL（黄）/ BUSY（红）/ 无数据（灰）
- **展开按钮**：点击「展开」查看 8 卡详情（利用率 + 锁状态），点击「收起」折叠
- **类型标签**：DEVICE（紫）/ NODE（灰），区分 Bot 类型
- **异常检测**：
  - 匿名占用（BUSY 或 PARTIAL 但无 Lock Bot 锁）→ 橙色「匿名占用」
  - 疑似僵尸锁（FREE 但有 Lock Bot 锁）→ 红色「疑似僵尸锁」
- **占用红条**：Lock Bot 当前锁 + 当天历史占用，显示用户名，hover 查看时间段
- **双线折线图**：节点级 Canvas 叠加 XPU 使用率（蓝色）+ 显存利用率（橙色），底部 1 小时间隔微网格
- **卡片详情**：展开节点后每张卡独立迷你折线图（24px）+ 当前 XPU/显存利用率 + 锁标记
- **红色时间线**：当前时刻竖线 + 时间标签，每分钟自动推进

### 筛选 & 排序
- 按状态筛选（全部 / FREE / PARTIAL / BUSY）
- 按 XPU 利用率降序（`currentUtil`）
- 按显存利用率降序（`avgMemUtil[nowIdx]`）
- 关键字搜索（节点名模糊匹配）

### 交互细节
- Canvas hover 快照恢复 + 增量画点，避免全路径重绘
- 展开/收起节点保持滚动位置不变（`instant` 回位）
- 页面不可见时自动跳过刷新，切回后立即刷新

### 渐进式渲染
- 首屏：Lock Bot 数据 1-3 秒先出（节点名、占用状态、红条即时可见）
- 补全：Monquery 数据到达后补上利用率 + 显存（可能 10-30 秒，~5MB）
- 任一 API 失败不阻断全局

### 自动刷新
- 每 60 秒自动拉取全部数据（Lock Bot 状态 + 历史占用 + Monquery）
- 仅页面可见时执行

## 架构

```
浏览器 (index.html — ES Module, 约 1000 行)
  │
  ├─ api.js         HTTP 请求层（30s 超时 AbortController）
  │   ├─ loginLockBot(token)              → POST /lockbot/api/auth/login
  │   ├─ fetchLockBotList(token)          → GET  /lockbot/api/bots
  │   ├─ fetchLockBotState(botId, token)  → GET  /lockbot/api/bots/{id}/state
  │   ├─ fetchLockBotOccupancy(botId, date, token) → GET  /lockbot/api/bots/{id}/occupancy?date=
  │   └─ fetchMonqueryUtilization(start, end) → GET  /monquery/monquery/getHistoryitemdata
  │                                              (44 nodes × 17 metrics, interval=300)
  │
  ├─ adapter.js     数据适配层
  │   ├─ adaptNodeData(lockBotState, monqueryData, nowIdx, botType, occupancyHistory) → NodeData[]
  │   └─ 辅助：toSlotIndex / parseSlotFromTimestamp / groupHistoryOccupations /
  │           deriveMemOccupations / fillUtilArray / buildOccupationRange
  │
  └─ index.html      渲染 & 交互
      ├─ loadAllData（两阶段：Lock Bot 先行，Monquery 后补）
      ├─ adaptAndRender（合并多 Bot → 适配 → 排序 → renderStats + renderList）
      ├─ renderStats / renderList / getFiltered（过滤 + 排序）
      ├─ drawUtilLine / captureLayerSnapshot / bindCanvasHover（Canvas 折线图 + 快照 hover）
      ├─ bindTooltips（占用块 hover 时间）
      ├─ addGlobalNowLine / updateNowIdx / calcNowIdx（红色时间线 + 每 60s 推进）
      └─ startAutoRefresh（每 60s 自动刷新，页面隐藏时跳过）
```

### 文件职责

| 文件 | 作用 |
|------|------|
| `index.html` | 前端仪表盘 UI + 内联 CSS + ES Module（约 1000 行） |
| `api.js` | API 调用层（纯 fetch 封装，无业务逻辑） |
| `adapter.js` | 数据适配层（原始 API 响应 → `NodeData[]`） |
| `proxy.js` | 本地代理（Node.js 原生 http 模块，推荐） |
| `proxy.py` | 本地代理（Python 3 备用） |
| `config.json` | 部署配置（代理端口 + 后端地址） |
| `config.example.json` | 配置模板，新环境直接复制修改 |
| `deploy.sh` | 一键部署脚本 |
| `pm2.config.cjs` | PM2 进程守护配置 |
| `xpu-monitor.service` | systemd 服务单元 |
| `CLAUDE.md` | 项目文档（给 AI 助手用） |

### 代理路由

代理启动后按以下映射转发请求：

| 前端路径 | 转发到 |
|----------|--------|
| `/lockbot/*` | `http://10.206.192.17:8875/*` |
| `/monquery/*` | `http://api.mt.noah.baidu.com:8557/*` |

后端地址通过 `config.json` 配置，支持环境变量覆盖（见部署章节）。

## 核心数据结构

```js
NodeData = {
  name: "node1",                     // 节点名（"node1" 或 "bdc9"）
  status: "FREE" | "BUSY" | "PARTIAL",  // 逐卡利用率判定，见关键逻辑
  currentUtil: 45.2,                 // 当前 5 分钟槽平均 XPU 使用率
  currentMemUtil: 32.1,              // 当前 5 分钟槽平均显存占用率
  avgUtil:    number[288],           // 288 槽平均 XPU 使用率（节点级）
  avgMemUtil: number[288],           // 288 槽平均显存利用率（8 卡均值）
  cardUtils:    number[8][288],      // 8×288 单卡 XPU 使用率
  cardMemUtils: number[8][288],      // 8×288 单卡显存利用率
  occupations:     [{start, end, user}],     // 节点级占用（当前锁 + 历史）
  cardOccupations: [{start, end, user}][8],  // 每卡 Lock Bot 锁记录
  cardMemOccupations: [{start, end}][8],     // 每卡显存推导占用
  botType: "DEVICE" | "NODE",        // Bot 类型
  hasMonqueryData: boolean,          // 是否有监控数据（bdc 为 false）
  hasActiveLock: boolean,            // 当前是否有活跃 Lock Bot 锁
  cardHasActiveLock: boolean[8],    // 逐卡 Lock Bot 锁状态
}
```

**时间轴**：0-287 索引 = 全天 288 个 5 分钟槽（`index = hour * 12 + floor(minutes / 5)`，0 = 00:00，287 = 23:55）。

## API 说明

### Lock Bot

| 项目 | 值 |
|------|-----|
| 地址 | `http://10.206.192.17:8875` |
| 鉴权 | JWT Bearer Token（`POST /api/auth/login` 获取） |

| 接口 | 方法 | 用途 |
|------|------|------|
| `/api/auth/login` | POST | 登录，返回 JWT token |
| `/api/bots` | GET | 获取用户所有 Bot 列表（含 bot_type） |
| `/api/bots/{id}/state` | GET | 获取 Bot 当前锁定状态 |
| `/api/bots/{id}/occupancy?date=YYYY-MM-DD` | GET | 获取 Bot 某天历史占用记录 |

Bot 类型：
- **NODE** — 整机锁定，state 格式 `{节点名: {status, current_users}}`
- **DEVICE** — 单卡锁定，state 格式 `{节点名: [{dev_id, status, current_users}]}`
- **QUEUE** — 整机 + 排队（当前未开通）

Occupancy API 返回格式：`[{node_key, user_id, start_time, end_time, duration_seconds}]`，时间字段为 ISO 字符串。

### Monquery（监控 3.0）

| 项目 | 值 |
|------|-----|
| 地址 | `http://api.mt.noah.baidu.com:8557` |
| 鉴权 | 无需（内网直接访问） |

| 接口 | 方法 | 用途 |
|------|------|------|
| `/monquery/getHistoryitemdata` | GET | 批量获取历史监控数据 |
| `/monquery/getItemList` | GET | 获取可用指标列表 |

查询参数：
- `namespaces` — 节点命名空间（逗号分隔，不支持通配符）
- `items` — 指标名（逗号分隔）
- `start` / `end` — 时间范围 `YYYYMMDDHHmmss`
- `interval` — 采样间隔（秒，当前 300 = 5 分钟）

单节点查询 17 个指标：`XPU_AVERAGE_UTILIZATION` + 8×`XPU{c}_XPU_UTILIZATION` + 8×`XPU{c}_MEM_UTILIZATION`。

## 关键逻辑

### 时间槽计算
```js
// Unix 秒 → 北京时间 5 分钟槽索引
toSlotIndex(ts) = Math.floor(((ts + 28800) % 86400) / 300)
// +28800 = UTC+8 偏移，% 86400 = 当天秒数，/ 300 = 5 分钟槽
```

`parseSlotFromTimestamp()` 统一处理三种格式：
- Unix 秒（≤ 1e12）
- Unix 毫秒（> 1e12，自动 /1000）
- ISO 字符串：**无时区标识时追加 `Z` 强制按 UTC 解析**（Lock Bot occupancy API 返回 UTC 时间但不带时区标识）

### 有效槽索引

```
nowIdx = hours * 12 + Math.floor(minutes / 5)   // 客户端当前时间槽
effectiveIdx = Math.max(0, nowIdx - 1)           // 回退一个槽
```

Monquery 数据有 0-5 分钟延迟，`nowIdx` 指向的槽数据尚未到达。**所有利用率读取统一使用 `effectiveIdx`**，避免读到未填充的 0 值导致误判 FREE。

### 节点状态判定

逐卡判定 8 张卡在 `effectiveIdx` 槽的利用率，阈值 **10%**（XPU 使用率 或 显存占用率）：

```
逐卡：cardMemUtils[c][effectiveIdx] >= 10 || cardUtils[c][effectiveIdx] >= 10 → 该卡 BUSY
节点：busyCards == 0 → FREE / busyCards == 8 → BUSY / 其他 → PARTIAL
bdc 节点（无 Monquery）：hasActiveLock ? BUSY : FREE
```

### 统计栏卡片规则

6 张统计卡片，分类规则：

| 卡片 | 有 Monquery 数据的节点 | 无 Monquery 的节点 (bdc) |
|------|---------------------|------------------------|
| **BUSY 节点** | busyCards > 0 | hasActiveLock → BUSY |
| **BUSY 卡数** | memUtil≥10 或 util≥10 逐卡判定 | hasActiveLock → 8，否则 0 |
| **LOCKED 节点** | hasActiveLock | hasActiveLock |
| **LOCKED 卡数** | cardHasActiveLock[c] 逐卡求和 | cardHasActiveLock[c] 逐卡求和 |

关键区分：**BUSY = 实际使用**（利用率），**LOCKED = Lock Bot 锁**。有监控数据的节点两者独立统计；bdc 节点无法获取利用率，BUSY = LOCKED。

### Lock Bot 锁判定

```
hasActiveLock        → 节点是否有活跃锁（任一卡或整机）
cardHasActiveLock[c] → 第 c 张卡是否有活跃锁

DEVICE bot：逐卡 dev.status !== 'idle' && current_users.length > 0
            hasActiveLock = 任一卡为 true
NODE bot：  整机 state.status !== 'idle' && current_users.length > 0
            cardHasActiveLock 全部 8 卡填相同值
```

### 利用率颜色阈值

```
utilClass(val, hasData):
  val >= 50 → high（红色）  // 高负载
  val >= 20 → mid（橙色）   // 中等负载
  val < 20  → low（绿色）   // 低负载
  !hasData  → nodata（灰色）// 无数据

### 异常检测

比较 Lock Bot 锁状态与 Monquery 利用率，标记三种不一致：

```
匿名占用：node.status ∈ {BUSY, PARTIAL} && !hasActiveLock
         → 橙色「匿名占用」badge（GPU 在使用但未通过 Lock Bot 锁）
丧尸锁：  node.status === FREE && hasActiveLock
         → 红色「疑似僵尸锁」badge（Lock Bot 有锁但 GPU 实际空闲）
一致：    其余情况 → 无额外标记
```

bdc 节点（无 Monquery 数据）不参与异常检测。

### 多 Bot 合并策略
- 登录后拉取用户所有 Bot
- 同一节点出现在多个 Bot → **先到先得**（不再 DEVICE 优先）
- botType 跟随先命中的 Bot 类型
- 合并后排序：node 在前 bdc 在后，各自按 ID 升序

### 两阶段渲染
```
loadAllData():
  1. Promise.all([所有 Bot 状态, 所有 Bot 历史占用]) → 秒出
  2. 若 nodes 中尚未有 Monquery 数据（首次加载），先渲染 Lock Bot 先行版
     → adaptAndRender(stateResults, null, status, occupancyHistory)
     → 节点名、占用红条即时可见，利用率显示 --
  3. await fetchMonqueryUtilization(start, end) → 可能 10-30s
  4. adaptAndRender(stateResults, monqueryData, status, occupancyHistory)
     → 补上利用率 + 显存数据
  5. 自动刷新时跳过步骤 2（hasExistingData 为 true），等 Monquery 数据到达后一次渲染
```

### 占用数据合并
- 当前活跃锁（来自 state API）+ 当天历史锁（来自 occupancy API）
- 按 `start,end,user` 三元组去重（相同记录只保留一份）
- **DEVICE 类型**：每卡独立记录锁，节点级占用取 8 卡时间范围并集（最左的 start ~ 最右的 end）
- **NODE 类型**：当前锁 + 历史锁均写入所有 8 卡（整机锁定）

### 显存推导占用
```js
deriveMemOccupations(memUtil288):
  逐槽扫描，显存 ≥ 10% 视为占用，连续占用槽合并为一条记录
  用于在无 Lock Bot 锁时推断实际使用情况（当前用于卡片展开视图）
```

## 部署

### 新环境快速部署

```bash
cd /home/users/v_qiujie04/monitor
bash deploy.sh                # 自动检查环境 + 复制配置 + 启动
```

首次部署从 `config.example.json` 自动创建 `config.json`。

### 环境变量覆盖

不修改 `config.json` 也可通过环境变量覆盖后端地址：

| 变量 | 对应配置 | 默认值 |
|------|---------|--------|
| `PROXY_PORT` | 代理监听端口 | 8900 |
| `LOCKBOT_HOST` | Lock Bot 服务 IP | 10.206.192.17 |
| `LOCKBOT_PORT` | Lock Bot 服务端口 | 8875 |
| `MONQUERY_HOST` | Monquery 服务 IP | api.mt.noah.baidu.com |
| `MONQUERY_PORT` | Monquery 服务端口 | 8557 |

```bash
LOCKBOT_HOST=192.168.1.100 LOCKBOT_PORT=8155 bash deploy.sh
```

### 进程守护

**PM2**：
```bash
pm2 start pm2.config.cjs
pm2 save
```

**systemd**：
```bash
sudo cp xpu-monitor.service /etc/systemd/system/
sudo systemctl enable --now xpu-monitor
```

### 手动启动

```bash
node proxy.js                    # 前台运行
nohup node proxy.js > /tmp/proxy.log 2>&1 &   # 后台运行
```

## 开发调试

### 本地开发

```bash
cd /home/users/v_qiujie04/monitor
node proxy.js                    # 启动代理
# 修改 index.html / api.js / adapter.js 后，pkill -f proxy.js 再重新启动
# 或者用 xpu-monitor-restart skill 一键重启 + 验证
```

### 诊断 API 连通性

```bash
# Monquery 单节点查询
curl -s "http://localhost:8900/monquery/monquery/getHistoryitemdata?namespaces=wxtky02-p800-backup-8nic-vd-node1.wxtky02&items=XPU_AVERAGE_UTILIZATION&start=20260624000000&end=20260624170000&interval=300"

# Lock Bot 登录测试（需真实账号）
curl -s -X POST http://localhost:8900/lockbot/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"YOUR_USER","password":"YOUR_PASS"}'

# Lock Bot 历史占用查询
curl -s -H 'Authorization: Bearer TOKEN' \
  "http://localhost:8900/lockbot/api/bots/1/occupancy?date=$(date +%Y-%m-%d)"
```

### 查看完整指标列表

```bash
curl -s "http://localhost:8900/monquery/monquery/getItemList?namespaces=wxtky02-p800-backup-8nic-vd-node1.wxtky02" | python3 -m json.tool
```

### 新增节点

编辑 `api.js` 的 `MONITORED_NODES` 数组，将新节点编号加入（当前排除 `[13, 14, 17, 33, 36]`），重启代理生效。

### 调整监控粒度

修改 `api.js` 中 `interval` 参数（当前 300 秒）。同步更新 `adapter.js` 的 `SLOT_COUNT` 和 `toSlotIndex` 除数。

## 注意事项

1. **CORS**：Lock Bot 和 Monquery 均不支持跨域，必须通过本地代理访问
2. **节点硬编码**：Monquery 不支持 namespace 通配符，`MONITORED_NODES` 需手动维护。当前 44 个 node（排除 13/14/17/33/36）+ 3 个 bdc
3. **bdc 节点**：仅通过 Lock Bot 展示占用情况，`hasMonqueryData = false`，利用率显示 `--`，无折线图
4. **多 Bot 合并**：同节点出现在多个 Bot 中时先到先得，不再区分类型优先级
5. **两阶段渲染**：首次加载先出 Lock Bot 数据（1-3 秒），Monquery 到后补全利用率（10-30 秒），避免白屏等待
6. **Now Line 独立定时器**：红色时间线每分钟自动推进，与数据刷新（60s）解耦但同频
7. **Canvas 快照**：渲染后捕获快照，hover 时快照恢复 + 增量画点，避免全路径重绘
8. **Token 持久化**：登录成功存 localStorage（4 小时过期），页面加载时自动恢复；退出登录时清除
9. **代理验证**：`http_proxy` 环境变量会劫持 localhost 请求，验证时必须 `curl --noproxy '*'`
10. **日期格式**：occupancy API 的 `date` 参数取客户端 `new Date()` 拼接为 `YYYY-MM-DD`，即发起请求的浏览器所在时区的日期
