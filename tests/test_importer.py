"""Tests for financial_categorizer.importer"""

import os
import tempfile
import pytest
from datetime import date

from financial_categorizer.db_handler import DatabaseHandler
from financial_categorizer.importer import CSVImporter, parse_date, parse_amount, detect_format


@pytest.fixture
def db():
    # :memory: handler stays connected from __init__
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


NORDEA_CSV = (
    "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
    "2024-01-04;-500,00;1111 11 11111;;;A1;999,99;SEK\n"
    "2024-01-02;1000,00;;1111 11 11111;;SWISH FRÅN Namn;999,99;SEK\n"
    "2024-01-01;-10,00;1111 11 11111;;;Lorem Ipsum;999,99;SEK\n"
)

ICA_CSV = (
    "Datum;Text;Typ;Budgetgrupp;Belopp;Saldo\n"
    "2024-01-04;201229 A1;Korttransaktion;Övrigt;-200 kr;999,99\n"
    "2024-01-04;210101 B1;Korttransaktion;Övrigt;-100 kr;999,99\n"
)


class TestParseDate:
    def test_dash_format(self):
        assert parse_date("2024-01-15") == date(2024, 1, 15)

    def test_slash_format(self):
        assert parse_date("2024/01/15") == date(2024, 1, 15)

    def test_dot_format(self):
        assert parse_date("2024.01.15") == date(2024, 1, 15)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_date("not-a-date")


class TestParseAmount:
    def test_negative_comma(self):
        assert parse_amount("-500,00") == -500.0

    def test_positive(self):
        assert parse_amount("1000,00") == 1000.0

    def test_with_kr(self):
        assert parse_amount("-200 kr") == -200.0

    def test_with_spaces(self):
        assert parse_amount("-1 000,00 kr") == -1000.0


class TestDetectFormat:
    def test_nordea(self):
        header = "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta".split(";")
        assert detect_format(header) == "nordea"

    def test_ica(self):
        header = "Datum;Text;Typ;Budgetgrupp;Belopp;Saldo".split(";")
        assert detect_format(header) == "ica"

    def test_unknown(self):
        header = "Foo;Bar;Baz".split(";")
        assert detect_format(header) is None


class TestImportNordea:
    def test_import_rows(self, importer, db):
        path = _write_csv(NORDEA_CSV)
        try:
            result = importer.import_file(path, account_name="nordea_checking")
            assert result["imported"] == 3
            assert result["skipped"] == 0
            assert result["errors"] == 0

            cur = db.get_cursor()
            cur.execute("SELECT COUNT(*) FROM transactions")
            assert cur.fetchone()[0] == 3
        finally:
            os.unlink(path)

    def test_amounts_parsed_correctly(self, importer, db):
        path = _write_csv(NORDEA_CSV)
        try:
            importer.import_file(path)
            cur = db.get_cursor()
            cur.execute(
                "SELECT amount FROM transactions WHERE description = 'A1'"
            )
            assert cur.fetchone()[0] == -500.0

            cur.execute(
                "SELECT amount FROM transactions WHERE description = 'SWISH FRÅN Namn'"
            )
            assert cur.fetchone()[0] == 1000.0
        finally:
            os.unlink(path)

    def test_dates_parsed_correctly(self, importer, db):
        path = _write_csv(NORDEA_CSV)
        try:
            importer.import_file(path)
            cur = db.get_cursor()
            cur.execute("SELECT date FROM transactions ORDER BY date")
            dates = [row[0] for row in cur.fetchall()]
            assert dates == [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 4)]
        finally:
            os.unlink(path)


class TestImportICA:
    def test_import_rows(self, importer, db):
        path = _write_csv(ICA_CSV)
        try:
            result = importer.import_file(path, account_name="ica_card")
            assert result["imported"] == 2
            assert result["errors"] == 0
        finally:
            os.unlink(path)

    def test_ica_amounts(self, importer, db):
        path = _write_csv(ICA_CSV)
        try:
            importer.import_file(path)
            cur = db.get_cursor()
            cur.execute("SELECT amount FROM transactions ORDER BY amount")
            amounts = [row[0] for row in cur.fetchall()]
            assert amounts == [-200.0, -100.0]
        finally:
            os.unlink(path)


class TestDeduplication:
    def test_duplicate_rows_skipped(self, importer, db):
        path = _write_csv(NORDEA_CSV)
        try:
            result1 = importer.import_file(path)
            result2 = importer.import_file(path)
            assert result1["imported"] == 3
            assert result2["imported"] == 0
            assert result2["skipped"] == 3

            cur = db.get_cursor()
            cur.execute("SELECT COUNT(*) FROM transactions")
            assert cur.fetchone()[0] == 3
        finally:
            os.unlink(path)


class TestAccountName:
    def test_default_from_filename(self, importer, db):
        path = _write_csv(NORDEA_CSV)
        try:
            importer.import_file(path)
            cur = db.get_cursor()
            cur.execute(
                "SELECT DISTINCT a.name FROM transactions t "
                "JOIN accounts a ON t.account_id = a.id"
            )
            accounts = [row[0] for row in cur.fetchall()]
            assert len(accounts) == 1
            assert accounts[0] != ""
        finally:
            os.unlink(path)

    def test_custom_account_name(self, importer, db):
        path = _write_csv(NORDEA_CSV)
        try:
            importer.import_file(path, account_name="my_checking")
            cur = db.get_cursor()
            cur.execute(
                "SELECT DISTINCT a.name FROM transactions t "
                "JOIN accounts a ON t.account_id = a.id"
            )
            assert cur.fetchone()[0] == "my_checking"
        finally:
            os.unlink(path)

    def test_auto_creates_account(self, importer, db):
        path = _write_csv(NORDEA_CSV)
        try:
            importer.import_file(path, account_name="auto_account")
            acct = db.get_account_by_name("auto_account")
            assert acct is not None
            assert acct["type"] == "personal"
        finally:
            os.unlink(path)

    def test_no_auto_account_raises(self, importer, db):
        path = _write_csv(NORDEA_CSV)
        try:
            with pytest.raises(ValueError, match="not found"):
                importer.import_file(path, account_name="missing",
                                     auto_create_account=False)
        finally:
            os.unlink(path)


class TestPendingTransactions:
    def test_pending_imported_with_today_date(self, importer, db):
        pending_csv = (
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            "Reserverat;-500,00;1111 11 11111;;;A1;999,99;SEK\n"
        )
        path = _write_csv(pending_csv)
        try:
            result = importer.import_file(path, account_name="test")
            assert result["imported"] == 1

            cur = db.get_cursor()
            cur.execute(
                "SELECT date, status FROM transactions WHERE description = 'A1'"
            )
            row = cur.fetchone()
            assert row[0] == date.today()
            assert row[1] == "pending"
        finally:
            os.unlink(path)

    def test_settled_updates_pending(self, importer, db):
        """A settled transaction with same description+account updates the pending one."""
        today = date.today().isoformat()
        pending_csv = (
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            "Reserverat;-500,00;1111 11 11111;;;A1;999,99;SEK\n"
        )
        settled_csv = (
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            f"{today};-500,00;1111 11 11111;;;A1;999,99;SEK\n"
        )
        pending_path = _write_csv(pending_csv)
        settled_path = _write_csv(settled_csv)
        try:
            importer.import_file(pending_path, account_name="test")
    
            cur = db.get_cursor()
            cur.execute("SELECT COUNT(*) FROM transactions WHERE status = 'pending'")
            assert cur.fetchone()[0] == 1
    
            result = importer.import_file(settled_path, account_name="test")
            assert result["settled_pending"] == 1
            assert result["imported"] == 1
    
            cur = db.get_cursor()
            cur.execute(
                "SELECT date, amount, status FROM transactions "
                "WHERE description = 'A1'"
            )
            row = cur.fetchone()
            assert row[0] == date.today()
            assert row[1] == -500.0
            assert row[2] == "settled"
    
            # No more pending rows
            cur.execute("SELECT COUNT(*) FROM transactions WHERE status = 'pending'")
            assert cur.fetchone()[0] == 0
        finally:
            os.unlink(pending_path)
            os.unlink(settled_path)

    def test_pending_no_match_different_account(self, importer, db):
        """Pending on one account doesn't match settled on another."""
        today = date.today().isoformat()
        pending_csv = (
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            "Reserverat;-500,00;1111 11 11111;;;A1;999,99;SEK\n"
        )
        settled_csv = (
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            f"{today};-500,00;1111 11 11111;;;A1;999,99;SEK\n"
        )
        pending_path = _write_csv(pending_csv)
        settled_path = _write_csv(settled_csv)
        try:
            importer.import_file(pending_path, account_name="account_a")
            result = importer.import_file(settled_path, account_name="account_b")
    
            assert result["settled_pending"] == 0
    
            cur = db.get_cursor()
            cur.execute("SELECT COUNT(*) FROM transactions")
            assert cur.fetchone()[0] == 2  # 1 + 1, no merge
        finally:
            os.unlink(pending_path)
            os.unlink(settled_path)

    def test_result_includes_settled_pending_count(self, importer, db):
        path = _write_csv(NORDEA_CSV)
        try:
            result = importer.import_file(path, account_name="test")
            assert result["settled_pending"] == 0
        finally:
            os.unlink(path)

    def test_settled_updates_pending_substring(self, importer, db):
        """A settled transaction with prefix matches pending with reservation prefix."""
        today = date.today().isoformat()
        pending_csv = (
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            "Reserverat;-51,00;1111 11 11111;;;Reservation Kortköp FABRIQUE STOCKH;999,99;SEK\n"
        )
        settled_csv = (
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            f"{today};-51,00;1111 11 11111;;;Kortköp 260504 FABRIQUE STOCKHOLM I;999,99;SEK\n"
        )
        p_path = _write_csv(pending_csv)
        s_path = _write_csv(settled_csv)
        try:
            importer.import_file(p_path, account_name="test")
            result = importer.import_file(s_path, account_name="test")
            assert result["settled_pending"] == 1
            assert result["imported"] == 1

            cur = db.get_cursor()
            cur.execute("SELECT description, amount, status FROM transactions")
            row = cur.fetchone()
            assert row[0] == "Kortköp 260504 FABRIQUE STOCKHOLM I"
            assert row[1] == -51.0
            assert row[2] == "settled"
        finally:
            os.unlink(p_path)
            os.unlink(s_path)

    def test_settled_updates_pending_rounding(self, importer, db):
        """A settled transaction updates pending even with up to 1.0 SEK rounding difference."""
        today = date.today().isoformat()
        pending_csv = (
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            "Reserverat;-33,86;1111 11 11111;;;Reservation Kortköp Coop Reimershol;999,99;SEK\n"
        )
        settled_csv = (
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            f"{today};-33,00;1111 11 11111;;;Kortköp 260409 COOP REIMERSHOLME;999,99;SEK\n"
        )
        p_path = _write_csv(pending_csv)
        s_path = _write_csv(settled_csv)
        try:
            importer.import_file(p_path, account_name="test")
            result = importer.import_file(s_path, account_name="test")
            assert result["settled_pending"] == 1

            cur = db.get_cursor()
            cur.execute("SELECT amount, status FROM transactions")
            row = cur.fetchone()
            assert row[0] == -33.0
            assert row[1] == "settled"
        finally:
            os.unlink(p_path)
            os.unlink(s_path)

    def test_settled_no_match_large_difference(self, importer, db):
        """A settled transaction does not match pending if amount difference is > 1.0 SEK."""
        today = date.today().isoformat()
        pending_csv = (
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            "Reserverat;-33,86;1111 11 11111;;;Reservation Kortköp Coop Reimershol;999,99;SEK\n"
        )
        settled_csv = (
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            f"{today};-10,44;1111 11 11111;;;Kortköp 260409 COOP REIMERSHOLME;999,99;SEK\n"
        )
        p_path = _write_csv(pending_csv)
        s_path = _write_csv(settled_csv)
        try:
            importer.import_file(p_path, account_name="test")
            result = importer.import_file(s_path, account_name="test")
            assert result["settled_pending"] == 0
            assert result["imported"] == 1

            cur = db.get_cursor()
            cur.execute("SELECT COUNT(*) FROM transactions")
            assert cur.fetchone()[0] == 2
        finally:
            os.unlink(p_path)
            os.unlink(s_path)

    def test_settled_updates_pending_conflict_deletes_pending(self, importer, db):
        """If updating a pending transaction to settled fails because the settled version
        already exists, the pending transaction is deleted."""
        today = date.today().isoformat()
        pending_csv = (
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            "Reserverat;-33,86;1111 11 11111;;;Reservation Kortköp Coop Reimershol;999,99;SEK\n"
        )
        settled_csv = (
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            f"{today};-33,86;1111 11 11111;;;Kortköp 260409 COOP REIMERSHOLME;999,99;SEK\n"
        )
        p_path = _write_csv(pending_csv)
        s_path = _write_csv(settled_csv)
        try:
            # Import pending
            importer.import_file(p_path, account_name="test")
            # Import settled first directly (to simulate it already existing in DB)
            importer.import_file(s_path, account_name="test")
            
            # Now let's try to import the settled transaction again.
            # The pending transaction should be deleted because the settled version already exists.
            result = importer.import_file(s_path, account_name="test")
            assert result["settled_pending"] == 0
            assert result["skipped"] == 1
            
            cur = db.get_cursor()
            cur.execute("SELECT status, COUNT(*) FROM transactions GROUP BY status")
            counts = dict(cur.fetchall())
            assert counts.get("pending", 0) == 0
            assert counts.get("settled", 0) == 1
        finally:
            os.unlink(p_path)
            os.unlink(s_path)

    def test_pending_skipped_if_settled_exists(self, importer, db):
        """If importing a pending transaction but the settled version already exists,
        the pending transaction is skipped."""
        today = date.today().isoformat()
        settled_csv = (
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            f"{today};-33,86;1111 11 11111;;;Kortköp 260409 COOP REIMERSHOLME;999,99;SEK\n"
        )
        pending_csv = (
            "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
            "Reserverat;-33,86;1111 11 11111;;;Reservation Kortköp Coop Reimershol;999,99;SEK\n"
        )
        s_path = _write_csv(settled_csv)
        p_path = _write_csv(pending_csv)
        try:
            # Import settled first
            importer.import_file(s_path, account_name="test")
            # Try to import pending next. It should be skipped because the settled version exists.
            result = importer.import_file(p_path, account_name="test")
            assert result["imported"] == 0
            assert result["skipped"] == 1
            
            cur = db.get_cursor()
            cur.execute("SELECT status, COUNT(*) FROM transactions GROUP BY status")
            counts = dict(cur.fetchall())
            assert counts.get("pending", 0) == 0
            assert counts.get("settled", 0) == 1
        finally:
            os.unlink(p_path)
            os.unlink(s_path)
