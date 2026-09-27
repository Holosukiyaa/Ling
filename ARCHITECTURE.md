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

`domain` 不得导入 `application`、`infrastructure` 或 `interfaces`。`application` 只依赖 `domain` 和 `application.ports`，不得导入 infrastructure、interfaces、SQLite、HTTP 客户端、MCP SDK 或 ag。只有 `bootstrap` 可以把具体实现组装起来。未来增加 FastAPI 时，它和 MCP 共享 application 用例，不复制业务规则。

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
│     │  │  ├─ id_generator.py
│     │  │  ├─ observer.py         # 只接收 tool name、arguments、result
│     │  │  └─ observability.py    # 本地运行事件，不是 domain 状态
│     │  └─ dto.py                  # MCP/HTTP 共用的应用输入输出
│     ├─ infrastructure/
│     │  ├─ persistence/sqlite/
│     │  │  ├─ connection.py
│     │  │  ├─ schema.py
│     │  │  ├─ repositories.py
│     │  │  └─ unit_of_work.py
│     │  ├─ observability/         # 可选 JSONL 运行事件；不参与业务
│     │  │  ├─ file_lock.py        # 旁车锁，多进程追加一行
│     │  │  └─ jsonl_sink.py
│     │  ├─ coordinator/           # 可选观测投影；不参与本地治理
│     │  │  ├─ async_observer.py   # 有上限的后台队列
│     │  │  └─ http_observer.py
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

SQLite 用 WAL。schema 版本记在 SQLite `user_version`，当前是 3。没有版本标记的现有库会升到当前版本，已有的槽位、票据、锁和心跳都保留。版本 2 增加 `operation_receipts`，版本 3 为 `created_at` 增加索引。高于当前版本的库拒绝打开，不会静默降级。迁移在一个事务里完成，失败则整段回滚。读到第一次 `get` 时开启 `BEGIN IMMEDIATE`，把同一次用例里的读取和写入放进同一个写事务，避免两个领取同时成功。领取、消费和派发仍靠领域检查；槽位、票据和路径另有唯一约束，重复插入不会静默覆盖。

九个修改型命令可以接收调用方的 `operation_id`。不提供时仍由 Ling 生成。成功提交时，回执和这次业务写入在同一个 commit 里落盘。之后用同一个 `operation_id`、同一个工具和同一组规范化参数重试，会返回第一次的成功结果，不再次执行状态转换，也不再次通知可选的 Coordinator 观测。工具或参数不同则返回 `conflict`。`invalid_input`、`not_found`、`forbidden`、领域拒绝和 `internal` 不写回执。`dashboard` 没有 `operation_id` 输入。

`operation_receipts` 不属于 domain，默认永久保留。部署者用 `python -m ling.maintenance --database PATH --before ISO_TIMESTAMP` 显式删除 `created_at` 早于 cutoff 的回执。这个命令不启动 MCP，不产生 Coordinator 观测，也不在服务启动时自动运行。清理是单独的事务，只删除回执；槽位、票据、锁和业务状态保持原样。被删掉的 `operation_id` 之后不再保证能重放。

## 6. MCP 边界

MCP 只是入站适配器。每个工具只做参数解析、身份提取、调用 application 用例和错误映射，不包含权限判断、SQL 或 HTTP 调用。本地结果确定之后，server 先生成并写入 RuntimeEvent，再把成功且非重放的调用交给可选的 `RuntimeObserver`。Coordinator 失败不能改掉已经写下的事件，也不能改 MCP 返回。两条观测互不替代。RuntimeEvent 记录工具名、结果摘要和耗时，不记录业务正文，也不是 operation receipt，也不是业务状态。多个 Ling 进程可以共享同一个 JSONL 文件；`<LING_EVENT_LOG>.lock` 保证一次只写完整的一行。`LING_EVENT_LOG` 未设置时不创建日志文件，也不创建锁文件。事件写失败或加锁失败只留在 stderr，不能改写本地结果，也不能把成功变成 `internal`。日志轮转可选：`LING_EVENT_LOG_MAX_BYTES` 未设置时只追加；设置后在同一把 `<LING_EVENT_LOG>.lock` 里把当前文件改名为 `.1`、`.2` 等，默认保留 3 个备份。本地 RuntimeEvent JSONL 是排障日志，不是 domain 状态，也不是 Ling 的界面，不影响 Agent Coordinator GUI 或 MCP。Ling 没有内置 GUI，也不启动 GUI、Agent 或 Coordinator。未设置 `LING_COORDINATOR_URL` 时，Ling 不产生外部请求，也可以单独运行。

配置 `LING_COORDINATOR_URL`、可选的 `LING_COORDINATOR_API_KEY` 和 `LING_COORDINATOR_WORKSPACE` 之后，成功且非重放的注册、心跳、派发、领取、提交、审核和消费会投影到已有的 Agent Coordinator。部署者单独打开 `http://localhost:9889/dashboard`。失败调用、重放调用和 dashboard 查询不进入该队列。队列满或投影失败只留在 stderr。Coordinator GUI 是外部可选观测界面，不是 Ling 内置界面。

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

Ling 是槽位、票据、队列、文件锁和权限的唯一事实来源。claim、放弃领取、file lock、票据状态机和 Agent 启动都不经过 Agent Coordinator。Agent Coordinator 只接收成功调用之后的观测投影，不能影响 Ling 的本地结果。

这层观测是可选的，只存在于 `infrastructure.coordinator`，是运行时适配器。`application` 不调用它，端口上也没有关闭方法。`LING_COORDINATOR_URL` 未设置或为空时使用 `NullRuntimeObserver`，不创建后台线程，也不产生任何外部请求。设置之后，一条 daemon 线程用标准库投递注册、心跳和活动。MCP 调用只负责入队。超时、连接失败、非 2xx 和无法解析的响应只记 stderr。`LING_COORDINATOR_TIMEOUT` 非法时使用 0.5 秒。`LING_COORDINATOR_WORKSPACE` 未设置时使用进程当前工作目录。服务关闭时放弃尚未发送的观测，不等待正在进行的 HTTP，也不提交或回滚本地数据库。

投影 agent id 是 `ling-` 加上 slot id 的 SHA-256 十六进制摘要前 24 位。它不是 Ling domain 的 external agent id，不写入 domain、SQLite 或 DTO。`application` 的 `RuntimeObserver` 只接受 tool name、arguments 和 result 这三组普通数据，用例和 domain 不依赖 HTTP。

观测不调用任务领取、任务状态、文件锁或 Agent 启停接口。`http://localhost:9889/dashboard` 是可选观测服务自己的页面，不是 Ling 的依赖，Ling 也不连接它。

Ling 不 import `ag`。`ag` 只是开发期治理工具，不是运行时服务，也不是 Ling 的运行时依赖。worker 如果要在自己的代码票里使用 ag，那是 worker 环境里的事，不是 Ling 用例的一步。

Ling 不选择模型，也不适配 Grok、Claude、Codex 或其他具体 Agent。用户自行启动 Agent，再连接 Ling 的 MCP。

## 8. 实施顺序

1. 建立 `pyproject.toml`、`src/ling` 和 import 方向检查。
2. 完成 domain：模板、槽位、票据、三队列、消费锁和完整状态机。
3. 完成 SQLite repository 和短事务。注册、心跳、领取和文件锁都写入 Ling 自己的表。
4. 接入 MCP stdio 工具，让调用方自己的 Agent loop 可以驱动完整票据流。
5. 增加只读 dashboard 查询。

第 4 步和第 5 步已经接上。`python -m ling` 提供这十个工具，数据库路径来自 `--database` 或 `LING_DATABASE`。dashboard 走只读查询。

第一版不实现 FastAPI 页面、模型启动器或具体 Agent 适配器。可选的 Agent Coordinator 观测不是运行时依赖；未配置 URL 时它不存在。

