"""Tests for the account balance (Saldo) column and the balances command."""

import os
import sqlite3
import tempfile
from datetime import date, timedelta

import pytest

from financial_categorizer.db_handler import DatabaseHandler
from financial_categorizer.importer import CSVImporter


@pytest.fixture
def db():
    handler = DatabaseHandler(":memory:")
    yield handler
    handler.disconnect()


@pytest.fixture
def importer(db):
    return CSVImporter(db)


def _write_csv(content: str) -> str:
    """Write content to a temp CSV file and return the path."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8"
    )
    f.write(content)
    f.close()
    return f.name


def _balances(db, account_name):
    """Return {date: balance} for one account, using the raw stored rows."""
    cur = db.get_cursor()
    cur.execute(
        "SELECT t.date, t.balance FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id "
        "WHERE a.name = ? ORDER BY t.date, t.id",
        (account_name,),
    )
    return {str(row[0]): row[1] for row in cur.fetchall()}


NORDEA_WITH_SALDO = (
    "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
    "2024-01-04;-500,00;1111 11 11111;;;Kortköp Store;9500,00;SEK\n"
    "2024-01-02;1000,00;;1111 11 11111;;Inbetalning;10000,00;SEK\n"
)

NORDEA_WITH_PENDING = (
    "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
    "Reserverat;-120,00;1111 11 11111;;;Reservation Kortköp Cafe;;SEK\n"
    "2024-01-02;-500,00;1111 11 11111;;;Kortköp Store;9500,00;SEK\n"
)

NORDEA_WITHOUT_SALDO = (
    "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Valuta\n"
    "2024-01-04;-500,00;1111 11 11111;;;Kortköp Store;SEK\n"
)

ICA_WITH_SALDO = (
    "Datum;Text;Typ;Budgetgrupp;Belopp;Saldo\n"
    "2024-01-04;201229 Butik;Korttransaktion;Övrigt;-200 kr;780,00\n"
)


class TestImportStoresBalance:
    def test_settled_rows_store_their_balance(self, db, importer):
        path = _write_csv(NORDEA_WITH_SALDO)
        try:
            importer.import_file(path, account_name="Test Account")
            assert _balances(db, "Test Account") == {
                "2024-01-02": 10000.00,
                "2024-01-04": 9500.00,
            }
        finally:
            os.unlink(path)

    def test_ica_export_balance_is_parsed(self, db, importer):
        path = _write_csv(ICA_WITH_SALDO)
        try:
            importer.import_file(path, account_name="ICA Account")
            assert _balances(db, "ICA Account") == {"2024-01-04": 780.00}
        finally:
            os.unlink(path)

    def test_pending_reservation_has_no_balance(self, db, importer):
        path = _write_csv(NORDEA_WITH_PENDING)
        try:
            importer.import_file(path, account_name="Test Account")
            cur = db.get_cursor()
            cur.execute(
                "SELECT t.balance, t.status FROM transactions t "
                "JOIN accounts a ON a.id = t.account_id "
                "WHERE a.name = ? AND t.status = 'pending'",
                ("Test Account",),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] is None
        finally:
            os.unlink(path)

    def test_export_without_saldo_column_still_imports(self, db, importer):
        path = _write_csv(NORDEA_WITHOUT_SALDO)
        try:
            result = importer.import_file(path, account_name="Test Account")
            assert result["imported"] == 1
            assert _balances(db, "Test Account") == {"2024-01-04": None}
        finally:
            os.unlink(path)


class TestPendingSettlementUpdatesBalance:
    def test_settled_charge_replaces_reservation_and_stores_balance(self, db, importer):
        today = date.today()
        earlier = today - timedelta(days=2)
        first = _write_csv(
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            "Reserverat;-120,00;1111 11 11111;;;Reservation Kortköp Cafe;;SEK\n"
        )
        second = _write_csv(
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            f"{today.isoformat()};-120,00;1111 11 11111;;;Kortköp Cafe;9380,00;SEK\n"
            f"{earlier.isoformat()};-500,00;1111 11 11111;;;Kortköp Store;9500,00;SEK\n"
        )
        try:
            importer.import_file(first, account_name="Test Account")
            importer.import_file(second, account_name="Test Account")

            cur = db.get_cursor()
            cur.execute(
                "SELECT COUNT(*) FROM transactions t "
                "JOIN accounts a ON a.id = t.account_id "
                "WHERE a.name = ? AND t.status = 'pending'",
                ("Test Account",),
            )
            assert cur.fetchone()[0] == 0
            assert _balances(db, "Test Account") == {
                earlier.isoformat(): 9500.00,
                today.isoformat(): 9380.00,
            }
        finally:
            os.unlink(first)
            os.unlink(second)


class TestBackfillOnReimport:
    def test_reimport_fills_balances_on_existing_rows(self, db, importer):
        # Simulate a database imported before balance parsing existed.
        without = _write_csv(NORDEA_WITHOUT_SALDO)
        with_saldo = _write_csv(NORDEA_WITH_SALDO)
        try:
            importer.import_file(without, account_name="Test Account")
            assert _balances(db, "Test Account") == {"2024-01-04": None}

            result = importer.import_file(with_saldo, account_name="Test Account")
            assert result["imported"] == 1  # only the 2024-01-02 row is new
            assert _balances(db, "Test Account") == {
                "2024-01-02": 10000.00,
                "2024-01-04": 9500.00,
            }
        finally:
            os.unlink(without)
            os.unlink(with_saldo)

    def test_reimport_does_not_overwrite_an_existing_balance(self, db, importer):
        first = _write_csv(NORDEA_WITH_SALDO)
        changed = _write_csv(
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            "2024-01-04;-500,00;1111 11 11111;;;Kortköp Store;1,00;SEK\n"
        )
        try:
            importer.import_file(first, account_name="Test Account")
            importer.import_file(changed, account_name="Test Account")
            assert _balances(db, "Test Account")["2024-01-04"] == 9500.00
        finally:
            os.unlink(first)
            os.unlink(changed)


class TestGetBalances:
    def test_returns_latest_balance_per_account(self, db, importer):
        path = _write_csv(NORDEA_WITH_SALDO)
        try:
            importer.import_file(path, account_name="Test Account")
            balances = {b["name"]: b for b in db.get_balances()}
            assert balances["Test Account"]["balance"] == 9500.00
            assert str(balances["Test Account"]["date"]) == "2024-01-04"
        finally:
            os.unlink(path)

    def test_as_of_returns_historical_balance(self, db, importer):
        path = _write_csv(NORDEA_WITH_SALDO)
        try:
            importer.import_file(path, account_name="Test Account")
            balances = {b["name"]: b for b in db.get_balances(as_of="2024-01-02")}
            assert balances["Test Account"]["balance"] == 10000.00
        finally:
            os.unlink(path)

    def test_account_without_balance_data_is_reported_as_none(self, db):
        db.add_account("Empty Account")
        balances = {b["name"]: b for b in db.get_balances()}
        assert balances["Empty Account"]["balance"] is None
        assert balances["Empty Account"]["date"] is None


class TestSchemaMigration:
    def test_balance_column_is_added_to_existing_database(self, tmp_path):
        db_path = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE transactions("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "date DATE NOT NULL,"
            "description TEXT NOT NULL,"
            "amount REAL NOT NULL,"
            "account_id INTEGER NOT NULL,"
            "source_file TEXT,"
            "imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
            "category_id INTEGER,"
            "comment TEXT,"
            "status TEXT NOT NULL DEFAULT 'settled',"
            "matched_rule_id INTEGER,"
            "adjusted_amount REAL,"
            "recurring_id INTEGER,"
            "occurrence INTEGER NOT NULL DEFAULT 1,"
            "UNIQUE(date, description, amount, account_id, status, occurrence))"
        )
        conn.execute(
            "INSERT INTO transactions (date, description, amount, account_id) "
            "VALUES ('2024-01-02', 'Legacy row', -100.0, 1)"
        )
        conn.commit()
        conn.close()

        handler = DatabaseHandler(db_path)
        try:
            cur = handler.get_cursor()
            cur.execute("PRAGMA table_info(transactions)")
            columns = [row[1] for row in cur.fetchall()]
            assert "balance" in columns

            cur.execute("SELECT balance FROM transactions WHERE description = 'Legacy row'")
            assert cur.fetchone()[0] is None
        finally:
            handler.disconnect()
