# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

开发机集群 XPU 资源监控仪表盘。展示 `wxtky02-p800-backup-8nic-vd` / `wxtky02-p800-8nic-vd` 集群（48 节点 × 8 卡，node1~node51，排除 node13/14/17）的 XPU 使用率、显存占用率、以及 Lock Bot 平台的资源锁定状态。额外展示 bdc9/19/28 节点通过 Lock Bot 白屏。

纯前端，无构建工具，直接 `node proxy.js` 后浏览器打开。

## 启动方式

修改代码后重启代理，请使用 `xpu-monitor-restart` skill（包含完整步骤和常见坑位）。

```bash
# 快速一键：
pkill -f "proxy.js" 2>/dev/null; sleep 1
cd /home/users/v_qiujie04/monitor && nohup /home/users/v_qiujie04/.nvm/versions/node/v22.23.1/bin/node proxy.js > /tmp/proxy.log 2>&1 &
# 验证（注意：必须 --noproxy '*' 绕过公司代理）
curl -s --noproxy '*' http://localhost:8900/index.html | head -3
# 页面用 Lock Bot 账号密码登录
```

前置条件：接入百度内网，能访问 Lock Bot（`10.206.192.17:8875`）和 Monquery（`api.mt.noah.baidu.com:8557`）。

## 开发调试

### 诊断 API 连通性

```bash
# 启动代理后，通过代理测试两个 API
# Monquery
curl -s "http://localhost:8900/monquery/monquery/getHistoryitemdata?namespaces=wxtky02-p800-backup-8nic-vd-node1.wxtky02&items=XPU_AVERAGE_UTILIZATION&start=20260624000000&end=20260624170000&interval=300"

# Lock Bot 登录（需要真实账号）
curl -s -X POST http://localhost:8900/lockbot/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"YOUR_USER","password":"YOUR_PASS"}'
```

### 查看 Monquery 完整指标列表

```bash
curl -s "http://localhost:8900/monquery/monquery/getItemList?namespaces=wxtky02-p800-backup-8nic-vd-node1.wxtky02" | python3 -m json.tool
```

### 新增节点

编辑 `api.js` 的 `MONITORED_NODES` 数组，将新节点编号加入数组（当前排除 `[13, 14, 17]`）。

## 架构

```
浏览器 (index.html 使用 ES Module type="module")
  │
  ├─ api.js        → HTTP 请求（30s 超时，AbortController）
  │   ├─ loginLockBot()          → POST /lockbot/api/auth/login
  │   ├─ fetchLockBotList()      → GET  /lockbot/api/bots
  │   ├─ fetchLockBotState()     → GET  /lockbot/api/bots/{id}/state
  │   ├─ fetchLockBotOccupancy() → GET  /lockbot/api/bots/{id}/occupancy?date=YYYY-MM-DD
  │   ├─ fetchAllBotStates()     → GET  /lockbot/api/bots/running-states（已定义，当前未使用）
  │   └─ fetchMonqueryUtilization() → GET /monquery/monquery/getHistoryitemdata
  │                                     (batch 48 nodes × 17 metrics, 300s interval)
  │
  ├─ adapter.js     → 数据适配
  │   ├─ adaptNodeData()  → (lockBotState, monqueryData, nowIdx, botType) → NodeData[]
  │   └─ 辅助: toSlotIndex(), fillUtilArray(), buildOccupationRange(), parseSlotFromTimestamp(), groupHistoryOccupations(), deriveMemOccupations()
  │
  ├─ index.html      → 渲染 & 交互
  │   ├─ renderStats()        → 顶部 2 行 × 4 列统计卡片
  │   │                         行1: 总节点｜总卡数｜XPU平均利用率｜显存平均利用率
  │   │                         行2: LOCKED节点｜BUSY节点｜LOCKED卡数｜BUSY卡数
  │   │                         XPU/显存平均卡片右上角 "?" 图标可点击查看计算说明
  │   ├─ renderList()         → 节点列表 + 过滤/排序/展开
  │   ├─ drawUtilLine()       → Canvas 折线图 + 微网格背景（每 1h 竖虚线 + 25/50/75% 横虚线）
  │   ├─ bindCanvasHover()    → Canvas 快照恢复 + 增量圆点（非全路径重绘）
  │   └─ addGlobalNowLine()   → 红色"当前时间"竖线
  │
  └─ average.html    → 移动平均利用率独立页面
      ├─ 深色主题，双线 Canvas 图 (XPU 紫 / 显存橙)
      ├─ 滑动窗口切换: 5m / 10m / 30m / 1h
      ├─ 昨日对比虚线 (并行拉取昨日 Monquery 数据)
      └─ 每 60s 自动刷新
```

### 数据流

```
Lock Bot API ─┐
              ├──> api.js ──> adapter.js ──> NodeData[] ──> index.html 渲染
Monquery API ─┘

<index.html:loadAllData()>
  now = new Date()
  start = formatDateStart(now)  → "YYYYMMDD000000"（今日 0 点）
  end   = formatDateTime(now)   → "YYYYMMDDHHmmss"（当前时刻）
  today = "YYYY-MM-DD"
  await Promise.all([
    allBots.map(bot => fetchLockBotState(bot.id, token)),  // 所有 Bot 状态并行
    allBots.map(bot => fetchLockBotOccupancy(bot.id, today, token))  // 当天历史占用
  ])
  → adaptAndRender(stateResults, null, status, occupancyHistory)  // 先出 Lock Bot 数据
  → await fetchMonqueryUtilization(start, end)
  → adaptAndRender(stateResults, monqueryData, status, occupancyHistory)  // 补上利用率
  → setInterval(60s) 自动刷新（仅 page.hidden === false 时）
```

Monquery 查询范围为今日 0 点到当前时刻，因此凌晨时段数据点较少，随时间推移逐步填满 288 槽。

### Bot 类型与合并策略

登录后调用 `fetchLockBotList()` 获取用户所有 Bot，合并规则：
- 同一节点出现在多个 Bot 中时，**先到先得**（不再 DEVICE 优先）
- botType 跟随先命中的 Bot 类型
- 合并后统一按节点名排序（node 在前、bdc 在后，各自按 ID 升序）

### 数据适配关键逻辑

- **节点名映射**: `gpu-node-01` → `node1`, `bdc9` → `bdc9`（`extractNodeId` 返回 `{prefix, id}`）
- **时间戳 → 5 分钟槽**: `toSlotIndex(ts) = floor(((ts + 28800) % 86400) / 300)`，北京时间 0-287
- **利用率填充**: Monquery 稀疏时间序列 → 288 槽数组（同槽多点取平均）
- **节点状态**: 基于 XPU 使用率 或 显存占用率判断（`avgUtil[effectiveIdx] >= 10 || avgMemUtil[effectiveIdx] >= 10` → BUSY，否则 FREE）
- **Lock Bot 占用**: 仅用于展示占用时段红条，不决定 BUSY/FREE 状态
- **当前时间槽**: 客户端 `new Date()` 计算 `nowIdx = hour * 12 + floor(minutes / 5)`（288 槽/天，5 分钟粒度），`effectiveIdx = Math.max(0, nowIdx - 1)` 用最近已完成槽
- **历史占用合并**: occupancy API 返回 ISO 时间字符串（`start_time`、`end_time`、`duration_seconds`），通过 `parseSlotFromTimestamp()` 统一转槽索引，支持 Unix 秒/毫秒/ISO 三种格式
- **bdc 节点**: Lock Bot 中有但 Monquery 查不到，`hasMonqueryData = false`，利用率显示 `--`

### XPU/显存平均利用率卡片计算

卡片显示的是"当日 10:00 至当前时刻"的平均利用率，只算 `hasMonqueryData === true` 的 node 节点（排除 bdc）:

1. 对每个 node，计算 slot 120（10:00）到 `effectiveIdx` 的 `avgUtil[]` / `avgMemUtil[]` 均值
2. 对所有 node 的均值再取平均（每个节点等权重）
3. 10 点前（`effectiveIdx < 120`）显示 `--`

### 统计卡片布局

8 个卡片，2 行 × 4 列，`.stats-bar-row` 居中 flex，卡片 `flex: 1 1 220px; max-width: 300px`，横向平铺（标签左、数值右）。利用率卡片右上角 `?` 图标（绝对定位，`padding-right: 40px` 避免遮挡），点击弹出 tooltip 说明计算方式。

### 状态栏

状态栏不再独占一行，已合并到图例行（`.legend`）右侧 `#status-mini` 中。显示格式：`[NORMAL/ERROR/CAUTION 标签] HH:MM:SS [↻ 刷新按钮]`。

### average.html（移动平均利用率独立页面）

独立 HTML 页面，与 `index.html` 共享 `api.js`、`adapter.js`、token 持久化。代理自动托管（无额外路由配置）。

- **布局**: 图表卡片 + 窗口切换按钮 (5m/10m/30m/1h) + 图例 + 底部当前值摘要
- **数据加载**: `loadAndRender()` 并行拉取今日 + 昨日 Monquery 数据 → `adaptNodeData()` 生成 NodeData → `computeMA(nodes, window)` 计算每槽均值 + 滑动平均
- **窗口切换**: 缓存的 raw nodes 直接重算 `computeMA()` + `drawChart()`，无需重新拉 API
- **图表**: Canvas 双线图（XPU 紫色实线 + 显存橙色实线），昨日为半透明虚线对比

## 关键数据结构

```ts
interface NodeData {
  name: string;              // "node1" ~ "node51" 或 "bdc9" / "bdc19" / "bdc28"
  status: "FREE" | "BUSY" | "PARTIAL";
  currentUtil: number;       // 当前 5 分钟槽平均 XPU 使用率
  currentMemUtil: number;    // 当前 5 分钟槽平均显存占用率
  avgUtil: number[288];      // 288 槽平均 XPU 使用率
  avgMemUtil: number[288];   // 288 槽平均显存利用率
  cardUtils: number[8][288]; // 8×288 单卡 XPU 使用率
  cardMemUtils: number[8][288]; // 8×288 单卡显存利用率
  occupations: { start: number, end: number, user: string }[];
  cardOccupations: { start: number, end: number, user: string }[][];
  cardMemOccupations: { start: number, end: number, user: string }[][];
  botType: "DEVICE" | "NODE";
  hasMonqueryData: boolean;
  hasActiveLock: boolean;
}
```

288 槽：`index = hour * 12 + floor(minutes / 5)`，0 = 00:00，287 = 23:55。

## 两个后端 API

### Monquery（监控 3.0）

| 项目 | 值 |
|------|-----|
| 接口 | `GET /monquery/getHistoryitemdata` |
| 地址 | `http://api.mt.noah.baidu.com:8557` |
| 粒度 | 5 分钟一个采样点（interval=300），前端直接 288 槽展示 |
| 超时 | 30 秒（前端内置 AbortController） |

一次查询 17 个指标：`XPU_AVERAGE_UTILIZATION` + 8 个 `XPU{c}_XPU_UTILIZATION` + 8 个 `XPU{c}_MEM_UTILIZATION`。

Monquery **不支持通配符 namespace**，节点列表必须硬编码维护。

### Lock Bot

| 项目 | 值 |
|------|-----|
| 地址 | `http://10.206.192.17:8875` |
| 鉴权 | JWT Bearer Token（`POST /api/auth/login`） |
| 核心接口 | `GET /api/bots/{id}/state`（实时占用）、`GET /api/bots/{id}/occupancy?date=`（当天历史） |

三种 Bot 类型：
- **NODE** — 整机锁定，state 按 `{节点名: {status, current_users}}`
- **DEVICE** — 单卡锁定，state 按 `{节点名: [{dev_id, status, current_users}]}`
- **QUEUE** — 整机 + 排队预约（当前未开通）

## 性能注意事项

1. **Canvas 重绘**: `bindCanvasHover()` 渲染后捕获快照，hover 时快照恢复 + 增量画点，避免全路径重绘
2. **自动刷新**: 每 60 秒 `loadAllData()` 同时请求 Lock Bot + Monquery（`Promise.all`），仅页面可见时执行
3. **Token 存储**: localStorage 持久化（4 小时过期），页面加载时自动恢复；退出登录时清除
4. **节点列表硬编码**（3 个故障节点排除：node13/14/17，实际监控 node1~node51 共 48 个节点；node32/34/35/37~51 使用非 backup namespace）
5. **代理**：两个 API 均不支持 CORS，必须通过 `proxy.js` 访问（`http.createServer` + Node.js 内置 `http.request`）

## 文件职责

| 文件 | 作用 |
|------|------|
| `index.html` | 前端仪表盘 UI（单文件，~1100 行）+ 内联 CSS + ES Module |
| `average.html` | 移动平均利用率独立页面（暗色主题，昨日对比，窗口切换） |
| `api.js` | API 调用层（纯 fetch 封装，无业务逻辑） |
| `adapter.js` | 数据适配层（原始 API 响应 → `NodeData[]`） |
| `proxy.js` | 本地代理（Node.js 原生 http 模块，固定 8900 端口） |
| `proxy.py` | 本地代理（Python 3 备用） |
| `config.json` | 部署配置（端口 + 后端地址） |
| `config.example.json` | 配置模板 |
| `deploy.sh` | 一键部署 |
| `pm2.config.cjs` | PM2 进程守护配置 |
| `xpu-monitor.service` | systemd 服务单元 |
| `monitor-api-doc.md` | Monquery API 详细文档 |
| `lock-bot-api-doc.md` | Lock Bot API 详细文档 |
| `TODO.md` | 待优化项清单 |
