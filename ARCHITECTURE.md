# Ling 架构设计

## 1. 定位

Ling 是多 AI 协作的任务、权限和状态控制内核。它不负责启动模型，也不负责实现模型的对话循环。

Ling 由调用方驱动。用户自己启动 Agent。Agent 主动连接 Ling。Ling 不启动 Agent，不选择模型，也不适配某一种 Agent 产品。没有外部 Agent Coordinator 时，注册、心跳、派发、领取、提交、审核、消费和文件锁都在 Ling 本地完成。

操作者启动 `python -m ling`，得到一个 MCP stdio 服务。Agent 自己连接它，获得一个由模板声明出来的槽位。自带 loop 的运行时可以长期运行自己的循环，按需调用 Ling 的 MCP 工具。Ling 只决定“谁可以在什么时候对哪张票做哪一步”，不决定模型如何思考，也不启动那个运行时。

Ling 是独立项目。`ag` 是 Ling 的开发交付治理工具，不是 Ling 运行时必须调用的服务。Ling 不修改 `C:\WorkSpace\Code\ag` 的目录、包名和命令。代码票进入 worker 后，worker 仍可在自己的环境里使用 ag 完成工作树、验收、switch 和最终收口。

## 2. 分层

采用 Spring Boot 风格的分层，但保持 Python 项目的轻量性：

```text
                    ┌─────────────────────────┐
                    │ bootstrap / composition  │
                    │ 组装配置、实现和 MCP 服务 │
                    └───────────┬─────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
   interfaces              infrastructure       application
   MCP / CLI / HTTP       SQLite                 本地用例和端口
          │                     │                     │
          └───────────────┬─────┴─────────────────────┘
                          │
                       domain
                 规则、实体、状态机
```

依赖方向约定如下（箭头表示“依赖”）：

```text
interfaces  -> application -> domain
infrastructure -> application.ports
infrastructure -> domain
bootstrap -> interfaces, application, infrastructure
```

`domain` 不得导入 `application`、`infrastructure` 或 `interfaces`。`application` 不得导入 HTTP 客户端、SQLite、MCP SDK 或 ag。只有 `bootstrap` 可以把具体实现组装起来。未来增加 FastAPI 时，它和 MCP 共享 application 用例，不复制业务规则。

不要建立一个所有层都依赖的 `utils` 或 `models` 大包。跨层对象要么是 domain value object，要么是 application DTO，要么是某个端口的协议。

## 3. 目录骨架

```text
ling/
├─ pyproject.toml
├─ README.md
├─ ARCHITECTURE.md
├─ src/
│  └─ ling/
│     ├─ __main__.py
│     ├─ bootstrap/
│     │  ├─ container.py          # 组装 SQLite、时钟，并启动 MCP stdio
│     │  └─ runtime.py            # 时钟，以及票据和操作 id
│     ├─ domain/
│     │  ├─ agents/
│     │  │  ├─ entities.py        # Template、Slot
│     │  │  ├─ values.py          # SlotId、TemplateId、Level
│     │  │  └─ policy.py          # 上下级和能力判断
│     │  ├─ tickets/
│     │  │  ├─ entities.py        # Ticket 聚合
│     │  │  ├─ states.py          # queued/claimed/...
│     │  │  └─ transitions.py     # transitions 的进程内封装
│     │  ├─ queues.py              # 三条队列的领域语义
│     │  ├─ locks.py               # 消费锁和本地文件锁
│     │  └─ errors.py              # 领域错误
│     ├─ application/
│     │  ├─ commands/              # 写操作用例
│     │  │  ├─ register_slot.py
│     │  │  ├─ heartbeat.py
│     │  │  ├─ dispatch.py
│     │  │  ├─ claim.py
│     │  │  ├─ abandon_claim.py
│     │  │  ├─ submit.py
│     │  │  ├─ review.py
│     │  │  ├─ consume.py
│     │  │  └─ acquire_file_lock.py
│     │  ├─ queries/                # 只读查询
│     │  │  └─ dashboard.py
│     │  ├─ ports/                  # application 需要的外部能力
│     │  │  ├─ repositories.py
│     │  │  ├─ unit_of_work.py
│     │  │  ├─ clock.py
│     │  │  └─ id_generator.py
│     │  └─ dto.py                  # MCP/HTTP 共用的应用输入输出
│     ├─ infrastructure/
│     │  ├─ persistence/sqlite/
│     │  │  ├─ connection.py
│     │  │  ├─ schema.py
│     │  │  ├─ repositories.py
│     │  │  └─ unit_of_work.py
│     │  ├─ coordinator/           # 空目录；当前不定义出站协议
│     │  └─ ag/                    # 空目录；ag 不是运行时依赖
│     └─ interfaces/
│        ├─ mcp/
│        │  ├─ server.py
│        │  ├─ schemas.py
│        │  ├─ adapter.py
│        │  └─ tools/
│        ├─ cli/                    # 后续的人工运维入口
│        └─ http/                   # 后续 FastAPI 入口，第一版不实现
```

## 4. Domain 核心模型

模板是数据，不是三种固定的 Python 类。初始模板只是三份声明：

| 模板 | 等级 | 允许的动作 | 管理范围 |
| --- | ---: | --- | --- |
| mentor | 2 | 向队列 1 派发；消费自己任务的队列 2 结果 | worker |
| worker | 1 | 从队列 1 领取；完成后提交到队列 3 | 无 |
| checker | 2 | 从队列 3 审核；结果进入队列 2 | 无，也不受 mentor 管理 |

核心对象：

- `Template`：模板声明、等级、能力和可管理的模板类型。
- `Slot`：模板的一次实例化，拥有稳定的 slot id、在线状态和最近心跳时间。slot id 不是外部系统的 agent id。
- `Ticket`：全程不变的 ticket id、发起槽位、目标类型、任务内容、当前状态、当前队列、领取者和结果。
- `ConsumptionLock`：防止同一个 mentor 在前一张结果未消费前重复派发；它是 Ling 自己的记录。
- `FileLock`：Ling 自己记录的路径占用，包含 ticket、持有槽位和路径集合。它不是操作系统文件锁。消费该票时释放。worker 放弃领取时，同一事务把票退回 queued，并删除该票的文件锁及其全部路径。消费锁不随放弃释放。票仍处于 claimed 时，没有单独的释放命令。

票据状态和队列的映射固定为：

```text
创建/派发       queued       队列 1（任务）
worker 领取     claimed      队列 1
worker 放弃领取 queued       队列 1（任务），领取者清空
worker 提交     submitted    队列 3（待审核）
checker 接受    accepted     队列 2（结果）
checker 拒绝    rejected     队列 2（结果）
mentor 消费     consumed     不再可消费
```

“调用被拒绝”与 checker 的“审核结论为 rejected”是两件事：前者不改变状态和队列，后者是合法状态转移的一种结果。

所有状态转移必须通过 `domain.tickets.transitions`。非法步骤停在原地，application 不得在状态机之外直接修改状态字段。

## 5. Application 用例

每个命令是一个独立的本地用例，负责一次完整的授权和状态机协调。这些用例不调用外部 Agent Coordinator。

| 用例 | 调用者 | 主要动作 |
| --- | --- | --- |
| `register_slot` | 任意已连接的客户端 | 校验模板，在 Ling 本地创建槽位 |
| `heartbeat` | 已注册槽位 | 把槽位标为在线，并写入最近心跳时间 |
| `dispatch` | mentor | 校验等级和消费锁，创建队列 1 票 |
| `claim` | worker | 校验槽位、queued 状态和领取冲突，在本地事务中记录领取者 |
| `abandon_claim` | worker | 只有当前领取者能把 claimed 票退回队列 1，清空领取者，并删除该票的文件锁；原 mentor 的消费锁仍绑定这张票 |
| `submit` | worker | 校验领取者，把票从队列 1 推到队列 3 |
| `review` | checker | 从队列 3 取票，产生 accepted/rejected 结论并放入队列 2 |
| `consume` | 原 mentor | 只能消费自己发出的 ticket id；消费后释放消费锁和该票的文件锁 |
| `acquire_file_lock` | 已领取票的 worker | 在 Ling 本地记录 ticket、持有槽位和路径；冲突路径不能被另一张票占用 |
| `dashboard` | 只读客户端 | 返回 Ling 自己的槽位、票据、队列、锁和心跳，不改状态 |

每个命令遵循同一顺序：

1. 在 Ling 的事务中读取槽位、票据和锁，完成权限检查。
2. 调用领域规则或状态机验证目标变化。
3. 检查通过后提交 Ling 状态；失败则保持原状态和原队列。

`dashboard` 只读取这些记录，不提交。

SQLite 用 WAL。读到第一次 `get` 时开启 `BEGIN IMMEDIATE`，把同一次用例里的读取和写入放进同一个写事务，避免两个领取同时成功。领取、消费和派发仍靠领域检查；槽位、票据和路径另有唯一约束，重复插入不会静默覆盖。

## 6. MCP 边界

MCP 只是入站适配器。每个工具只做参数解析、身份提取、调用 application 用例和错误映射，不包含权限判断、SQL 或 HTTP 调用。

第一批工具可以稳定为：

```text
ling_register_slot
ling_heartbeat
ling_dispatch
ling_claim
ling_abandon_claim
ling_submit
ling_review
ling_consume
ling_acquire_file_lock
ling_dashboard
```

工具返回统一的结构化结果：`ok`、`operation_id`、`ticket_id`、`state`、`queue`、`error_code` 和可读消息。缺字段、类型错误和空字符串是 `invalid_input`。权限和状态机错误沿用用例的错误码，例如 `forbidden`、`invalid_transition`、`already_claimed`、`not_found`、`conflict`。未预期的失败是 `internal`，客户端只看到这句说明，堆栈留在 stderr。

调用方的 loop 位于 Ling 进程之外：

```text
调用方自己的 Agent loop
        │ MCP stdio
        ▼
interfaces.mcp
        ▼
application use case
        ├─ domain rules + transitions
        └─ Ling SQLite
```

因此，给一个运行时分配 worker 槽位只需要注册一份模板声明，并让该运行时自己连接 Ling。Ling 不知道该运行时的内部循环，也不绑定具体产品。

## 7. 外部系统边界

本地治理不依赖 Agent Coordinator、HTTP 服务或某个 Agent CLI。`application` 不定义出站协调协议，也不发明一套 HTTP/JSON 接口。

如果将来确有一个外部系统要参与，只能作为可选适配器加在 infrastructure，不能变成用例的必需参数。那个适配器出现之前，不预写它的协议。

Ling 不 import `ag`。`ag` 不是运行时服务。worker 如果要在自己的代码票里使用 ag，那是 worker 环境里的事，不是 Ling 用例的一步。

## 8. 实施顺序

1. 建立 `pyproject.toml`、`src/ling` 和 import 方向检查。
2. 完成 domain：模板、槽位、票据、三队列、消费锁和完整状态机。
3. 完成 SQLite repository 和短事务。注册、心跳、领取和文件锁都写入 Ling 自己的表。
4. 接入 MCP stdio 工具，让调用方自己的 Agent loop 可以驱动完整票据流。
5. 增加只读 dashboard 查询。

第 4 步和第 5 步已经接上。`python -m ling` 提供这十个工具，数据库路径来自 `--database` 或 `LING_DATABASE`。dashboard 走只读查询。

第一版不实现 FastAPI 页面、模型启动器、具体 Agent 适配器，也不把外部 Coordinator 当成运行时依赖。

