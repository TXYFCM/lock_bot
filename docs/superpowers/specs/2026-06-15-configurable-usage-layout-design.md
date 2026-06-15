# 可配置的集群使用情况显示布局

## 背景与目标

集群使用情况（query 命令的输出）的显示布局当前**硬编码在源码里**，平台网页无任何配置项可调。

目标：让**负责添加机器人的管理员**通过网页「机器人高级配置」自由控制 query 输出的布局——排序、分组、单行格式。终端用户（在聊天里发命令的人）只负责看，不参与配置。

适用于全部三种机器人类型：DEVICE / NODE / QUEUE。

## 配置项

复用现有 `config_overrides` 机制（[router.py `_build_config_dict`](../../../python/lockbot/backend/app/bots/router.py)）。管理员在网页为**单个机器人**设置，立即随该机器人所有 query 输出生效。

在 [config.py `_CONFIG_SCHEMA`](../../../python/lockbot/core/config.py) 新增 4 个 key，均 `env=False`：

| Key | 作用 | 取值 | 默认值 |
|---|---|---|---|
| `USAGE_SORT` | 节点排序依据 | `name` / `dur_asc` / `dur_desc` | `dur_asc` |
| `USAGE_GROUP` | 空闲节点分组位置 | `none` / `idle_first` / `idle_last` | `idle_first` |
| `USAGE_LINE_TEMPLATE` | 被占用行格式模板 | str | `"{node} {dev} {user}{mode} {dur}"` |
| `USAGE_IDLE_TEMPLATE` | 空闲行格式模板 | str | `"{node} {dev} {status}"` |

**默认值复现「紧凑 + 空闲置顶 + 按剩余时长升序」**（方案 A）。管理员不填任何配置即得此效果；想要其它样子再去配。

- `USAGE_SORT`：
  - `name` — 按节点名原始顺序（dict 插入序）
  - `dur_asc` — 按节点最小剩余时长升序（短的在前）
  - `dur_desc` — 降序（长的在前）
- `USAGE_GROUP`：
  - `none` — 不分组，纯按 `USAGE_SORT` 排
  - `idle_first` — 空闲节点整体置顶，组内再按 `USAGE_SORT`
  - `idle_last` — 空闲节点整体置底

排序与分组用独立枚举，模板**只管单行格式**，互不耦合。

## 模板占位符

模板用 Python `str.format` 风格占位符。渲染层为每行构造字段字典并 `.format()`：

| 占位符 | 含义 | 空值场景 |
|---|---|---|
| `{node}` | 节点名 | 仅每节点首行有值，续行为空字符串 |
| `{dev}` | 设备范围，如 `dev0-7` | NODE/QUEUE 恒为空 |
| `{model}` | GPU 型号 | 仅异构 DEVICE 节点首行有值，否则空 |
| `{user}` | 使用者 user_id | 空闲行为空 |
| `{mode}` | 访问模式后缀（独占/共享，i18n） | 空闲行为空 |
| `{dur}` | 剩余时长（已格式化，i18n） | 空闲行为空 |
| `{status}` | 状态文案（空闲，i18n） | 仅空闲行使用 |

支持 Python 原生格式规格做对齐：`{dev:<8}` = 左对齐补空格到 8 宽。

## 换行与空格规则

- **换行**：模板只代表一行。模板内的 `\r`/`\n` 在渲染前一律 strip。行与行之间永远是单个 `\n`，由渲染层统一添加；管理员控制不了空行。唯一可能的整体空行来自异构警告 `hetero_warning`（独立 i18n 文案，不归模板管）。
- **行内普通空格**：模板里字面写几个就是几个，所见即所得。
- **对齐填充空格**：用 `:<N` 等 Python 格式规格表达，由 `str.format` 原生处理。

## 渲染架构

把「数据提取」与「字符串拼装」解耦，模板/排序/分组只作用在最后一层。

### 共享渲染引擎

新建 `python/lockbot/core/usage_render.py`，三种 bot 共用：

```
render_line(template, fields, fallback_template, *, bot_name=None) -> str
    # 1. 去掉 template 中的 \r \n
    # 2. try: return template.format(**fields)
    #    except (KeyError, ValueError, IndexError): 记 WARNING 日志, 用 fallback_template.format(**fields)

sort_and_group(node_entries, sort_mode, group_mode) -> list[node_entry]
    # node_entry 至少含 {is_idle, min_remaining, order_index}
    # 先分组（idle_first/idle_last/none），组内按 sort_mode（name=order_index / dur_asc / dur_desc）
```

`node_entry` 携带 `order_index`（原始插入序，供 `name` 排序与 stable sort 用）和 `min_remaining`（沿用 `_node_min_remaining` 逻辑）。

### DEVICE

改 [device_usage_utils.py](../../../python/lockbot/core/device_usage_utils.py)：

- `render_device_lines()` 现有的分段逻辑保留，但改为产出**结构化字段字典**列表（每个 dict 含 `node/dev/model/user/mode/dur/status` 及 `is_idle`），而非直接拼好的字符串。
- `get_current_usage()` 负责：为每节点构造 entries → `sort_and_group` → 逐行 `render_line`（按 `is_idle` 选模板）→ 拼接 `\n`。续行 `{node}` 置空、`{model}` 仅异构首行有值，在构造字段字典时决定。

### NODE / QUEUE

改 [node_bot.py](../../../python/lockbot/core/node_bot.py) 与 [queue_bot.py](../../../python/lockbot/core/queue_bot.py) 的 `_current_usage`：

- 抽掉各自手写的字符串拼装，改为构造字段字典（`{dev}`/`{model}` 为空）并调用同一套 `sort_and_group` + `render_line`。
- QueueBot 的 `booking_list`（排队信息）**保持独立渲染**，不进模板。模板只覆盖 current_users 行；排队列表沿用原 i18n 逻辑，接在对应节点行之后。

## 容错

模板写错（不存在占位符 `{foo}`、语法错误 `{dev`）→ `render_line` 捕获 `KeyError`/`ValueError`/`IndexError` → 回退到默认模板 → 在该机器人日志写一条 WARNING。绝不让坏模板使整个 query 输出崩溃。用户侧始终有正常输出，管理员可从日志发现填错。

## 默认行为不变量

- 不传任何 `USAGE_*` 配置时，DEVICE 输出 = 当前 release（已含本轮「紧凑+空闲置顶+时长升序」改动）逐字符一致。
- NODE/QUEUE 在默认配置下，输出语义与改造前一致（应用同样的紧凑+排序默认）。

## 测试方案

- **新增** `tests/core/test_usage_render.py`：
  - 排序：`name` / `dur_asc` / `dur_desc`
  - 分组：`none` / `idle_first` / `idle_last`
  - 模板正常渲染
  - 模板含 `\n` 被 strip
  - 坏模板（缺字段 / 语法错）回退默认 + 不抛异常
  - `:<N` 对齐生效
- **回归**：现有 [test_device_usage_utils.py](../../../tests/core/test_device_usage_utils.py)、test_node_bot、test_queue_bot 全绿。
- 关键不变量：默认配置逐字符复现当前输出。

## 网页前端

`config_overrides` 是通用 key-value 高级配置，新增的 4 个 key 通过现有高级配置入口即可填写，**前端无需改动**即可使用。（可选的后续增强：在 BotForm 高级配置区为这 4 个 key 加专门的表单控件与说明，本 spec 不含。）

## 不做（YAGNI）

- 不做终端用户级的临时覆盖（如 `query --sort=time`）。
- 不做预设模板下拉。
- 不让模板字符串承担排序/分组职责。
- 不做前端专用表单控件（沿用通用 config_overrides 输入）。
