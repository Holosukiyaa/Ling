# Ling

多 AI 协作的任务、权限和状态内核。当前包含领域模型，以及通过端口编排的同步 application 用例。

Application 测试使用 `tests/unit/application` 里的内存 unit of work 和 fake coordinator，不连接 SQLite 或 Agent Coordinator。

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
python -m pytest -q
python -c "import ling.domain"
```
