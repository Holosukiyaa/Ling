# Ling

多 AI 协作的任务、权限和状态内核。调用方自己启动 Agent，Agent 再连接 Ling。Ling 不启动 Agent。Ling 是本地状态和权限的唯一事实来源，不把 Agent Coordinator 当作运行依赖。

当前包含领域模型、本地 application 用例、SQLite 持久化，以及 MCP stdio 入站适配器。调用方自己启动 Agent，再连接这个进程。Ling 不启动 Agent。

```text
python -m ling --database PATH
```

`LING_DATABASE` 可以代替 `--database`。协议输出只走 stdout，提示和日志走 stderr。

Agent Coordinator 观测是可选的。只有 `LING_COORDINATOR_URL` 非空时，成功的本地 MCP 调用才会把槽位在线状态和活动投影出去。未设置或为空时完全关闭，Ling 不产生外部请求。投影失败不改变本地结果。

Agent Coordinator 不参与 claim、file lock、票据状态机，也不启动 Agent。GUI 地址是 `http://localhost:9889/dashboard`。投影用的 agent id 由 slot id 哈希得到，只存在于这次观测请求里，不是 Ling domain 的 external agent id。

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

本地检查：

```text
pip install -e ".[dev]"
python -c "import ling"
```
