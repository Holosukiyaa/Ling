# Ling

多 AI 协作的任务、权限和状态内核。它是 client-driven、MCP-first 的独立项目。用户自行启动 Agent，再连接 Ling 的 MCP。Ling 不启动 Agent，不选择模型，也不适配 Grok、Claude、Codex 或其他具体 Agent。Ling 是本地状态和权限的唯一事实来源。Agent Coordinator 只是可选观测层，不是运行依赖。

当前包含领域模型、本地 application 用例、SQLite 持久化，以及 MCP stdio 入站适配器。调用方自己启动 Agent，再连接这个进程。Ling 不启动 Agent。

```text
python -m ling --database PATH
```

`LING_DATABASE` 可以代替 `--database`。协议输出只走 stdout，提示和日志走 stderr。

SQLite schema 版本是 5，记在 `user_version`。没有版本标记的旧文件会升到当前版本，并保留已有数据。版本 2 会补上成功操作回执表，版本 3 给回执的创建时间加索引，版本 4 给票据增加可空的 `target_slot_id`，旧票保持 NULL。版本 5 增加 `slot_credentials` 和 `attachment_sessions`。已有槽位保留，但没有凭据，直到用相同模板 provision 一次。比 5 更新的文件会拒绝打开。迁移在一个事务里完成，失败则整段回滚。派发可以省略 `target_slot_id`，这时仍按目标模板进入队列。带上它时，该槽位必须已经注册，且模板与 `target_template_id` 相同；对不上返回 `invalid_input`，槽位不存在返回 `not_found`。离线槽位也可以接票。绑定了目标槽位的票只能由那个槽位领取，其他 worker 得到 `forbidden`，票据状态不变。

`ling_register_slot` 必须带上至少 16 个字符的 `attachment_token`。新槽位会同时写入凭据。没有凭据的旧槽位可以在模板相同的情况下 provision 一次；模板不同或已经有凭据时返回 `conflict`。Ling 只保存该 token 的 SHA-256 十六进制摘要。原始 token 不会进入 SQLite、operation receipt、RuntimeEvent、Coordinator 观测或错误消息。回执指纹只纳入这个摘要，不纳入原始 token。

`ling_attach` 接收 `slot_id` 和 `attachment_token`，校验摘要后创建新的不透明 session，并绑定当前 MCP stdio 进程。过期时间由正整数环境变量 `LING_ATTACHMENT_TTL_SECONDS` 决定，默认 3600 秒；空值或非法值使用默认值，并在 stderr 记一条警告。槽位不存在、没有凭据、摘要不匹配、session 过期或已撤销都返回同一个 `attachment_rejected`，消息不说明是哪一种。`ling_detach` 撤销当前 session 并清除绑定，重复调用仍然成功。进程结束时撤销当前 session，且不向 stdout 写内容。attach 和 detach 不写 operation receipt，也不通知 Coordinator。

除 `ling_register_slot`、`ling_attach`、`ling_detach` 以外，现有工具都要求已接驳的 session。未接驳返回 `attachment_required`，此时不改业务状态，也不通知 Coordinator。heartbeat、dispatch、claim、abandon_claim、submit、review、consume、acquire_file_lock 和 dashboard 的槽位身份来自这个 session。请求里如果仍然带了 `slot_id`、`issuer_slot_id` 或 `actor_slot_id`，它必须等于绑定的槽位，否则返回 `forbidden`。省略这些字段时使用绑定槽位。`target_slot_id` 仍是票据目标，不是调用者身份。这一版里任意已接驳 session 都能看到完整 dashboard。总控租约、按槽位过滤工具和 dashboard 不在这一版。

修改型工具可以带上 `operation_id`。不带时 Ling 仍会自己生成。同一个 `operation_id` 配上相同工具和相同参数，会原样返回第一次成功提交的结果，不会再次改状态。同一个 `operation_id` 配上不同工具或参数会返回 `conflict`。回执和本地状态在同一次提交里写入。只有成功提交的修改型操作会留下回执；失败不重放。`dashboard` 不接收 `operation_id`。回执默认永久保留，不属于 domain。部署者可以显式清理早于某个时刻的回执：

```text
python -m ling.maintenance --database PATH --before ISO_TIMESTAMP
```

`--before` 必须带时区。`LING_DATABASE` 可以代替 `--database`。成功时删除数量和 cutoff 只写到 stdout。清理不会改槽位、票据、锁或业务状态，也不会在启动 MCP 时自动执行。被删掉的 `operation_id` 之后不再保证能重放。

本地 RuntimeEvent 观测是可选的。设置 `LING_EVENT_LOG` 后，每次 MCP 请求在返回前追加一行 JSONL，内容是工具名、结果摘要和耗时，不包含业务正文、原始参数或文件路径。多个 Ling 进程可以写同一个文件；每次追加使用旁边的 `<LING_EVENT_LOG>.lock`，在独占锁里写完一整行再 flush。未设置或为空时不创建日志文件，也不创建锁文件。写日志或加锁失败只留在 stderr，不改变 MCP 结果。日志轮转是可选的：设置正整数 `LING_EVENT_LOG_MAX_BYTES` 后，追加会超过该大小时，把当前文件改名为 `<LING_EVENT_LOG>.1`，已有备份依次后移，并只保留 `.1` 到 `.N`。`LING_EVENT_LOG_BACKUPS` 是非负整数，默认 3。未设置最大字节数时不轮转，也不产生备份文件。非法配置只记 stderr，并关闭轮转或改用默认备份数，不影响 MCP 请求。RuntimeEvent 不是 domain 状态，也不是 operation receipt，也不改变 Agent Coordinator GUI 或 MCP 行为。

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
