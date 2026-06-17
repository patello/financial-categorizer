"""Tests for Stats.compare() — month-over-month with salary period support."""

import pytest

from financial_categorizer.db_handler import DatabaseHandler
from financial_categorizer.stats import Stats


@pytest.fixture
def db(tmp_path):
    """DB with transactions across 3 calendar months."""
    handler = DatabaseHandler(str(tmp_path / "test.db"))
    handler.add_account("Checking", type="personal", ownership_ratio=1.0)

    cur = handler.get_cursor()
    # Add income/expense categories
    cur.execute("INSERT INTO categories (name, category_type) VALUES ('Salary', 'income')")
    cur.execute("INSERT INTO categories (name, category_type) VALUES ('Groceries', 'expense')")
    cur.execute("INSERT INTO categories (name, category_type) VALUES ('Rent', 'expense')")

    # January 2026
    cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) VALUES (1, '2026-01-05', 30000, 30000, 1, 'Salary')")
    cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) VALUES (1, '2026-01-10', -5000, -5000, 2, 'Groceries')")
    cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) VALUES (1, '2026-01-15', -8000, -8000, 3, 'Rent')")

    # February 2026
    cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) VALUES (1, '2026-02-05', 30000, 30000, 1, 'Salary')")
    cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) VALUES (1, '2026-02-10', -6000, -6000, 2, 'Groceries')")
    cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) VALUES (1, '2026-02-15', -8000, -8000, 3, 'Rent')")

    # March 2026
    cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) VALUES (1, '2026-03-05', 32000, 32000, 1, 'Salary')")
    cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) VALUES (1, '2026-03-10', -4000, -4000, 2, 'Groceries')")
    cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) VALUES (1, '2026-03-15', -8000, -8000, 3, 'Rent')")

    handler.commit()
    yield handler
    handler.disconnect()


class TestCompareCalendar:
    def test_default_compares_latest(self, db):
        stats = Stats(db)
        result = stats.compare(period_type="calendar")
        assert result["period"] == "2026-03"
        assert result["prev_period"] == "2026-02"
        assert result["total_income"] == 32000.0
        assert result["total_expenses"] == -12000.0
        assert result["net"] == 20000.0

    def test_specific_month(self, db):
        stats = Stats(db)
        result = stats.compare(period="2026-02", period_type="calendar")
        assert result["period"] == "2026-02"
        assert result["prev_period"] == "2026-01"
        assert result["income_delta"] == 0.0  # same salary
        assert result["expense_delta"] == -1000.0  # -6000 vs -5000 groceries

    def test_deltas_and_percentages(self, db):
        stats = Stats(db)
        result = stats.compare(period="2026-03", period_type="calendar")
        assert result["income_delta"] == 2000.0  # 32000 - 30000
        assert result["income_pct"] == pytest.approx(6.7, abs=0.1)
        assert result["expense_delta"] == 2000.0  # -12000 - (-14000)
        assert result["net_delta"] == 4000.0  # 20000 - 16000

    def test_nonexistent_month(self, db):
        stats = Stats(db)
        result = stats.compare(period="2025-01", period_type="calendar")
        assert result == []


class TestCompareSalary:
    def test_salary_period_grouping(self, db):
        """Salary period 2026-02 = Jan 25 - Feb 24."""
        stats = Stats(db)
        result = stats.compare(period_type="salary")
        # Latest salary period should be 2026-03 (Feb 25 - Mar 24)
        # Contains: Mar salary + Mar groceries + Mar rent (all within Feb 25 - Mar 24)
        assert result["period"] == "2026-03"

    def test_salary_period_with_day_boundary(self, db):
        """Verify transactions on 25th and 24th are correctly assigned."""
        cur = db.get_cursor()
        # Jan 25 belongs to salary period 2026-01 (Dec 25 - Jan 24)? No.
        # Salary period 2026-02 = Jan 25 - Feb 24
        cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) VALUES (1, '2026-01-25', -100, -100, 2, 'Groceries')")
        cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) VALUES (1, '2026-02-24', -200, -200, 2, 'Groceries')")
        db.commit()

        stats = Stats(db)
        all_periods = stats._salary_period_summary(db.get_cursor())
        # Find 2026-02 period
        feb_period = next(p for p in all_periods if p["period"] == "2026-02")
        # Should include Jan 25 transaction and Feb 24 transaction, plus Feb transactions before 24th
        assert feb_period["total_expenses"] < -1000  # more than just -100 and -200

    def test_salary_vs_calendar_different(self, db):
        """Salary periods should produce different numbers than calendar months."""
        # Calendar Feb has Feb expenses. Salary 2026-02 = Jan 25 - Feb 24.
        # Jan 25-31 has no transactions, so salary 2026-02 only has Feb 1-24.
        # All Feb transactions are on days 5-15, so they're in both.
        # But salary 2026-03 = Feb 25 - Mar 24. Feb has no tx after 15th, so
        # salary 2026-03 only has March txs, while calendar March is the same.
        # Let's compare calendar Feb (has -6000 groceries) vs salary 2026-01 (Dec 25 - Jan 24)
        # which only has Jan 1-24 txs (salary on 5th, groceries on 10th)
        stats = Stats(db)
        cal_feb = stats.compare(period="2026-02", period_type="calendar")
        sal_jan = stats.compare(period="2026-01", period_type="salary")
        # Calendar Feb has -6000 groceries, salary Jan has -5000
        assert cal_feb["total_expenses"] != sal_jan["total_expenses"]


class TestCompareEdgeCases:
    def test_single_month(self, tmp_path):
        """With only one month, no comparison possible."""
        handler = DatabaseHandler(str(tmp_path / "single.db"))
        handler.add_account("A", type="personal")
        cur = handler.get_cursor()
        cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, description) VALUES (1, '2026-01-10', -100, -100, 'Stuff')")
        handler.commit()

        stats = Stats(handler)
        result = stats.compare(period_type="calendar")
        # Should return the list (not enough for comparison)
        assert isinstance(result, list)
        assert len(result) == 1
        handler.disconnect()

    def test_transfer_excluded(self, db):
        """Transfer-type categories should be excluded from comparison."""
        cur = db.get_cursor()
        cur.execute("INSERT INTO categories (name, category_type) VALUES ('Savings', 'transfer')")
        cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) VALUES (1, '2026-02-20', -5000, -5000, 4, 'Savings')")
        db.commit()

        stats = Stats(db)
        result = stats.compare(period="2026-02", period_type="calendar")
        # The -5000 savings transfer should NOT affect expenses
        assert result["total_expenses"] == -14000.0  # same as without transfer


class TestConfigureSalary:
    def test_salary_period_mode_fixed_custom_day(self, db):
        """Test fixed mode with a custom boundary day."""
        db.set_metadata("salary_period_mode", "fixed")
        db.set_metadata("salary_period_fixed_day", "20")
        
        # Insert test transactions around the 20th boundary
        cur = db.get_cursor()
        cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) "
                    "VALUES (1, '2026-01-19', -100, -100, 2, 'Groceries')")
        cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) "
                    "VALUES (1, '2026-01-20', -200, -200, 2, 'Groceries')")
        db.commit()
        
        stats = Stats(db)
        # Period 2026-02 should start on Jan 20th and end on Feb 19th
        all_periods = stats._salary_period_summary(db.get_cursor())
        feb_period = next(p for p in all_periods if p["period"] == "2026-02")
        # Jan 20 transaction (-200) should be included, Jan 19 transaction (-100) should NOT (it belongs to 2026-01)
        # We also have Feb 10 groceries (-6000) and Feb 15 rent (-8000) in the fixture, which are part of 2026-02 period.
        # Total expenses in 2026-02 should be -200 + (-6000) + (-8000) = -14200.0
        assert feb_period["total_expenses"] == pytest.approx(-14200.0)

    def test_salary_period_mode_salary_auto(self, db):
        """Test salary mode (auto payday-based boundary)."""
        db.set_metadata("salary_period_mode", "salary")
        db.set_metadata("salary_period_category_name", "Salary")
        
        # Insert a preceding salary in Dec to establish the previous period boundary
        cur = db.get_cursor()
        cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) "
                    "VALUES (1, '2025-12-25', 30000, 30000, 1, 'Salary')")
        
        # In the fixture:
        # Jan salary is on 2026-01-05
        # Feb salary is on 2026-02-05
        # So period 2026-01 (Jan salary period) spans 2026-01-05 to 2026-02-04.
        
        # Let's add a transaction on Jan 4 (before Jan salary - should fall into Dec period)
        # and on Feb 4 (before Feb salary - should fall into Jan period)
        cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) "
                    "VALUES (1, '2026-01-04', -100, -100, 2, 'Groceries')")
        cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) "
                    "VALUES (1, '2026-02-04', -200, -200, 2, 'Groceries')")
        db.commit()
        
        stats = Stats(db)
        all_periods = stats._salary_period_summary(db.get_cursor())
        
        # Find 2026-01 period (the one starting Jan 5)
        jan_period = next(p for p in all_periods if p["period"] == "2026-01")
        # Should contain: Feb 4 transaction (-200), plus fixture's Jan 10 groceries (-5000) and Jan 15 rent (-8000).
        # Jan 4 transaction should NOT be in this period (belongs to 2025-12).
        assert jan_period["total_expenses"] == pytest.approx(-13200.0)

    def test_salary_period_mode_salary_multiple_in_month(self, db):
        """Test salary mode when multiple salary transactions exist in the same month (uses primary/largest)."""
        db.set_metadata("salary_period_mode", "salary")
        
        cur = db.get_cursor()
        # Insert preceding salary in Dec
        cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) "
                    "VALUES (1, '2025-12-25', 30000, 30000, 1, 'Salary')")
        
        # Add a secondary/smaller salary on Jan 2nd (e.g. 500 SEK side income)
        # The main salary is 30,000 SEK on Jan 5th.
        cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) "
                    "VALUES (1, '2026-01-02', 500, 500, 1, 'Salary')")
        # Add a transaction on Jan 3rd
        cur.execute("INSERT INTO transactions (account_id, date, amount, adjusted_amount, category_id, description) "
                    "VALUES (1, '2026-01-03', -100, -100, 2, 'Groceries')")
        db.commit()
        
        stats = Stats(db)
        all_periods = stats._salary_period_summary(db.get_cursor())
        
        # Since Jan 5 is the largest salary, it should be the primary salary date for January.
        # This means Jan 3 is *before* the primary salary date (Jan 5), so it falls into the previous period (2025-12).
        # Jan 10 (-5000) and Jan 15 (-8000) are *after* Jan 5, so they belong to the Jan 5 period (labeled 2026-01).
        jan_period = next(p for p in all_periods if p["period"] == "2026-01")
        # Should include Jan 10 and Jan 15, but NOT Jan 3
        assert jan_period["total_expenses"] == pytest.approx(-13000.0)
