"""Tests for ghost pending transaction handling.

Covers umlaut-variant settlement, split-authorization (aggregate) matching
during import, and the cleanup-pending database routine.
"""

import tempfile
import os
import pytest
from datetime import date, timedelta

from financial_categorizer.db_handler import DatabaseHandler
from financial_categorizer.importer import CSVImporter


NORDEA_HEADER = (
    "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
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
    """Write content to a temp CSV file and return the path."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8"
    )
    f.write(content)
    f.close()
    return f.name


def _import(importer, rows, account_name="test"):
    """Import a Nordea CSV built from raw Rubrik rows; returns import result."""
    content = NORDEA_HEADER + "".join(row + "\n" for row in rows)
    path = _write_csv(content)
    try:
        return importer.import_file(path, account_name=account_name)
    finally:
        os.unlink(path)


def _count(db, status=None):
    cur = db.get_cursor()
    if status:
        cur.execute("SELECT COUNT(*) FROM transactions WHERE status = ?", (status,))
    else:
        cur.execute("SELECT COUNT(*) FROM transactions")
    return cur.fetchone()[0]


def _setup_account(db, name="test"):
    cur = db.get_cursor()
    cur.execute(
        "INSERT INTO accounts (name, type, ownership_ratio) VALUES (?, 'tracked', 1.0)",
        (name,),
    )
    db.commit()
    return cur.lastrowid


def _add_txn(db, account_id, txn_date, desc, amount, status):
    cur = db.get_cursor()
    cur.execute(
        "INSERT INTO transactions (date, description, amount, account_id, status, adjusted_amount) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (txn_date, desc, amount, account_id, status, amount),
    )
    db.commit()
    return cur.lastrowid


class TestUmlautSettlement:
    def test_settled_matches_umlaut_variant(self, importer, db):
        """Reservation 'KIOSK ÄLVAN' settles against transliterated 'KIOSK ALVAN'."""
        today = date.today().isoformat()
        _import(importer, [
            "Reserverat;-78,40;1111 11 11111;;;Reservation Kortköp KIOSK ÄLVAN;999,99;SEK",
        ])
        assert _count(db, "pending") == 1

        result = _import(importer, [
            f"{today};-78,40;1111 11 11111;;;Kortköp 260104 KIOSK ALVAN AB;999,99;SEK",
        ])

        assert result["settled_pending"] == 1
        assert _count(db, "pending") == 0
        assert _count(db, "settled") == 1

    def test_pending_skipped_when_settled_umlaut_exists(self, importer, db):
        """A reservation whose transliterated settled row exists is skipped."""
        today = date.today().isoformat()
        _import(importer, [
            f"{today};-78,40;1111 11 11111;;;Kortköp 260104 KIOSK ALVAN AB;999,99;SEK",
        ])
        result = _import(importer, [
            "Reserverat;-78,40;1111 11 11111;;;Reservation Kortköp KIOSK ÄLVAN;999,99;SEK",
        ])

        assert result["imported"] == 0
        assert _count(db, "pending") == 0
        assert _count(db, "settled") == 1


class TestSplitAuthorizationSettlement:
    def test_split_reservations_settle_as_one(self, importer, db):
        """Two reservations summing (within 1.0) to one settled charge merge."""
        today = date.today().isoformat()
        _import(importer, [
            "Reserverat;-114,00;1111 11 11111;;;Reservation Kortköp MARKNADEN;999,99;SEK",
            "Reserverat;-25,50;1111 11 11111;;;Reservation Kortköp MARKNADEN;999,99;SEK",
        ])
        assert _count(db, "pending") == 2

        result = _import(importer, [
            f"{today};-138,90;1111 11 11111;;;Kortköp 260831 MARKNADEN STHLM;999,99;SEK",
        ])

        assert result["settled_pending"] == 1
        assert _count(db, "pending") == 0
        # One settled row carrying the final amount
        cur = db.get_cursor()
        cur.execute("SELECT amount FROM transactions WHERE status = 'settled'")
        assert cur.fetchone()[0] == -138.90

    def test_split_reservations_outside_tolerance_kept(self, importer, db):
        """A settled charge further than the tolerance from the sum does not match."""
        today = date.today().isoformat()
        _import(importer, [
            "Reserverat;-114,00;1111 11 11111;;;Reservation Kortköp MARKNADEN;999,99;SEK",
            "Reserverat;-25,50;1111 11 11111;;;Reservation Kortköp MARKNADEN;999,99;SEK",
        ])
        # Sum -139.50 vs settled -138.35 -> 1.15 apart, tolerance 1.0 -> no match
        result = _import(importer, [
            f"{today};-138,35;1111 11 11111;;;Kortköp 260831 MARKNADEN STHLM;999,99;SEK",
        ])

        assert result["settled_pending"] == 0
        assert _count(db, "pending") == 2
        assert _count(db, "settled") == 1

    def test_single_match_preferred_over_group(self, importer, db):
        """An exact single match must not consume other same-merchant pendings."""
        today = date.today().isoformat()
        _import(importer, [
            "Reserverat;-50,00;1111 11 11111;;;Reservation Kortköp MARKNADEN;999,99;SEK",
            "Reserverat;-60,00;1111 11 11111;;;Reservation Kortköp MARKNADEN;999,99;SEK",
        ])
        result = _import(importer, [
            f"{today};-50,00;1111 11 11111;;;Kortköp 260831 MARKNADEN STHLM;999,99;SEK",
        ])

        assert result["settled_pending"] == 1
        assert _count(db, "pending") == 1
        cur = db.get_cursor()
        cur.execute("SELECT amount FROM transactions WHERE status = 'pending'")
        assert cur.fetchone()[0] == -60.00


class TestCleanupPending:
    def test_cleanup_deletes_fossil_ghost(self, db):
        """Identical pending+settled pair (pre-feature fossil) is removed."""
        acct = _setup_account(db)
        _add_txn(db, acct, "2026-04-09", "Reservation Kortköp WEBBSHOP X", -100.00, "pending")
        _add_txn(db, acct, "2026-04-10", "Kortköp 260409 WEBBSHOP X", -100.00, "settled")

        report = db.cleanup_pending(dry_run=True)
        assert len(report["ghosts"]) == 1
        assert report["deleted"] == 0
        assert _count(db, "pending") == 1  # dry-run changes nothing

        report = db.cleanup_pending(dry_run=False)
        assert report["deleted"] == 1
        assert _count(db, "pending") == 0
        assert _count(db, "settled") == 1
        assert report["unresolved"] == []

    def test_cleanup_deletes_umlaut_ghost(self, db):
        """Reservation 'ÄLVAN' vs settled 'ALVAN' is recognized as a ghost."""
        acct = _setup_account(db)
        _add_txn(db, acct, "2026-07-23", "Reservation Kortköp KIOSK ÄLVAN", -78.40, "pending")
        _add_txn(db, acct, "2026-07-23", "Kortköp 260722 KIOSK ALVAN AB", -78.40, "settled")

        report = db.cleanup_pending(dry_run=False)
        assert report["deleted"] == 1
        assert _count(db, "pending") == 0

    def test_cleanup_deletes_split_group(self, db):
        """Two pendings summing to one settled charge (within tolerance) go together."""
        acct = _setup_account(db)
        _add_txn(db, acct, "2026-08-31", "Reservation Kortköp MARKNADEN", -114.00, "pending")
        _add_txn(db, acct, "2026-08-31", "Reservation Kortköp MARKNADEN", -25.50, "pending")
        _add_txn(db, acct, "2026-09-01", "Kortköp 260831 MARKNADEN STHLM", -138.90, "settled")

        report = db.cleanup_pending(dry_run=False)
        assert report["deleted"] == 2
        assert _count(db, "pending") == 0
        assert _count(db, "settled") == 1

    def test_cleanup_consumes_settled_once(self, db):
        """One settled row can only justify one pending deletion."""
        acct = _setup_account(db)
        _add_txn(db, acct, "2026-04-08", "Reservation Kortköp WEBBSHOP X", -50.00, "pending")
        _add_txn(db, acct, "2026-04-09", "Reservation Kortköp WEBBSHOP X", -50.00, "pending")
        _add_txn(db, acct, "2026-04-10", "Kortköp 260409 WEBBSHOP X", -50.00, "settled")

        report = db.cleanup_pending(dry_run=False)
        assert report["deleted"] == 1
        assert len(report["unresolved"]) == 1
        assert _count(db, "pending") == 1

    def test_cleanup_keeps_unresolved_amount_mismatch(self, db):
        """A pending whose settled counterpart is far outside the inexact band stays."""
        acct = _setup_account(db)
        _add_txn(db, acct, "2026-06-17", "Reservation Kortköp MARKNADEN", -100.00, "pending")
        _add_txn(db, acct, "2026-06-19", "Kortköp 260618 MARKNADEN STHLM", -50.00, "settled")

        report = db.cleanup_pending(dry_run=False)
        assert report["deleted"] == 0
        assert len(report["unresolved"]) == 1
        assert _count(db, "pending") == 1

    def test_cleanup_ignores_out_of_window(self, db):
        """A settled charge more than 10 days after the reservation does not match."""
        acct = _setup_account(db)
        _add_txn(db, acct, "2026-04-01", "Reservation Kortköp WEBBSHOP X", -100.00, "pending")
        _add_txn(db, acct, "2026-04-30", "Kortköp 260429 WEBBSHOP X", -100.00, "settled")

        report = db.cleanup_pending(dry_run=True)
        assert report["deleted"] == 0
        assert len(report["ghosts"]) == 0
        assert len(report["unresolved"]) == 1
