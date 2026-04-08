"""Tests for financial_categorizer.categorizer"""

import pytest
from datetime import date

from financial_categorizer.db_handler import DatabaseHandler
from financial_categorizer.categorizer import Categorizer


@pytest.fixture
def db():
    # :memory: handler stays connected from __init__
    handler = DatabaseHandler(":memory:")
    yield handler
    handler.disconnect()


@pytest.fixture
def cat(db):
    return Categorizer(db)


def _add_txn(db, desc, amount=-100.0, dt=None, account="checking"):
    """Helper to insert a transaction and return its id."""
    if dt is None:
        dt = date(2024, 1, 15)
    cur = db.get_cursor()
    cur.execute(
        "INSERT INTO transactions (date, description, amount, account) VALUES (?, ?, ?, ?)",
        (dt, desc, amount, account),
    )
    db.commit()
    return cur.lastrowid


def _add_cat(cat, name, parent_id=None):
    return cat.add_category(name, parent_id=parent_id)


class TestRuleMatching:
    def test_regex_match(self, db, cat):
        food_id = _add_cat(cat, "Food")
        cat.add_rule(food_id, r"ica|maxica|coop", match_type="regex")

        txn_id = _add_txn(db, "Kortköp ICA Supermarket")
        result = cat.categorize(txn_id)
        assert result == food_id

    def test_exact_match(self, db, cat):
        salary_id = _add_cat(cat, "Salary")
        cat.add_rule(salary_id, "Salary", match_type="exact")

        txn_id = _add_txn(db, "Salary")
        assert cat.categorize(txn_id) == salary_id

        # Exact match should NOT match partial
        txn_id2 = _add_txn(db, "Monthly Salary", dt=date(2024, 1, 16))
        assert cat.categorize(txn_id2) is None

    def test_contains_match(self, db, cat):
        transport_id = _add_cat(cat, "Transport")
        cat.add_rule(transport_id, "SL-kort", match_type="contains")

        txn_id = _add_txn(db, "Autoload SL-kort 12345")
        assert cat.categorize(txn_id) == transport_id

    def test_no_match(self, db, cat):
        txn_id = _add_txn(db, "Something completely unknown")
        assert cat.categorize(txn_id) is None

    def test_priority_ordering(self, db, cat):
        """Higher priority rules match first."""
        broad_id = _add_cat(cat, "General Shopping")
        specific_id = _add_cat(cat, "Groceries")

        cat.add_rule(broad_id, r"köp", match_type="regex", priority=1)
        cat.add_rule(specific_id, r"ICA", match_type="regex", priority=10)

        txn_id = _add_txn(db, "Kortköp ICA Maximat")
        result = cat.categorize(txn_id)
        assert result == specific_id  # higher priority wins


class TestManualMatch:
    def test_manual_override(self, db, cat):
        food_id = _add_cat(cat, "Food")
        transport_id = _add_cat(cat, "Transport")

        cat.add_rule(transport_id, "ICA", match_type="regex")

        txn_id = _add_txn(db, "ICA Store")
        # Manual override should take precedence over rules
        cat.add_manual_match(txn_id, food_id)

        result = cat.categorize(txn_id)
        assert result == food_id

    def test_manual_match_updates_transaction(self, db, cat):
        food_id = _add_cat(cat, "Food")
        txn_id = _add_txn(db, "Test transaction")
        cat.add_manual_match(txn_id, food_id)

        cur = db.get_cursor()
        cur.execute("SELECT category_id FROM transactions WHERE id = ?", (txn_id,))
        assert cur.fetchone()[0] == food_id


class TestCategoryHierarchy:
    def test_category_path(self, db, cat):
        root_id = _add_cat(cat, "Expenses")
        child_id = _add_cat(cat, "Food", parent_id=root_id)
        leaf_id = _add_cat(cat, "Groceries", parent_id=child_id)

        path = cat.get_category_path(leaf_id)
        names = [p["name"] for p in path]
        assert names == ["Groceries", "Food", "Expenses"]

    def test_get_all_tags(self, db, cat):
        root_id = _add_cat(cat, "Expenses")
        child_id = _add_cat(cat, "Food", parent_id=root_id)

        cat.add_rule(child_id, "ICA", match_type="regex")
        txn_id = _add_txn(db, "ICA Store")

        cat.categorize(txn_id)
        tags = cat.get_all_tags(txn_id)
        assert "Food" in tags
        assert "Expenses" in tags


class TestCategorizeAll:
    def test_categorize_all(self, db, cat):
        food_id = _add_cat(cat, "Food")
        cat.add_rule(food_id, "ICA", match_type="regex")

        _add_txn(db, "ICA Store 1")
        _add_txn(db, "ICA Store 2", dt=date(2024, 1, 16))
        _add_txn(db, "Unknown thing", dt=date(2024, 1, 17))

        result = cat.categorize_all()
        assert result["matched"] == 2
        assert result["unmatched"] == 1


class TestCategorizeNew:
    def test_only_uncategorized(self, db, cat):
        food_id = _add_cat(cat, "Food")
        cat.add_rule(food_id, "ICA", match_type="regex")

        txn1 = _add_txn(db, "ICA Store")
        cat.categorize(txn1)  # already categorized
        txn2 = _add_txn(db, "ICA Store 2", dt=date(2024, 1, 16))
        _add_txn(db, "Unknown thing", dt=date(2024, 1, 17))

        result = cat.categorize_new()
        assert result["matched"] == 1  # only txn2
        assert result["unmatched"] == 1


class TestRecategorize:
    def test_categorize_all_reruns_rules(self, db, cat):
        """categorize_all() picks up new rules for previously matched transactions."""
        general_id = _add_cat(cat, "General")
        food_id = _add_cat(cat, "Food")

        cat.add_rule(general_id, "ICA", match_type="regex")
        txn_id = _add_txn(db, "ICA Store")
        cat.categorize(txn_id)

        # Verify initial category
        cur = db.get_cursor()
        cur.execute("SELECT category_id FROM transactions WHERE id = ?", (txn_id,))
        assert cur.fetchone()[0] == general_id

        # Add a higher-priority rule
        cat.add_rule(food_id, "ICA", match_type="regex", priority=10)
        result = cat.categorize_all()

        assert result["matched"] == 1
        cur.execute("SELECT category_id FROM transactions WHERE id = ?", (txn_id,))
        assert cur.fetchone()[0] == food_id

    def test_categorize_all_preserves_manual_overrides(self, db, cat):
        """categorize_all() never overwrites manual matches."""
        food_id = _add_cat(cat, "Food")
        transport_id = _add_cat(cat, "Transport")

        cat.add_rule(transport_id, "ICA", match_type="regex")
        txn_id = _add_txn(db, "ICA Store")
        cat.add_manual_match(txn_id, food_id)

        cat.categorize_all()

        cur = db.get_cursor()
        cur.execute("SELECT category_id FROM transactions WHERE id = ?", (txn_id,))
        assert cur.fetchone()[0] == food_id  # manual override preserved

    def test_categorize_all_tracks_rule_id(self, db, cat):
        """Rule-based matches get matched_rule_id set."""
        food_id = _add_cat(cat, "Food")
        rule_id = cat.add_rule(food_id, "ICA", match_type="regex")
        txn_id = _add_txn(db, "ICA Store")

        cat.categorize(txn_id)

        cur = db.get_cursor()
        cur.execute("SELECT matched_rule_id FROM transactions WHERE id = ?", (txn_id,))
        assert cur.fetchone()[0] == rule_id

    def test_manual_match_clears_rule_id(self, db, cat):
        """Manual override sets matched_rule_id to NULL."""
        food_id = _add_cat(cat, "Food")
        transport_id = _add_cat(cat, "Transport")
        cat.add_rule(transport_id, "ICA", match_type="regex")
        txn_id = _add_txn(db, "ICA Store")

        cat.categorize(txn_id)  # rule-based
        cat.add_manual_match(txn_id, food_id)  # manual override

        cur = db.get_cursor()
        cur.execute("SELECT matched_rule_id, category_id FROM transactions WHERE id = ?", (txn_id,))
        row = cur.fetchone()
        assert row[0] is None  # no rule tracked
        assert row[1] == food_id


class TestPreviewRule:
    def test_preview(self, db, cat):
        _add_txn(db, "ICA Store 1")
        _add_txn(db, "Coop Store")
        _add_txn(db, "Unknown thing", dt=date(2024, 1, 16))

        matches = cat.preview_rule("ICA|Coop", match_type="regex")
        assert len(matches) == 2
        descs = {m["description"] for m in matches}
        assert "ICA Store 1" in descs
        assert "Coop Store" in descs


class TestCategoryManagement:
    def test_add_and_list(self, cat):
        cat.add_category("Root")
        cat.add_category("Child", parent_id=1)
        cats = cat.list_categories()
        assert len(cats) == 2

    def test_get_by_name(self, cat):
        cid = cat.add_category("Food")
        result = cat.get_category_by_name("Food")
        assert result["id"] == cid

    def test_get_children(self, cat):
        root_id = cat.add_category("Expenses")
        c1 = cat.add_category("Food", parent_id=root_id)
        c2 = cat.add_category("Transport", parent_id=root_id)

        children = cat.get_children(root_id)
        child_ids = {c["id"] for c in children}
        assert child_ids == {c1, c2}
