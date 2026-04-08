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
            CREATE TABLE IF NOT EXISTS accounts(
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT UNIQUE NOT NULL,
                type            TEXT NOT NULL DEFAULT 'personal'
                                CHECK(type IN ('personal','shared','savings','external')),
                ownership_ratio REAL NOT NULL DEFAULT 1.0
                                CHECK(ownership_ratio > 0 AND ownership_ratio <= 1.0),
                currency        TEXT NOT NULL DEFAULT 'SEK',
                description     TEXT
            )""")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions(
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        DATE NOT NULL,
                description TEXT NOT NULL,
                amount      REAL NOT NULL,
                account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
                source_file TEXT,
                imported_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                comment     TEXT,
                status      TEXT NOT NULL DEFAULT 'settled'
                            CHECK(status IN ('pending','settled')),
                matched_rule_id INTEGER REFERENCES match_rules(id) ON DELETE SET NULL,
                adjusted_amount REAL,
                UNIQUE(date, description, amount, account_id, status)
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

    # ------------------------------------------------------------------ #
    #  Account helpers
    # ------------------------------------------------------------------ #

    def add_account(
        self,
        name: str,
        type: str = "personal",
        ownership_ratio: float = 1.0,
        currency: str = "SEK",
        description: str = None,
    ) -> int:
        """Add a new account. Returns the account id."""
        cur = self.get_cursor()
        cur.execute(
            "INSERT INTO accounts (name, type, ownership_ratio, currency, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, type, ownership_ratio, currency, description),
        )
        self.commit()
        return cur.lastrowid

    def get_account(self, account_id: int) -> dict | None:
        """Look up an account by ID."""
        cur = self.get_cursor()
        cur.execute(
            "SELECT id, name, type, ownership_ratio, currency, description "
            "FROM accounts WHERE id = ?",
            (account_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "type": row[2],
            "ownership_ratio": row[3],
            "currency": row[4],
            "description": row[5],
        }

    def get_account_by_name(self, name: str) -> dict | None:
        """Look up an account by name."""
        cur = self.get_cursor()
        cur.execute(
            "SELECT id, name, type, ownership_ratio, currency, description "
            "FROM accounts WHERE name = ?",
            (name,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "type": row[2],
            "ownership_ratio": row[3],
            "currency": row[4],
            "description": row[5],
        }

    def list_accounts(self) -> list[dict]:
        """Return all accounts."""
        cur = self.get_cursor()
        cur.execute(
            "SELECT id, name, type, ownership_ratio, currency, description "
            "FROM accounts ORDER BY name"
        )
        return [
            {
                "id": row[0],
                "name": row[1],
                "type": row[2],
                "ownership_ratio": row[3],
                "currency": row[4],
                "description": row[5],
            }
            for row in cur.fetchall()
        ]

    def update_account(
        self,
        account_id: int,
        name: str | None = None,
        type: str | None = None,
        ownership_ratio: float | None = None,
        currency: str | None = None,
        description: str | None = ...,
    ) -> bool:
        """Update an account's fields. Returns True if any change was made."""
        updates = []
        params = []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if type is not None:
            updates.append("type = ?")
            params.append(type)
        if ownership_ratio is not None:
            old = self.get_account(account_id)
            updates.append("ownership_ratio = ?")
            params.append(ownership_ratio)
        else:
            old = None
        if currency is not None:
            updates.append("currency = ?")
            params.append(currency)
        if description is not ...:
            updates.append("description = ?")
            params.append(description)

        if not updates:
            return False

        params.append(account_id)
        cur = self.get_cursor()
        cur.execute(
            f"UPDATE accounts SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.commit()
        changed = cur.rowcount > 0

        # If ownership_ratio changed, recalc adjusted_amount for this account's transactions
        if changed and old and old["ownership_ratio"] != ownership_ratio:
            self.recalculate_adjusted_amounts(account_id)

        return changed

    def recalculate_adjusted_amounts(self, account_id: int | None = None) -> int:
        """Recalculate adjusted_amount for transactions.

        If account_id is given, only recalc transactions on that account.
        Otherwise recalc all transactions.

        adjusted_amount = amount * account.ownership_ratio
        (Links will be layered on top in step 3.)

        Returns the number of rows updated.
        """
        cur = self.get_cursor()
        if account_id is not None:
            cur.execute(
                "UPDATE transactions SET adjusted_amount = amount * "
                "(SELECT ownership_ratio FROM accounts WHERE accounts.id = transactions.account_id) "
                "WHERE account_id = ?",
                (account_id,),
            )
        else:
            cur.execute(
                "UPDATE transactions SET adjusted_amount = amount * "
                "(SELECT ownership_ratio FROM accounts WHERE accounts.id = transactions.account_id)"
            )
        self.commit()
        return cur.rowcount

    def delete_account(self, account_id: int) -> bool:
        """Delete an account. Fails if transactions reference it (ON DELETE RESTRICT).

        Returns True if the account was deleted.
        """
        cur = self.get_cursor()
        cur.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        self.commit()
        return cur.rowcount > 0

    def ensure_account(self, name: str, **kwargs) -> int:
        """Get account ID by name, creating it if it doesn't exist.

        Extra kwargs passed to add_account on creation.
        Returns the account ID.
        """
        existing = self.get_account_by_name(name)
        if existing:
            return existing["id"]
        return self.add_account(name, **kwargs)
