# Ling

多 AI 协作的任务、权限和状态内核。它是 client-driven、MCP-first 的独立项目。用户自行启动 Agent，再连接 Ling 的 MCP。Ling 不启动 Agent，不选择模型，也不适配 Grok、Claude、Codex 或其他具体 Agent。Ling 是本地状态和权限的唯一事实来源。Agent Coordinator 只是可选观测层，不是运行依赖。

当前包含领域模型、本地 application 用例、SQLite 持久化，以及 MCP stdio 入站适配器。调用方自己启动 Agent，再连接这个进程。Ling 不启动 Agent。

```text
python -m ling --database PATH
```

`LING_DATABASE` 可以代替 `--database`。协议输出只走 stdout，提示和日志走 stderr。

SQLite schema 版本是 6，记在 `user_version`。没有版本标记的旧文件会升到当前版本，并保留已有数据。版本 2 会补上成功操作回执表，版本 3 给回执的创建时间加索引，版本 4 给票据增加可空的 `target_slot_id`，旧票保持 NULL。版本 5 增加 `slot_credentials` 和 `attachment_sessions`。已有槽位保留，但没有凭据，直到被 provision。版本 6 增加 `controller_leases`，同时只允许一份有效租约。比 6 更新的文件会拒绝打开。迁移在一个事务里完成，失败则整段回滚。派发可以省略 `target_slot_id`，这时仍按目标模板进入队列。带上它时，该槽位必须已经注册，且模板与 `target_template_id` 相同；对不上返回 `invalid_input`，槽位不存在返回 `not_found`。离线槽位也可以接票。绑定了目标槽位的票只能由那个槽位领取，其他 worker 得到 `forbidden`，票据状态不变。没有 `target_slot_id` 的票仍按目标模板进入队列，但 worker 不能领取，也不能在 dashboard 里看到。

`ling_register_slot` 是一次性启动路径，不是普通发证入口。它只接受还没有凭据的 `codex-commander`，模板必须是 `mentor`，并带上至少 16 个字符的 `attachment_token`。这样第一份总控凭据不必先持有租约。该槽位已有凭据时返回 `conflict`，不能用这条路径轮换。其他槽位返回 `forbidden`，不创建槽位，也不写凭据。worker 不能给自己发放或替换 attachment token。已接驳并持有有效总控租约的 `codex-commander` 用 `ling_provision_slot` 创建或轮换其他槽位的凭据；没有租约时返回 `lease_required`。Ling 只保存 token 的 SHA-256 十六进制摘要。原始 token 不会进入 SQLite、operation receipt、RuntimeEvent、Coordinator 观测、错误消息或工具结果。回执指纹只纳入这个摘要，不纳入原始 token。

`ling_attach` 接收 `slot_id` 和 `attachment_token`，校验摘要后创建新的不透明 session，并绑定当前 MCP stdio 进程。过期时间由正整数环境变量 `LING_ATTACHMENT_TTL_SECONDS` 决定，默认 3600 秒；空值或非法值使用默认值，并在 stderr 记一条警告。槽位不存在、没有凭据、摘要不匹配、session 过期或已撤销都返回同一个 `attachment_rejected`，消息不说明是哪一种。`ling_detach` 撤销当前 session 并清除绑定，重复调用仍然成功。进程结束或 detach 时撤销当前 session，并释放绑定在该 session 上的总控租约，且不向 stdout 写内容。同一次 attach 替换旧 session 时，旧 session 的租约也会释放。attach 和 detach 不写 operation receipt，也不通知 Coordinator。

除 `ling_register_slot`、`ling_attach`、`ling_detach` 和只读的 `ling_diagnostics` 以外，现有工具都要求已接驳的 session。未接驳的 `ling_diagnostics` 不读取全局库。未接驳返回 `attachment_required`，此时不改业务状态，也不通知 Coordinator。heartbeat、dispatch、claim、abandon_claim、submit、review、consume、acquire_file_lock、dashboard、总控租约和 `ling_provision_slot` 的槽位身份来自这个 session。请求里如果仍然带了 `slot_id`、`issuer_slot_id` 或 `actor_slot_id`，它必须等于绑定的槽位，否则返回 `forbidden`。省略这些字段时使用绑定槽位。`target_slot_id` 仍是票据目标，不是调用者身份。worker 模板的 session 只能调用 `ling_heartbeat`、`ling_dashboard`、`ling_diagnostics`、`ling_claim`、`ling_abandon_claim`、`ling_submit`、`ling_acquire_file_lock`，以及连接用的 `ling_attach` 和 `ling_detach`。调用 `ling_dispatch`、`ling_review`、`ling_consume`、总控租约工具、`ling_provision_slot` 或 `ling_register_slot` 时，在进入用例之前返回 `forbidden`，消息是 `worker session cannot call this tool`。请求里的 `actor_slot_id`、`issuer_slot_id`、`target_slot_id` 或其他字段不能扩大这个 session 的可见范围或权限。mentor 和 checker 仍使用原来的完整工具。`tools/list` 从第一次响应起返回完整且稳定的公共目录，包含原有工具和只读的 `ling_diagnostics`。目录不随 attach、detach、过期或身份改变，也不依赖 `notifications/tools/list_changed`。目录说明不包含槽位秘密、票据正文或凭据。授权仍在调用时执行。未接驳的业务调用返回 `attachment_required`。worker 只能调用上面的白名单；派发、审核、消费、总控租约、凭据发放和 `ling_register_slot` 在进入用例前返回 `forbidden`。mentor 和 checker 可以调用目录中的其余工具。工具出现在目录里不等于可以调用。初始化声明资源订阅，目录本身不再用来表示身份变化。

worker 的 dashboard 在查询边界裁剪，形状与完整快照相同，但只保留自己的槽位和心跳，以及 `target_slot_id` 恰好等于该槽位的票据。这些票据上的消费锁保留，持有者是该 worker 的文件锁保留。其他 worker 的票、未绑定目标的票、其他领取者和队列、其他 mentor 的消费锁、以及无关文件锁都直接省略，不留空壳。mentor 和 checker 仍看到完整 dashboard。这一版不做总控专用投影。槽位的 `online` 按最近一次心跳时间计算，不沿用存储的在线标记。默认超过 900 秒为 `stale`，没有心跳记录为 `unknown`，只有未过期的心跳才是 `online`。结果同时给出 `presence` 和 `heartbeat_age_seconds`。这个计算不改存储行。心跳不会推进票据进展时间。`ling_dashboard` 的接驳校验和业务快照都走只读单元：各自普通 `BEGIN`，不是 `BEGIN IMMEDIATE`，因此都不占用写锁。接驳校验只读取当前 session 和对应槽位；业务快照在另一个连接里读取已提交的槽位、票据、消费锁和文件锁。两条只读事务都在返回前结束并关闭连接，成功和失败都关闭。整次已接驳的 dashboard 请求不执行 `BEGIN IMMEDIATE`，不写业务表，也不读、不写 operation receipt，也不通知 Coordinator。修改型用例仍在第一次领域读取时执行 `BEGIN IMMEDIATE`，并保持到提交或回滚；它们的接驳校验仍使用写事务。

`ling_acquire_controller_lease`、`ling_renew_controller_lease` 和 `ling_release_controller_lease` 只接受已接驳的 `codex-commander`。worker、checker、其他槽位和伪造 actor 得到 `forbidden`，不改租约。租约绑定当前 MCP session，同一时间只有一份有效租约。TTL 由正整数 `LING_CONTROLLER_LEASE_TTL_SECONDS` 决定，默认 3600 秒；空值或非法值使用默认值，并在 stderr 记一条警告。未过期时其他 session 取得租约会得到 `conflict`。过期、已释放，或绑定的 session 已撤销时，下一次取得会在同一个写事务里接管。续期和释放只作用于当前 session 持有的未过期租约，否则返回 `lease_required`。这些调用使用和原来一样的 `operation_id` 重放与 `conflict` 规则。

修改型工具可以带上 `operation_id`。不带时 Ling 仍会自己生成。同一个 `operation_id` 配上相同工具和相同参数，会原样返回第一次成功提交的结果，不会再次改状态。同一个 `operation_id` 配上不同工具或参数会返回 `conflict`。回执和本地状态在同一次提交里写入。只有成功提交的修改型操作会留下回执；失败不重放。`dashboard` 不接收 `operation_id`。回执默认永久保留，不属于 domain。部署者可以显式清理早于某个时刻的回执：

```text
python -m ling.maintenance --database PATH --before ISO_TIMESTAMP
```

`--before` 必须带时区。`LING_DATABASE` 可以代替 `--database`。成功时删除数量和 cutoff 只写到 stdout。清理不会改槽位、票据、锁或业务状态，也不会在启动 MCP 时自动执行。被删掉的 `operation_id` 之后不再保证能重放。

失败结果在原有 `error_code` 之外带有 `reason_code`、`stage`、`retryable`、`next_action`、`commit_state` 和独立的 `request_id`。`request_id` 不是 `operation_id`。同一次成功重放保持原来的业务载荷，新的 `request_id` 只出现在 MCP `_meta`，并且不会推进票据进展时间。不能确定是否已经提交时，`commit_state` 和诊断里的 `result` 都是 `unknown`，不能当成已经失败或已经回滚。同一个 `operation_id` 配上不同参数时，`reason_code` 是 `operation_mismatch`，`next_action` 是 `check_receipt_stop_retry`：核对原回执后停止盲重试，禁止换成新 id 再执行一遍。参数相同的成功重放仍返回第一次的结果。打开数据库失败、事务还没开始时是 `storage_unavailable` / `not_started`，不是 `internal`。凭据错误和槽位不存在对未授权调用方都是同一个 `attachment_rejected`。未预期异常的 MCP 消息是 `request failed`，stderr 只留 `request_id` 和异常类型，不回传堆栈、SQL、凭据、token 摘要、session id 或路径。同一次请求里的内层失败（打开存储、用例、dashboard、诊断读取）使用这次请求的同一个 `request_id`。编号只存在于这次调用的上下文，工作线程各自一份，调用结束后不会留给下一次请求。没有请求背景的巡检、ping 或队列轮询不写 `request_id`。

队列变化使用固定资源 `ling://queue`。initialize 声明资源订阅。订阅成功前先读取已提交的可见快照并记下基线，所以订阅返回之后的第一次变化不会被吞掉。通知发送失败时基线不前移，下一轮会再试。订阅和读取都按当前 session 裁剪；worker 看不到其他槽的票据。`notifications/resources/updated` 只表示可见快照变了，不表示已经领取或执行。不订阅的客户端仍用 `ling_dashboard` 拉取。断线后先读当前快照。同一条连接上的数据库调用在工作线程里串行执行，协议 ping 不跟它们抢事件循环。连接状态按 `instance_id` 分开，过期的 ping 不再算作仍有响应，一个进程关闭也不会把另一个仍在运行的进程标成停止。已经通过代次和身份复核并进入传输的无正文队列变更提示，网络交付可能在撤销之后才完成；Ling 不能撤回已经在途的消息。撤销、过期或重新绑定之后不会再用旧身份开始一次新的发送，之后读取队列仍会重新确认当前身份。

`python -m ling.runtime --database PATH --event-log LOG --state-file STATE` 是前台巡检进程，默认每 5 秒一轮，可用 `--interval` 改为正数。它只读打开已经存在且 schema 为 6 的库，不创建、不迁移、不改业务表。`--event-log` 只决定日志路径，轮转仍用 `LING_EVENT_LOG_MAX_BYTES` 和 `LING_EVENT_LOG_BACKUPS`。启动写任何状态、日志、锁、临时文件或轮转备份之前，会拒绝与数据库、SQLite sidecar 或其他输出重合的路径，包括大小写、相对路径和已存在文件的硬链接；拒绝时不改已有字节。坏掉的 JSONL 记录会被跳过并在诊断里标出，不会让下一轮巡检退出，也不会把错误类型改写成正常证据。同一个 state 文件只允许一个巡检实例，重复启动直接失败。状态文件是脱敏快照，原子替换，不是另一套任务库。进程存活、协议 ping、槽位心跳和票据进展是四件不同的事。ping 成功不会写成 `ling_heartbeat`，心跳也不会让票据看起来有进展。失联和超时只产生诊断，不释放文件锁或消费锁，不改票据，不续租约。巡检异常退出后，旧快照会因时间过期而不再被当成活服务。

`python -m ling.diagnostics --database PATH --event-log LOG --state-file STATE` 把一份脱敏 JSON 打到 stdout，错误走 stderr。它不启动 MCP，不通知 Coordinator，不修改数据库。可选 `--request-id`。MCP 工具 `ling_diagnostics` 按身份裁剪：未接驳只说明本连接阶段、协议版本、观测是否启用和下一步，不读全局库；worker 只看自己的槽和目标票据；mentor 与 checker 看全局。没有证据时宿主是否刷新目录、模型是否看到通知，都是 `not_confirmed` 或不可观察。没有入站记录不能反推客户端策略阻断。MCP 诊断和本命令读取同一套有效轮转范围：未设置或非法 `LING_EVENT_LOG_MAX_BYTES` 时只读当前文件；设置了正整数上限时再读 `.1` 到 `.N`，N 是有效备份数（`0` 不读备份，非法备份数用默认 3），不会按文件名前缀无限制搜索。坏记录、半行和读失败分别是 `event_log_rejected_record`、`event_log_partial_tail`、`event_log_unreadable`。单条坏记录会留下前后的有效记录。读失败不是空日志。这些告警只有代码，不含路径、正文、凭据、会话或其他槽位。已经被轮转删掉或读不到的请求保持不可见，不能写成没有发生过。

本地 RuntimeEvent 观测是可选的。设置 `LING_EVENT_LOG` 后，MCP 请求会追加 `ling.runtime_event.v1` 完成摘要，以及 `ling.runtime_event.v2` 的 received/finished 生命周期。摘要含工具名、结果和耗时，不含业务正文、原始参数、路径、token 或 session id。写锁等待有上限，超时或写失败只留在 stderr，不改变业务结果。多个 Ling 进程可以写同一个文件；每次追加使用旁边的 `<LING_EVENT_LOG>.lock`，在独占锁里写完一整行再 flush。未设置或为空时不创建日志文件，也不创建锁文件。写日志或加锁失败只留在 stderr，不改变 MCP 结果。日志轮转是可选的：设置正整数 `LING_EVENT_LOG_MAX_BYTES` 后，追加会超过该大小时，把当前文件改名为 `<LING_EVENT_LOG>.1`，已有备份依次后移，并只保留 `.1` 到 `.N`。`LING_EVENT_LOG_BACKUPS` 是非负整数，默认 3。未设置最大字节数时不轮转，也不产生备份文件。独立 runtime 使用同一套轮转规则。非法配置只记 stderr，并关闭轮转或改用默认备份数，不影响 MCP 请求。RuntimeEvent 不是 domain 状态，也不是 operation receipt，也不改变 Agent Coordinator GUI 或 MCP 行为。

本地 RuntimeEvent JSONL 只是排障日志，不是 Ling 的界面。Ling 没有内置 GUI，也不启动 GUI、Agent 或 Coordinator。

Agent Coordinator GUI 是外部可选观测界面。部署者单独启动已有的 Agent Coordinator，再打开 `http://localhost:9889/dashboard`。只有 `LING_COORDINATOR_URL` 非空时，成功且非重放的本地调用才会把槽位在线状态和活动放进后台队列，包括注册、心跳、派发、领取、提交、审核和消费。未设置或为空时，Ling 不创建后台线程，不产生外部请求，也可以单独运行。MCP 结果不等待这些 HTTP 请求。队列有上限，满了就丢弃新的观测并记 stderr。投影失败不改变本地结果。失败调用、重放调用、dashboard 查询、attach 和 detach 不通知 Coordinator。注册观测不携带原始 attachment token。Coordinator 不参与 claim、file lock 或票据状态机。投影用的 agent id 由 slot id 哈希得到，只存在于这次观测请求里，不是 Ling domain 的 external agent id。

可选环境变量：

- `LING_COORDINATOR_API_KEY`：存在时作为请求头 `X-API-Key`
- `LING_COORDINATOR_WORKSPACE`：未设置时使用进程当前工作目录
- `LING_COORDINATOR_TIMEOUT`：秒；非法值使用 0.5
- `LING_EVENT_LOG`：本地 JSONL 运行事件路径；未设置时不写文件
- `LING_EVENT_LOG_MAX_BYTES`：可选正整数；未设置时不轮转
- `LING_EVENT_LOG_BACKUPS`：可选非负整数，默认 3
- `LING_ATTACHMENT_TTL_SECONDS`：接驳 session 的正整数秒数，默认 3600；非法值使用默认值
- `LING_CONTROLLER_LEASE_TTL_SECONDS`：总控租约的正整数秒数，默认 3600；非法值使用默认值
- `LING_HEARTBEAT_STALE_SECONDS`：槽位心跳过期秒数，默认 900
- `LING_PROGRESS_STALE_SECONDS`：票据无进展告警秒数，默认 1800
- `LING_REQUEST_TIMEOUT_SECONDS`：请求有开始无结束的超时秒数，默认 1800
- `LING_PROTOCOL_PING_SECONDS`：协议 ping 间隔，默认 30
- `LING_PROTOCOL_PING_TIMEOUT_SECONDS`：协议 ping 超时，默认 2
- `LING_QUEUE_POLL_SECONDS`：订阅者只读变化检测间隔，默认 1
- `LING_RUNTIME_STATE`：可选巡检状态文件，供 MCP 诊断判断巡检是否过期
- `LING_SQLITE_TIMEOUT_SECONDS`：等待写锁的秒数，默认 5

依赖方向：

```text
interfaces -> application -> domain
infrastructure -> application.ports
infrastructure -> domain
bootstrap -> interfaces, application, infrastructure
```

`application` 只依赖 ports 和 domain。Agent Coordinator 观测只放在 infrastructure，是可选运行时适配器。`ag` 只是开发期治理工具，不是 Ling 的运行时依赖。Coordinator GUI 和具体 Agent CLI 都不是 Ling 的必需依赖。

本地检查：

```text
pip install -e ".[dev]"
python -c "import ling"
```
