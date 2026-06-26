# Lock Bot 平台 API 接口文档

> **版本**: v1.0  
> **基础地址**: http://10.206.192.17:8875
> **鉴权方式**: JWT Bearer Token  
> **更新日期**: 2026-06-24

---

## 目录

1. [概述](#概述)
2. [鉴权](#鉴权)
3. [Bot 列表](#bot-列表)
4. [节点列表与占用状态（核心）](#节点列表与占用状态核心)
5. [占用粒度说明](#占用粒度说明)
6. [状态编辑（管理员）](#状态编辑管理员)
7. [附录：数据结构速查](#附录数据结构速查)

---

## 概述

Lock Bot 是一个资源锁定管理平台，通过三种类型的 Bot 管理 GPU 集群的占用状态：

| Bot 类型   | 占用粒度    | 说明           | 场景     |
| -------- | ------- | ------------ | ------ |
| `NODE`   | 整机      | 以节点为单位独占/共享  | 多机lock |
| `DEVICE` | XPU 卡   | 以单张 XPU 卡为单位 | 单机lock |
| `QUEUE`  | 整机 + 排队 | 节点级锁定，支持预约排队 | 暂无     |

**对外对接的核心场景**：查询节点列表、查看谁在占用、占用时间、占用粒度。

---

## 鉴权

### 登录获取 Token

```http
POST /api/auth/login
Content-Type: application/json

{
    "username": "your_username",
    "password": "your_password"
}
```

**成功响应** (200):

```json
{
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "bearer",
    "must_change_password": false
}
```

**后续所有请求**需携带 Header:

```http
Authorization: Bearer <access_token>
```

### 查看当前用户信息

```http
GET /api/auth/me
Authorization: Bearer <token>
```

---

## Bot 列表

### 获取当前用户的所有 Bot

```http
GET /api/bots
Authorization: Bearer <token>
```

**响应** (200): `BotOut[]`

```json
[
    {
        "id": 1,
        "user_id": 1,
        "name": "A100训练集群",
        "bot_type": "DEVICE",
        "platform": "Infoflow",
        "group_id": "group_chat_001",
        "last_user_id": "zhangsan",
        "status": "running",
        "last_request_at": "2026-06-24T10:30:00",
        "cluster_configs": "{\"gpu-node-01\":{\"ip\":\"10.0.0.1\",\"devices\":[\"a100\",\"a100\",\"a100\",\"a100\"]},\"gpu-node-02\":{\"ip\":\"10.0.0.2\",\"devices\":[\"v100\",\"v100\"]}}",
        "config_overrides": "{}",
        "created_at": "2026-06-01T08:00:00",
        "updated_at": "2026-06-20T12:00:00"
    }
]
```

**关键字段说明**:

| 字段                | 类型            | 说明                                 |
| ----------------- | ------------- | ---------------------------------- |
| `id`              | int           | Bot 唯一 ID，用于后续查询状态                 |
| `bot_type`        | string        | `NODE` / `DEVICE` / `QUEUE`，决定占用粒度 |
| `status`          | string        | `running` / `stopped` / `error`    |
| `cluster_configs` | string (JSON) | 集群节点配置，记录节点名、IP、GPU 型号列表           |

### 获取单个 Bot 详情

```http
GET /api/bots/{bot_id}
Authorization: Bearer <token>
```

返回 `BotDetail`（比 `BotOut` 多了加密凭证的脱敏/原始值、owner 信息）。

---

## 节点列表与占用状态（核心）

### 获取单个 Bot 的状态

```http
GET /api/bots/{bot_id}/state
Authorization: Bearer <token>
```

这是**对接最核心的接口**，返回该 Bot 下所有节点的实时占用状态。

#### 响应格式（按 Bot 类型不同）

##### 1. NODE 类型 —— 整机粒度

```json
{
    "node-01": {
        "status": "exclusive",
        "current_users": [
            {
                "user_id": "zhangsan",
                "start_time": 1719200000,
                "duration": 7200,
                "is_notified": false
            }
        ],
        "booking_list": []
    },
    "node-02": {
        "status": "idle",
        "current_users": [],
        "booking_list": []
    }
}
```

##### 2. QUEUE 类型 —— 整机粒度 + 排队

```json
{
    "node-01": {
        "status": "exclusive",
        "current_users": [
            {
                "user_id": "zhangsan",
                "start_time": 1719200000,
                "duration": 7200,
                "is_notified": true
            }
        ],
        "booking_list": [
            {
                "user_id": "lisi",
                "start_time": 1719201000,
                "duration": 3600,
                "is_notified": false
            }
        ]
    }
}
```

##### 3. DEVICE 类型 —— XPU 卡粒度

```json
{
    "gpu-node-01": [
        {
            "dev_id": 0,
            "dev_model": "a100",
            "status": "exclusive",
            "current_users": [
                {
                    "user_id": "zhangsan",
                    "start_time": 1719200000,
                    "duration": 7200,
                    "is_notified": false
                }
            ]
        },
        {
            "dev_id": 1,
            "dev_model": "a100",
            "status": "shared",
            "current_users": [
                {
                    "user_id": "wangwu",
                    "start_time": 1719201800,
                    "duration": 3600,
                    "is_notified": false
                },
                {
                    "user_id": "zhaoliu",
                    "start_time": 1719202000,
                    "duration": 1800,
                    "is_notified": false
                }
            ]
        },
        {
            "dev_id": 2,
            "dev_model": "a100",
            "status": "idle",
            "current_users": []
        }
    ]
}
```

#### 字段详解

**节点/设备状态 `status`**:

| 值           | 含义        |
| ----------- | --------- |
| `idle`      | 空闲，无人占用   |
| `exclusive` | 独占，仅一人占用  |
| `shared`    | 共享，多人同时占用 |

**占用者信息 `current_users[]`**:

| 字段            | 类型     | 说明                        |
| ------------- | ------ | ------------------------- |
| `user_id`     | string | **占用人标识**（如流用户名）          |
| `start_time`  | int    | **占用开始时间**，Unix 秒级时间戳     |
| `duration`    | int    | **占用时长**（秒），默认 7200（2 小时） |
| `is_notified` | bool   | 是否已发送即将到期提醒（提前 5 分钟预警）    |


**排队信息 `booking_list[]`**:

结构同 `current_users[]`，仅 QUEUE 类型有实际排队逻辑。

**DEVICE 特有字段**:

| 字段          | 类型     | 说明                      |
| ----------- | ------ | ----------------------- |
| `dev_id`    | int    | GPU 设备编号，从 0 开始         |
| `dev_model` | string | GPU 型号（如 `a100`、`v100`） |

### 获取所有 Bot 的状态（批量）

```http
GET /api/bots/running-states
Authorization: Bearer <token>
```

一次性返回当前用户所有 Bot 的状态，格式为:

```json
{
    "1": { /* bot_id=1 的 state 字典 */ },
    "2": { /* bot_id=2 的 state 字典 */ }
}
```

**适用场景**：前端仪表盘、外部系统批量查询所有节点占用。

### 剩余时间计算

API 不直接返回剩余时间，调用方需要自行计算：

```
到期时间戳 = start_time + duration
剩余秒数   = max(duration - (当前时间戳 - start_time), 0)
```

**示例（Python）**:

```python
import time

def remaining_seconds(user_info):
    elapsed = int(time.time()) - user_info["start_time"]
    return max(user_info["duration"] - elapsed, 0)

def expires_at(user_info):
    return user_info["start_time"] + user_info["duration"]
```

---

## 占用粒度说明

### 三种粒度的对比

```
NODE    —— 锁住整台机器
  例: lock node-01 3h
  API 返回: { "node-01": { "status": "exclusive", "current_users": [...], ... } }

DEVICE  —— 锁住机器上的指定 GPU 卡
  例: lock gpu-node-01 dev0,1 3h
  API 返回: { "gpu-node-01": [ { "dev_id": 0, "status": "exclusive", ... }, ... ] }

QUEUE   —— 锁住整台机器，但用预约排队方式
  例: book node-01 → 排队 → 被通知 → lock node-01 → 超时自动释放
  API 返回: { "node-01": { "status": "exclusive", "current_users": [...], "booking_list": [...], ... } }
```

### 获取 Bot 粒度的方式

调用 `GET /api/bots/{bot_id}` 查看 `bot_type` 字段即可判断：

| `bot_type` | 占用单位 | state 顶层结构 | 锁定命令示例 |
|------------|----------|----------------|-------------|
| `NODE` | 整机 | `{节点名: {status, current_users, booking_list}}` | `lock <node>` |
| `DEVICE` | 单卡 | `{节点名: [{dev_id, dev_model, status, current_users}]}` | `lock <node> dev <ids>` |
| `QUEUE` | 整机 | 同 NODE（booking_list 有实际逻辑） | `book` → `lock` |

### DEVICE 类型的节点级汇总

对于 DEVICE bot，如需要按节点维度汇总：

- **FREE** — 节点内所有 XPU 卡 `status == "idle"`
- **BUSY** — 节点内所有 XPU 卡 `status != "idle"`
- **PARTIAL** — 部分卡空闲、部分卡占用

---

## 状态编辑（管理员）

### 更新 Bot 状态

```http
PUT /api/bots/{bot_id}/state
Authorization: Bearer <token>    # 需要 admin 或 super_admin 角色
Content-Type: application/json

{
    "node-01": {
        "status": "idle",
        "current_users": [],
        "booking_list": []
    }
}
```

**限制**：
- Bot 必须处于 `stopped` 状态才能编辑
- 自动校验：缺失节点会补充默认 idle 状态，多余设备会被裁剪，非法 status 会被修正
- 返回编辑后的 state 及 warning 列表

---

## 附录：数据结构速查

### user_info（占用者/排队者）

```typescript
interface UserInfo {
    user_id: string;      // 占用人标识
    start_time: number;   // 占用开始 Unix 时间戳（秒）
    duration: number;     // 占用时长（秒），默认 7200
    is_notified: boolean; // 是否已发送到期提醒
}
```

### NODE/QUEUE state 节点项

```typescript
interface NodeState {
    status: "idle" | "exclusive" | "shared";
    current_users: UserInfo[];
    booking_list: UserInfo[];
}
```

### DEVICE state 单卡项

```typescript
interface DeviceState {
    dev_id: number;
    dev_model: string;
    status: "idle" | "exclusive" | "shared";
    current_users: UserInfo[];
}
```

### 对接四要素速查

| 需求        | 从哪里取                                                                         |
| --------- | ---------------------------------------------------------------------------- |
| **节点列表**  | `GET /api/bots/{id}/state` 返回字典的 key                                         |
| **占用人**   | `current_users[].user_id`                                                    |
| **占用时间戳** | `current_users[].start_time`（开始）+ `current_users[].duration`（时长秒数）           |
| **占用粒度**  | `GET /api/bots/{id}` 的 `bot_type` 字段：`NODE`=整机, `DEVICE`=XPU卡, `QUEUE`=整机+排队 |

### 完整对接流程

```
1. POST /api/auth/login              → 获取 JWT token
2. GET  /api/bots                    → 获取所有 bot 列表（拿到 id、bot_type）
3. GET  /api/bots/running-states     → 一次性获取所有 bot 的占用状态
   或 GET /api/bots/{id}/state       → 逐个获取
4. 解析响应 → 节点列表、占用人、时间戳、粒度全部可得
```
