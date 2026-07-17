import pytest
from datetime import date, timedelta
from financial_categorizer.db_handler import DatabaseHandler
from financial_categorizer.categorizer import Categorizer
from financial_categorizer.recurring import RecurringManager

@pytest.fixture
def db():
    handler = DatabaseHandler(":memory:")
    yield handler
    handler.disconnect()

@pytest.fixture
def rm(db):
    return RecurringManager(db)


class TestRecurringMatching:
    def test_matches_schedule_monthly_exact(self):
        start = date(2025, 1, 15)
        # Billed monthly on day 15
        assert RecurringManager.matches_schedule(
            tx_dt=date(2025, 1, 15), start_dt=start, end_dt=None,
            interval_type="monthly", interval_value=1,
            day_of_month=15, day_of_week=None, week_of_month=None, tolerance_days=4
        ) is True

        # Within tolerance window (e.g. 18th)
        assert RecurringManager.matches_schedule(
            tx_dt=date(2025, 1, 18), start_dt=start, end_dt=None,
            interval_type="monthly", interval_value=1,
            day_of_month=15, day_of_week=None, week_of_month=None, tolerance_days=4
        ) is True

        # Outside tolerance window (e.g. 20th)
        assert RecurringManager.matches_schedule(
            tx_dt=date(2025, 1, 20), start_dt=start, end_dt=None,
            interval_type="monthly", interval_value=1,
            day_of_month=15, day_of_week=None, week_of_month=None, tolerance_days=4
        ) is False

        # Next month match (2025-02-15)
        assert RecurringManager.matches_schedule(
            tx_dt=date(2025, 2, 14), start_dt=start, end_dt=None,
            interval_type="monthly", interval_value=1,
            day_of_month=15, day_of_week=None, week_of_month=None, tolerance_days=4
        ) is True

    def test_matches_schedule_monthly_last_day(self):
        start = date(2025, 1, 31)
        # Billed monthly on the last day of month
        assert RecurringManager.matches_schedule(
            tx_dt=date(2025, 2, 28), start_dt=start, end_dt=None,
            interval_type="monthly", interval_value=1,
            day_of_month=-1, day_of_week=None, week_of_month=None, tolerance_days=4
        ) is True

    def test_matches_schedule_first_monday(self):
        start = date(2025, 1, 1)
        # Billed on first Monday of the month (2025-01-06 was first Monday)
        assert RecurringManager.matches_schedule(
            tx_dt=date(2025, 1, 6), start_dt=start, end_dt=None,
            interval_type="monthly", interval_value=1,
            day_of_month=None, day_of_week=0, week_of_month=1, tolerance_days=2
        ) is True

    def test_matches_schedule_weekly(self):
        start = date(2025, 1, 1) # Wednesday
        # Weekly, every Wednesday
        assert RecurringManager.matches_schedule(
            tx_dt=date(2025, 1, 8), start_dt=start, end_dt=None,
            interval_type="weekly", interval_value=1,
            day_of_month=None, day_of_week=None, week_of_month=None, tolerance_days=1
        ) is True

        # Shipped weekday check: Thursday the 9th
        assert RecurringManager.matches_schedule(
            tx_dt=date(2025, 1, 9), start_dt=start, end_dt=None,
            interval_type="weekly", interval_value=1,
            day_of_month=None, day_of_week=2, week_of_month=None, tolerance_days=1
        ) is True


class TestRecurringCRUD:
    def test_add_recurring(self, db, rm):
        aid = db.add_account("checking")
        cid = Categorizer(db).add_category("Media")
        rid = rm.add_recurring(
            name="Netflix", pattern="Netflix.com", interval_type="monthly",
            interval_value=1, start_date=date(2025, 1, 1),
            category_id=cid, account_id=aid
        )
        cur = db.get_cursor()
        cur.execute("SELECT name, pattern, interval_type, category_id FROM recurring_payments WHERE id = ?", (rid,))
        row = cur.fetchone()
        assert row[0] == "Netflix"
        assert row[1] == "Netflix.com"
        assert row[2] == "monthly"
        assert row[3] == cid

    def test_update_recurring(self, db, rm):
        rid = rm.add_recurring(
            name="Netflix", pattern="Netflix.com", interval_type="monthly",
            interval_value=1, start_date=date(2025, 1, 1)
        )
        success = rm.update_recurring(rid, name="Netflix Premium", pattern="netflix")
        assert success is True
        cur = db.get_cursor()
        cur.execute("SELECT name, pattern FROM recurring_payments WHERE id = ?", (rid,))
        row = cur.fetchone()
        assert row[0] == "Netflix Premium"
        assert row[1] == "netflix"

    def test_remove_recurring_soft(self, db, rm):
        rid = rm.add_recurring(
            name="Netflix", pattern="Netflix.com", interval_type="monthly",
            interval_value=1, start_date=date(2025, 1, 1)
        )
        success = rm.remove_recurring(rid, hard=False, cancel_date="2025-06-01")
        assert success is True
        cur = db.get_cursor()
        cur.execute("SELECT end_date FROM recurring_payments WHERE id = ?", (rid,))
        res = cur.fetchone()[0]
        if isinstance(res, str):
            assert res == "2025-06-01"
        else:
            assert res == date(2025, 6, 1)


class TestRecurringWorkflow:
    def test_linking_and_autoresume(self, db, rm):
        aid = db.add_account("checking")
        cid = Categorizer(db).add_category("Media")
        
        cur = db.get_cursor()
        cur.executemany("""
            INSERT INTO transactions (date, description, amount, account_id, category_id)
            VALUES (?, ?, ?, ?, ?)
        """, [
            ("2025-04-22", "Kortköp 250417 Disney Plus", -99.0, aid, cid),
            ("2025-07-04", "Kortköp 250703 DISNEY PLUS", -99.0, aid, cid),
            ("2025-11-11", "Kortköp 251110 DISNEY PLUS", -159.0, aid, cid)
        ])
        db.commit()

        # Add cancelled subscription to test resumption
        rid = rm.add_recurring(
            name="Disney Plus", pattern="Disney Plus", match_type="contains",
            interval_type="monthly", interval_value=1,
            start_date=date(2025, 4, 22), end_date=date(2025, 5, 1),
            category_id=cid, account_id=aid, tolerance_days=8
        )

        result = rm.link_transactions(dry_run=False, auto_close=False)
        
        assert len(result["linked"]) == 3
        # First transaction (April) links to Conf 1.
        # Second transaction (July) triggers 1 resumption (creates Conf 2).
        # Third transaction (November) links to the active Conf 2.
        assert len(result["resumed"]) == 1 
        
        cur.execute("SELECT id, start_date, end_date FROM recurring_payments WHERE name = 'Disney Plus'")
        records = cur.fetchall()
        # There should be 2 Disney Plus configurations in the DB now (rid and resumed rid2)
        assert len(records) == 2

    def test_auto_close_missing(self, db, rm):
        aid = db.add_account("checking")
        
        rid = rm.add_recurring(
            name="Netflix", pattern="Netflix", interval_type="monthly",
            interval_value=1, start_date=date(2025, 1, 7), account_id=aid, tolerance_days=8
        )

        cur = db.get_cursor()
        cur.executemany("""
            INSERT INTO transactions (date, description, amount, account_id)
            VALUES (?, ?, ?, ?)
        """, [
            ("2025-01-07", "Kortköp 250106 Netflix.com", -149.0, aid),
            ("2025-03-01", "Kortköp 250301 Grocery Store", -45.0, aid)
        ])
        db.commit()

        rm.link_transactions(dry_run=False, auto_close=False)

        result = rm.link_transactions(dry_run=False, auto_close=True)
        assert len(result["closed"]) == 1
        assert result["closed"][0]["id"] == rid
        
        cur.execute("SELECT end_date FROM recurring_payments WHERE id = ?", (rid,))
        res = cur.fetchone()[0]
        if isinstance(res, str):
            assert res == "2025-01-07"
        else:
            assert res == date(2025, 1, 7)

    def test_link_transactions_amount_bounds_warning(self, db, rm):
        aid = db.add_account("checking")
        
        rid = rm.add_recurring(
            name="Netflix", pattern="Netflix", interval_type="monthly",
            interval_value=1, start_date=date(2025, 1, 7), account_id=aid, tolerance_days=8,
            amount_min=100.0, amount_max=200.0
        )

        cur = db.get_cursor()
        cur.execute("""
            INSERT INTO transactions (date, description, amount, account_id)
            VALUES (?, ?, ?, ?)
        """, ("2025-01-07", "Netflix subscription", 250.0, aid))
        db.commit()

        result = rm.link_transactions(dry_run=False, auto_close=False)
        
        assert len(result["linked"]) == 0
        assert len(result["warnings"]) == 1
        w = result["warnings"][0]
        assert w["type"] == "amount_bounds"
        assert w["id"] == rid
        assert w["name"] == "Netflix"
        assert w["tx_amount"] == 250.0
        assert w["amount_min"] == 100.0
        assert w["amount_max"] == 200.0


class TestAutoDiscovery:
    def test_discover_recurring(self, db, rm):
        aid = db.add_account("checking")
        cur = db.get_cursor()
        cur.executemany("""
            INSERT INTO transactions (date, description, amount, account_id)
            VALUES (?, ?, ?, ?)
        """, [
            ("2025-01-28", "Autogiro ownit", -89.0, aid),
            ("2025-02-28", "Autogiro ownit", -89.0, aid),
            ("2025-03-28", "Autogiro ownit", -89.0, aid)
        ])
        db.commit()

        cands = rm.discover_recurring_candidates(dry_run=True)
        assert len(cands) == 1
        assert cands[0]["name"] == "Autogiro ownit"
        assert cands[0]["interval_type"] == "monthly"
        assert cands[0]["day_of_month"] == 28
        assert cands[0]["amount_min"] == -89.0

    def test_discover_recurring_drifting_days(self, db, rm):
        aid = db.add_account("checking")
        cur = db.get_cursor()
        cur.executemany("""
            INSERT INTO transactions (date, description, amount, account_id)
            VALUES (?, ?, ?, ?)
        """, [
            ("2025-08-01", "Autogiro drifting", -100.0, aid),
            ("2025-09-01", "Autogiro drifting", -100.0, aid),
            ("2025-10-01", "Autogiro drifting", -100.0, aid),
            ("2025-11-03", "Autogiro drifting", -100.0, aid),
            ("2025-12-01", "Autogiro drifting", -100.0, aid),
            ("2026-01-02", "Autogiro drifting", -100.0, aid),
            ("2026-02-02", "Autogiro drifting", -100.0, aid),
            ("2026-03-02", "Autogiro drifting", -100.0, aid),
            ("2026-03-30", "Autogiro drifting", -100.0, aid),
            ("2026-04-28", "Autogiro drifting", -100.0, aid),
            ("2026-05-28", "Autogiro drifting", -100.0, aid),
            ("2026-06-29", "Autogiro drifting", -100.0, aid)
        ])
        db.commit()

        cands = rm.discover_recurring_candidates(dry_run=True)
        drifting_cand = next((c for c in cands if c["name"] == "Autogiro drifting"), None)
        assert drifting_cand is not None
        assert drifting_cand["interval_type"] == "days"
        assert drifting_cand["interval_value"] in (30, 31)
        assert drifting_cand["day_of_month"] is None
