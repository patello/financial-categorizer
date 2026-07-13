import pytest
from datetime import date, timedelta
from financial_categorizer.db_handler import DatabaseHandler
from financial_categorizer.recurring import RecurringManager
from financial_categorizer.stats import Stats

@pytest.fixture
def db():
    handler = DatabaseHandler(":memory:")
    cur = handler.get_cursor()
    # Add dummy account
    cur.execute("INSERT INTO accounts (id, name, type, ownership_ratio) VALUES (1, 'Joint Account', 'tracked', 0.5)")
    # Add dummy category
    cur.execute("INSERT INTO categories (id, name, category_type) VALUES (1, 'Utilities', 'expense')")
    cur.execute("INSERT INTO categories (id, name, category_type) VALUES (2, 'Food', 'expense')")
    cur.execute("INSERT INTO categories (id, name, category_type) VALUES (3, 'Salary', 'income')")
    yield handler
    handler.disconnect()

def test_period_boundaries_fixed_mode(db):
    cur = db.get_cursor()
    cur.execute("INSERT INTO metadata (key, value) VALUES ('salary_period_mode', 'fixed')")
    cur.execute("INSERT INTO metadata (key, value) VALUES ('salary_period_fixed_day', '25')")
    
    stats = Stats(db)
    # Test date greater than or equal to fixed_day
    start, end, name = stats.get_period_boundaries(date(2026, 6, 26))
    assert start == date(2026, 6, 25)
    assert end == date(2026, 7, 25)
    assert name == "2026-07"

    # Test date less than fixed_day
    start, end, name = stats.get_period_boundaries(date(2026, 6, 20))
    assert start == date(2026, 5, 25)
    assert end == date(2026, 6, 25)
    assert name == "2026-06"

def test_period_boundaries_salary_mode(db):
    cur = db.get_cursor()
    cur.execute("INSERT INTO metadata (key, value) VALUES ('salary_period_mode', 'salary')")
    # Insert salary transactions
    cur.execute("INSERT INTO transactions (id, account_id, category_id, date, description, amount, adjusted_amount) VALUES (1, 1, 3, '2026-05-25', 'Salary payout', 50000.0, 25000.0)")
    cur.execute("INSERT INTO transactions (id, account_id, category_id, date, description, amount, adjusted_amount) VALUES (2, 1, 3, '2026-06-25', 'Salary payout', 50000.0, 25000.0)")
    
    stats = Stats(db)
    # Test date inside period
    start, end, name = stats.get_period_boundaries(date(2026, 6, 10))
    assert start == date(2026, 5, 25)
    assert end == date(2026, 6, 25)
    assert name == "2026-05"

def test_historical_daily_spend_excludes_recurring(db):
    cur = db.get_cursor()
    # Add historical non-recurring transactions
    cur.execute("INSERT INTO transactions (account_id, category_id, date, description, amount, adjusted_amount) VALUES (1, 2, '2026-06-01', 'Groceries', -100.0, -50.0)")
    cur.execute("INSERT INTO transactions (account_id, category_id, date, description, amount, adjusted_amount) VALUES (1, 2, '2026-06-02', 'Dining', -200.0, -100.0)")
    # Add dummy recurring configuration to avoid foreign key failure
    cur.execute("INSERT INTO recurring_payments (id, name, pattern, match_type, interval_type, interval_value, start_date) VALUES (99, 'Broadband', 'Broadband', 'contains', 'monthly', 1, '2026-01-01')")
    # Add historical recurring transaction (should be excluded)
    cur.execute("INSERT INTO transactions (account_id, category_id, date, description, amount, adjusted_amount, recurring_id) VALUES (1, 1, '2026-06-03', 'Broadband', -300.0, -150.0, 99)")

    
    stats = Stats(db)
    # Calculate daily average over 10 days
    hist = stats.get_historical_daily_spend(date(2026, 6, 10), window_days=10)
    
    # Food: -150.0 total / 10 days = -15.0 / day
    assert hist["categories"]["Food"] == -15.0
    # Utilities: excluded because it has recurring_id
    assert "Utilities" not in hist["categories"]
    assert hist["total_daily_average"] == -15.0

def test_get_expected_in_range(db):
    cur = db.get_cursor()
    # Add active recurring config
    cur.execute("""
        INSERT INTO recurring_payments (id, name, pattern, match_type, interval_type, interval_value, day_of_month, start_date, account_id, category_id)
        VALUES (1, 'Spotify', 'spotify', 'contains', 'monthly', 1, 28, '2026-01-01', 1, 1)
    """)
    # Anchor transaction on 2026-05-28
    cur.execute("INSERT INTO transactions (account_id, category_id, date, description, amount, adjusted_amount, recurring_id) VALUES (1, 1, '2026-05-28', 'Kortkop Spotify', -169.0, -84.5, 1)")
    
    rm = RecurringManager(db)
    # Project in range [2026-06-01, 2026-07-31]
    expected = rm.get_expected_in_range(date(2026, 6, 1), date(2026, 7, 31))
    
    # Expecting Spotify on 2026-06-28 and 2026-07-28
    assert len(expected) == 2
    assert expected[0]["date"] == date(2026, 6, 28)
    assert expected[0]["amount"] == -84.5
    assert expected[1]["date"] == date(2026, 7, 28)
    assert expected[1]["amount"] == -84.5

def test_historical_daily_spend_rollup_levels(db):
    cur = db.get_cursor()
    # Add child category 'Groceries' under parent category 'Food' (id=2)
    cur.execute("INSERT INTO categories (id, name, parent_id, category_type) VALUES (4, 'Groceries', 2, 'expense')")
    
    # Add transaction under 'Groceries'
    cur.execute("INSERT INTO transactions (account_id, category_id, date, description, amount, adjusted_amount) VALUES (1, 4, '2026-06-01', 'ICA Groceries', -300.0, -150.0)")
    
    stats = Stats(db)
    
    # Level 1 rollup (to Food)
    hist_l1 = stats.get_historical_daily_spend(date(2026, 6, 10), window_days=10, level=1)
    assert "Food" in hist_l1["categories"]
    assert hist_l1["categories"]["Food"] == -15.0  # -150.0 / 10 days
    assert "Groceries" not in hist_l1["categories"]
    
    # Level 2 rollup (detailed)
    hist_l2 = stats.get_historical_daily_spend(date(2026, 6, 10), window_days=10, level=2)
    assert "Groceries" in hist_l2["categories"]
    assert hist_l2["categories"]["Groceries"] == -15.0
    assert "Food" not in hist_l2["categories"]
    
    # Level 0 rollup (no categories)
    hist_l0 = stats.get_historical_daily_spend(date(2026, 6, 10), window_days=10, level=0)
    assert "Total Spend" in hist_l0["categories"]
    assert hist_l0["categories"]["Total Spend"] == -15.0

def test_historical_daily_spend_threshold(db):
    cur = db.get_cursor()
    # Add low spending transaction (adjusted_amount is -5.0, average over 10 days is -0.5, which is < 1.0 SEK)
    cur.execute("INSERT INTO transactions (account_id, category_id, date, description, amount, adjusted_amount) VALUES (1, 2, '2026-06-01', 'Cheap Candy', -10.0, -5.0)")
    
    stats = Stats(db)
    hist = stats.get_historical_daily_spend(date(2026, 6, 10), window_days=10)
    
    # Food is filtered out because average daily spend magnitude is 0.5 (which is < 1.0 SEK)
    assert "Food" not in hist["categories"]
    assert hist["total_daily_average"] == -0.5  # total sum daily average is still calculated

def test_projected_spend_includes_income(db):
    cur = db.get_cursor()
    # Set up fixed period (starts 2026-05-25, ends 2026-06-25, as_of_date is 2026-06-10)
    cur.execute("INSERT INTO metadata (key, value) VALUES ('salary_period_mode', 'fixed')")
    cur.execute("INSERT INTO metadata (key, value) VALUES ('salary_period_fixed_day', '25')")
    
    # Add salary payment (income category) inside the period
    cur.execute("INSERT INTO transactions (account_id, category_id, date, description, amount, adjusted_amount) VALUES (1, 3, '2026-05-26', 'Salary payout', 50000.0, 25000.0)")
    # Add recurring expense inside the period
    cur.execute("INSERT INTO recurring_payments (id, name, pattern, match_type, interval_type, interval_value, start_date) VALUES (99, 'Rent', 'Rent', 'contains', 'monthly', 1, '2026-01-01')")
    cur.execute("INSERT INTO transactions (account_id, category_id, date, description, amount, adjusted_amount, recurring_id) VALUES (1, 1, '2026-05-27', 'Monthly Rent', -10000.0, -5000.0, 99)")
    
    stats = Stats(db)
    proj = stats.get_projected_spend(date(2026, 6, 10), window_days=10)
    
    # Net period PTD actuals: salary (+25000) + recurring expense (-5000) = +20000.00
    assert proj["actual_total"] == 20000.0
    assert proj["actual_rec_expense"] == -5000.0
    assert proj["actual_rec_income"] == 0.0
    assert proj["actual_non_rec_expense"] == 0.0
    assert proj["actual_non_rec_income"] == 25000.0
    assert proj["actual_total_expense"] == -5000.0
    assert proj["actual_total_income"] == 25000.0
    assert proj["actual_net_flow"] == 20000.0


def test_recurring_projection_early_and_late(db):
    cur = db.get_cursor()
    # Add recurring template Spotify (ID=101) scheduled monthly on day 28
    cur.execute("""
        INSERT INTO recurring_payments (id, name, pattern, match_type, interval_type, interval_value, day_of_month, start_date, account_id, category_id)
        VALUES (101, 'Spotify', 'spotify', 'contains', 'monthly', 1, 28, '2026-01-01', 1, 1)
    """)
    
    # Case A: Delayed payment.
    # Last transaction was May 28. Today is July 10 (as_of_date). June 28 payment has not appeared yet!
    # Period is June 23 to July 23.
    # period_start = June 23, start_date = July 11, end_date = July 22.
    # Since June 28 payment has NOT appeared, get_expected_in_range should project July 28 (which is outside the range)
    # AND June 28 (which is inside the period [June 23, July 22] and hasn't appeared yet).
    cur.execute("INSERT INTO transactions (account_id, category_id, date, description, amount, adjusted_amount, recurring_id) VALUES (1, 1, '2026-05-28', 'Spotify May', -169.0, -84.5, 101)")
    
    rm = RecurringManager(db)
    expected = rm.get_expected_in_range(date(2026, 7, 11), date(2026, 7, 22), period_start=date(2026, 6, 23))
    
    # Spotify June payment is scheduled for June 28, falls inside [June 23, July 22], and has NOT occurred. So it should be projected!
    assert len(expected) == 1
    assert expected[0]["date"] == date(2026, 6, 28)
    assert expected[0]["amount"] == -84.5
    
    # Case B: Already appeared.
    # The June 28 payment actually occurred early on June 22. Today is June 26.
    # Period is June 23 to July 23. period_start = June 23, start_date = June 27, end_date = July 22.
    # Since June 22 is outside the current period, has_current_period_pmt is False.
    # But last payment date is June 22.
    # Projecting from June 22: next is July 22.
    # July 22 is inside the period [June 23, July 22], so it should be projected!
    # Let's delete the old transaction, update config's day_of_month to 22, and add the June 22 transaction.
    cur.execute("DELETE FROM transactions WHERE recurring_id = 101")
    cur.execute("UPDATE recurring_payments SET day_of_month = 22 WHERE id = 101")
    cur.execute("INSERT INTO transactions (account_id, category_id, date, description, amount, adjusted_amount, recurring_id) VALUES (1, 1, '2026-06-22', 'Spotify Early June', -169.0, -84.5, 101)")
    
    expected_b = rm.get_expected_in_range(date(2026, 6, 27), date(2026, 7, 22), period_start=date(2026, 6, 23))
    assert len(expected_b) == 1

    assert expected_b[0]["date"] == date(2026, 7, 22)



