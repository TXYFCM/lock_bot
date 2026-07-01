# 开发机集群 XPU 资源监控仪表盘

纯前端单页应用，实时展示百度百舸 GPU 集群的 **XPU 使用率**、**显存占用率**，以及 **Lock Bot 平台的资源锁定状态**。

**监控集群**：`wxtky02-p800-backup-8nic-vd`（主）+ `wxtky02-p800-8nic-vd`（部分节点），48 节点 × 8 卡。

## 快速开始

```bash
cd /home/users/v_qiujie04/monitor
node proxy.js                     # 启动本地代理（端口 8900）
# 浏览器打开 http://localhost:8900/index.html
# 价值展示页 http://10.206.192.17:8900/value.html
```

页面使用 **Lock Bot 平台账号密码** 登录。Token 自动存入 `localStorage`，4 小时有效期内刷新无需重登。

> **前置条件**：已接入百度内网，能访问 Lock Bot（`10.206.192.17:8875`）和 Monquery 监控 3.0（`api.mt.noah.baidu.com:8557`）。两个后端 API 均不支持 CORS，必须通过本地代理访问。

## 架构

```
浏览器 (index.html — ES Module)
  │
  ├─ styles.css      仪表盘样式（从 index.html 拆出）
  │
  ├─ timeline.js     时间线 / Canvas 视觉工具
  │   ├─ drawUtilLine()             → XPU / 显存折线叠加
  │   ├─ drawTimelineGrid()         → 1h 竖虚线 + 25/50/75% 横虚线
  │   ├─ captureLayerSnapshot()     → 渲染后捕获快照
  │   └─ bindCanvasHover()          → 快照恢复 + 增量圆点 + tooltip
  │
  ├─ api.js          HTTP 请求层（30s AbortController 超时）
  │   ├─ loginLockBot()              → POST /lockbot/api/auth/login
  │   ├─ fetchLockBotList()          → GET  /lockbot/api/bots
  │   ├─ fetchLockBotState()         → GET  /lockbot/api/bots/{id}/state
  │   ├─ fetchLockBotOccupancy()     → GET  /lockbot/api/bots/{id}/occupancy?date=
  │   ├─ fetchMonqueryNodeUtilization()       → 整机 XPU，首屏快速渲染
  │   ├─ fetchMonqueryCardUtilizationBatches() → 卡级 XPU/MEM，分批渐进渲染
  │   └─ fetchMonqueryUtilization()            → 完整 17 指标兼容接口
  │
  ├─ adapter.js      数据适配层
  │   ├─ adaptNodeData(...)          → Lock Bot + Monquery → NodeData[]
  │   └─ mergeMonqueryData(...)      → 整机/卡级批次增量合并
  │
  └─ index.html      页面结构 + 渲染编排
      ├─ 登录 / token 持久化 / 自动登录
      ├─ 双视图：「全部」(average) + 「个人」(节点列表)
      ├─ loadAllData()   多阶段渐进式加载（Lock Bot → 整机 Monquery → 卡级批次）
      ├─ renderStats()   8 张统计卡片（2 行 × 4 列）
      ├─ renderList()    节点列表 + 过滤/排序/展开 + 加载态
      ├─ addGlobalNowLine()   红色当前时间竖线
      ├─ updateNowIdx()       每 60s 推进时间槽 + 重新判定节点状态
      └─ startAutoRefresh()   每 60s 自动刷新（page.hidden 时跳过）
```

### 代理路由

代理（`proxy.js`）按路径前缀转发：

| 前端请求路径 | 转发到 |
|-------------|--------|
| `/lockbot/*` | `http://10.206.192.17:8875/*` |
| `/monquery/*` | `http://api.mt.noah.baidu.com:8557/*` |
| 其他 | 本地静态文件（`index.html`、`styles.css`、`timeline.js`、`api.js`、`adapter.js`、`average.html`、`value.html` 等） |

后端地址通过 `config.json` 配置，支持环境变量覆盖。代理启动时读取一次，不热更新。

### 文件职责

| 文件 | 作用 |
|------|------|
| `index.html` | 前端仪表盘 UI 结构、登录/会话、视图切换、渐进式数据编排、统计/列表渲染入口 |
| `styles.css` | 从 `index.html` 拆出的仪表盘样式 |
| `timeline.js` | 时间线 Canvas 绘制、hover 快照恢复、tooltip 等纯视觉 helper |
| `api.js` | API 调用层：Lock Bot fetch 封装 + Monquery 整机优先 / 卡级批次 / 完整兼容查询 |
| `adapter.js` | 数据适配层：原始 API 响应 → `NodeData[]`、增量 Monquery 合并、部分数据状态 flags |
| `average.html` | 全部视图的移动平均利用率页面（复用 `api.js` / `adapter.js`） |
| `value.html` | 价值展示页（面向管理层，说明监控数据如何驱动决策） |
| `proxy.js` | 本地代理（Node.js 原生 `http` 模块，约 140 行） |
| `config.json` | 部署配置（代理端口 + 后端地址） |
| `deploy.sh` | 一键部署脚本 |
| `pm2.config.cjs` | PM2 进程守护配置 |
| `xpu-monitor.service` | systemd 服务单元 |

## 核心数据结构

```js
NodeData = {
  name: "node1",                     // 节点名（"node1" 或 "bdc9"）
  status: "FREE" | "BUSY" | "PARTIAL",  // 逐卡利用率判定
  currentUtil: 45.2,                 // 当前有效槽的节点级平均 XPU 使用率
  currentMemUtil: 32.1,              // 当前有效槽的 8 卡平均显存占用率
  avgUtil:    number[288],           // 288 槽 XPU 平均使用率（节点级）
  avgMemUtil: number[288],           // 288 槽显存平均利用率（8 卡逐槽取均值）
  cardUtils:    number[8][288],      // 8×288 单卡 XPU 使用率
  cardMemUtils: number[8][288],      // 8×288 单卡显存利用率
  occupations:     [{start, end, user}],     // 节点级占用（当前锁 + 历史）
  cardOccupations: [{start, end, user}][8],  // 每卡 Lock Bot 占用记录
  cardMemOccupations: [{start, end}][8],     // 每卡显存推导占用
  botType: "DEVICE" | "NODE",        // 锁定粒度
  hasMonqueryData: boolean,          // 是否已有任意 Monquery 数据（兼容性总标记）
  hasNodeMonqueryData: boolean,      // 是否已有整机级 XPU_AVERAGE_UTILIZATION
  hasCardMonqueryData: boolean,      // 是否已有卡级 XPU 或 MEM 指标
  hasMemMonqueryData: boolean,       // 是否已有卡级显存指标（决定显存均值/显存推导占用是否可信）
  statusSource: "lockbot" | "node-metric" | "card-metrics",  // 当前状态来源
  hasActiveLock: boolean,            // 当前是否有活跃 Lock Bot 锁
  cardHasActiveLock: boolean[8],    // 逐卡 Lock Bot 活跃锁
  cardCount: number,                 // 实际卡数（通常 8）
}
```

## 数值常量 & 阈值速查

以下所有数值均直接取自源码，修改时需同步更新：

### 时间系统

| 常量 | 值 | 来源 | 说明 |
|------|----|------|------|
| `SLOT_COUNT` | `288` | `adapter.js:5`, `index.html:290` | 全天 288 个 5 分钟槽（24h ÷ 5min） |
| `CARD_COUNT` | `8` | `adapter.js:4` | 每节点 8 张 XPU 卡 |
| UTC+8 偏移 | `28800` 秒 | `adapter.js:13` | `toSlotIndex(ts) = floor(((ts + 28800) % 86400) / 300)` |
| 槽间隔 | `300` 秒 | `api.js:122` | Monquery interval 参数，与 SLOT_COUNT 严格对应 |
| 有效槽回退 | `nowIdx - 1` | `adapter.js:358`, `index.html:859` | `effectiveIdx = max(0, nowIdx - 1)`，避免读取未到达的当前槽数据 |
| 自动刷新间隔 | `60,000` ms | `index.html:482,493,1175` | 数据刷新 + nowLine 推进均用此间隔 |
| Token 过期 | `4 × 3600 × 1000` ms | `index.html:404` | 4 小时，超时自动清除 localStorage |

### 利用率 & 状态判定阈值

| 常量 | 值 | 来源 | 说明 |
|------|----|------|------|
| BUSY 阈值 | **≥ 10%** | `adapter.js` | XPU 使用率 或 显存占用率 ≥ 10% → BUSY |
| 整机临时判定 | 节点 XPU ≥ 10% | `adapter.js` | 卡级指标未到时，`currentUtil >= 10 → BUSY` |
| FREE 判定 | 全部 8 卡 < 10% | `adapter.js` | 卡级指标到达后，`busyCards == 0 → FREE` |
| BUSY 判定 | 全部 8 卡 ≥ 10% | `adapter.js` | 卡级指标到达后，`busyCards == 8 → BUSY` |
| PARTIAL 判定 | 1~7 卡 ≥ 10% | `adapter.js` | 卡级指标到达后，其他情况 → PARTIAL |
| 利用率颜色：高 | **≥ 50%** | `index.html:1274` | 红色 `.util-value.high` |
| 利用率颜色：中 | **≥ 20%** | `index.html:1275` | 橙色 `.util-value.mid` |
| 利用率颜色：低 | **< 20%** | `index.html:1276` | 绿色 `.util-value.low` |
| 显存推导占用阈值 | **≥ 10%** | `adapter.js:210` | `deriveMemOccupations()` 连续 ≥10% 槽合并为占用段 |
| 占用区间合并 gap | **≤ 1 槽** | `adapter.js:98` | 同用户相邻 ≤ 1 槽（5 分钟）合并为同一次连续占用 |

### 僵尸锁检测

| 常量 | 值 | 来源 | 说明 |
|------|----|------|------|
| 扫描窗口 | **24 槽 = 2h** | `index.html:1317` | 从 `effectiveIdx` 向前扫描 24 个槽 |
| 判定条件 | **全部 24 槽 XPU < 10% 且 MEM < 10%** | `index.html:1324-1331` | 任一槽有利用率 ≥ 10% 即排除嫌疑 |
| 前置条件 | 有 Lock Bot 活跃锁 + 有 Monquery 数据 | `index.html:1315` | bdc 节点不参与检测 |

### 统计卡片

| 常量 | 值 | 来源 | 说明 |
|------|----|------|------|
| 起始槽 | `SLOT_10AM = 120` | `index.html:862` | 10:00 对应槽索引，平均利用率从此开始计算 |
| 计算范围 | slot 120 → effectiveIdx | `index.html:867-873` | 仅含 `hasMonqueryData === true` 的 node 节点，排除 bdc |
| effectiveIdx < 120 | 显示 `--` | `index.html:864` | 10 点前不计算平均 |

### 节点 & 命名空间

| 常量 | 值 | 来源 | 说明 |
|------|----|------|------|
| 监控节点总数 | **48** | `api.js:17-18` | node1~51 排除 [13, 14, 17] |
| 排除节点 | `[13, 14, 17]` | `api.js:18` | 故障机，无 Monquery 数据 |
| 备用集群 | `wxtky02-p800-backup-8nic-vd` | `api.js:10` | 默认 namespace |
| 非备用集群 | `wxtky02-p800-8nic-vd` | `api.js:11` | 特定节点使用 |
| 非备用节点 | `[32,34,35,37~51]` | `api.js:14` | 18 个节点走非备用 namespace |
| bdc 节点 | `bdc9, bdc19, bdc28` | Lock Bot 侧 | 仅展示占用，无 Monquery 数据 |
| Monquery 完整指标数 | **17** | `api.js` | 1 个 XPU_AVG + 8 个单卡 XPU + 8 个单卡 MEM |
| 整机首屏查询 | **24 节点/批 × 1 指标** | `fetchMonqueryNodeUtilization()` | 只拉 `XPU_AVERAGE_UTILIZATION`，用于快速展示节点级利用率 |
| 卡级渐进查询 | **8 节点/批 × 16 指标** | `fetchMonqueryCardUtilizationBatches()` | 单卡 XPU + 单卡 MEM 分批并行，按完成顺序渲染 |
| 完整兼容查询 | **16 节点/批 × 17 指标** | `fetchMonqueryUtilization()` | 保留给 `average.html` 等完整数据场景 |

### 图表渲染

| 常量 | 值 | 来源 | 说明 |
|------|----|------|------|
| 时间网格竖线 | 每 **12 槽（1h）** | `timeline.js` | `for (i = 0; i <= 288; i += 12)` |
| 利用率横线 | **25% / 50% / 75%** | `timeline.js` | `for (pct of [0.25, 0.5, 0.75])` |
| XPU 图表 Y 轴上限 | **35%** | `index.html:1067` | `avgDrawChart(..., 35, [0,5,10,15,20,25,30,35])` |
| MEM 图表 Y 轴上限 | **70%** | `index.html:1068` | `avgDrawChart(..., 70, [0,10,20,30,40,50,60,70])` |
| 移动平均窗口 | **1/2/6/12** 槽 | `index.html:241-245` | 对应 5m / 10m / 30m / 1h |
| 图表 margin | `{top:10, right:16, bottom:20, left:48}` | `index.html:315` | average view 图表边距 |

### 错误处理

| 常量 | 值 | 来源 | 说明 |
|------|----|------|------|
| fetch 超时 | **30,000** ms | `api.js:32` | `AbortController` + `setTimeout` |
| 连续失败阈值 | **≥ 3 次** | `index.html:612` | 双 API 均失败 + 连续 3 次 → 全页错误覆盖层 |
| Toast 显示时长 | **5,000** ms | `index.html:1178` | 自动刷新失败浮动通知 |

## 关键判定逻辑

### 1. 时间槽映射

```
槽索引 = hour × 12 + floor(minutes / 5)
0 = 00:00, 12 = 01:00, 120 = 10:00, 287 = 23:55
```

`toSlotIndex(Unix秒) = floor(((ts + 28800) % 86400) / 300)`。`+28800` 将 Unix 时间转为 UTC+8 北京时间，`%86400` 取当天秒数。

`parseSlotFromTimestamp()` 统一处理三种格式：
- **Unix 秒**（≤ 1e12）→ 直接使用
- **Unix 毫秒**（> 1e12）→ `floor(ts / 1000)`
- **ISO 字符串**：无时区标识时附加 `Z` 按 UTC 解析（Lock Bot occupancy API 返回 UTC 时间但不带 `Z` 后缀）

### 2. 节点状态判定（分层）

```
effectiveIdx = max(0, nowIdx - 1)    // 最近一个已完成的 5 分钟槽

① 无 Monquery 数据（如 bdc）：
   hasActiveLock ? BUSY : FREE
   statusSource = "lockbot"

② 只有整机 XPU 指标（卡级数据还在加载）：
   currentUtil >= 10 ? BUSY : FREE
   statusSource = "node-metric"

③ 卡级 XPU/MEM 指标到达后：
   对每张卡 c (0~7):
     cardMemUtils[c][effectiveIdx] >= 10 || cardUtils[c][effectiveIdx] >= 10  →  该卡 BUSY

   busyCards == 0  →  FREE
   busyCards == 8  →  BUSY
   其他            →  PARTIAL
   statusSource = "card-metrics"
```

`hasMonqueryData` 是兼容性总标记；渲染显存、卡级详情、精确 BUSY 卡数时应优先看 `hasMemMonqueryData` / `hasCardMonqueryData`。

### 3. 僵尸锁检测（2h 滑动窗口）

```
前置条件：hasMonqueryData && hasActiveLock

扫描范围：startSlot ~ endSlot（共 24 槽 = 2h）
         endSlot = max(0, nowIdx - 1)
         startSlot = max(0, endSlot - 23)

判定：全部 24 个槽的 avgUtil[s] < 10 && avgMemUtil[s] < 10
      → ⚠已锁定≥2h未使用（红色 badge）

任一槽利用率 ≥ 10% → 不标记
```

### 4. Lock Bot 锁判定

```
DEVICE bot：
  cardHasActiveLock[c] = dev.status !== 'idle' && current_users.length > 0
  hasActiveLock = 任一卡为 true

NODE bot：
  hasActiveLock = state.status !== 'idle' && current_users.length > 0
  cardHasActiveLock[0..7] 全部填相同值
```

### 5. 多 Bot 合并

- 登录后拉取用户所有 Bot（`GET /api/bots`）
- 同一节点出现在多个 Bot → **先到先得**（遍历顺序即 API 返回顺序）
- `botType` 跟随先命中 Bot 的类型
- 合并后排序：`node` 前缀在前、`bdc` 在后，各自按数字 ID 升序

### 6. 多阶段渐进式渲染

```
loadAllData():
  ① Promise.all([所有 Bot state, 所有 Bot 历史占用])
     → adaptAndRender(state, null, { phase: 'lockbot' })
     → 节点名、占用红条即时可见，利用率显示 "--"

  ② await fetchMonqueryNodeUtilization(start, end)
     → 只拉 XPU_AVERAGE_UTILIZATION（24 节点/批）
     → mergeMonqueryData(existing, nodeData)
     → adaptAndRender(..., { phase: 'node', cardLoading: true })
     → 节点级 XPU 利用率先出现，显存/卡级仍显示加载态

  ③ for await (batch of fetchMonqueryCardUtilizationBatches(start, end, { batchSize: 8 }))
     → 拉 8 卡 XPU + 8 卡 MEM（8 节点/批，并行请求，按完成顺序 yield）
     → mergeMonqueryData(existing, batch.data)
     → adaptAndRender(..., { phase: 'cards', cardLoading: true })
     → 卡级详情、显存折线、显存均值逐批补齐

  ④ 所有卡级批次完成
     → adaptAndRender(..., { phase: 'complete', cardLoading: false })
```

每次 `loadAllData()` 都会递增 `loadSeq`。任何 `await` 后如果发现当前序列号已不是最新值，就直接放弃本次渲染，避免手动刷新 / 自动刷新并发时旧请求晚到覆盖新数据。

UI 加载语义：整机阶段显存显示 `--` / `加载中`；展开节点时如果 `hasMonqueryData && !hasCardMonqueryData`，展示“卡级数据加载中…”，避免把尚未到达的卡级指标误显示为 0%。

### 7. 占用数据来源

| 来源 | API | 说明 |
|------|-----|------|
| 当前活跃锁 | `GET /api/bots/{id}/state` | `current_users[].start_time + duration` |
| 当天历史锁 | `GET /api/bots/{id}/occupancy?date=` | `start_time + end_time + duration_seconds` |
| 显存推导 | `deriveMemOccupations()` | `hasMemMonqueryData === true` 后逐槽扫 `cardMemUtils[c]`，连续 ≥10% 合并为占用段 |

三类数据按 `start,end,user` 三元组去重后合并。DEVICE bot 的节点级占用取 8 卡时间范围的并集（最左 start ~ 最右 end）。卡级显存数据未到达前，时间线上只展示 Lock Bot 当前/历史占用，不展示显存推导占用。

### 8. 统计卡片分类

| 卡片 | 整机 Monquery 阶段 | 卡级 Monquery 到达后 | 无 Monquery 的节点 (bdc) |
|------|-------------------|----------------------|--------------------------|
| **BUSY 节点** | `currentUtil >= 10` 临时判定 | `busyCards > 0` | `hasActiveLock → BUSY` |
| **BUSY 卡数** | 等待卡级数据，不用假 0 统计 | 逐卡判定 memUtil≥10 或 util≥10 | `hasActiveLock → 8 张全算` |
| **LOCKED 节点** | `hasActiveLock` | `hasActiveLock` | `hasActiveLock` |
| **LOCKED 卡数** | `cardHasActiveLock` 逐卡求和 | `cardHasActiveLock` 逐卡求和 | `cardHasActiveLock` 逐卡求和 |

关键区分：**BUSY = 实际使用**（利用率判定），**LOCKED = Lock Bot 锁状态**。bdc 节点无法获取利用率，BUSY = LOCKED。整机阶段可以先给节点级临时状态，但精确 BUSY 卡数必须等待卡级指标。

### 9. XPU / 显存平均利用率卡片

```
仅计算有 Monquery 数据的 node 节点（排除 bdc）
计算范围：slot 120 (10:00) → effectiveIdx

XPU 平均：hasNodeMonqueryData === true 后即可计算 avgUtil[] 均值
显存平均：hasMemMonqueryData === true 后才计算 avgMemUtil[] 均值
对所有 node 的均值再取平均（每节点等权重）

effectiveIdx < 120 → 显示 "--"（10 点前不计算）
```

## 功能

### 「全部」视图（average）
- Canvas 双线图：XPU 使用率（紫色实线）+ 显存利用率（橙色实线）
- 昨日对比：虚线（半透明）并行展示昨日同时段数据
- 滑动窗口切换：**5m / 10m / 30m / 1h**（对应 1/2/6/12 槽移动平均）
- 日期选择器：可回溯历史日期，默认当天
- Hover 十字线 + 数据点 + 多系列 tooltip
- 每 60s 自动刷新（仅当天 + 页面可见时）

### 「个人」视图（节点列表）
- **状态徽章**：FREE（绿）/ PARTIAL（橙）/ BUSY（红）/ 无数据（灰）
- **Bot 类型标签**：DEVICE（靛蓝）/ NODE（灰）
- **僵尸锁检测**：有锁但 2h 内利用率持续 < 10% → 红色 `⚠已锁定≥2h未使用` badge + tooltip
- **展开按钮**：点击「展开」查看 8 卡详情（逐卡利用率 + 锁标记 + 迷你折线图），点击「收起」折叠；展开/收起保持滚动位置
- **占用红条**：Lock Bot 当前锁 + 当天历史占用，叠加在时间线上，hover 显示用户名 + 时间段
- **双线折线图**：节点级 Canvas 叠加 XPU（蓝）+ 显存（橙），1h 间隔竖虚线 + 25/50/75% 横虚线微网格
- **红色当前时间线**：贯穿所有节点的竖线 + 时间标签，每 60s 自动推进
- **过滤**：按状态（全部 / FREE / PARTIAL / BUSY）
- **排序**：XPU 利用率降序 / 显存利用率降序
- **搜索**：节点名关键字模糊匹配

### 状态栏 & 降级策略

| 状态 | 显示 | 条件 |
|------|------|------|
| NORMAL 🟢 | 数据正常 | Lock Bot + Monquery 双通道正常 |
| CAUTION 🟡 | 监控数据获取失败 | Monquery 不可达，利用率显示 `--`，占用信息仍可见 |
| ERROR 🔴 | 所有 Bot 离线 | Lock Bot 全挂，保留上次节点列表 |

个人视图状态栏还会展示当前加载阶段：`lockbot`（锁状态）→ `node`（整机监控）→ `cards`（卡级加载中）→ `complete`（已完成）。

双 API 均不可达且连续失败 ≥ 3 次 → 全页错误覆盖层（显示连续失败次数 + 上次成功时间，可手动重试或返回登录）。

### 交互细节
- **Canvas hover 快照恢复**：实现位于 `timeline.js`。渲染后捕获 `toDataURL()` 快照，hover 时 `drawImage` 恢复 + 增量 `arc()` 画圆点，不重绘路径
- **页面不可见跳过刷新**：`document.hidden` 时跳过自动刷新，切回后立即拉取
- **Token 持久化**：`localStorage` 存 JSON `{token, username, savedAt}`，4 小时过期自动清除；退出登录时主动清除
- **自动登录**：页面加载时先尝试恢复 session，失败则自动用默认账号登录，再失败才显示登录表单

## 两个后端 API

### Lock Bot

| 项目 | 值 |
|------|-----|
| 地址 | `http://10.206.192.17:8875` |
| 鉴权 | JWT Bearer Token（`POST /api/auth/login` 获取） |

| 接口 | 方法 | 用途 |
|------|------|------|
| `/api/auth/login` | POST | 登录，body: `{username, password}` → `{access_token}` |
| `/api/bots` | GET | 获取用户所有 Bot 列表 |
| `/api/bots/{id}/state` | GET | 获取 Bot 当前锁定状态 |
| `/api/bots/{id}/occupancy?date=YYYY-MM-DD` | GET | 获取某天历史占用记录 |

**Bot 类型**：
- **NODE** — 整机锁定，state 格式 `{节点名: {status, current_users}}`
- **DEVICE** — 单卡锁定，state 格式 `{节点名: [{dev_id, status, current_users}]}`
- **QUEUE** — 整机 + 排队预约（当前未开通）

Occupancy API 返回格式：`[{node_key, user_id, start_time, end_time, duration_seconds}]`，时间字段为 ISO 字符串（UTC 但不带 `Z` 后缀，`parseSlotFromTimestamp` 自动补 `Z` 纠正）。

### Monquery（监控 3.0）

| 项目 | 值 |
|------|-----|
| 地址 | `http://api.mt.noah.baidu.com:8557` |
| 鉴权 | 无（内网直接访问） |

| 接口 | 方法 | 用途 |
|------|------|------|
| `/monquery/getHistoryitemdata` | GET | 批量获取历史监控数据 |
| `/monquery/getItemList` | GET | 获取可用指标列表 |

**查询参数**：
- `namespaces` — 节点命名空间（逗号分隔，**不支持通配符**，节点列表必须硬编码）
- `items` — 指标名（逗号分隔，按查询模式选择）
- `start` / `end` — 时间范围，格式 `YYYYMMDDHHmmss`
- `interval` — 采样间隔秒数，固定 300（5 分钟）

**指标分组**：
```
XPU_AVERAGE_UTILIZATION                    // 节点级平均 XPU 使用率（整机首屏）
XPU0_XPU_UTILIZATION ~ XPU7_XPU_UTILIZATION // 8 张卡各自 XPU 使用率
XPU0_MEM_UTILIZATION ~ XPU7_MEM_UTILIZATION // 8 张卡各自显存利用率
```

**查询模式**：
| 函数 | 指标 | 批大小 | 用途 |
|------|------|--------|------|
| `fetchMonqueryNodeUtilization()` | 1 个整机 XPU 指标 | 24 节点/批 | 个人视图首屏快速展示节点级利用率 |
| `fetchMonqueryCardUtilizationBatches()` | 16 个卡级 XPU/MEM 指标 | 8 节点/批 | async generator，卡级数据分批到达后渐进渲染 |
| `fetchMonqueryUtilization()` | 完整 17 指标 | 16 节点/批 | 兼容完整数据场景，如 `average.html` |

## 部署

### 新环境快速部署

```bash
cd /home/users/v_qiujie04/monitor
bash deploy.sh                # 自动检查环境 + 从 config.example.json 创建 config.json + 启动
```

### 环境变量覆盖

不修改 `config.json` 即可覆盖后端地址：

| 变量 | 对应配置 | 默认值 |
|------|---------|--------|
| `PROXY_PORT` | 代理监听端口 | `8900` |
| `LOCKBOT_HOST` | Lock Bot IP | `10.206.192.17` |
| `LOCKBOT_PORT` | Lock Bot 端口 | `8875` |
| `MONQUERY_HOST` | Monquery IP | `api.mt.noah.baidu.com` |
| `MONQUERY_PORT` | Monquery 端口 | `8557` |

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
node proxy.js                                       # 前台
nohup node proxy.js > /tmp/proxy.log 2>&1 &         # 后台
```

## 开发调试

### 本地开发

修改 `index.html` / `styles.css` / `timeline.js` / `api.js` / `adapter.js` 等运行时文件后需刷新页面；修改 `index.html` / `api.js` / `adapter.js` / `proxy.js` / `config.json` 后按项目约定重启代理。README-only 文档修改不需要重启代理。

```bash
# 一键重启 + 验证（推荐）
# 或手动：
pkill -f "proxy.js" 2>/dev/null; sleep 1
cd /home/users/v_qiujie04/monitor
nohup node proxy.js > /tmp/proxy.log 2>&1 &
curl -s --noproxy '*' http://localhost:8900/index.html | head -3
```

> ⚠️ 验证时必须 `--noproxy '*'`，因为 shell 环境的 `http_proxy` 变量会把 `localhost` 请求也路由到公司代理。

### 诊断 API 连通性

```bash
# Monquery 单节点查询
curl -s "http://localhost:8900/monquery/monquery/getHistoryitemdata?namespaces=wxtky02-p800-backup-8nic-vd-node1.wxtky02&items=XPU_AVERAGE_UTILIZATION&start=$(date +%Y%m%d)000000&end=$(date +%Y%m%d%H%M%S)&interval=300"

# Lock Bot 登录测试
curl -s -X POST http://localhost:8900/lockbot/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"YOUR_USER","password":"YOUR_PASS"}'

# Lock Bot 历史占用查询
curl -s -H "Authorization: Bearer TOKEN" \
  "http://localhost:8900/lockbot/api/bots/1/occupancy?date=$(date +%Y-%m-%d)"
```

### 查看 Monquery 完整指标列表

```bash
curl -s "http://localhost:8900/monquery/monquery/getItemList?namespaces=wxtky02-p800-backup-8nic-vd-node1.wxtky02" | python3 -m json.tool
```

### 新增节点

编辑 `api.js`，将节点编号加入 `MONITORED_NODES` 数组：
```js
const MONITORED_NODES = Array.from({ length: 51 }, (_, i) => i + 1)
  .filter(n => ![13, 14, 17].includes(n));   // ← 从排除列表移除
```

如果新节点不在备用集群，还需加入 `NON_BACKUP_NODES`（当前包含 node32/34/35/37~51）。

### 调整监控粒度

修改三处（必须同步）：
- `api.js:122` — `interval` 参数（当前 `300`）
- `adapter.js:5` — `SLOT_COUNT`（当前 `288`）
- `adapter.js:13` — `toSlotIndex` 除数（当前 `/300`）
