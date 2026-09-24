# Ling 架构设计

## 1. 定位

Ling 是多 AI 协作的任务、权限和状态控制内核。它不负责启动模型，也不负责实现模型的对话循环。

一个 AI 运行时通过 MCP 连接 Ling，获得一个由模板声明出来的槽位。Grok Build CLI 这类自带 loop 的运行时可以长期运行自己的循环，按需调用 Ling 的 MCP 工具。Ling 只决定“谁可以在什么时候对哪张票做哪一步”，不决定模型如何思考。

Ling 是独立项目，不修改 `C:\WorkSpace\Code\ag` 的目录、包名和命令。代码票进入 worker 后，仍由 ag 完成工作树、验收、switch 和最终收口。

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
   MCP / CLI / HTTP       SQLite / HTTP / ag     用例和端口
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
│     │  ├─ settings.py          # 环境变量和配置文件
│     │  ├─ container.py          # 唯一的依赖组装处
│     │  └─ mcp_app.py            # 构造 MCP server
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
│     │  ├─ locks.py               # 消费锁语义
│     │  └─ errors.py              # 领域错误
│     ├─ application/
│     │  ├─ commands/              # 写操作用例
│     │  │  ├─ register_slot.py
│     │  │  ├─ heartbeat.py
│     │  │  ├─ dispatch.py
│     │  │  ├─ claim.py
│     │  │  ├─ submit.py
│     │  │  ├─ review.py
│     │  │  ├─ consume.py
│     │  │  └─ acquire_file_lock.py
│     │  ├─ queries/                # 只读查询
│     │  │  └─ dashboard.py
│     │  ├─ ports/                  # application 需要的外部能力
│     │  │  ├─ repositories.py
│     │  │  ├─ coordinator.py
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
│     │  ├─ coordinator/
│     │  │  └─ http_client.py      # localhost:9889 适配器
│     │  ├─ ag/
│     │  │  └─ delivery_gateway.py # 后续接入 ag 命令，不导入 ag 包
│     │  └─ system_clock.py
│     └─ interfaces/
│        ├─ mcp/
│        │  ├─ server.py
│        │  ├─ schemas.py
│        │  └─ tools/
│        ├─ cli/                    # 后续的人工运维入口
│        └─ http/                   # 后续 FastAPI 入口，第一版不实现
└─ tests/
   ├─ unit/domain/
   ├─ unit/application/
   ├─ integration/
   └─ architecture/                # import 方向检查
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
- `Slot`：模板的一次实例化，拥有稳定的 slot id、外部 coordinator agent id、在线状态和心跳时间。
- `Ticket`：全程不变的 ticket id、发起槽位、目标类型、任务内容、当前状态、当前队列、领取者和结果。
- `ConsumptionLock`：防止同一个 mentor 在前一张结果未消费前重复派发；它是 Ling 自己的记录。
- `FileLock`：代码写入前由 Agent Coordinator 提供的租约；Ling 通过端口调用，不伪造文件锁。

票据状态和队列的映射固定为：

```text
创建/派发       queued       队列 1（任务）
worker 领取     claimed      队列 1
worker 提交     submitted    队列 3（待审核）
checker 接受    accepted     队列 2（结果）
checker 拒绝    rejected     队列 2（结果）
mentor 消费     consumed     不再可消费
```

“调用被拒绝”与 checker 的“审核结论为 rejected”是两件事：前者不改变状态和队列，后者是合法状态转移的一种结果。

所有状态转移必须通过 `domain.tickets.transitions`。非法步骤停在原地，application 不得在状态机之外直接修改状态字段。

## 5. Application 用例

每个命令是一个独立用例，负责一次完整的授权、状态机和外部调用协调：

| 用例 | 调用者 | 主要动作 |
| --- | --- | --- |
| `register_slot` | 任意 AI 运行时 | 校验模板，创建槽位，调用 `/agents/register` |
| `heartbeat` | 已注册槽位 | 更新心跳，调用 `/agents/{id}/heartbeat` |
| `dispatch` | mentor | 校验等级和消费锁，创建队列 1 票 |
| `claim` | worker | 校验槽位和票，确认状态机允许后调用 `/tasks/claim`；成功才记录领取者 |
| `submit` | worker | 校验领取者，把票从队列 1 推到队列 3 |
| `review` | checker | 从队列 3 取票，产生 accepted/rejected 结论并放入队列 2 |
| `consume` | 原 mentor | 只能消费自己发出的 ticket id，消费后释放消费锁 |
| `acquire_file_lock` | 已领取代码票的 worker | 调用 `/locks/acquire`，没有租约就不能写文件 |
| `dashboard` | 操作员/只读客户端 | 汇总 Ling 记录和 coordinator 状态 |

每个命令遵循同一顺序：

1. 在 Ling 的事务中读取槽位、票据和消费锁，完成权限检查。
2. 调用领域状态机验证目标转移。
3. 必要时调用 Agent Coordinator。
4. 外部成功后提交 Ling 状态；失败则保持原状态和原队列。

外部调用使用 ticket id 或 operation id 做幂等键。SQLite 用 WAL 和短事务；领取、消费和派发必须有唯一约束，防止两个 MCP 请求同时成功。对“外部成功但本地提交失败”的情况保留操作记录，允许使用相同 id 重试，不通过猜测状态补写队列。

## 6. MCP 边界

MCP 只是入站适配器。每个工具只做参数解析、身份提取、调用 application 用例和错误映射，不包含权限判断、SQL 或 HTTP 调用。

第一批工具可以稳定为：

```text
ling_register_slot
ling_heartbeat
ling_dispatch
ling_claim
ling_submit
ling_review
ling_consume
ling_acquire_file_lock
ling_dashboard
```

工具返回统一的结构化结果：`ok`、`operation_id`、`ticket_id`、`state`、`queue`、`error_code` 和可读消息。错误码区分 `forbidden`、`invalid_transition`、`already_claimed`、`not_found`、`coordinator_unavailable`、`conflict`，这样 Grok 的 loop 可以决定等待、重试还是结束本轮。

Grok Build CLI 的 loop 位于 Ling 进程之外：

```text
Grok Build CLI loop
        │ MCP stdio
        ▼
interfaces.mcp
        ▼
application use case
        ├─ domain rules + transitions
        ├─ Ling SQLite
        └─ Agent Coordinator HTTP
```

因此，给 Grok 分配一个 worker 槽位只需要注册一份模板声明和运行一个 MCP 客户端，不需要 Ling 知道 Grok 的内部循环实现。以后接入别的 AI，只增加客户端配置或新的槽位声明，不改领域规则。

## 7. Agent Coordinator 适配边界

第一版只依赖这些接口：

- `POST /agents/register`
- `POST /agents/{id}/heartbeat`
- `POST /tasks/claim`
- `POST /locks/acquire`
- 现有 `GET /dashboard` 供操作员查看

Agent Coordinator 不是 Ling 的权威数据源，也不接管验收、switch 和收口。`infrastructure/coordinator/http_client.py` 实现 `application.ports.CoordinatorPort`，返回 application 能理解的结果；任何 HTTP 状态码和 JSON 细节都在适配器内消化。

后续接 ag 时只增加 `DeliveryGateway` 适配器。Ling 不 import `ag`，不改 ag 仓库；worker 在自己的代码票上下文中继续执行原有 ag 命令链。

## 8. 实施顺序

1. 建立 `pyproject.toml`、`src/ling` 和 import 方向检查。
2. 完成 domain：模板、槽位、票据、三队列、消费锁和完整状态机。
3. 完成 SQLite repository、事务和幂等操作记录；用 fake coordinator 写 application 测试。
4. 接入真实 Agent Coordinator HTTP 适配器和槽位注册/心跳/抢票/文件锁。
5. 接入 MCP stdio 工具，让 Grok Build CLI 可以驱动完整票据流。
6. 增加只读 dashboard 查询，并在最后接入 ag 的工作树、验收、switch 和收口。

第一版不实现 FastAPI 页面、模型启动器、LangGraph/Studio、收费云适配器或 Agent Coordinator 的替代实现。

