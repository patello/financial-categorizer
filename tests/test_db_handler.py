"""Tests for financial_categorizer.db_handler"""

import os
import tempfile
import pytest
from datetime import date

from financial_categorizer.db_handler import DatabaseHandler


@pytest.fixture
def db():
    """Create a fresh in-memory database for each test."""
    handler = DatabaseHandler(":memory:")
    # :memory: handler stays connected from __init__
    yield handler
    handler.disconnect()


class TestDatabaseHandler:
    def test_create_tables(self, db):
        """All expected tables are created."""
        cur = db.get_cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        assert "transactions" in tables
        assert "categories" in tables
        assert "match_rules" in tables
        assert "id_matches" in tables
        assert "metadata" in tables
        assert "accounts" in tables

    def test_connect_disconnect(self):
        """Can connect and disconnect from a file-based DB."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            handler = DatabaseHandler(db_path)
            handler.connect()
            assert handler.conn is not None
            handler.disconnect()
            assert handler.conn is None
        finally:
            os.unlink(db_path)

    def test_commit_without_connection_raises(self, db):
        """Committing without a connection raises RuntimeError."""
        db.disconnect()
        with pytest.raises(RuntimeError):
            db.commit()

    def test_get_cursor_auto_connects(self, db):
        """get_cursor reconnects if connection is None."""
        db.disconnect()
        cur = db.get_cursor()
        assert cur is not None
        assert db.conn is not None

    def test_insert_transaction(self, db):
        """Can insert and retrieve a transaction."""
        aid = db.add_account("checking")
        cur = db.get_cursor()
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id) "
            "VALUES (?, ?, ?, ?)",
            (date(2024, 1, 15), "Test purchase", -100.0, aid),
        )
        db.commit()

        cur.execute("SELECT description, amount FROM transactions")
        row = cur.fetchone()
        assert row[0] == "Test purchase"
        assert row[1] == -100.0

    def test_unique_constraint(self, db):
        """Duplicate transactions are rejected."""
        aid = db.add_account("checking")
        cur = db.get_cursor()
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id) "
            "VALUES (?, ?, ?, ?)",
            (date(2024, 1, 15), "Dupe", -50.0, aid),
        )
        db.commit()

        with pytest.raises(Exception):
            cur.execute(
                "INSERT INTO transactions (date, description, amount, account_id) "
                "VALUES (?, ?, ?, ?)",
                (date(2024, 1, 15), "Dupe", -50.0, aid),
            )

    def test_category_hierarchy(self, db):
        """Categories support parent-child relationships."""
        cur = db.get_cursor()
        cur.execute(
            "INSERT INTO categories (name) VALUES (?)", ("Root",)
        )
        root_id = cur.lastrowid
        cur.execute(
            "INSERT INTO categories (name, parent_id) VALUES (?, ?)",
            ("Child", root_id),
        )
        child_id = cur.lastrowid
        db.commit()

        cur.execute("SELECT parent_id FROM categories WHERE id = ?", (child_id,))
        assert cur.fetchone()[0] == root_id

    def test_metadata(self, db):
        """Metadata set/get works with upsert semantics."""
        db.set_metadata("test_key", "test_value")
        assert db.get_metadata("test_key") == "test_value"

        # Upsert
        db.set_metadata("test_key", "new_value")
        assert db.get_metadata("test_key") == "new_value"

        # Default
        assert db.get_metadata("missing", "default") == "default"

    def test_get_all_metadata(self, db):
        """get_all_metadata returns all key-value pairs."""
        db.set_metadata("k1", "v1")
        db.set_metadata("k2", "v2")
        meta = db.get_all_metadata()
        assert meta == {"k1": "v1", "k2": "v2"}

    def test_match_rules_table(self, db):
        """Can insert match rules with different types."""
        cur = db.get_cursor()
        cur.execute("INSERT INTO categories (name) VALUES (?)", ("Food",))
        cat_id = cur.lastrowid

        for match_type in ("regex", "exact", "contains"):
            cur.execute(
                "INSERT INTO match_rules (category_id, pattern, match_type) "
                "VALUES (?, ?, ?)",
                (cat_id, "test", match_type),
            )
        db.commit()

        cur.execute("SELECT COUNT(*) FROM match_rules")
        assert cur.fetchone()[0] == 3

    def test_id_matches_unique(self, db):
        """id_matches enforces one override per transaction."""
        aid = db.add_account("checking")
        cur = db.get_cursor()
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id) "
            "VALUES (?, ?, ?, ?)",
            (date(2024, 1, 15), "Txn", -50.0, aid),
        )
        txn_id = cur.lastrowid
        cur.execute("INSERT INTO categories (name) VALUES (?)", ("Cat1",))
        cat1 = cur.lastrowid
        cur.execute("INSERT INTO categories (name) VALUES (?)", ("Cat2",))
        cat2 = cur.lastrowid

        cur.execute(
            "INSERT INTO id_matches (transaction_id, category_id) VALUES (?, ?)",
            (txn_id, cat1),
        )
        db.commit()

        # Second insert should fail due to UNIQUE constraint
        with pytest.raises(Exception):
            cur.execute(
                "INSERT INTO id_matches (transaction_id, category_id) VALUES (?, ?)",
                (txn_id, cat2),
            )

    def test_cleanup_orphaned_records(self, db):
        """Test that orphaned id_matches and transaction_links are deleted by cleanup_orphaned_records."""
        cur = db.get_cursor()
        cur.execute("PRAGMA foreign_keys = OFF;")

        # Insert an orphaned id_matches record (pointing to non-existent txn ID 9999)
        cur.execute("INSERT INTO categories (name) VALUES ('Test Orphan Cat')")
        cat_id = cur.lastrowid
        cur.execute("INSERT INTO id_matches (transaction_id, category_id) VALUES (9999, ?)", (cat_id,))

        # Insert an orphaned transaction_links record (pointing to non-existent txn ID 9999)
        cur.execute(
            "INSERT INTO transaction_links (from_transaction_id, to_transaction_id, link_type, ratio) "
            "VALUES (9999, NULL, 'external_transfer', 1.0)"
        )
        db.commit()
        cur.execute("PRAGMA foreign_keys = ON;")

        # Check they exist
        cur.execute("SELECT COUNT(*) FROM id_matches WHERE transaction_id = 9999")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT COUNT(*) FROM transaction_links WHERE from_transaction_id = 9999")
        assert cur.fetchone()[0] == 1

        # Run dry-run cleanup
        report = db.cleanup_orphaned_records(dry_run=True)
        assert report["orphaned_id_matches"] == 1
        assert report["orphaned_links"] == 1

        # Records should still exist since it was a dry run
        cur.execute("SELECT COUNT(*) FROM id_matches WHERE transaction_id = 9999")
        assert cur.fetchone()[0] == 1

        # Run actual cleanup
        report = db.cleanup_orphaned_records(dry_run=False)
        assert report["orphaned_id_matches"] == 1
        assert report["orphaned_links"] == 1

        # Records should be deleted
        cur.execute("SELECT COUNT(*) FROM id_matches WHERE transaction_id = 9999")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM transaction_links WHERE from_transaction_id = 9999")
        assert cur.fetchone()[0] == 0
