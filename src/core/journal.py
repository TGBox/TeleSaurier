"""SQLite-backed log of all sent and received faxes."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from src.core.models import Direction, FaxStatus, JournalEntry

_SCHEMA = """
CREATE TABLE IF NOT EXISTS faxes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL,
    number TEXT NOT NULL,
    document TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    ticket_id INTEGER,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    remote_id TEXT,
    origin TEXT,
    route TEXT NOT NULL DEFAULT '',
    user TEXT NOT NULL DEFAULT '',
    pages INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
# Columns added after the first release: name -> definition, see Journal._migrate.
_ADDED_COLUMNS = {
    "route": "TEXT NOT NULL DEFAULT ''",
    "user": "TEXT NOT NULL DEFAULT ''",
    "pages": "INTEGER NOT NULL DEFAULT 0",
    "attempts": "INTEGER NOT NULL DEFAULT 0",
    "next_attempt_at": "TEXT",
}
_COLUMNS = (
    "id, direction, number, document, subject, ticket_id, status, detail, remote_id, "
    "route, user, pages, attempts, next_attempt_at, created_at, updated_at"
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _to_entry(row: sqlite3.Row) -> JournalEntry:
    return JournalEntry(
        id=row["id"],
        direction=Direction(row["direction"]),
        number=row["number"],
        document=Path(row["document"]),
        subject=row["subject"],
        ticket_id=row["ticket_id"],
        status=FaxStatus(row["status"]),
        detail=row["detail"],
        remote_id=row["remote_id"],
        route=row["route"],
        user=row["user"],
        pages=row["pages"],
        attempts=row["attempts"],
        next_attempt_at=(
            datetime.fromisoformat(row["next_attempt_at"]) if row["next_attempt_at"] else None
        ),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


class Journal:
    """Opens a fresh connection per call, so it is safe to use from several threads."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._execute(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns that databases created by older versions lack."""
        with closing(self._connect()) as conn, conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(faxes)")}
            for name, definition in _ADDED_COLUMNS.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE faxes ADD COLUMN {name} {definition}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _execute(self, sql: str, params: tuple = ()) -> int:
        with closing(self._connect()) as conn, conn:
            return conn.execute(sql, params).lastrowid or 0

    def _query(self, sql: str, params: tuple = ()) -> list[JournalEntry]:
        with closing(self._connect()) as conn:
            return [_to_entry(row) for row in conn.execute(sql, params)]

    def add(
        self,
        direction: Direction,
        number: str,
        document: Path,
        status: FaxStatus,
        route: str,
        *,
        subject: str = "",
        ticket_id: int | None = None,
        remote_id: str | None = None,
        origin: str | None = None,
        user: str = "",
        pages: int = 0,
    ) -> JournalEntry:
        now = _now()
        entry_id = self._execute(
            "INSERT INTO faxes (direction, number, document, subject, ticket_id, status, "
            "remote_id, origin, route, user, pages, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                direction,
                number,
                str(document),
                subject,
                ticket_id,
                status,
                remote_id,
                origin,
                route,
                user,
                pages,
                now,
                now,
            ),
        )
        return self.get(entry_id)

    def update(
        self,
        entry_id: int,
        status: FaxStatus,
        detail: str = "",
        *,
        remote_id: str | None = None,
        attempts: int | None = None,
        next_attempt_at: datetime | None = None,
    ) -> JournalEntry:
        """``remote_id`` and ``attempts`` keep their value when omitted;
        ``next_attempt_at`` is always overwritten (None clears it)."""
        self._execute(
            "UPDATE faxes SET status = ?, detail = ?, remote_id = COALESCE(?, remote_id), "
            "attempts = COALESCE(?, attempts), next_attempt_at = ?, updated_at = ? WHERE id = ?",
            (
                status,
                detail,
                remote_id,
                attempts,
                next_attempt_at.isoformat() if next_attempt_at else None,
                _now(),
                entry_id,
            ),
        )
        return self.get(entry_id)

    def get(self, entry_id: int) -> JournalEntry:
        rows = self._query(f"SELECT {_COLUMNS} FROM faxes WHERE id = ?", (entry_id,))
        if not rows:
            raise KeyError(entry_id)
        return rows[0]

    def entries(self, limit: int = 500) -> list[JournalEntry]:
        """Newest first."""
        return self._query(f"SELECT {_COLUMNS} FROM faxes ORDER BY id DESC LIMIT ?", (limit,))

    def unfinished(self) -> list[JournalEntry]:
        return self._query(
            f"SELECT {_COLUMNS} FROM faxes WHERE direction = ? AND status IN (?, ?)",
            (Direction.OUTBOUND, FaxStatus.QUEUED, FaxStatus.SENDING),
        )

    def has_inbound(self, remote_id: str) -> bool:
        return bool(
            self._query(
                f"SELECT {_COLUMNS} FROM faxes WHERE direction = ? AND remote_id = ?",
                (Direction.INBOUND, remote_id),
            )
        )

    def has_origin(self, origin: str) -> bool:
        return bool(self._query(f"SELECT {_COLUMNS} FROM faxes WHERE origin = ?", (origin,)))
