import sqlite3
from pathlib import Path


def test_member_database_migration_is_valid_sql():
    migration = (
        Path(__file__).parents[1] / "db" / "migrations" / "0001_members.sql"
    ).read_text(encoding="utf-8")
    database = sqlite3.connect(":memory:")
    database.executescript(migration)

    tables = {
        row[0]
        for row in database.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "users",
        "sessions",
        "shared_stocks",
        "member_favorites",
        "annotations",
        "login_attempts",
    }.issubset(tables)
