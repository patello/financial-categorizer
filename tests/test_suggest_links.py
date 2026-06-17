"""Tests for TransferManager.suggest_links()."""

import sqlite3
from datetime import date

import pytest

from financial_categorizer.db_handler import DatabaseHandler, TransferManager


@pytest.fixture
def db(tmp_path):
    """Create a fresh DatabaseHandler with two accounts and sample transactions."""
    handler = DatabaseHandler(str(tmp_path / "test.db"))
    # Two accounts
    handler.add_account("Checking", type="tracked", ownership_ratio=1.0)
    handler.add_account("Savings", type="external", ownership_ratio=1.0)

    cur = handler.get_cursor()
    # Matching pair: -5000 out of checking, +5000 into savings, same day
    cur.execute(
        "INSERT INTO transactions (account_id, date, amount, description) VALUES (1, '2026-01-15', -5000.0, 'Transfer to savings')"
    )
    cur.execute(
        "INSERT INTO transactions (account_id, date, amount, description) VALUES (2, '2026-01-15', 5000.0, 'Transfer from checking')"
    )
    # Non-matching: same account, different amounts, already linked, below min_amount
    cur.execute(
        "INSERT INTO transactions (account_id, date, amount, description) VALUES (1, '2026-01-20', -100.0, 'Groceries')"
    )
    cur.execute(
        "INSERT INTO transactions (account_id, date, amount, description) VALUES (2, '2026-01-20', 50.0, 'Small deposit')"
    )
    # Already linked pair
    cur.execute(
        "INSERT INTO transactions (account_id, date, amount, description) VALUES (1, '2026-02-01', -3000.0, 'Already linked out')"
    )
    cur.execute(
        "INSERT INTO transactions (account_id, date, amount, description) VALUES (2, '2026-02-01', 3000.0, 'Already linked in')"
    )
    cur.execute(
        "INSERT INTO transaction_links (from_transaction_id, to_transaction_id, link_type, ratio) VALUES (5, 6, 'internal_transfer', 1.0)"
    )
    handler.commit()
    yield handler
    handler.disconnect()


class TestSuggestLinks:
    def test_finds_matching_transfer(self, db):
        tm = TransferManager(db)
        results = tm.suggest_links()
        assert len(results) == 1
        s = results[0]
        assert s["from_transaction_id"] == 1  # negative amount = from
        assert s["to_transaction_id"] == 2  # positive amount = to
        assert s["from_amount"] == -5000.0
        assert s["to_amount"] == 5000.0
        assert s["days_apart"] == 0

    def test_excludes_already_linked(self, db):
        tm = TransferManager(db)
        results = tm.suggest_links()
        ids = {r["from_transaction_id"] for r in results} | {r["to_transaction_id"] for r in results}
        assert 5 not in ids
        assert 6 not in ids

    def test_min_amount_filter(self, db):
        tm = TransferManager(db)
        # With high min_amount, the 5000 pair should still show
        results = tm.suggest_links(min_amount=1000.0)
        assert len(results) == 1

        # With very high min_amount, nothing matches
        results = tm.suggest_links(min_amount=10000.0)
        assert len(results) == 0

    def test_days_tolerance(self, db):
        cur = db.get_cursor()
        # Add a pair 5 days apart
        cur.execute(
            "INSERT INTO transactions (account_id, date, amount, description) VALUES (1, '2026-03-01', -2000.0, 'Out')"
        )
        cur.execute(
            "INSERT INTO transactions (account_id, date, amount, description) VALUES (2, '2026-03-06', 2000.0, 'In')"
        )
        db.commit()

        tm = TransferManager(db)
        results = tm.suggest_links(days_tolerance=3)
        pair_ids = {(r["from_transaction_id"], r["to_transaction_id"]) for r in results}
        # 5-day pair should NOT be in results
        assert (7, 8) not in pair_ids

        results = tm.suggest_links(days_tolerance=7)
        pair_ids = {(r["from_transaction_id"], r["to_transaction_id"]) for r in results}
        assert (7, 8) in pair_ids

    def test_same_account_excluded(self, db):
        cur = db.get_cursor()
        # Same account, opposite amounts — should NOT match
        cur.execute(
            "INSERT INTO transactions (account_id, date, amount, description) VALUES (1, '2026-04-01', -999.0, 'Out same')"
        )
        cur.execute(
            "INSERT INTO transactions (account_id, date, amount, description) VALUES (1, '2026-04-01', 999.0, 'In same')"
        )
        db.commit()

        tm = TransferManager(db)
        results = tm.suggest_links()
        for r in results:
            assert r["from_transaction_id"] not in (9, 10)
            assert r["to_transaction_id"] not in (9, 10)

    def test_each_transaction_matched_once(self, db):
        """A transaction should appear in at most one suggestion."""
        cur = db.get_cursor()
        # Two deposits to savings matching one withdrawal from checking
        cur.execute(
            "INSERT INTO transactions (account_id, date, amount, description) VALUES (1, '2026-05-01', -1500.0, 'Big out')"
        )
        cur.execute(
            "INSERT INTO transactions (account_id, date, amount, description) VALUES (2, '2026-05-01', 1500.0, 'Match A')"
        )
        db.commit()

        tm = TransferManager(db)
        results = tm.suggest_links()
        all_ids = []
        for r in results:
            all_ids.extend([r["from_transaction_id"], r["to_transaction_id"]])
        assert len(all_ids) == len(set(all_ids))

    def test_empty_db(self, tmp_path):
        handler = DatabaseHandler(str(tmp_path / "empty.db"))
        tm = TransferManager(handler)
        results = tm.suggest_links()
        assert results == []
        handler.disconnect()
