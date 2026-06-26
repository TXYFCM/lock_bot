# TODO — 待优化项

> 记录页面展示层面的矛盾与优化方向，来源于 Lock Bot（锁状态）和 Monquery（显存/XPU 使用率）两套数据源的交互问题。

---

## 三种矛盾场景

| 场景 | 显存 | Lock Bot | 页面表现 | 问题 |
|------|------|----------|---------|------|
| 一致 | 高/低 | 有锁/无锁 | 状态与红条一致 | ✅ 正常 |
| 锁了但没用 | < 10% (FREE) | current_users 有值 | FREE + "疑似僵尸锁"标记 | ✅ 已标记，可发现 |
| 用了但没锁 | >= 10% (BUSY) | current_users 为空 | BUSY + "匿名占用"标记 | ✅ 已标记，可发现 |

---

## 已完成

- [x] **展示当天历史占用** — 接入 `GET /api/bots/{id}/occupancy?date=` 接口，合并当天所有锁记录到时间轴。
- [x] **卡片层红条改用显存实际占用** — 红条与徽章判定依据统一为显存 >= 10%。卡片保留 🔒 图标提示 Lock Bot 锁记录。
- [x] **DEVICE 节点默认展开卡片** — DEVICE bot 直接展示 8 卡详情；NODE bot 保持折叠交互。
- [x] **节点行增加显存利用率展示** — 同时显示 XPU 和显存利用率，判断依据透明。
- [x] **「锁了但没用」视觉标记** — FREE + 活跃锁 → 红色"疑似僵尸锁" badge。
- [x] **「用了但没锁」视觉标记** — BUSY + 无活跃锁 → 橙色"匿名占用" badge。
- [x] **图例文字更新** — "占用时段" → "Lock Bot 锁记录"。
- [x] **错误覆盖层** — 双 API 均不可达时全页错误卡片，支持重试/返回登录。
- [x] **部署适配** — config.json + deploy.sh + 环境变量覆盖，新环境快速部署。

---

## 待优化项

### 🔴 P0 — bug / 数据错误

1. [x] **proxy.py Lock Bot 地址错误** ✅
   - 文件：`proxy.py` → 已修正为 `10.206.192.17:8875`，并跟进 config.json + env var 覆盖

### 🟡 P1 — 功能完善

2. [x] **proxy.py 未跟进 config 化** ✅
   - 文件：`proxy.py` → 已从 config.json 读取配置 + 5 个环境变量覆盖，与 proxy.js 一致

3. [x] **无进程守护** ✅
   - 新增 `pm2.config.cjs`（PM2 配置）+ `xpu-monitor.service`（systemd unit 模板）

### 🟢 P2 — 体验优化

4. [x] **Token 仅存内存** ✅
   - 文件：`demo.html` → 登录成功存 `localStorage`，页面加载时自动恢复（4 小时过期），退出时清除

5. [x] **Canvas hover 重绘开销** ✅
   - 文件：`demo.html` → 渲染后 `captureLayerSnapshot()` 存快照，hover 时 `restoreSnapshot()` + 增量画圆点，无需逐路径重绘

### ⚪ 文档

6. [x] **CLAUDE.md Lock Bot 地址过时** ✅
