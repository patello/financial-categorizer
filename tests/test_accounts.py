"""Tests for financial_categorizer.db_handler account CRUD"""

import pytest
from financial_categorizer.db_handler import DatabaseHandler


@pytest.fixture
def db():
    handler = DatabaseHandler(":memory:")
    yield handler
    handler.disconnect()


class TestAccountCRUD:
    def test_add_account_defaults(self, db):
        aid = db.add_account("checking")
        acct = db.get_account(aid)
        assert acct["name"] == "checking"
        assert acct["type"] == "personal"
        assert acct["ownership_ratio"] == 1.0
        assert acct["currency"] == "SEK"
        assert acct["description"] is None

    def test_add_account_with_options(self, db):
        aid = db.add_account(
            "shared", type="shared", ownership_ratio=0.5,
            currency="SEK", description="Joint account"
        )
        acct = db.get_account(aid)
        assert acct["type"] == "shared"
        assert acct["ownership_ratio"] == 0.5
        assert acct["description"] == "Joint account"

    def test_add_account_invalid_type(self, db):
        with pytest.raises(Exception):
            db.add_account("bad", type="invalid")

    def test_add_account_ratio_bounds(self, db):
        with pytest.raises(Exception):
            db.add_account("bad", ownership_ratio=0.0)
        with pytest.raises(Exception):
            db.add_account("bad", ownership_ratio=1.5)

    def test_add_account_duplicate_name(self, db):
        db.add_account("checking")
        with pytest.raises(Exception):
            db.add_account("checking")

    def test_get_account_not_found(self, db):
        assert db.get_account(999) is None

    def test_get_account_by_name(self, db):
        aid = db.add_account("savings")
        acct = db.get_account_by_name("savings")
        assert acct["id"] == aid

    def test_get_account_by_name_not_found(self, db):
        assert db.get_account_by_name("nonexistent") is None

    def test_list_accounts(self, db):
        db.add_account("checking")
        db.add_account("savings")
        accts = db.list_accounts()
        names = {a["name"] for a in accts}
        assert names == {"checking", "savings"}

    def test_list_accounts_ordered_by_name(self, db):
        db.add_account("zulu")
        db.add_account("alpha")
        accts = db.list_accounts()
        assert accts[0]["name"] == "alpha"
        assert accts[1]["name"] == "zulu"

    def test_update_account_name(self, db):
        aid = db.add_account("old_name")
        db.update_account(aid, name="new_name")
        acct = db.get_account(aid)
        assert acct["name"] == "new_name"

    def test_update_account_type(self, db):
        aid = db.add_account("checking")
        db.update_account(aid, type="savings")
        assert db.get_account(aid)["type"] == "savings"

    def test_update_account_ownership(self, db):
        aid = db.add_account("shared")
        db.update_account(aid, ownership_ratio=0.5)
        assert db.get_account(aid)["ownership_ratio"] == 0.5

    def test_update_account_nothing(self, db):
        aid = db.add_account("checking")
        assert db.update_account(aid) is False

    def test_update_account_not_found(self, db):
        assert db.update_account(999, name="x") is False

    def test_delete_account(self, db):
        aid = db.add_account("temp")
        assert db.delete_account(aid) is True
        assert db.get_account(aid) is None

    def test_delete_account_not_found(self, db):
        assert db.delete_account(999) is False

    def test_delete_account_with_transactions_restricted(self, db):
        """Can't delete an account that has transactions."""
        from datetime import date
        aid = db.add_account("checking")
        cur = db.get_cursor()
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id) "
            "VALUES (?, ?, ?, ?)",
            (date(2024, 1, 1), "test", -100.0, aid),
        )
        db.commit()
        with pytest.raises(Exception):  # FK RESTRICT
            db.delete_account(aid)

    def test_ensure_account_creates(self, db):
        aid = db.ensure_account("new_account")
        assert aid is not None
        assert db.get_account(aid)["name"] == "new_account"

    def test_ensure_account_returns_existing(self, db):
        aid1 = db.add_account("existing")
        aid2 = db.ensure_account("existing")
        assert aid1 == aid2

    def test_ensure_account_with_kwargs(self, db):
        aid = db.ensure_account("shared_acct", type="shared", ownership_ratio=0.5)
        acct = db.get_account(aid)
        assert acct["type"] == "shared"
        assert acct["ownership_ratio"] == 0.5
