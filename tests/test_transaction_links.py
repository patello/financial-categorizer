"""Tests for transaction links and TransferManager (step 3)."""

import datetime
import pytest
from financial_categorizer.db_handler import DatabaseHandler, TransferManager


@pytest.fixture
def db():
    handler = DatabaseHandler(":memory:")
    yield handler
    handler.disconnect()


@pytest.fixture
def tm(db):
    return TransferManager(db)


def _add_txn(db, date, description, amount, account_id, adjusted_amount=None):
    """Insert a transaction, optionally with adjusted_amount."""
    cur = db.get_cursor()
    if adjusted_amount is not None:
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id, adjusted_amount) "
            "VALUES (?, ?, ?, ?, ?)",
            (date, description, amount, account_id, adjusted_amount),
        )
    else:
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id) "
            "VALUES (?, ?, ?, ?)",
            (date, description, amount, account_id),
        )
    db.commit()
    return cur.lastrowid


def _get_adjusted(db, txn_id):
    cur = db.get_cursor()
    cur.execute("SELECT adjusted_amount FROM transactions WHERE id = ?", (txn_id,))
    return cur.fetchone()[0]


class TestInternalTransfer:

    def test_basic_transfer_neutralizes_both_sides(self, db, tm):
        """Internal transfer: both sides adjusted toward 0."""
        a1 = db.add_account("Checking")
        a2 = db.add_account("Savings")
        # Outgoing -1000 from checking
        t1 = _add_txn(db, datetime.date(2026, 1, 1), "Transfer out", -1000.0, a1, adjusted_amount=-1000.0)
        # Incoming +1000 to savings
        t2 = _add_txn(db, datetime.date(2026, 1, 1), "Transfer in", 1000.0, a2, adjusted_amount=1000.0)

        tm.mark_transfer(t1, t2)

        assert _get_adjusted(db, t1) == pytest.approx(0.0)
        assert _get_adjusted(db, t2) == pytest.approx(0.0)

    def test_transfer_with_shared_account(self, db, tm):
        """Transfer with ownership_ratio still neutralizes fully."""
        a1 = db.add_account("Shared", ownership_ratio=0.5)
        a2 = db.add_account("Personal")
        t1 = _add_txn(db, datetime.date(2026, 1, 1), "Transfer out", -1000.0, a1, adjusted_amount=-500.0)
        t2 = _add_txn(db, datetime.date(2026, 1, 1), "Transfer in", 1000.0, a2, adjusted_amount=1000.0)

        tm.mark_transfer(t1, t2)

        assert _get_adjusted(db, t1) == pytest.approx(0.0)
        assert _get_adjusted(db, t2) == pytest.approx(0.0)

    def test_transfer_with_ratio(self, db, tm):
        """Partial transfer only neutralizes ratio portion."""
        a1 = db.add_account("Checking")
        a2 = db.add_account("Savings")
        t1 = _add_txn(db, datetime.date(2026, 1, 1), "Transfer out", -1000.0, a1, adjusted_amount=-1000.0)
        t2 = _add_txn(db, datetime.date(2026, 1, 1), "Transfer in", 1000.0, a2, adjusted_amount=1000.0)

        tm.mark_transfer(t1, t2, ratio=0.6)

        # from side: -1000 + 1000*0.6 = -400
        assert _get_adjusted(db, t1) == pytest.approx(-400.0)
        # to side: 1000 - 1000*0.6 = 400
        assert _get_adjusted(db, t2) == pytest.approx(400.0)


class TestExternalTransfer:

    def test_external_sets_to_zero(self, db, tm):
        a1 = db.add_account("Checking")
        t1 = _add_txn(db, datetime.date(2026, 1, 1), "To external", -500.0, a1, adjusted_amount=-500.0)

        tm.mark_external(t1)

        assert _get_adjusted(db, t1) == pytest.approx(0.0)

    def test_external_rejects_to_transaction_id(self, db, tm):
        a1 = db.add_account("Checking")
        t1 = _add_txn(db, datetime.date(2026, 1, 1), "X", -100.0, a1, adjusted_amount=-100.0)
        t2 = _add_txn(db, datetime.date(2026, 1, 1), "Y", -100.0, a1, adjusted_amount=-100.0)

        with pytest.raises(ValueError, match="does not take"):
            tm.link_transactions(t1, t2, "external_transfer")


class TestReimbursement:

    def test_full_reimbursement(self, db, tm):
        a1 = db.add_account("Checking")
        t_expense = _add_txn(db, datetime.date(2026, 1, 1), "Dinner", -500.0, a1, adjusted_amount=-500.0)
        t_reimb = _add_txn(db, datetime.date(2026, 1, 5), "Reimb dinner", 300.0, a1, adjusted_amount=300.0)

        tm.mark_reimbursement(t_reimb, t_expense)

        # Reimb neutralizes to 0, expense gets credited
        assert _get_adjusted(db, t_reimb) == pytest.approx(0.0)
        assert _get_adjusted(db, t_expense) == pytest.approx(-200.0)  # -500 + 300

    def test_partial_reimbursement(self, db, tm):
        a1 = db.add_account("Checking")
        t_expense = _add_txn(db, datetime.date(2026, 1, 1), "Dinner", -200.0, a1, adjusted_amount=-200.0)
        t_reimb = _add_txn(db, datetime.date(2026, 1, 5), "Reimb dinner", 75.0, a1, adjusted_amount=75.0)

        tm.mark_reimbursement(t_reimb, t_expense)

        # Reimb = 0, expense = -200 + 75 = -125
        assert _get_adjusted(db, t_reimb) == pytest.approx(0.0)
        assert _get_adjusted(db, t_expense) == pytest.approx(-125.0)

    def test_reimbursement_without_to(self, db, tm):
        """Reimbursement with to_id=None is allowed."""
        a1 = db.add_account("Checking")
        t_reimb = _add_txn(db, datetime.date(2026, 1, 5), "Reimb", 200.0, a1, adjusted_amount=200.0)

        tm.link_transactions(t_reimb, None, "reimbursement")

        assert _get_adjusted(db, t_reimb) == pytest.approx(0.0)

    def test_reimbursement_ratio_splits_across_expenses(self, db, tm):
        """Ratio splits reimbursement credit across multiple expenses."""
        a1 = db.add_account("Checking")
        t_reimb = _add_txn(db, datetime.date(2026, 1, 5), "Reimb", 200.0, a1, adjusted_amount=200.0)
        t_exp1 = _add_txn(db, datetime.date(2026, 1, 1), "Dinner 1", -200.0, a1, adjusted_amount=-200.0)
        t_exp2 = _add_txn(db, datetime.date(2026, 1, 2), "Dinner 2", -200.0, a1, adjusted_amount=-200.0)

        # Split 50/50 across two expenses
        tm.mark_reimbursement(t_reimb, t_exp1, ratio=0.5)
        tm.mark_reimbursement(t_reimb, t_exp2, ratio=0.5)

        # Reimb: 200 - 200*0.5 - 200*0.5 = 0
        assert _get_adjusted(db, t_reimb) == pytest.approx(0.0)
        # Each expense gets 200*0.5 = 100 credit
        assert _get_adjusted(db, t_exp1) == pytest.approx(-100.0)
        assert _get_adjusted(db, t_exp2) == pytest.approx(-100.0)

    def test_reimbursement_scales_by_target_ownership_ratio(self, db, tm):
        """Reimbursement credits the expense transaction scaled by the target account's ownership ratio."""
        a_shared = db.add_account("Shared", type="tracked", ownership_ratio=0.5)
        a_personal = db.add_account("Personal", type="tracked", ownership_ratio=1.0)
        
        t_expense = _add_txn(db, datetime.date(2026, 1, 1), "Dinner", -1000.0, a_shared)
        t_reimb = _add_txn(db, datetime.date(2026, 1, 5), "Reimb dinner", 400.0, a_personal)
        
        tm.mark_reimbursement(t_reimb, t_expense, ratio=1.0)
        
        # The reimbursement on personal account should be fully neutralized to 0.0
        assert _get_adjusted(db, t_reimb) == pytest.approx(0.0)
        # The expense on shared account should be credited by 400 * 0.5 = 200 SEK,
        # changing its adjusted amount from -500.0 to -300.0 SEK.
        assert _get_adjusted(db, t_expense) == pytest.approx(-300.0)


class TestUnlink:

    def test_unlink_restores_adjusted_amount(self, db, tm):
        a1 = db.add_account("Checking")
        t1 = _add_txn(db, datetime.date(2026, 1, 1), "X", -500.0, a1, adjusted_amount=-500.0)

        link_id = tm.mark_external(t1)
        assert _get_adjusted(db, t1) == 0.0

        tm.unlink(link_id)
        assert _get_adjusted(db, t1) == pytest.approx(-500.0)

    def test_unlink_nonexistent_returns_false(self, db, tm):
        assert tm.unlink(9999) is False


class TestGetLinks:

    def test_get_links_returns_both_sides(self, db, tm):
        a1 = db.add_account("Checking")
        a2 = db.add_account("Savings")
        t1 = _add_txn(db, datetime.date(2026, 1, 1), "Out", -100.0, a1, adjusted_amount=-100.0)
        t2 = _add_txn(db, datetime.date(2026, 1, 1), "In", 100.0, a2, adjusted_amount=100.0)

        tm.mark_transfer(t1, t2)

        links_from = tm.get_links(t1)
        links_to = tm.get_links(t2)
        assert len(links_from) == 1
        assert len(links_to) == 1
        assert links_from[0]["id"] == links_to[0]["id"]


class TestListLinks:

    def test_list_links_filter_by_type(self, db, tm):
        a1 = db.add_account("Checking")
        a2 = db.add_account("Savings")
        t1 = _add_txn(db, datetime.date(2026, 1, 1), "Out", -100.0, a1, adjusted_amount=-100.0)
        t2 = _add_txn(db, datetime.date(2026, 1, 1), "In", 100.0, a2, adjusted_amount=100.0)
        t3 = _add_txn(db, datetime.date(2026, 1, 2), "External", -50.0, a1, adjusted_amount=-50.0)

        tm.mark_transfer(t1, t2)
        tm.mark_external(t3)

        internal = tm.list_links(link_type="internal_transfer")
        external = tm.list_links(link_type="external_transfer")
        assert len(internal) == 1
        assert len(external) == 1

    def test_list_links_filter_by_date(self, db, tm):
        a1 = db.add_account("Checking")
        t1 = _add_txn(db, datetime.date(2026, 1, 1), "X", -100.0, a1, adjusted_amount=-100.0)
        t2 = _add_txn(db, datetime.date(2026, 3, 1), "Y", -200.0, a1, adjusted_amount=-200.0)

        tm.mark_external(t1)
        tm.mark_external(t2)

        jan = tm.list_links(date_from=datetime.date(2026, 1, 1), date_to=datetime.date(2026, 1, 31))
        assert len(jan) == 1


class TestValidation:

    def test_invalid_link_type(self, db, tm):
        a1 = db.add_account("Checking")
        t1 = _add_txn(db, datetime.date(2026, 1, 1), "X", -100.0, a1)
        with pytest.raises(ValueError, match="Invalid link_type"):
            tm.link_transactions(t1, None, "invalid_type")

    def test_internal_transfer_requires_to(self, db, tm):
        a1 = db.add_account("Checking")
        t1 = _add_txn(db, datetime.date(2026, 1, 1), "X", -100.0, a1)
        with pytest.raises(ValueError, match="to_transaction_id is required"):
            tm.link_transactions(t1, None, "internal_transfer")

    def test_nonexistent_transaction(self, db, tm):
        a1 = db.add_account("Checking")
        t1 = _add_txn(db, datetime.date(2026, 1, 1), "X", -100.0, a1)
        with pytest.raises(ValueError, match="not found"):
            tm.link_transactions(t1, 9999, "internal_transfer")
