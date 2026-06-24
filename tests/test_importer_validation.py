import os
import tempfile
import pytest
from datetime import date

from financial_categorizer.db_handler import DatabaseHandler
from financial_categorizer.importer import (
    CSVImporter,
    extract_account_identifier,
    is_identifier_match,
)


@pytest.fixture
def db():
    handler = DatabaseHandler(":memory:")
    yield handler
    handler.disconnect()


@pytest.fixture
def importer(db):
    return CSVImporter(db)


def _write_csv(content: str) -> str:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8"
    )
    f.write(content)
    f.close()
    return f.name


NORDEA_CSV_34138 = (
    "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
    "2024-01-04;-500,00;3266 01 34138;;;A1;999,99;SEK\n"
)

NORDEA_CSV_34189 = (
    "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
    "2024-01-04;-500,00;3266 01 34189;;;B1;999,99;SEK\n"
)


class TestExtractAccountIdentifier:
    def test_extract_from_csv(self):
        path = _write_csv(NORDEA_CSV_34138)
        try:
            assert extract_account_identifier(path) == "32660134138"
        finally:
            os.unlink(path)

    def test_extract_from_filename_clearing(self):
        path = os.path.join(tempfile.gettempdir(), "PERSONKONTO_3266_01_34138_-_2026-06-22.csv")
        with open(path, "w") as f:
            f.write("Datum;Text;Typ;Budgetgrupp;Belopp;Saldo\n")
        try:
            assert extract_account_identifier(path) == "32660134138"
        finally:
            os.unlink(path)

    def test_extract_from_filename_digits_fallback(self):
        path = os.path.join(tempfile.gettempdir(), "personligt_34138_new.csv")
        with open(path, "w") as f:
            f.write("Datum;Text;Typ;Budgetgrupp;Belopp;Saldo\n")
        try:
            assert extract_account_identifier(path) == "34138"
        finally:
            os.unlink(path)

    def test_no_identifier(self):
        path = _write_csv("Datum;Text;Typ;Budgetgrupp;Belopp;Saldo\n")
        try:
            assert extract_account_identifier(path) is None
        finally:
            os.unlink(path)


class TestIdentifierMatch:
    def test_substring_matches(self):
        assert is_identifier_match("34138", "32660134138") is True
        assert is_identifier_match("32660134138", "34138") is True
        assert is_identifier_match("34138", "34138") is True
        assert is_identifier_match("34138", "34189") is False


class TestAutoDetectionAndValidation:
    def test_auto_detect_from_history(self, importer, db):
        # 1. Setup accounts
        db.ensure_account("Personligt")
        db.ensure_account("Gemensamt")

        # 2. Import file 1 to Personligt with explicit account name
        path1 = os.path.join(tempfile.gettempdir(), "PERSONKONTO_3266_01_34138_-_2026-06-01.csv")
        with open(path1, "w", encoding="utf-8") as f:
            f.write(NORDEA_CSV_34138)
        
        try:
            importer.import_file(path1, account_name="Personligt")
            
            # 3. Import file 2 with account_name=None (auto-detect)
            path2 = os.path.join(tempfile.gettempdir(), "PERSONKONTO_3266_01_34138_-_2026-06-15.csv")
            with open(path2, "w", encoding="utf-8") as f:
                NORDEA_CSV_34138_diff = NORDEA_CSV_34138.replace("A1", "A2")
                f.write(NORDEA_CSV_34138_diff)
                
            try:
                result = importer.import_file(path2, account_name=None)
                assert result["imported"] == 1
                
                # Check that it was auto-detected as "Personligt"
                cur = db.get_cursor()
                cur.execute("SELECT account_id FROM transactions WHERE description = 'A2'")
                acct_id = cur.fetchone()[0]
                personligt_acct = db.get_account_by_name("Personligt")
                assert acct_id == personligt_acct["id"]
            finally:
                os.unlink(path2)
        finally:
            os.unlink(path1)

    def test_auto_detect_from_metadata(self, importer, db):
        # Setup account with the identifier in the name
        db.ensure_account("Gemensamt 34189")

        path = os.path.join(tempfile.gettempdir(), "PERSONKONTO_3266_01_34189_-_2026-06-01.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write(NORDEA_CSV_34189)
            
        try:
            result = importer.import_file(path, account_name=None)
            assert result["imported"] == 1
            
            cur = db.get_cursor()
            cur.execute("SELECT account_id FROM transactions WHERE description = 'B1'")
            acct_id = cur.fetchone()[0]
            gemensamt_acct = db.get_account_by_name("Gemensamt 34189")
            assert acct_id == gemensamt_acct["id"]
        finally:
            os.unlink(path)

    def test_validation_mismatch_blocks(self, importer, db):
        # 1. Setup accounts
        db.ensure_account("Personligt")
        db.ensure_account("Gemensamt")

        # 2. Import file 1 to Personligt
        path1 = os.path.join(tempfile.gettempdir(), "PERSONKONTO_3266_01_34138_-_2026-06-01.csv")
        with open(path1, "w", encoding="utf-8") as f:
            f.write(NORDEA_CSV_34138)
            
        try:
            importer.import_file(path1, account_name="Personligt")
            
            # 3. Try importing file 2 (contains 34138) to Gemensamt - should raise mismatch
            path2 = os.path.join(tempfile.gettempdir(), "PERSONKONTO_3266_01_34138_-_2026-06-15.csv")
            with open(path2, "w", encoding="utf-8") as f:
                f.write(NORDEA_CSV_34138.replace("A1", "A2"))
                
            try:
                with pytest.raises(ValueError, match="Account mismatch"):
                    importer.import_file(path2, account_name="Gemensamt")
            finally:
                os.unlink(path2)
        finally:
            os.unlink(path1)
