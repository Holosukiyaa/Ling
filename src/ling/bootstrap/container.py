"""Wire the SQLite persistence boundary. No HTTP, MCP, or coordinator client."""

from pathlib import Path

from ling.infrastructure.persistence.sqlite import SqliteDatabase


def compose(database_path: str | Path) -> SqliteDatabase:
    """Open a Ling database file and ensure its schema exists."""

    return SqliteDatabase(database_path)
