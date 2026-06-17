"""Tests for configurable transfer rules and external transfer via categorize."""

import pytest
from datetime import date
from financial_categorizer.db_handler import DatabaseHandler, TransferManager
from financial_categorizer.categorizer import Categorizer


@pytest.fixture
def db():
    handler = DatabaseHandler(":memory:")
    yield handler
    handler.disconnect()


@pytest.fixture
def setup_accounts(db):
    db.add_account("Checking", type="tracked")
    db.add_account("Savings", type="external")
    return db


class TestTransferRuleCRUD:
    def test_add_and_list_transfer_rules(self, db):
        assert db.get_transfer_rules() == []
        rid = db.add_transfer_rule("överföring")
        assert rid == 1
        rules = db.get_transfer_rules()
        assert len(rules) == 1
        assert rules[0]["pattern"] == "överföring"
        assert rules[0]["match_type"] == "contains"

    def test_add_transfer_rule_with_type(self, db):
        db.add_transfer_rule(r"^transfer\s+\d+$", match_type="regex")
        rules = db.get_transfer_rules()
        assert rules[0]["match_type"] == "regex"

    def test_remove_transfer_rule(self, db):
        rid = db.add_transfer_rule("test pattern")
        assert db.remove_transfer_rule(rid)
        assert db.get_transfer_rules() == []

    def test_remove_nonexistent_rule(self, db):
        assert not db.remove_transfer_rule(999)


class TestAutoLinkWithTransferRules:
    def _add_txn(self, db, account_name, desc, amount, d=None):
        if d is None:
            d = date(2024, 1, 15)
        acct = db.get_account_by_name(account_name)
        cur = db.get_cursor()
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
            (d, desc, amount, acct["id"]),
        )
        db.commit()
        return cur.lastrowid

    def test_no_rules_finds_nothing(self, setup_accounts):
        db = setup_accounts
        self._add_txn(db, "Checking", "Överföring", -1000)
        self._add_txn(db, "Savings", "Insättning", 1000)
        tm = TransferManager(db)
        result = tm.auto_link_transfers()
        assert result["internal"] == []

    def test_rule_matches_internal_transfer(self, setup_accounts):
        db = setup_accounts
        db.add_transfer_rule("överföring")
        self._add_txn(db, "Checking", "Överföring till savings", -1000)
        self._add_txn(db, "Savings", "Överföring från checking", 1000)
        tm = TransferManager(db)
        result = tm.auto_link_transfers()
        assert len(result["internal"]) == 1
        assert result["internal"][0]["amount"] == 1000

    def test_regex_rule(self, setup_accounts):
        db = setup_accounts
        db.add_transfer_rule(r"transfer\s+\d+", match_type="regex")
        self._add_txn(db, "Checking", "Transfer 12345", -500)
        self._add_txn(db, "Savings", "Transfer 12345", 500)
        tm = TransferManager(db)
        result = tm.auto_link_transfers()
        assert len(result["internal"]) == 1

    def test_exact_rule(self, setup_accounts):
        db = setup_accounts
        db.add_transfer_rule("överföring", match_type="exact")
        # "exact" won't match "Överföring till savings" since it's not exact
        self._add_txn(db, "Checking", "överföring", -500)
        self._add_txn(db, "Savings", "överföring", 500)
        tm = TransferManager(db)
        result = tm.auto_link_transfers()
        assert len(result["internal"]) == 1

    def test_no_external_in_result(self, setup_accounts):
        db = setup_accounts
        db.add_transfer_rule("överföring")
        self._add_txn(db, "Checking", "Överföring", -1000)
        self._add_txn(db, "Savings", "Överföring", 1000)
        tm = TransferManager(db)
        result = tm.auto_link_transfers()
        assert "external" not in result

    def test_dry_run(self, setup_accounts):
        db = setup_accounts
        db.add_transfer_rule("överföring")
        self._add_txn(db, "Checking", "Överföring", -1000)
        self._add_txn(db, "Savings", "Överföring", 1000)
        tm = TransferManager(db)
        result = tm.auto_link_transfers(dry_run=True)
        assert len(result["internal"]) == 1
        # Verify no links actually created
        links = tm.list_links()
        assert links == []


class TestExternalTransferViaCategorize:
    def test_categorize_transfer_creates_external_link(self, db):
        db.add_account("Checking")
        cat = Categorizer(db)
        cat.add_category("External Transfers", category_type="transfer")
        cat.add_rule(
            cat.get_category_by_name("External Transfers")["id"],
            "autogiro avanza",
            match_type="contains",
        )
        # Add a transaction
        cur = db.get_cursor()
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
            (date(2024, 1, 15), "Autogiro Avanza Bank", -5000, 1),
        )
        db.commit()
        txn_id = cur.lastrowid

        cat.categorize(txn_id)

        # Verify external_transfer link created
        cur.execute(
            "SELECT link_type FROM transaction_links WHERE from_transaction_id = ?",
            (txn_id,),
        )
        links = cur.fetchall()
        assert len(links) == 1
        assert links[0][0] == "external_transfer"

    def test_categorize_expense_does_not_create_link(self, db):
        db.add_account("Checking")
        cat = Categorizer(db)
        cat.add_category("Food", category_type="expense")
        cat.add_rule(
            cat.get_category_by_name("Food")["id"],
            "restaurant",
            match_type="contains",
        )
        cur = db.get_cursor()
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
            (date(2024, 1, 15), "Restaurant lunch", -150, 1),
        )
        db.commit()
        txn_id = cur.lastrowid

        cat.categorize(txn_id)

        cur.execute("SELECT id FROM transaction_links")
        assert cur.fetchall() == []

    def test_re_categorize_no_duplicate_link(self, db):
        db.add_account("Checking")
        cat = Categorizer(db)
        cat.add_category("External Transfers", category_type="transfer")
        cid = cat.get_category_by_name("External Transfers")["id"]
        cat.add_rule(cid, "autogiro", match_type="contains")

        cur = db.get_cursor()
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
            (date(2024, 1, 15), "Autogiro Avanza", -5000, 1),
        )
        db.commit()
        txn_id = cur.lastrowid

        # Categorize twice
        cat.categorize(txn_id)
        cat.categorize(txn_id)

        cur.execute("SELECT id FROM transaction_links WHERE from_transaction_id = ?", (txn_id,))
        assert len(cur.fetchall()) == 1
