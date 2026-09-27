# Ling

多 AI 协作的任务、权限和状态内核。它是 client-driven、MCP-first 的独立项目。用户自行启动 Agent，再连接 Ling 的 MCP。Ling 不启动 Agent，不选择模型，也不适配 Grok、Claude、Codex 或其他具体 Agent。Ling 是本地状态和权限的唯一事实来源。Agent Coordinator 只是可选观测层，不是运行依赖。

当前包含领域模型、本地 application 用例、SQLite 持久化，以及 MCP stdio 入站适配器。调用方自己启动 Agent，再连接这个进程。Ling 不启动 Agent。

```text
python -m ling --database PATH
```

`LING_DATABASE` 可以代替 `--database`。协议输出只走 stdout，提示和日志走 stderr。

SQLite schema 版本是 3，记在 `user_version`。没有版本标记的旧文件会升到当前版本，并保留已有数据。版本 2 会补上成功操作回执表，版本 3 给回执的创建时间加索引。比 3 更新的文件会拒绝打开。

修改型工具可以带上 `operation_id`。不带时 Ling 仍会自己生成。同一个 `operation_id` 配上相同工具和相同参数，会原样返回第一次成功提交的结果，不会再次改状态。同一个 `operation_id` 配上不同工具或参数会返回 `conflict`。回执和本地状态在同一次提交里写入。只有成功提交的修改型操作会留下回执；失败不重放。`dashboard` 不接收 `operation_id`。回执默认永久保留，不属于 domain。部署者可以显式清理早于某个时刻的回执：

```text
python -m ling.maintenance --database PATH --before ISO_TIMESTAMP
```

`--before` 必须带时区。`LING_DATABASE` 可以代替 `--database`。成功时删除数量和 cutoff 只写到 stdout。清理不会改槽位、票据、锁或业务状态，也不会在启动 MCP 时自动执行。被删掉的 `operation_id` 之后不再保证能重放。

Agent Coordinator 观测是可选的。只有 `LING_COORDINATOR_URL` 非空时，成功的本地 MCP 调用才会把槽位在线状态和活动放进后台队列。MCP 结果不等待这些 HTTP 请求。队列有上限，满了就丢弃新的观测并记 stderr。未设置或为空时完全关闭，不创建后台线程，也不产生外部请求。投影失败不改变本地结果。

Agent Coordinator 不参与 claim、file lock、票据状态机，也不启动 Agent。`http://localhost:9889/dashboard` 是可选观测服务自己的页面，不是 Ling 的依赖。投影用的 agent id 由 slot id 哈希得到，只存在于这次观测请求里，不是 Ling domain 的 external agent id。

可选环境变量：

- `LING_COORDINATOR_API_KEY`：存在时作为请求头 `X-API-Key`
- `LING_COORDINATOR_WORKSPACE`：未设置时使用进程当前工作目录
- `LING_COORDINATOR_TIMEOUT`：秒；非法值使用 0.5

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
