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
    aid = db.ensure_account(account)
    cur = db.get_cursor()
    cur.execute(
        "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
        (dt, desc, amount, aid),
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

    def test_categorize_all_batch_recalculate_count(self, db, cat):
        """Verify that db.recalculate_adjusted_amounts() is called only once in categorize_all."""
        transfer_id = cat.add_category("Transfer", category_type="transfer")
        cat.add_rule(transfer_id, "Transfer", match_type="contains")

        # Add multiple transfer transactions that will trigger auto-linking and recalculation
        _add_txn(db, "Transfer to Savings 1")
        _add_txn(db, "Transfer to Savings 2")
        _add_txn(db, "Transfer to Savings 3")

        # Spy on the recalculate_adjusted_amounts method
        call_count = 0
        original_recalculate = db.recalculate_adjusted_amounts

        def spy_recalculate():
            nonlocal call_count
            call_count += 1
            return original_recalculate()

        db.recalculate_adjusted_amounts = spy_recalculate

        # Run categorize_all
        cat.categorize_all()

        # It should be called exactly once at the end
        assert call_count == 1

    def test_categorize_all_batch_commit_count(self, db, cat):
        """Verify that db.commit() is called in batch rather than per-transaction in categorize_all."""
        food_id = _add_cat(cat, "Food")
        cat.add_rule(food_id, "ICA", match_type="contains")

        # Add multiple transactions
        _add_txn(db, "ICA 1")
        _add_txn(db, "ICA 2")
        _add_txn(db, "ICA 3")

        commit_count = 0
        original_commit = db.commit

        def spy_commit():
            nonlocal commit_count
            commit_count += 1
            return original_commit()

        db.commit = spy_commit

        # Run categorize_all
        cat.categorize_all()

        # Commits should happen:
        # 1. Once in categorize_all to save the updates
        # 2. Once in recalculate_adjusted_amounts to save adjusted amounts
        assert commit_count == 2


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

    def test_categorize_new_batch_commit_count(self, db, cat):
        """Verify that db.commit() is called in batch rather than per-transaction in categorize_new."""
        food_id = _add_cat(cat, "Food")
        cat.add_rule(food_id, "ICA", match_type="contains")

        # Add multiple transactions
        _add_txn(db, "ICA 1")
        _add_txn(db, "ICA 2")
        _add_txn(db, "ICA 3")

        commit_count = 0
        original_commit = db.commit

        def spy_commit():
            nonlocal commit_count
            commit_count += 1
            return original_commit()

        db.commit = spy_commit

        # Run categorize_new
        cat.categorize_new()

        # Commits should happen:
        # 1. Once in categorize_new to save the updates
        # 2. Once in recalculate_adjusted_amounts to save adjusted amounts
        assert commit_count == 2


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


class TestAutoCategorize:
    def test_add_rule_auto_categorizes(self, db, cat):
        """Adding a rule automatically re-categorizes all transactions."""
        food_id = _add_cat(cat, "Food")
        _add_txn(db, "ICA Store 1")
        _add_txn(db, "ICA Store 2", dt=date(2024, 1, 16))
        _add_txn(db, "Unknown thing", dt=date(2024, 1, 17))

        cat.add_rule(food_id, "ICA", match_type="regex")

        cur = db.get_cursor()
        cur.execute("SELECT category_id FROM transactions WHERE description LIKE 'ICA%'")
        for row in cur.fetchall():
            assert row[0] == food_id

    def test_remove_rule_auto_recategorizes(self, db, cat):
        """Removing a rule re-categorizes, leaving former matches uncategorized."""
        food_id = _add_cat(cat, "Food")
        txn_id = _add_txn(db, "ICA Store")

        rule_id = cat.add_rule(food_id, "ICA", match_type="regex")

        # Verified categorized
        cur = db.get_cursor()
        cur.execute("SELECT category_id FROM transactions WHERE id = ?", (txn_id,))
        assert cur.fetchone()[0] == food_id

        cat.remove_rule(rule_id)

        # Now uncategorized
        cur.execute("SELECT category_id FROM transactions WHERE id = ?", (txn_id,))
        assert cur.fetchone()[0] is None


class TestUpdateCategory:
    def test_rename(self, db, cat):
        cid = cat.add_category("Food")
        cat.update_category(cid, name="Groceries")
        result = cat.get_category_by_name("Groceries")
        assert result is not None
        assert result["id"] == cid

    def test_reparent(self, db, cat):
        root_id = cat.add_category("Expenses")
        child_id = cat.add_category("Food", parent_id=root_id)
        other_root = cat.add_category("Other")

        cat.update_category(child_id, parent_id=other_root)

        children = cat.get_children(other_root)
        assert children[0]["id"] == child_id

    def test_no_change_returns_false(self, cat):
        cid = cat.add_category("Test")
        assert cat.update_category(cid) is False


class TestDeleteCategory:
    def test_delete_empty_category(self, db, cat):
        cid = cat.add_category("Unused")
        assert cat.delete_category(cid) is True
        assert cat.get_category_by_name("Unused") is None

    def test_delete_nonexistent_returns_false(self, cat):
        assert cat.delete_category(9999) is False

    def test_children_require_reassign(self, db, cat):
        parent_id = cat.add_category("Parent")
        cat.add_category("Child", parent_id=parent_id)

        with pytest.raises(ValueError, match="child"):
            cat.delete_category(parent_id)

    def test_reassign_children(self, db, cat):
        old_parent = cat.add_category("Old Parent")
        new_parent = cat.add_category("New Parent")
        child_id = cat.add_category("Child", parent_id=old_parent)

        cat.delete_category(old_parent, reassign=new_parent)

        # Child now under new parent
        children = cat.get_children(new_parent)
        assert len(children) == 1
        assert children[0]["id"] == child_id

    def test_promote_children(self, db, cat):
        root_id = cat.add_category("Root")
        mid_id = cat.add_category("Mid", parent_id=root_id)
        leaf_id = cat.add_category("Leaf", parent_id=mid_id)

        # Delete mid, promote leaf to root
        cat.delete_category(mid_id, reassign=root_id)

        children = cat.get_children(root_id)
        assert leaf_id in [c["id"] for c in children]

    def test_rules_require_reassign_or_force(self, db, cat):
        food_id = cat.add_category("Food")
        cat.add_rule(food_id, "ICA", match_type="regex")

        with pytest.raises(ValueError, match="rule"):
            cat.delete_category(food_id)

    def test_force_deletes_rules(self, db, cat):
        food_id = cat.add_category("Food")
        rule_id = cat.add_rule(food_id, "ICA", match_type="regex")

        cat.delete_category(food_id, force=True)

        rules = cat.list_rules()
        assert len(rules) == 0

    def test_reassign_rules(self, db, cat):
        old_id = cat.add_category("Old")
        new_id = cat.add_category("New")
        txn_id = _add_txn(db, "ICA Store")

        cat.add_rule(old_id, "ICA", match_type="regex")
        cat.delete_category(old_id, reassign=new_id)

        # Transaction should be categorized under new via reassigned rule
        cur = db.get_cursor()
        cur.execute("SELECT category_id FROM transactions WHERE id = ?", (txn_id,))
        assert cur.fetchone()[0] == new_id

    def test_reassign_manual_matches(self, db, cat):
        old_id = cat.add_category("Old")
        new_id = cat.add_category("New")
        txn_id = _add_txn(db, "Something")

        cat.add_manual_match(txn_id, old_id)
        cat.delete_category(old_id, reassign=new_id)

        cur = db.get_cursor()
        cur.execute("SELECT category_id FROM transactions WHERE id = ?", (txn_id,))
        assert cur.fetchone()[0] == new_id

    def test_transactions_uncategorized_without_reassign(self, db, cat):
        food_id = cat.add_category("Food")
        txn_id = _add_txn(db, "ICA Store")
        cat.add_manual_match(txn_id, food_id)

        cat.delete_category(food_id, force=True)

        cur = db.get_cursor()
        cur.execute("SELECT category_id FROM transactions WHERE id = ?", (txn_id,))
        assert cur.fetchone()[0] is None


class TestCategoryHelpers:
    """Tests for get_category, get_parent, get_subtree."""

    @pytest.fixture(autouse=True)
    def setup_tree(self, cat):
        """Build: Food -> Groceries, Dining -> Restaurants, Cafes."""
        self.food = cat.add_category("Food")
        self.groceries = cat.add_category("Groceries", parent_id=self.food)
        self.dining = cat.add_category("Dining", parent_id=self.food)
        self.restaurants = cat.add_category("Restaurants", parent_id=self.dining)
        self.cafes = cat.add_category("Cafes", parent_id=self.dining)

    def test_get_category(self, cat):
        c = cat.get_category(self.food)
        assert c["name"] == "Food"
        assert c["parent_id"] is None

    def test_get_category_not_found(self, cat):
        assert cat.get_category(999) is None

    def test_get_parent_of_child(self, cat):
        p = cat.get_parent(self.groceries)
        assert p["id"] == self.food

    def test_get_parent_of_grandchild(self, cat):
        p = cat.get_parent(self.restaurants)
        assert p["id"] == self.dining

    def test_get_parent_of_root(self, cat):
        assert cat.get_parent(self.food) is None

    def test_get_parent_of_nonexistent(self, cat):
        assert cat.get_parent(999) is None

    def test_get_subtree_food(self, cat):
        subtree = cat.get_subtree(self.food)
        names = {s["name"] for s in subtree}
        assert names == {"Groceries", "Dining", "Restaurants", "Cafes"}

    def test_get_subtree_depth(self, cat):
        subtree = cat.get_subtree(self.food)
        by_name = {s["name"]: s for s in subtree}
        assert by_name["Groceries"]["depth"] == 0
        assert by_name["Dining"]["depth"] == 0
        assert by_name["Restaurants"]["depth"] == 1
        assert by_name["Cafes"]["depth"] == 1

    def test_get_subtree_leaf(self, cat):
        subtree = cat.get_subtree(self.cafes)
        assert subtree == []

    def test_get_subtree_nonexistent(self, cat):
        subtree = cat.get_subtree(999)
        assert subtree == []

    def test_get_subtree_dining(self, cat):
        subtree = cat.get_subtree(self.dining)
        names = {s["name"] for s in subtree}
        assert names == {"Restaurants", "Cafes"}


class TestUncategorizedGrouped:
    def test_groups_by_description(self, db):
        cat = Categorizer(db)
        a1 = db.add_account("Checking")
        food = cat.add_category("Food")

        # Uncategorized: 3x Swish (different amounts), 2x ATM
        for amt in [-100.0, -50.0, -75.0]:
            db.get_cursor().execute(
                "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
                (date(2026, 1, 1), "Swish Person A", amt, a1),
            )
        for amt in [-500.0, -200.0]:
            db.get_cursor().execute(
                "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
                (date(2026, 1, 2), "ATM Withdrawal", amt, a1),
            )
        # Categorized: should not appear
        db.get_cursor().execute(
            "INSERT INTO transactions (date, description, amount, account_id, category_id) VALUES (?, ?, ?, ?, ?)",
            (date(2026, 1, 1), "ICA", -80.0, a1, food),
        )
        db.commit()

        groups = cat.get_uncategorized_grouped()
        assert len(groups) == 2
        # Sorted by count desc
        assert groups[0]["description"] == "Swish Person A"
        assert groups[0]["count"] == 3
        assert groups[0]["total"] == pytest.approx(-225.0)
        assert groups[1]["description"] == "ATM Withdrawal"
        assert groups[1]["count"] == 2

    def test_get_uncategorized_grouped_non_zero(self, db):
        cat = Categorizer(db)
        a1 = db.add_account("Checking")
        
        # Insert uncategorized transactions, some with adjusted_amount = 0 (reimbursed)
        db.get_cursor().execute(
            "INSERT INTO transactions (date, description, amount, adjusted_amount, account_id) VALUES (?, ?, ?, ?, ?)",
            (date(2026, 1, 1), "Swish Person A", -100.0, -100.0, a1),
        )
        db.get_cursor().execute(
            "INSERT INTO transactions (date, description, amount, adjusted_amount, account_id) VALUES (?, ?, ?, ?, ?)",
            (date(2026, 1, 1), "Swish Person A", -50.0, 0.0, a1), # zero sum!
        )
        db.get_cursor().execute(
            "INSERT INTO transactions (date, description, amount, adjusted_amount, account_id) VALUES (?, ?, ?, ?, ?)",
            (date(2026, 1, 2), "ATM Withdrawal", -200.0, -200.0, a1),
        )
        db.commit()
        
        # Without non_zero=True, both appear
        groups_all = cat.get_uncategorized_grouped(non_zero=False)
        assert len(groups_all) == 2
        
        # With non_zero=True, zero sum is filtered out
        groups_nonzero = cat.get_uncategorized_grouped(non_zero=True)
        assert len(groups_nonzero) == 2
        swish_group = next(g for g in groups_nonzero if g["description"] == "Swish Person A")
        assert swish_group["count"] == 1
        assert swish_group["total"] == pytest.approx(-100.0)

    def test_empty_when_all_categorized(self, db):
        cat = Categorizer(db)
        assert cat.get_uncategorized_grouped() == []


class TestAmountBasedRules:
    def test_amount_min(self, db):
        cat = Categorizer(db)
        a1 = db.add_account("Checking")
        housing = cat.add_category("Housing")
        parking = cat.add_category("Parking")

        cat.add_rule(housing, "HSB", match_type="contains", amount_max=-4000)
        cat.add_rule(parking, "HSB", match_type="contains", amount_min=-1100)

        db.get_cursor().execute(
            "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
            (date(2026, 1, 1), "HSB Stockholm", -5000.0, a1),
        )
        db.get_cursor().execute(
            "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
            (date(2026, 1, 1), "HSB Stockholm", -1000.0, a1),
        )
        db.commit()

        cat.categorize_new()
        cur = db.get_cursor()
        cur.execute("SELECT amount, category_id FROM transactions ORDER BY amount")
        rows = cur.fetchall()
        assert rows[0][1] == housing  # -5000 -> housing
        assert rows[1][1] == parking  # -1000 -> parking

    def test_amount_range(self, db):
        cat = Categorizer(db)
        a1 = db.add_account("Checking")
        medium = cat.add_category("Medium")

        cat.add_rule(medium, "Test", match_type="contains", amount_min=-200, amount_max=-50)

        db.get_cursor().execute(
            "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
            (date(2026, 1, 1), "Test A", -100.0, a1),
        )
        db.get_cursor().execute(
            "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
            (date(2026, 1, 1), "Test B", -300.0, a1),
        )
        db.commit()

        cat.categorize_new()
        cur = db.get_cursor()
        cur.execute("SELECT description, category_id FROM transactions ORDER BY amount")
        rows = cur.fetchall()
        assert rows[0][1] is None   # -300 out of range
        assert rows[1][1] == medium # -100 in range

    def test_rule_without_amount_matches_any(self, db):
        cat = Categorizer(db)
        a1 = db.add_account("Checking")
        food = cat.add_category("Food")

        cat.add_rule(food, "ICA", match_type="contains")

        db.get_cursor().execute(
            "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
            (date(2026, 1, 1), "ICA", -50.0, a1),
        )
        db.get_cursor().execute(
            "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
            (date(2026, 1, 1), "ICA", -5000.0, a1),
        )
        db.commit()

        cat.categorize_new()
        cur = db.get_cursor()
        cur.execute("SELECT category_id FROM transactions")
        assert all(row[0] == food for row in cur.fetchall())

    def test_amount_rule_takes_precedence_over_no_amount(self, db):
        cat = Categorizer(db)
        a1 = db.add_account("Checking")
        food = cat.add_category("Food")
        big = cat.add_category("Big Purchase")

        # Same priority, amount-specific rule added after — should win via ordering
        cat.add_rule(food, "Store", match_type="contains", priority=0)
        cat.add_rule(big, "Store", match_type="contains", priority=1, amount_max=-1000)

        db.get_cursor().execute(
            "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
            (date(2026, 1, 1), "Store A", -2000.0, a1),
        )
        db.commit()

        cat.categorize_new()
        cur = db.get_cursor()
        cur.execute("SELECT category_id FROM transactions")
        assert cur.fetchone()[0] == big
