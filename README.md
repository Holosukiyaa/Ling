# Ling

多 AI 协作的任务、权限和状态内核。调用方自己启动 Agent，Agent 再连接 Ling。Ling 不启动 Agent，也不依赖外部 Agent Coordinator。

当前包含领域模型、本地 application 用例、SQLite 持久化，以及 MCP stdio 入站适配器。调用方自己启动 Agent，再连接这个进程。Ling 不启动 Agent。

```text
python -m ling --database PATH
```

`LING_DATABASE` 可以代替 `--database`。协议输出只走 stdout，提示和日志走 stderr。

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
