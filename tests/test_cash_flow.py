import datetime
import os
import sqlite3
import pytest
from financial_categorizer.db_handler import DatabaseHandler, TransferManager
from financial_categorizer.categorizer import Categorizer
from financial_categorizer.stats import Stats


def test_db_migration_rebuilds_accounts_table(tmp_path):
    # 1. Create a database with the old schema structure manually
    db_file = str(tmp_path / "old_schema.db")
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE accounts(
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT UNIQUE NOT NULL,
            type            TEXT NOT NULL DEFAULT 'personal'
                            CHECK(type IN ('personal','shared','savings','external')),
            ownership_ratio REAL NOT NULL DEFAULT 1.0
                            CHECK(ownership_ratio > 0 AND ownership_ratio <= 1.0),
            currency        TEXT NOT NULL DEFAULT 'SEK',
            description     TEXT
        )""")
    
    # Insert some accounts with the old types
    cur.execute("INSERT INTO accounts (name, type, ownership_ratio) VALUES ('My Personal', 'personal', 1.0)")
    cur.execute("INSERT INTO accounts (name, type, ownership_ratio) VALUES ('Our Joint', 'shared', 0.5)")
    cur.execute("INSERT INTO accounts (name, type, ownership_ratio) VALUES ('My Savings', 'savings', 1.0)")
    cur.execute("INSERT INTO accounts (name, type, ownership_ratio) VALUES ('Brokerage External', 'external', 1.0)")
    conn.commit()
    conn.close()

    # 2. Instantiate DatabaseHandler (triggers migrations)
    db = DatabaseHandler(db_file)
    try:
        # Verify the migration converted types and added cash_neutral column
        acct1 = db.get_account_by_name("My Personal")
        assert acct1["type"] == "tracked"
        assert acct1["cash_neutral"] == 0

        acct2 = db.get_account_by_name("Our Joint")
        assert acct2["type"] == "tracked"
        assert acct2["cash_neutral"] == 0

        acct3 = db.get_account_by_name("My Savings")
        assert acct3["type"] == "external"
        assert acct3["cash_neutral"] == 0

        acct4 = db.get_account_by_name("Brokerage External")
        assert acct4["type"] == "external"
        assert acct4["cash_neutral"] == 0
    finally:
        db.disconnect()


def test_cash_flow_calculations():
    db = DatabaseHandler(":memory:")
    try:
        # Create accounts
        checking_id = db.add_account("Checking", type="tracked", ownership_ratio=1.0)
        joint_id = db.add_account("Joint Checking", type="tracked", ownership_ratio=0.5)
        
        # Savings neutral, Brokerage non-neutral (defaults to 0)
        savings_id = db.add_account("Savings", type="external", ownership_ratio=1.0, cash_neutral=1)
        brokerage_id = db.add_account("Brokerage", type="external", ownership_ratio=1.0, cash_neutral=0)

        # Create categories
        cat = Categorizer(db)
        cat_salary = cat.add_category("Salary", category_type="income")
        cat_groceries = cat.add_category("Groceries", category_type="expense")
        cat_transfer = cat.add_category("Transfers", category_type="transfer")

        cur = db.get_cursor()

        # Date: 2026-06-15
        # 1. Income on Checking: +30,000 SEK
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id, category_id) VALUES (?, ?, ?, ?, ?)",
            (datetime.date(2026, 6, 15), "Salary paycheck", 30000.0, checking_id, cat_salary)
        )
        # 2. Groceries on Joint: -1,000 SEK (ownership 0.5 -> -500 SEK)
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id, category_id) VALUES (?, ?, ?, ?, ?)",
            (datetime.date(2026, 6, 16), "ICA groceries", -1000.0, joint_id, cat_groceries)
        )
        # 3. Transfer from Checking to Joint Checking (tracked to tracked -> neutral)
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id, category_id) VALUES (?, ?, ?, ?, ?)",
            (datetime.date(2026, 6, 17), "Transfer checking-joint", -5000.0, checking_id, cat_transfer)
        )
        t_to_joint_id = cur.lastrowid
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id, category_id) VALUES (?, ?, ?, ?, ?)",
            (datetime.date(2026, 6, 17), "Transfer checking-joint", 5000.0, joint_id, cat_transfer)
        )
        t_from_checking_id = cur.lastrowid

        # 4. Transfer to Savings (tracked to external neutral -> neutral)
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id, category_id) VALUES (?, ?, ?, ?, ?)",
            (datetime.date(2026, 6, 18), "Transfer to savings", -3000.0, checking_id, cat_transfer)
        )
        t_to_savings_id = cur.lastrowid

        # 5. Transfer to Brokerage (tracked to external non-neutral -> outflow)
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id, category_id) VALUES (?, ?, ?, ?, ?)",
            (datetime.date(2026, 6, 19), "Transfer to brokerage", -2000.0, checking_id, cat_transfer)
        )
        t_to_brokerage_id = cur.lastrowid

        # 6. Untracked Transfer (tracked to untracked -> outflow)
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id, category_id) VALUES (?, ?, ?, ?, ?)",
            (datetime.date(2026, 6, 20), "Transfer untracked", -1000.0, checking_id, cat_transfer)
        )
        db.commit()

        # Link transactions
        tm = TransferManager(db)
        tm.link_transactions(t_to_joint_id, t_from_checking_id, "internal_transfer")
        tm.link_transactions(t_to_savings_id, None, "external_transfer", to_account_id=savings_id)
        tm.link_transactions(t_to_brokerage_id, None, "external_transfer", to_account_id=brokerage_id)

        # Recalculate adjusted amounts
        db.recalculate_adjusted_amounts()

        stats = Stats(db)
        cf = stats.cash_flow_summary(period_type="calendar")

        assert len(cf) == 1
        summary = cf[0]
        assert summary["period"] == "2026-06"
        # Operating: 30000.0 (Salary) + (-1000.0 * 0.5) (Groceries) = 29500.0
        assert summary["operating"] == 29500.0
        # Transfers:
        # - Transfer checking-joint: neutral (0.0)
        # - Transfer to savings (neutral): neutral (0.0)
        # - Transfer to brokerage (non-neutral): -2000.0 * 1.0 = -2000.0
        # - Untracked transfer (non-neutral): -1000.0 * 1.0 = -1000.0
        # Total transfers: -3000.0
        assert summary["transfers"] == -3000.0
        assert summary["net"] == 26500.0

        # Change Brokerage to neutral, and verify transfers change
        db.update_account(brokerage_id, cash_neutral=1)
        cf2 = stats.cash_flow_summary(period_type="calendar")
        summary2 = cf2[0]
        # Now only the untracked transfer (-1000.0) should remain as outflow
        assert summary2["transfers"] == -1000.0
        assert summary2["net"] == 28500.0
    finally:
        db.disconnect()


def test_cli_cash_neutral_and_cashflow(tmp_path, monkeypatch, capsys):
    import sys
    from cli import main
    
    db_file = str(tmp_path / "cli_test.db")
    db = DatabaseHandler(db_file)
    db.disconnect()

    # 1. Add account with cash_neutral = 1
    monkeypatch.setattr(
        sys, "argv",
        ["cli.py", "--db", db_file, "add-account", "Savings", "--type", "external", "--cash-neutral", "true"]
    )
    main()
    
    db = DatabaseHandler(db_file)
    try:
        acct = db.get_account_by_name("Savings")
        assert acct["type"] == "external"
        assert acct["cash_neutral"] == 1
    finally:
        db.disconnect()

    # 2. Update account to cash_neutral = 0
    monkeypatch.setattr(
        sys, "argv",
        ["cli.py", "--db", db_file, "update-account", "1", "--cash-neutral", "false"]
    )
    main()

    db = DatabaseHandler(db_file)
    try:
        acct = db.get_account_by_name("Savings")
        assert acct["cash_neutral"] == 0
    finally:
        db.disconnect()

    # 3. Create tracked account and add transfer
    db = DatabaseHandler(db_file)
    try:
        checking_id = db.add_account("Checking", type="tracked", ownership_ratio=1.0)
        cur = db.get_cursor()
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
            (datetime.date(2026, 6, 15), "Transfer out", -500.0, checking_id)
        )
        db.commit()
        db.recalculate_adjusted_amounts()
    finally:
        db.disconnect()

    # 4. View cashflow report
    monkeypatch.setattr(
        sys, "argv",
        ["cli.py", "--db", db_file, "stats-cashflow", "--period-type", "calendar"]
    )
    capsys.readouterr()  # clear buffer
    main()
    captured = capsys.readouterr()
    assert "2026-06" in captured.out
    assert "-500.00" in captured.out
