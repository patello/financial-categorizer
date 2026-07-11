"""Tests for stats views and query functions (step 4+6)."""

import datetime
import pytest
from financial_categorizer.db_handler import DatabaseHandler
from financial_categorizer.categorizer import Categorizer
from financial_categorizer.stats import Stats


@pytest.fixture
def db():
    handler = DatabaseHandler(":memory:")
    yield handler
    handler.disconnect()


@pytest.fixture
def stats(db):
    return Stats(db)


def _seed(db, cat=None):
    """Seed with accounts, categories, transactions. Returns dict of ids."""
    a1 = db.add_account("Checking")
    a2 = db.add_account("Shared", ownership_ratio=0.5)
    c = Categorizer(db)

    food_id = c.add_category("Food")
    rent_id = c.add_category("Rent")
    salary_id = c.add_category("Salary")
    dining_id = c.add_category("Dining Out", parent_id=food_id)

    # January
    db.get_cursor().execute(
        "INSERT INTO transactions (date, description, amount, account_id, adjusted_amount, category_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (datetime.date(2026, 1, 5), "Salary", 30000, a1, 30000, salary_id),
    )
    db.get_cursor().execute(
        "INSERT INTO transactions (date, description, amount, account_id, adjusted_amount, category_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (datetime.date(2026, 1, 10), "ICA", -800, a1, -800, food_id),
    )
    db.get_cursor().execute(
        "INSERT INTO transactions (date, description, amount, account_id, adjusted_amount, category_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (datetime.date(2026, 1, 15), "Rent", -8000, a1, -8000, rent_id),
    )
    # Shared account — ownership 0.5
    db.get_cursor().execute(
        "INSERT INTO transactions (date, description, amount, account_id, adjusted_amount, category_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (datetime.date(2026, 1, 20), "Groceries shared", -1000, a2, -500, food_id),
    )

    # February
    db.get_cursor().execute(
        "INSERT INTO transactions (date, description, amount, account_id, adjusted_amount, category_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (datetime.date(2026, 2, 5), "Salary", 30000, a1, 30000, salary_id),
    )
    db.get_cursor().execute(
        "INSERT INTO transactions (date, description, amount, account_id, adjusted_amount, category_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (datetime.date(2026, 2, 12), "Restaurant", -600, a1, -600, dining_id),
    )
    db.get_cursor().execute(
        "INSERT INTO transactions (date, description, amount, account_id, adjusted_amount, category_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (datetime.date(2026, 2, 15), "Rent", -8000, a1, -8000, rent_id),
    )

    # Uncategorized
    db.get_cursor().execute(
        "INSERT INTO transactions (date, description, amount, account_id, adjusted_amount) "
        "VALUES (?, ?, ?, ?, ?)",
        (datetime.date(2026, 1, 25), "Mystery", -200, a1, -200),
    )

    db.commit()

    return {
        "accounts": {"checking": a1, "shared": a2},
        "categories": {"food": food_id, "rent": rent_id, "salary": salary_id, "dining": dining_id},
    }


class TestMonthlySummary:

    def test_all_months(self, db, stats):
        _seed(db)
        rows = stats.monthly_summary()
        assert len(rows) == 2

        jan = [r for r in rows if r["month"] == "2026-01"][0]
        assert jan["total_income"] == pytest.approx(30000)
        assert jan["total_expenses"] == pytest.approx(-9500)
        assert jan["net"] == pytest.approx(20500)

    def test_filter_month(self, db, stats):
        _seed(db)
        rows = stats.monthly_summary(month="2026-02")
        assert len(rows) == 1
        assert rows[0]["total_income"] == pytest.approx(30000)
        assert rows[0]["total_expenses"] == pytest.approx(-8600)


class TestCategoryTotal:

    def test_category_with_subcategories(self, db, stats):
        ids = _seed(db)["categories"]
        # Food + Dining Out + shared groceries
        result = stats.category_total(ids["food"], month="2026-01")
        assert result["total"] == pytest.approx(-1300)  # -800 + -500
        assert result["count"] == 2

    def test_single_category(self, db, stats):
        ids = _seed(db)["categories"]
        result = stats.category_total(ids["rent"], month="2026-01")
        assert result["total"] == pytest.approx(-8000)
        assert result["count"] == 1

    def test_no_data_returns_zero(self, db, stats):
        _seed(db)
        c = Categorizer(db)
        cat_id = c.add_category("Empty")
        result = stats.category_total(cat_id, month="2026-01")
        assert result["total"] == 0.0
        assert result["count"] == 0


class TestTopSpending:

    def test_top_categories(self, db, stats):
        _seed(db)
        rows = stats.top_spending(month="2026-01", limit=5)
        assert len(rows) >= 2
        # Rent is biggest spending
        assert rows[0]["category_name"] == "Rent"

    def test_top_limit(self, db, stats):
        _seed(db)
        rows = stats.top_spending(limit=1)
        assert len(rows) == 1


class TestTrend:

    def test_trend_across_months(self, db, stats):
        ids = _seed(db)["categories"]
        rows = stats.trend(ids["food"])
        assert len(rows) == 2
        jan = [r for r in rows if r["month"] == "2026-01"][0]
        feb = [r for r in rows if r["month"] == "2026-02"][0]
        assert jan["total"] == pytest.approx(-1300)
        assert feb["total"] == pytest.approx(-600)

    def test_trend_with_date_range(self, db, stats):
        ids = _seed(db)["categories"]
        rows = stats.trend(
            ids["rent"],
            date_from=datetime.date(2026, 1, 1),
            date_to=datetime.date(2026, 1, 31),
        )
        assert len(rows) == 1
        assert rows[0]["total"] == pytest.approx(-8000)


class TestViewsCreated:

    def test_effective_transactions_view(self, db, stats):
        _seed(db)
        cur = db.get_cursor()
        cur.execute("SELECT COUNT(*) FROM v_effective_transactions")
        assert cur.fetchone()[0] == 8  # all 8 transactions have adjusted_amount

    def test_views_idempotent(self, db):
        """Creating Stats twice should not error."""
        _seed(db)
        Stats(db)
        Stats(db)


class TestCategoryType:

    def test_transfer_excluded_from_monthly_summary(self, db):
        a1 = db.add_account("Checking")
        c = Categorizer(db)
        food = c.add_category("Food", category_type="expense")
        savings = c.add_category("Savings", category_type="transfer")
        salary = c.add_category("Salary", category_type="income")

        db.get_cursor().execute(
            "INSERT INTO transactions (date, description, amount, account_id, adjusted_amount, category_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.date(2026, 1, 5), "Salary", 30000, a1, 30000, salary),
        )
        db.get_cursor().execute(
            "INSERT INTO transactions (date, description, amount, account_id, adjusted_amount, category_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.date(2026, 1, 10), "ICA", -800, a1, -800, food),
        )
        db.get_cursor().execute(
            "INSERT INTO transactions (date, description, amount, account_id, adjusted_amount, category_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.date(2026, 1, 15), "Savings", -5000, a1, -5000, savings),
        )
        db.commit()

        stats = Stats(db)
        rows = stats.monthly_summary(month="2026-01")
        assert len(rows) == 1
        # Transfer excluded from income/expenses
        assert rows[0]["total_income"] == pytest.approx(30000)
        assert rows[0]["total_expenses"] == pytest.approx(-800)
        assert rows[0]["net"] == pytest.approx(29200)

    def test_transfer_category_type_stored(self, db):
        c = Categorizer(db)
        cid = c.add_category("Savings", category_type="transfer")
        cat = c.get_category(cid)
        assert cat["category_type"] == "transfer"

    def test_default_category_type_is_expense(self, db):
        c = Categorizer(db)
        cid = c.add_category("Food")
        cat = c.get_category(cid)
        assert cat["category_type"] == "expense"

    def test_update_category_type(self, db):
        c = Categorizer(db)
        cid = c.add_category("Misc", category_type="expense")
        c.update_category(cid, category_type="transfer")
        cat = c.get_category(cid)
        assert cat["category_type"] == "transfer"


class TestNewAnalyticalViews:

    def test_new_views_exist_and_queryable(self, db):
        _seed(db)
        # Stats initialization registers the views
        Stats(db)
        cur = db.get_cursor()

        # 1. Test v_cumulative_spending_monthly
        cur.execute("SELECT COUNT(*) FROM v_cumulative_spending_monthly")
        assert cur.fetchone()[0] > 0

        # Verify running cumulative sum resets by month
        cur.execute("SELECT date, cumulative_amount FROM v_cumulative_spending_monthly ORDER BY date")
        rows = cur.fetchall()
        assert rows[0][1] < 0

        # 2. Test v_daily_spending_moving_average
        cur.execute("SELECT COUNT(*) FROM v_daily_spending_moving_average")
        assert cur.fetchone()[0] > 0

        # 3. Test v_category_monthly_averages
        cur.execute("SELECT category_name, average_monthly_spending FROM v_category_monthly_averages")
        averages = {row[0]: row[1] for row in cur.fetchall()}
        assert "Food" in averages
        assert "Rent" in averages
        assert averages["Rent"] == -8000.0

        # 4. Test v_salary_period_summary
        cur.execute("SELECT period, total_income, total_expenses FROM v_salary_period_summary")
        periods = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
        assert "2026-01" in periods

        # 5. Test v_breakout_categories
        cur.execute("SELECT COUNT(*) FROM v_breakout_categories")
        assert cur.fetchone()[0] > 0

        # 6. Test v_uncategorized_groups
        cur.execute("SELECT COUNT(*) FROM v_uncategorized_groups")
        assert cur.fetchone()[0] > 0


class TestUnsplitAndGross:

    def test_unsplit_and_gross_totals(self, db, stats):
        a1 = db.add_account("Checking")
        a2 = db.add_account("Shared", ownership_ratio=0.5)

        c = Categorizer(db)
        food_id = c.add_category("Food")
        reimb_id = c.add_category("Reimbursement", category_type="income")

        # Original shared expense of -2000 SEK (adjusted base = -1000 SEK)
        cur = db.get_cursor()
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id, category_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (datetime.date(2026, 1, 10), "ICA Maxi shared", -2000.0, a2, food_id)
        )
        t_exp_id = cur.lastrowid

        # Reimbursement of +500 SEK from partner (adjusted base = +500 SEK)
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id, category_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (datetime.date(2026, 1, 12), "Swish reimbursement", 500.0, a1, reimb_id)
        )
        t_reimb_id = cur.lastrowid
        db.commit()

        # Link them as a reimbursement
        from financial_categorizer.db_handler import TransferManager
        linker = TransferManager(db)
        linker.link_transactions(t_reimb_id, t_exp_id, link_type="reimbursement", ratio=1.0)

        # Verify adjusted amounts
        cur.execute("SELECT adjusted_amount FROM transactions WHERE id = ?", (t_exp_id,))
        assert cur.fetchone()[0] == pytest.approx(-750.0)

        # Re-initialize Stats to recreate views
        stats = Stats(db)

        # 1. Default total for Food category
        tot_default = stats.category_total(food_id)
        assert tot_default["total"] == pytest.approx(-750.0)

        # 2. Unsplit total (household net of reimbursement)
        tot_unsplit = stats.category_total(food_id, unsplit=True)
        assert tot_unsplit["total"] == pytest.approx(-1500.0)

        # 3. Gross total (household before reimbursement)
        tot_gross = stats.category_total(food_id, gross=True)
        assert tot_gross["total"] == pytest.approx(-2000.0)

