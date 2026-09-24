"""Static import-direction checks for the ling package."""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
PACKAGE_ROOT = SRC_ROOT / "ling"

LAYERS = ("domain", "application", "infrastructure", "interfaces", "bootstrap")
SHARED_PACKAGE_NAMES = frozenset({"utils", "models", "services", "service"})
FORBIDDEN_SDK_ROOTS = frozenset(
    {
        "ag",
        "aiohttp",
        "fastapi",
        "fastmcp",
        "httpx",
        "langgraph",
        "mcp",
        "requests",
        "sqlalchemy",
        "sqlite3",
        "urllib3",
        "uvicorn",
    }
)
FORBIDDEN_SDK_MODULES = frozenset({"http.client", "urllib.request"})


def layer_of(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) >= 2 and parts[0] == "ling" and parts[1] in LAYERS:
        return parts[1]
    return None


def _root(module: str) -> str:
    return module.split(".", 1)[0]


def is_forbidden_sdk(module: str) -> bool:
    if _root(module) in FORBIDDEN_SDK_ROOTS:
        return True
    return any(
        module == banned or module.startswith(banned + ".")
        for banned in FORBIDDEN_SDK_MODULES
    )


def _under(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def imported_modules(current: str, tree: ast.AST, *, in_package: bool) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(current, node.module, node.level, in_package=in_package)
            if node.names[0].name == "*":
                found.append(base)
                continue
            for alias in node.names:
                if base:
                    found.append(f"{base}.{alias.name}")
                else:
                    found.append(alias.name)
    return found


def _resolve_from(current: str, module: str | None, level: int, *, in_package: bool) -> str:
    if level == 0:
        return module or ""
    parts = current.split(".")
    drop = level - 1 if in_package else level
    if drop < 0 or drop > len(parts):
        raise ValueError(f"relative import escapes package: {current}")
    parent = parts[: len(parts) - drop]
    if module:
        parent.extend(module.split("."))
    return ".".join(parent)


def violation(current: str, imported: str) -> str | None:
    parts = current.split(".")
    if any(part in SHARED_PACKAGE_NAMES for part in parts):
        return f"{current} uses a shared package name"

    layer = layer_of(current)
    if layer is None:
        if _under(imported, "ling.infrastructure") or _under(imported, "ling.interfaces"):
            return f"{current} imports {imported}; only bootstrap may wire adapters"
        if is_forbidden_sdk(imported):
            return f"{current} imports forbidden SDK {imported}"
        return None

    if is_forbidden_sdk(imported) and layer != "infrastructure":
        return f"{current} imports forbidden SDK {imported}"

    if not imported.startswith("ling"):
        return None

    if layer == "domain":
        if imported.startswith("ling.") and not _under(imported, "ling.domain"):
            return f"domain imports {imported}"
    elif layer == "application":
        for banned in ("ling.infrastructure", "ling.interfaces", "ling.bootstrap"):
            if _under(imported, banned):
                return f"application imports {imported}"
    elif layer == "interfaces":
        allowed = ("ling.interfaces", "ling.application", "ling.domain")
        if not any(_under(imported, prefix) for prefix in allowed):
            return f"interfaces imports {imported}"
    elif layer == "infrastructure":
        if _under(imported, "ling.interfaces") or _under(imported, "ling.bootstrap"):
            return f"infrastructure imports {imported}"
        if _under(imported, "ling.application") and not _under(
            imported, "ling.application.ports"
        ):
            return f"infrastructure imports {imported} outside application.ports"
    return None


def check_source(current: str, source: str, *, in_package: bool = False) -> list[str]:
    failures: list[str] = []
    if any(part in SHARED_PACKAGE_NAMES for part in current.split(".")):
        failures.append(f"{current} uses a shared package name")
    modules = imported_modules(current, ast.parse(source), in_package=in_package)
    failures.extend(message for name in modules if (message := violation(current, name)))
    return failures


def iter_package_modules(root: Path = PACKAGE_ROOT) -> list[tuple[str, Path]]:
    modules: list[tuple[str, Path]] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(SRC_ROOT).with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules.append((".".join(parts), path))
    return modules


def check_tree(root: Path = PACKAGE_ROOT) -> list[str]:
    failures: list[str] = []
    for module, path in iter_package_modules(root):
        failures.extend(
            f"{path.name}: {item}"
            for item in check_source(
                module,
                path.read_text(encoding="utf-8"),
                in_package=path.name == "__init__.py",
            )
        )
    return failures
