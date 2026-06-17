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


def test_db_migration_foreign_keys_integrity(tmp_path):
    # Create database with old schema structure manually, including FK constraints
    db_file = str(tmp_path / "old_schema_fk.db")
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON;")
    
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
    cur.execute("""
        CREATE TABLE categories(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE NOT NULL,
            parent_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
            category_type TEXT NOT NULL DEFAULT 'expense'
                          CHECK(category_type IN ('income','expense','transfer')),
            description TEXT
        )""")
    cur.execute("""
        CREATE TABLE transactions(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        DATE NOT NULL,
            description TEXT NOT NULL,
            amount      REAL NOT NULL,
            account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
            source_file TEXT,
            imported_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
            comment     TEXT,
            status      TEXT NOT NULL DEFAULT 'settled'
                        CHECK(status IN ('pending','settled')),
            adjusted_amount REAL,
            UNIQUE(date, description, amount, account_id, status)
        )""")
    
    # Insert some initial data
    cur.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'Checking', 'personal')")
    cur.execute("INSERT INTO categories (id, name, category_type) VALUES (1, 'Food', 'expense')")
    cur.execute("INSERT INTO transactions (date, description, amount, account_id, category_id) VALUES ('2026-06-15', 'ICA', -100.0, 1, 1)")
    conn.commit()
    conn.close()

    # Connect using DatabaseHandler, which triggers migration
    db = DatabaseHandler(db_file)
    try:
        # Check that migration converted accounts successfully
        acct = db.get_account(1)
        assert acct["type"] == "tracked"
        assert acct["cash_neutral"] == 0

        # Verify that foreign key constraints on the rebuilt accounts table are completely intact and working.
        # Try to insert a new transaction referencing account_id = 1.
        cur = db.get_cursor()
        cur.execute("PRAGMA foreign_keys = ON;")
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id, category_id) VALUES (?, ?, ?, ?, ?)",
            (datetime.date(2026, 6, 16), "Coop", -50.0, 1, 1)
        )
        db.commit()

        # Check transaction was inserted successfully
        cur.execute("SELECT count(*) FROM transactions")
        assert cur.fetchone()[0] == 2
    finally:
        db.disconnect()


def test_db_migration_rebuilds_corrupted_v1_1_0_db(tmp_path):
    # Create a database matching the corrupted v1.1.0 schema:
    # accounts table has the cash_neutral column, but other tables point to accounts_old
    db_file = str(tmp_path / "corrupted_v1_1_0.db")
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = OFF;")
    
    # Rebuild standard tables except with references pointing to accounts_old (representing the corrupt schema state)
    cur.execute("""
        CREATE TABLE accounts(
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT UNIQUE NOT NULL,
            type            TEXT NOT NULL DEFAULT 'tracked'
                            CHECK(type IN ('tracked','external')),
            ownership_ratio REAL NOT NULL DEFAULT 1.0
                            CHECK(ownership_ratio > 0 AND ownership_ratio <= 1.0),
            currency        TEXT NOT NULL DEFAULT 'SEK',
            description     TEXT,
            cash_neutral    INTEGER NOT NULL DEFAULT 0 CHECK(cash_neutral IN (0, 1))
        )""")
        
    cur.execute("""
        CREATE TABLE categories(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE NOT NULL,
            parent_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
            category_type TEXT NOT NULL DEFAULT 'expense'
                          CHECK(category_type IN ('income','expense','transfer')),
            description TEXT,
            associated_account_id INTEGER REFERENCES accounts_old(id) ON DELETE SET NULL
        )""")
        
    cur.execute("""
        CREATE TABLE transactions(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        DATE NOT NULL,
            description TEXT NOT NULL,
            amount      REAL NOT NULL,
            account_id  INTEGER NOT NULL REFERENCES accounts_old(id) ON DELETE RESTRICT,
            source_file TEXT,
            imported_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
            comment     TEXT,
            status      TEXT NOT NULL DEFAULT 'settled'
                        CHECK(status IN ('pending','settled')),
            adjusted_amount REAL,
            UNIQUE(date, description, amount, account_id, status)
        )""")
        
    cur.execute("""
        CREATE TABLE transaction_links(
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            from_transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
            to_transaction_id   INTEGER REFERENCES transactions(id) ON DELETE CASCADE,
            link_type           TEXT NOT NULL CHECK(link_type IN ('internal_transfer','external_transfer','reimbursement')),
            ratio               REAL NOT NULL DEFAULT 1.0
                                    CHECK(ratio > 0 AND ratio <= 1.0),
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            comment             TEXT,
            to_account_id       INTEGER REFERENCES accounts_old(id) ON DELETE SET NULL
        )""")

    # Insert some initial data
    cur.execute("INSERT INTO accounts (id, name, type, cash_neutral) VALUES (1, 'Checking', 'tracked', 0)")
    cur.execute("INSERT INTO categories (id, name, category_type, associated_account_id) VALUES (1, 'Food', 'expense', 1)")
    cur.execute("INSERT INTO transactions (id, date, description, amount, account_id, category_id) VALUES (1, '2026-06-15', 'ICA', -100.0, 1, 1)")
    cur.execute("INSERT INTO transaction_links (from_transaction_id, link_type, to_account_id) VALUES (1, 'external_transfer', 1)")
    conn.commit()
    conn.close()

    # Now instantiate DatabaseHandler, which triggers the self-healing migration
    db = DatabaseHandler(db_file)
    try:
        # Check that we can insert a new transaction referencing account_id = 1 without foreign key errors
        cur = db.get_cursor()
        cur.execute("PRAGMA foreign_keys = ON;")
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id, category_id) VALUES (?, ?, ?, ?, ?)",
            (datetime.date(2026, 6, 16), "Coop", -50.0, 1, 1)
        )
        db.commit()

        # Check transaction was inserted successfully
        cur.execute("SELECT count(*) FROM transactions")
        assert cur.fetchone()[0] == 2
        
        # Verify that schema SQL no longer contains any reference to accounts_old
        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND sql LIKE '%accounts_old%'")
        assert len(cur.fetchall()) == 0
    finally:
        db.disconnect()

