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
            cur.execute("SELECT DISTINCT account FROM transactions")
            # Should use the filename without extension
            accounts = [row[0] for row in cur.fetchall()]
            assert len(accounts) == 1
            # Filename is a temp file, just check it's not empty
            assert accounts[0] != ""
        finally:
            os.unlink(path)

    def test_custom_account_name(self, importer, db):
        path = _write_csv(NORDEA_CSV)
        try:
            importer.import_file(path, account_name="my_checking")
            cur = db.get_cursor()
            cur.execute("SELECT DISTINCT account FROM transactions")
            assert cur.fetchone()[0] == "my_checking"
        finally:
            os.unlink(path)
