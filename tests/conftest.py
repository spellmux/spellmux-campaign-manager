"""Test-only speed settings.

The suite is dominated by two costs that carry no coverage. SQLite fsyncs every
commit, which on container overlay storage took the suite from seconds to about
fourteen minutes, and Argon2 is deliberately expensive per password operation
while every client uses the same fixed test password.
"""

from __future__ import annotations

from functools import lru_cache

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from campaign_manager import auth


@event.listens_for(Engine, "connect")
def _relax_sqlite_durability(dbapi_connection, _record) -> None:
    """Stop SQLite fsyncing per commit; tests never survive the process anyway.

    This makes the suite fast on any filesystem rather than only on a tmpfs, and
    only ever applies to SQLite, which is a test-only backend.
    """
    if type(dbapi_connection).__module__.split(".")[0] != "sqlite3":
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA synchronous=OFF")
        cursor.execute("PRAGMA journal_mode=MEMORY")
    finally:
        cursor.close()


@pytest.fixture(autouse=True)
def _memoize_password_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify each distinct password once per session.

    Hashing the same fixed password repeatedly proves nothing, and Argon2 is
    tuned to be slow. Real Argon2 is still exercised, just once per password, so
    a genuinely wrong password still fails to verify.
    """
    real_verify = auth.verify_password

    @lru_cache(maxsize=None)
    def cached_verify(password: str, password_hash: str) -> bool:
        return real_verify(password, password_hash)

    # authenticate() resolves this on the module, so patching here reaches it.
    # Hashing is memoized where it is called, because test modules import the
    # function by name and would keep their own reference past a patch.
    monkeypatch.setattr(auth, "verify_password", cached_verify)
