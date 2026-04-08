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
            CREATE TABLE IF NOT EXISTS transaction_links(
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                from_transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
                to_transaction_id   INTEGER REFERENCES transactions(id) ON DELETE CASCADE,
                link_type           TEXT NOT NULL CHECK(link_type IN ('internal_transfer','external_transfer','reimbursement')),
                ratio               REAL NOT NULL DEFAULT 1.0
                                    CHECK(ratio > 0 AND ratio <= 1.0),
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                comment             TEXT
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
            self.recalculate_adjusted_amounts()

        return changed

    def recalculate_adjusted_amounts(self) -> int:
        """Recalculate adjusted_amount for all transactions.

        Step 1: base = amount * account.ownership_ratio
        Step 2: apply link adjustments:
          - internal_transfer: from side gets +from.amount*ratio,
                               to side gets -to.amount*ratio
          - external_transfer: adjusted_amount = 0
          - reimbursement: from side gets -from.amount*ratio
        """
        cur = self.get_cursor()

        # Step 1: base = amount * ownership_ratio
        cur.execute(
            "UPDATE transactions SET adjusted_amount = amount * "
            "(SELECT ownership_ratio FROM accounts WHERE accounts.id = transactions.account_id)"
        )
        total_updated = cur.rowcount

        # Step 2: apply link adjustments in Python (handles multiple links per txn)
        cur.execute(
            "SELECT from_transaction_id, to_transaction_id, link_type, ratio "
            "FROM transaction_links"
        )
        links = cur.fetchall()

        # Build per-transaction adjustments
        adjustments: dict[int, float] = {}  # txn_id -> delta to apply

        for from_id, to_id, link_type, ratio in links:
            if link_type == "external_transfer":
                adjustments[from_id] = "ZERO"  # marker to set to 0
            elif link_type == "internal_transfer":
                # Neutralize both sides toward 0 based on their adjusted_amount
                cur.execute("SELECT adjusted_amount FROM transactions WHERE id = ?", (from_id,))
                from_adj = cur.fetchone()[0]
                # from side: subtract its own adjusted_amount * ratio
                adjustments[from_id] = adjustments.get(from_id, 0) - from_adj * ratio
                if to_id is not None:
                    cur.execute("SELECT adjusted_amount FROM transactions WHERE id = ?", (to_id,))
                    to_adj = cur.fetchone()[0]
                    adjustments[to_id] = adjustments.get(to_id, 0) - to_adj * ratio
            elif link_type == "reimbursement":
                # from side: subtract its adjusted_amount * ratio
                cur.execute("SELECT adjusted_amount FROM transactions WHERE id = ?", (from_id,))
                from_adj = cur.fetchone()[0]
                adjustments[from_id] = adjustments.get(from_id, 0) - from_adj * ratio

        # Apply adjustments
        for txn_id, delta in adjustments.items():
            if delta == "ZERO":
                cur.execute(
                    "UPDATE transactions SET adjusted_amount = 0 WHERE id = ?", (txn_id,)
                )
            else:
                cur.execute(
                    "UPDATE transactions SET adjusted_amount = adjusted_amount + ? WHERE id = ?",
                    (delta, txn_id),
                )

        self.commit()
        return total_updated

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


# ------------------------------------------------------------------ #
#  Transfer Manager
# ------------------------------------------------------------------ #

VALID_LINK_TYPES = ("internal_transfer", "external_transfer", "reimbursement")


class TransferManager:
    """Manages transaction links (transfers, reimbursements).

    Links connect transactions to adjust their adjusted_amount:
    - internal_transfer: between own accounts (neutralize both sides)
    - external_transfer: outgoing to non-tracked account (set to 0)
    - reimbursement: partial/full refund of an expense
    """

    def __init__(self, db_handler: DatabaseHandler):
        self.db = db_handler

    def link_transactions(
        self,
        from_transaction_id: int,
        to_transaction_id: int | None,
        link_type: str,
        ratio: float = 1.0,
        comment: str | None = None,
    ) -> int:
        """Create a link between two transactions. Returns the link id."""
        if link_type not in VALID_LINK_TYPES:
            raise ValueError(f"Invalid link_type. Must be one of {VALID_LINK_TYPES}")
        if link_type == "internal_transfer" and to_transaction_id is None:
            raise ValueError("to_transaction_id is required for internal_transfer")
        if link_type == "external_transfer" and to_transaction_id is not None:
            raise ValueError("external_transfer does not take a to_transaction_id")

        cur = self.db.get_cursor()
        # Verify transactions exist
        cur.execute("SELECT id FROM transactions WHERE id = ?", (from_transaction_id,))
        if not cur.fetchone():
            raise ValueError(f"from_transaction_id {from_transaction_id} not found")
        if to_transaction_id is not None:
            cur.execute("SELECT id FROM transactions WHERE id = ?", (to_transaction_id,))
            if not cur.fetchone():
                raise ValueError(f"to_transaction_id {to_transaction_id} not found")

        cur.execute(
            "INSERT INTO transaction_links (from_transaction_id, to_transaction_id, link_type, ratio, comment) "
            "VALUES (?, ?, ?, ?, ?)",
            (from_transaction_id, to_transaction_id, link_type, ratio, comment),
        )
        self.db.commit()
        link_id = cur.lastrowid

        self.db.recalculate_adjusted_amounts()
        return link_id

    def unlink(self, link_id: int) -> bool:
        """Remove a link. Returns True if it existed."""
        cur = self.db.get_cursor()
        cur.execute("DELETE FROM transaction_links WHERE id = ?", (link_id,))
        self.db.commit()
        removed = cur.rowcount > 0
        if removed:
            self.db.recalculate_adjusted_amounts()
        return removed

    def get_links(self, transaction_id: int) -> list[dict]:
        """Get all links involving a transaction."""
        cur = self.db.get_cursor()
        cur.execute(
            "SELECT id, from_transaction_id, to_transaction_id, link_type, ratio, created_at, comment "
            "FROM transaction_links "
            "WHERE from_transaction_id = ? OR to_transaction_id = ?",
            (transaction_id, transaction_id),
        )
        return [
            {
                "id": row[0],
                "from_transaction_id": row[1],
                "to_transaction_id": row[2],
                "link_type": row[3],
                "ratio": row[4],
                "created_at": row[5],
                "comment": row[6],
            }
            for row in cur.fetchall()
        ]

    def list_links(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        link_type: str | None = None,
    ) -> list[dict]:
        """List links, optionally filtered by date range and type."""
        cur = self.db.get_cursor()
        conditions = []
        params = []

        if link_type is not None:
            conditions.append("tl.link_type = ?")
            params.append(link_type)
        if date_from is not None:
            conditions.append("t.date >= ?")
            params.append(date_from)
        if date_to is not None:
            conditions.append("t.date <= ?")
            params.append(date_to)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        cur.execute(
            f"SELECT tl.id, tl.from_transaction_id, tl.to_transaction_id, "
            f"tl.link_type, tl.ratio, tl.created_at, tl.comment "
            f"FROM transaction_links tl "
            f"JOIN transactions t ON t.id = tl.from_transaction_id "
            f"{where} ORDER BY tl.created_at",
            params,
        )
        return [
            {
                "id": row[0],
                "from_transaction_id": row[1],
                "to_transaction_id": row[2],
                "link_type": row[3],
                "ratio": row[4],
                "created_at": row[5],
                "comment": row[6],
            }
            for row in cur.fetchall()
        ]

    # Convenience methods

    def mark_transfer(
        self,
        from_transaction_id: int,
        to_transaction_id: int,
        ratio: float = 1.0,
        comment: str | None = None,
    ) -> int:
        """Link two transactions as an internal transfer."""
        return self.link_transactions(
            from_transaction_id, to_transaction_id, "internal_transfer", ratio, comment
        )

    def mark_external(
        self,
        transaction_id: int,
        ratio: float = 1.0,
        comment: str | None = None,
    ) -> int:
        """Mark a transaction as an external transfer."""
        return self.link_transactions(
            transaction_id, None, "external_transfer", ratio, comment
        )

    def mark_reimbursement(
        self,
        reimbursement_transaction_id: int,
        original_transaction_id: int,
        ratio: float = 1.0,
        comment: str | None = None,
    ) -> int:
        """Link a reimbursement to the original expense."""
        return self.link_transactions(
            reimbursement_transaction_id, original_transaction_id, "reimbursement", ratio, comment
        )
