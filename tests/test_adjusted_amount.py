"""Tests for adjusted_amount column (step 1.5)."""

import datetime
import pytest
from financial_categorizer.db_handler import DatabaseHandler


@pytest.fixture
def db():
    handler = DatabaseHandler(":memory:")
    yield handler
    handler.disconnect()


def _add_txn(db, date, description, amount, account_id, **kwargs):
    """Helper to insert a transaction and return its id."""
    cur = db.get_cursor()
    status = kwargs.get("status", "settled")
    adjusted = kwargs.get("adjusted_amount")
    if adjusted is None:
        # Match what the schema does — NULL until explicitly set or recalced
        adjusted = "NULL"
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (date, description, amount, account_id, status),
        )
    else:
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id, status, adjusted_amount) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (date, description, amount, account_id, status, adjusted),
        )
    db.commit()
    return cur.lastrowid


def _get_adjusted(db, txn_id):
    cur = db.get_cursor()
    cur.execute("SELECT adjusted_amount FROM transactions WHERE id = ?", (txn_id,))
    return cur.fetchone()[0]


class TestRecalculateAdjustedAmounts:

    def test_recalc_sets_amount_times_ratio(self, db):
        """adjusted_amount = amount * ownership_ratio after recalc."""
        acct = db.add_account("Shared", type="shared", ownership_ratio=0.5)
        tid = _add_txn(db, datetime.date(2026, 1, 1), "Coffee", -40.0, acct)
        assert _get_adjusted(db, tid) is None

        db.recalculate_adjusted_amounts()
        assert _get_adjusted(db, tid) == pytest.approx(-20.0)

    def test_recalc_full_ownership(self, db):
        acct = db.add_account("Personal", ownership_ratio=1.0)
        tid = _add_txn(db, datetime.date(2026, 1, 1), "Salary", 30000.0, acct)
        db.recalculate_adjusted_amounts()
        assert _get_adjusted(db, tid) == pytest.approx(30000.0)

    def test_recalc_specific_account_only(self, db):
        acct1 = db.add_account("Shared", ownership_ratio=0.5)
        acct2 = db.add_account("Personal", ownership_ratio=1.0)
        t1 = _add_txn(db, datetime.date(2026, 1, 1), "Rent", -8000.0, acct1)
        t2 = _add_txn(db, datetime.date(2026, 1, 1), "Coffee", -40.0, acct2)

        db.recalculate_adjusted_amounts(acct1)
        assert _get_adjusted(db, t1) == pytest.approx(-4000.0)
        assert _get_adjusted(db, t2) is None

    def test_recalc_returns_row_count(self, db):
        acct = db.add_account("Personal")
        _add_txn(db, datetime.date(2026, 1, 1), "A", -10.0, acct)
        _add_txn(db, datetime.date(2026, 1, 2), "B", -20.0, acct)
        _add_txn(db, datetime.date(2026, 1, 3), "C", -30.0, acct)
        count = db.recalculate_adjusted_amounts()
        assert count == 3

    def test_recalc_overwrites_existing(self, db):
        acct = db.add_account("Shared", ownership_ratio=0.5)
        tid = _add_txn(db, datetime.date(2026, 1, 1), "Rent", -8000.0, acct, adjusted_amount=-999.0)
        assert _get_adjusted(db, tid) == -999.0

        db.recalculate_adjusted_amounts()
        assert _get_adjusted(db, tid) == pytest.approx(-4000.0)


class TestUpdateAccountTriggersRecalc:

    def test_ratio_change_recalcs_transactions(self, db):
        acct = db.add_account("Shared", ownership_ratio=0.5)
        t1 = _add_txn(db, datetime.date(2026, 1, 1), "Rent", -8000.0, acct, adjusted_amount=-4000.0)
        t2 = _add_txn(db, datetime.date(2026, 1, 2), "Groceries", -500.0, acct, adjusted_amount=-250.0)

        db.update_account(acct, ownership_ratio=0.7)

        assert _get_adjusted(db, t1) == pytest.approx(-5600.0)
        assert _get_adjusted(db, t2) == pytest.approx(-350.0)

    def test_name_change_does_not_recalc(self, db):
        acct = db.add_account("Old Name", ownership_ratio=0.5)
        tid = _add_txn(db, datetime.date(2026, 1, 1), "Rent", -8000.0, acct, adjusted_amount=-4000.0)

        db.update_account(acct, name="New Name")

        assert _get_adjusted(db, tid) == -4000.0  # unchanged

    def test_same_ratio_no_recalc(self, db):
        acct = db.add_account("Shared", ownership_ratio=0.5)
        tid = _add_txn(db, datetime.date(2026, 1, 1), "Rent", -8000.0, acct, adjusted_amount=-4000.0)

        db.update_account(acct, ownership_ratio=0.5)

        assert _get_adjusted(db, tid) == -4000.0  # no change

    def test_ratio_change_only_affects_that_account(self, db):
        a1 = db.add_account("Shared", ownership_ratio=0.5)
        a2 = db.add_account("Personal", ownership_ratio=1.0)
        t1 = _add_txn(db, datetime.date(2026, 1, 1), "Rent", -8000.0, a1, adjusted_amount=-4000.0)
        t2 = _add_txn(db, datetime.date(2026, 1, 1), "Coffee", -40.0, a2, adjusted_amount=-40.0)

        db.update_account(a1, ownership_ratio=0.6)

        assert _get_adjusted(db, t1) == pytest.approx(-4800.0)
        assert _get_adjusted(db, t2) == -40.0  # untouched
