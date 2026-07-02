# 集群 XPU 资源监控 — API 接口文档

> 数据源：监控 3.0（monquery）  
> 集群：`wxtky02-p800-backup-8nic-vd` / `wxtky02-p800-8nic-vd`（48 节点 × 8 卡，node1~node51，排除 node13/14/17）

---

## 一、基本信息

| 项目  | 值                                   |
| --- | ----------------------------------- |
| 接口  | `GET /monquery/getHistoryitemdata`  |
| 地址  | `http://api.mt.noah.baidu.com:8557` |
| 粒度  | 5 分钟一个采样点                          |
| 超时  | 30 秒                                 |


---

## 二、请求参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `namespaces` | 是 | 节点命名空间，格式 `{cluster}-node{N}.wxtky02`，多个逗号分隔 |
| `items` | 是 | 指标项名称，多个逗号分隔（见 §三） |
| `start` | 是 | 起始时间，格式 `YYYYMMDDHHmmss` |
| `end` | 是 | 结束时间，格式 `YYYYMMDDHHmmss` |
| `interval` | 否 | 采样间隔（秒），默认 1800（30 分钟） |

---

## 三、三种核心指标

### 3.1 节点平均使用率

| 指标项 | 含义 | 单位 |
|--------|------|------|
| `XPU_AVERAGE_UTILIZATION` | 节点所有 8 张 XPU 卡的平均利用率 | %（0~100） |

### 3.2 单卡使用率

每张卡的 XPU 计算利用率，共 8 项：

| 指标项 | 含义 | 单位 |
|--------|------|------|
| `XPU0_XPU_UTILIZATION` | 第 0 号卡 XPU 使用率 | % |
| `XPU1_XPU_UTILIZATION` | 第 1 号卡 XPU 使用率 | % |
| `XPU2_XPU_UTILIZATION` | 第 2 号卡 XPU 使用率 | % |
| `XPU3_XPU_UTILIZATION` | 第 3 号卡 XPU 使用率 | % |
| `XPU4_XPU_UTILIZATION` | 第 4 号卡 XPU 使用率 | % |
| `XPU5_XPU_UTILIZATION` | 第 5 号卡 XPU 使用率 | % |
| `XPU6_XPU_UTILIZATION` | 第 6 号卡 XPU 使用率 | % |
| `XPU7_XPU_UTILIZATION` | 第 7 号卡 XPU 使用率 | % |

### 3.3 显存利用率

每张卡的显存占用率，共 8 项：

| 指标项 | 含义 | 单位 |
|--------|------|------|
| `XPU0_MEM_UTILIZATION` | 第 0 号卡显存占用率 | % |
| `XPU1_MEM_UTILIZATION` | 第 1 号卡显存占用率 | % |
| `XPU2_MEM_UTILIZATION` | 第 2 号卡显存占用率 | % |
| `XPU3_MEM_UTILIZATION` | 第 3 号卡显存占用率 | % |
| `XPU4_MEM_UTILIZATION` | 第 4 号卡显存占用率 | % |
| `XPU5_MEM_UTILIZATION` | 第 5 号卡显存占用率 | % |
| `XPU6_MEM_UTILIZATION` | 第 6 号卡显存占用率 | % |
| `XPU7_MEM_UTILIZATION` | 第 7 号卡显存占用率 | % |

> 完整查询需要传 17 个 items：`XPU_AVERAGE_UTILIZATION,XPU0_XPU_UTILIZATION,XPU0_MEM_UTILIZATION,...,XPU7_XPU_UTILIZATION,XPU7_MEM_UTILIZATION`

---

## 四、响应格式

```json
{
  "data": [
    {
      "NameSpace": "wxtky02-p800-backup-8nic-vd-node1.wxtky02",
      "Items": {
        "XPU_AVERAGE_UTILIZATION": [
          {"Timestamp": 1719187200, "Value": 0},
          {"Timestamp": 1719189000, "Value": 6.5}
        ],
        "XPU0_XPU_UTILIZATION": [
          {"Timestamp": 1719187200, "Value": 0},
          {"Timestamp": 1719189000, "Value": 12.3}
        ],
        "XPU0_MEM_UTILIZATION": [
          {"Timestamp": 1719187200, "Value": 93.5},
          {"Timestamp": 1719189000, "Value": 95.1}
        ]
      }
    }
  ],
  "message": "OK",
  "success": true
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `data[].NameSpace` | string | 节点命名空间 |
| `data[].Items` | object | key=指标项名，value=时间序列 |
| `Items[].Timestamp` | int | Unix 时间戳（秒） |
| `Items[].Value` | float | 指标值（0~100） |

---

## 五、XPU 使用率 vs 显存利用率

| 场景 | 显存利用率 | XPU 使用率 |
|------|-----------|-----------|
| 部署模型（加载权重） | 高（~90%+） | ≈0% |
| 执行推理 | 高 | 有实际数值 |
| 空闲（无模型加载） | 低 | ≈0% |
| **显存浪费**（僵尸进程） | >80% | <10% |

---

## 六、调用示例

### 6.1 查询单个节点的三个指标

```bash
curl -s "http://api.mt.noah.baidu.com:8557/monquery/getHistoryitemdata?\
namespaces=wxtky02-p800-backup-8nic-vd-node1.wxtky02&\
items=XPU_AVERAGE_UTILIZATION,XPU0_XPU_UTILIZATION,XPU0_MEM_UTILIZATION&\
start=20260624000000&\
end=20260624170000&\
interval=300"
```

### 6.2 Python 批量查询全部 17 个指标

```python
import json, urllib.request
from datetime import datetime

API = "http://api.mt.noah.baidu.com:8557/monquery/getHistoryitemdata"
CLUSTER = "wxtky02-p800-backup-8nic-vd"

# 构建完整 items 列表
items = ["XPU_AVERAGE_UTILIZATION"]
for c in range(8):
    items.append(f"XPU{c}_XPU_UTILIZATION")
    items.append(f"XPU{c}_MEM_UTILIZATION")

# 构建 namespace
ns = f"{CLUSTER}-node1.wxtky02"

now = datetime.now()
today = now.replace(hour=0, minute=0, second=0, microsecond=0)

params = {
    "namespaces": ns,
    "items": ",".join(items),
    "interval": "1800",
    "start": today.strftime("%Y%m%d%H%M%S"),
    "end": now.strftime("%Y%m%d%H%M%S"),
}
url = API + "?" + "&".join(f"{k}={v}" for k, v in params.items())

with urllib.request.urlopen(url, timeout=30) as resp:
    data = json.loads(resp.read())

node = data["data"][0]
avg = node["Items"]["XPU_AVERAGE_UTILIZATION"]
print(f"节点平均 XPU: {avg[-1]['Value']}%")

for c in range(8):
    util = node["Items"][f"XPU{c}_XPU_UTILIZATION"][-1]["Value"]
    mem  = node["Items"][f"XPU{c}_MEM_UTILIZATION"][-1]["Value"]
    print(f"  卡{c}: XPU={util}%, 显存={mem}%")
```

---

## 七、集群节点

| 项目 | 数值 |
|------|------|
| 集群 | `wxtky02-p800-backup-8nic-vd` / `wxtky02-p800-8nic-vd` |
| 总节点 | 51（node1 ~ node51） |
| 有监控数据 | 48 |
| 排除节点 | node13, node14, node17 |
| 每节点卡数 | 8 张 XPU |

> monquery 不支持通配符或自动发现 namespace。节点列表需硬编码维护，新增节点时手动追加。

---

## 八、附：其他可用指标

除上述三种核心指标外，每个节点还提供 947 个系统级监控项（CPU、内存、磁盘、网络、进程等），可通过以下接口查询完整列表：

```
GET /monquery/getItemList?namespaces={cluster}-node{N}.wxtky02
```

常用补充指标举例：

| 指标 | 含义 |
|------|------|
| `XPU{c}_MEM_USED` | 第 c 号卡显存已用量（MB） |
| `XPU{c}_MEM_TOTAL` | 第 c 号卡显存总量（MB） |
| `XPU{c}_XPU_OCCUPIED` | 第 c 号卡是否被占用（0/1） |
| `CPU_IDLE` | CPU 空闲率 |
| `MEM_USED_PERCENT` | 系统内存使用率 |
