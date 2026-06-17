import datetime
import pytest
from financial_categorizer.db_handler import DatabaseHandler, TransferManager
from financial_categorizer.categorizer import Categorizer
from financial_categorizer.stats import Stats


def test_manual_link_to_external_account():
    db = DatabaseHandler(":memory:")
    try:
        # Create origin accounts (personal and shared)
        checking_id = db.add_account("Checking", type="personal", ownership_ratio=1.0)
        shared_id = db.add_account("Shared Joint", type="shared", ownership_ratio=0.5)

        # Create external accounts
        avanza_id = db.add_account("Avanza", type="external")
        nordea_savings_id = db.add_account("Nordea Savings", type="savings")

        # Create transactions
        cur = db.get_cursor()
        # Outflow from personal Checking: -10000 SEK
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
            (datetime.date(2026, 6, 1), "AVANZA OUT", -10000.0, checking_id)
        )
        t1_id = cur.lastrowid

        # Outflow from shared Joint: -5000 SEK
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
            (datetime.date(2026, 6, 2), "SPARA OUT", -5000.0, shared_id)
        )
        t2_id = cur.lastrowid
        db.commit()

        # Manually link transactions to external accounts
        tm = TransferManager(db)
        tm.link_transactions(t1_id, None, "external_transfer", to_account_id=avanza_id)
        tm.link_transactions(t2_id, None, "external_transfer", to_account_id=nordea_savings_id)

        stats = Stats(db)
        summary = stats.external_transfers_summary()

        # Check summary results:
        # -10000.0 * 1.0 * -1 = +10000.0 to Avanza
        # -5000.0 * 0.5 * -1 = +2500.0 to Nordea Savings
        avanza_summary = next(s for s in summary if s["account_name"] == "Avanza")
        nordea_summary = next(s for s in summary if s["account_name"] == "Nordea Savings")

        assert avanza_summary["net_transferred"] == 10000.0
        assert nordea_summary["net_transferred"] == 2500.0
    finally:
        db.disconnect()


def test_auto_link_via_categorization():
    db = DatabaseHandler(":memory:")
    try:
        checking_id = db.add_account("Checking", type="personal", ownership_ratio=1.0)
        avanza_id = db.add_account("Avanza Brokerage", type="external")

        # Create category with associated_account_id and type "transfer"
        cat = Categorizer(db)
        cat_id = cat.add_category(
            "Brokerage Transfer", parent_id=None, category_type="transfer",
            description="Transfer to Avanza", associated_account_id=avanza_id
        )

        # Create transaction
        cur = db.get_cursor()
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
            (datetime.date(2026, 6, 10), "AVANZA TRANSFER", -3000.0, checking_id)
        )
        t_id = cur.lastrowid
        db.commit()

        # Categorizing should trigger auto-link
        cat.add_manual_match(t_id, cat_id)

        # Check that link was created and associated with Avanza Brokerage
        tm = TransferManager(db)
        links = tm.get_links(t_id)
        assert len(links) == 1
        assert links[0]["link_type"] == "external_transfer"
        assert links[0]["to_account_id"] == avanza_id

        # Check stats
        stats = Stats(db)
        summary = stats.external_transfers_summary()
        assert len(summary) == 1
        assert summary[0]["account_name"] == "Avanza Brokerage"
        assert summary[0]["net_transferred"] == 3000.0
    finally:
        db.disconnect()


def test_no_double_counting():
    db = DatabaseHandler(":memory:")
    try:
        checking_id = db.add_account("Checking", type="personal", ownership_ratio=1.0)
        avanza_id = db.add_account("Avanza Brokerage", type="external")

        cat = Categorizer(db)
        cat_id = cat.add_category(
            "Brokerage Transfer", parent_id=None, category_type="transfer",
            description="Transfer to Avanza", associated_account_id=avanza_id
        )

        cur = db.get_cursor()
        cur.execute(
            "INSERT INTO transactions (date, description, amount, account_id) VALUES (?, ?, ?, ?)",
            (datetime.date(2026, 6, 10), "AVANZA TRANSFER", -3000.0, checking_id)
        )
        t_id = cur.lastrowid
        db.commit()

        # 1. Categorize it (triggers auto-link)
        cat.add_manual_match(t_id, cat_id)

        # 2. Add an explicit manual link for the same transaction (e.g. updating target account)
        # First check that one link exists
        tm = TransferManager(db)
        links = tm.get_links(t_id)
        assert len(links) == 1

        # Check stats
        stats = Stats(db)
        summary = stats.external_transfers_summary()
        assert len(summary) == 1
        assert summary[0]["net_transferred"] == 3000.0
    finally:
        db.disconnect()
