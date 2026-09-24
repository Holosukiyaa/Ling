# Ling

多 AI 协作的任务、权限和状态内核。本仓库当前只包含项目骨架和分层 import 方向检查。

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
