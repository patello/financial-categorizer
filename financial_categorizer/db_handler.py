"""SQLite database handler for financial-categorizer.

Manages tables for transactions, categories, match rules, manual overrides,
and metadata. Follows a connect/disconnect pattern with date type adapters.
"""

import sqlite3
from datetime import date, datetime


def adapt_date(val):
    """Convert datetime.date to ISO format string for SQLite storage."""
    return val.isoformat()


def convert_date(val):
    """Convert ISO format string from SQLite to datetime.date."""
    return date.fromisoformat(val.decode() if isinstance(val, bytes) else val)


def adapt_datetime(val):
    """Convert datetime.datetime to ISO format string for SQLite storage."""
    return val.isoformat()


def convert_datetime(val):
    """Convert ISO format string from SQLite to datetime.datetime."""
    s = val.decode() if isinstance(val, bytes) else val
    return datetime.fromisoformat(s)


sqlite3.register_adapter(date, adapt_date)
sqlite3.register_converter("date", convert_date)
sqlite3.register_adapter(datetime, adapt_datetime)
sqlite3.register_converter("timestamp", convert_datetime)


class DatabaseHandler:
    """Handles connection to a SQLite database for financial categorization.

    Creates the schema on init. Supports connect/disconnect/commit pattern
    with PARSE_DECLTYPES and PARSE_COLNAMES for automatic type conversion.

    For :memory: databases, the connection is kept open after init since
    closing it would destroy all data. For file-based databases, the
    connection is closed after schema creation.
    """

    def __init__(self, db_file: str):
        """
        Args:
            db_file: Path to the SQLite database file. Use ':memory:' for
                     an in-memory database (useful for testing).
        """
        self.db_file = db_file
        self.conn = None
        self.connect()
        self.create_tables()
        # Keep connection open for :memory: databases (data is lost on disconnect)
        if db_file != ":memory:":
            self.disconnect()

    def connect(self) -> None:
        """Open a connection with type parsing enabled."""
        self.conn = sqlite3.connect(
            self.db_file,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        self.conn.execute("PRAGMA foreign_keys = ON;")

    def disconnect(self) -> None:
        """Close the current connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def commit(self) -> None:
        """Commit pending changes. Raises if not connected."""
        if self.conn:
            self.conn.commit()
        else:
            raise RuntimeError("Cannot commit: no database connection.")

    def get_cursor(self) -> sqlite3.Cursor:
        """Return a cursor, connecting automatically if needed."""
        if self.conn is None:
            self.connect()
        return self.conn.cursor()

    # ------------------------------------------------------------------ #
    #  Schema
    # ------------------------------------------------------------------ #

    def create_tables(self) -> list[str]:
        """Create all tables if they don't exist. Returns table names."""
        if not self.conn:
            raise RuntimeError("Database connection not established.")

        cur = self.conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON;")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS categories(
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT UNIQUE NOT NULL,
                parent_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                description TEXT
            )""")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions(
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        DATE NOT NULL,
                description TEXT NOT NULL,
                amount      REAL NOT NULL,
                account     TEXT NOT NULL,
                source_file TEXT,
                imported_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                comment     TEXT,
                UNIQUE(date, description, amount, account)
            )""")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS match_rules(
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
                pattern     TEXT NOT NULL,
                match_type  TEXT NOT NULL DEFAULT 'regex'
                            CHECK(match_type IN ('regex','exact','contains')),
                priority    INTEGER DEFAULT 0,
                enabled     INTEGER DEFAULT 1,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS id_matches(
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
                category_id    INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(transaction_id)
            )""")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS metadata(
                key   TEXT PRIMARY KEY,
                value TEXT
            )""")

        self.conn.commit()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return [row[0] for row in cur.fetchall()]

    # ------------------------------------------------------------------ #
    #  Metadata helpers
    # ------------------------------------------------------------------ #

    def set_metadata(self, key: str, value: str) -> None:
        cur = self.get_cursor()
        cur.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.commit()

    def get_metadata(self, key: str, default: str = None) -> str | None:
        cur = self.get_cursor()
        cur.execute("SELECT value FROM metadata WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else default

    def get_all_metadata(self) -> dict:
        cur = self.get_cursor()
        cur.execute("SELECT key, value FROM metadata")
        return dict(cur.fetchall())
