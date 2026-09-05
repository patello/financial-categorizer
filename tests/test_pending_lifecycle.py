"""Tests for the pending lifecycle: inexact settlement matching
(amount-changed authorizations), disappearance warnings, and manual
force deletion."""

import tempfile
import os
import pytest
from datetime import date, timedelta

from financial_categorizer.db_handler import DatabaseHandler
from financial_categorizer.importer import CSVImporter
from financial_categorizer.matching import inexact_amount_match


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
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8"
    )
    f.write(content)
    f.close()
    return f.name


def _import(importer, rows, account_name="test"):
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


class TestInexactSettlementImport:
    def test_inexact_settles_in_place(self, importer, db):
        """A reservation settling 6% lower (weighed items) settles in place."""
        today = date.today().isoformat()
        _import(importer, [
            "Reserverat;-1000,00;1111 11 11111;;;Reservation Kortköp MAXI ICA STORMA;999,99;SEK",
        ])
        assert _count(db, "pending") == 1

        result = _import(importer, [
            f"{today};-940,00;1111 11 11111;;;Kortköp 260904 MAXI ICA STORMA;999,99;SEK",
        ])

        assert result["settled_pending"] == 1
        assert _count(db, "pending") == 0
        cur = db.get_cursor()
        cur.execute("SELECT amount FROM transactions WHERE status = 'settled'")
        assert cur.fetchone()[0] == -940.00

    def test_inexact_upper_band_matched(self, importer, db):
        """A settled charge slightly HIGHER than the reservation (tip, FX) matches."""
        today = date.today().isoformat()
        _import(importer, [
            "Reserverat;-100,00;1111 11 11111;;;Reservation Kortköp STEAM GAME;999,99;SEK",
        ])
        result = _import(importer, [
            f"{today};-104,00;1111 11 11111;;;Kortköp 260904 STEAM GAME;999,99;SEK",
        ])
        assert result["settled_pending"] == 1
        assert _count(db, "pending") == 0

    def test_inexact_out_of_band_not_matched(self, importer, db):
        """A settled charge far outside the band does not match."""
        today = date.today().isoformat()
        _import(importer, [
            "Reserverat;-100,00;1111 11 11111;;;Reservation Kortköp WEBBSHOP X;999,99;SEK",
        ])
        result = _import(importer, [
            f"{today};-60,00;1111 11 11111;;;Kortköp 260904 WEBBSHOP X;999,99;SEK",
        ])
        assert result["settled_pending"] == 0
        assert _count(db, "pending") == 1
        assert _count(db, "settled") == 1

    def test_inexact_deep_discount_not_matched(self, importer, db):
        """A settled charge >15% below the reservation could be a genuinely
        separate purchase: no auto-match, manual review instead."""
        today = date.today().isoformat()
        _import(importer, [
            "Reserverat;-1000,00;1111 11 11111;;;Reservation Kortköp MAXI ICA STORMA;999,99;SEK",
        ])
        result = _import(importer, [
            f"{today};-780,00;1111 11 11111;;;Kortköp 260904 MAXI ICA STORMA;999,99;SEK",
        ])
        assert result["settled_pending"] == 0
        assert _count(db, "pending") == 1
        assert _count(db, "settled") == 1


class TestInexactBand:
    def test_band_bounds(self):
        """Floor and ceiling are inclusive; sign must match."""
        assert not inexact_amount_match(-1000.00, -849.99)  # just below floor
        assert inexact_amount_match(-1000.00, -850.00)      # floor inclusive
        assert inexact_amount_match(-1000.00, -1050.00)     # ceiling inclusive
        assert not inexact_amount_match(-1000.00, -1050.01)  # just above ceiling
        assert not inexact_amount_match(-1000.00, 500.00)   # sign mismatch
        assert inexact_amount_match(1000.00, 940.00)        # inflow side


class TestInexactAmbiguityImport:
    def test_inexact_ambiguous_not_matched(self, importer, db):
        """Two reservations both inside the band of one settled charge: no guess."""
        today = date.today().isoformat()
        _import(importer, [
            "Reserverat;-950,00;1111 11 11111;;;Reservation Kortköp MAXI ICA STORMA;999,99;SEK",
            "Reserverat;-1000,00;1111 11 11111;;;Reservation Kortköp MAXI ICA STORMA;999,99;SEK",
        ])
        # -960 is inside the band of BOTH -950 and -1000 -> ambiguous
        result = _import(importer, [
            f"{today};-960,00;1111 11 11111;;;Kortköp 260904 MAXI ICA STORMA;999,99;SEK",
        ])
        assert result["settled_pending"] == 0
        assert _count(db, "pending") == 2
        assert _count(db, "settled") == 1

    def test_outstanding_reservation_not_reinserted(self, importer, db):
        """A still-pending reservation re-listed in a later export is skipped,
        not inserted a second time under a new import date."""
        acct = _setup_account(db)
        _add_txn(
            db, acct, (date.today() - timedelta(days=3)).isoformat(),
            "Reservation Kortköp OLD SHOP", -500.00, "pending",
        )
        today = date.today().isoformat()
        result = _import(importer, [
            f"{today};-45,00;1111 11 11111;;;Kortköp 260904 OTHER SHOP;999,99;SEK",
            "Reserverat;-500,00;1111 11 11111;;;Reservation Kortköp OLD SHOP;999,99;SEK",
        ])
        assert result["imported"] == 1  # only the settled row
        assert _count(db, "pending") == 1  # no duplicate reservation
        assert result["warnings"] == []


class TestDisappearanceWarnings:
    def test_disappeared_reservation_warned(self, importer, db):
        """A pending no longer listed in the export (and not settled) warns."""
        acct = _setup_account(db)
        pid = _add_txn(
            db, acct, (date.today() - timedelta(days=20)).isoformat(),
            "Reservation Kortköp OLD SHOP", -500.00, "pending",
        )
        today = date.today().isoformat()
        result = _import(importer, [
            f"{today};-45,00;1111 11 11111;;;Kortköp 260904 OTHER SHOP;999,99;SEK",
        ])
        assert len(result["warnings"]) == 1
        assert result["warnings"][0]["id"] == pid

    def test_recent_missing_reservation_not_warned(self, importer, db):
        """A reservation absent for only a few days may still settle later."""
        acct = _setup_account(db)
        _add_txn(
            db, acct, (date.today() - timedelta(days=5)).isoformat(),
            "Reservation Kortköp OLD SHOP", -500.00, "pending",
        )
        today = date.today().isoformat()
        result = _import(importer, [
            f"{today};-45,00;1111 11 11111;;;Kortköp 260904 OTHER SHOP;999,99;SEK",
        ])
        assert result["warnings"] == []

    def test_still_listed_reservation_not_warned(self, importer, db):
        """A pending that the export still shows does not warn."""
        acct = _setup_account(db)
        _add_txn(
            db, acct, (date.today() - timedelta(days=20)).isoformat(),
            "Reservation Kortköp OLD SHOP", -500.00, "pending",
        )
        today = date.today().isoformat()
        result = _import(importer, [
            f"{today};-45,00;1111 11 11111;;;Kortköp 260904 OTHER SHOP;999,99;SEK",
            "Reserverat;-500,00;1111 11 11111;;;Reservation Kortköp OLD SHOP;999,99;SEK",
        ])
        assert result["warnings"] == []


class TestCleanupInexact:
    def test_inexact_unique_candidate_deleted(self, db):
        """June Maxi pattern: auth -2931.75 settles at -2759.16 (in band,
        unique in window) -> ghost removed, reported as inexact."""
        acct = _setup_account(db)
        _add_txn(db, acct, "2026-06-17", "Reservation Kortköp MAXI ICA STORMA", -2931.75, "pending")
        _add_txn(db, acct, "2026-06-19", "Kortköp 260618 MAXI ICA STORMA", -2759.16, "settled")
        _add_txn(db, acct, "2026-06-30", "Kortköp 260629 MAXI ICA STORMA", -1041.66, "settled")

        report = db.cleanup_pending(dry_run=True)
        assert len(report["ghosts"]) == 1
        assert report["ghosts"][0]["match_type"] == "inexact"
        assert report["deleted"] == 0

        report = db.cleanup_pending(dry_run=False)
        assert report["deleted"] == 1
        assert _count(db, "pending") == 0
        assert _count(db, "settled") == 2

    def test_inexact_ambiguous_kept(self, db):
        """Two pendings inside one settled charge's band: both stay unresolved."""
        acct = _setup_account(db)
        _add_txn(db, acct, "2026-08-01", "Reservation Kortköp MAXI ICA STORMA", -950.00, "pending")
        _add_txn(db, acct, "2026-08-01", "Reservation Kortköp MAXI ICA STORMA", -1000.00, "pending")
        _add_txn(db, acct, "2026-08-02", "Kortköp 260801 MAXI ICA STORMA", -960.00, "settled")

        report = db.cleanup_pending(dry_run=False)
        assert report["deleted"] == 0
        assert len(report["unresolved"]) == 2

    def test_inexact_out_of_band_kept_with_candidates(self, db):
        """Aug Maxi tail: -25.08 has a same-merchant settled charge far outside
        the band -> unresolved, candidate listed as a manual-review hint."""
        acct = _setup_account(db)
        pid = _add_txn(db, acct, "2026-08-31", "Reservation Kortköp MAXI ICA STORMA", -25.08, "pending")
        sid = _add_txn(db, acct, "2026-09-01", "Kortköp 260831 MAXI ICA STORMA", -895.32, "settled")

        report = db.cleanup_pending(dry_run=True)
        assert report["ghosts"] == []
        assert len(report["unresolved"]) == 1
        u = report["unresolved"][0]
        assert u["id"] == pid
        assert [c["id"] for c in u["candidates"]] == [sid]
        assert u["probable_cancelled"] is False


class TestForceId:
    def test_force_id_deletes_without_counterpart(self, db):
        acct = _setup_account(db)
        pid = _add_txn(db, acct, "2026-08-31", "Reservation Kortköp MAXI ICA STORMA", -25.08, "pending")

        report = db.cleanup_pending(dry_run=True, force_ids=[pid])
        assert [g["id"] for g in report["ghosts"]] == [pid]
        assert report["ghosts"][0]["match_type"] == "forced"
        assert _count(db, "pending") == 1  # dry run deletes nothing

        report = db.cleanup_pending(dry_run=False, force_ids=[pid])
        assert report["deleted"] == 1
        assert _count(db, "pending") == 0
        assert report["unresolved"] == []

    def test_force_id_invalid_raises(self, db):
        acct = _setup_account(db)
        _add_txn(db, acct, "2026-06-19", "Kortköp 260618 WEBBSHOP X", -100.00, "settled")
        with pytest.raises(ValueError):
            db.cleanup_pending(dry_run=True, force_ids=[99999])
        # A settled transaction is not force-deletable either
        cur = db.get_cursor()
        cur.execute("SELECT id FROM transactions WHERE status = 'settled'")
        settled_id = cur.fetchone()[0]
        with pytest.raises(ValueError):
            db.cleanup_pending(dry_run=True, force_ids=[settled_id])

    def test_force_id_not_duplicated_when_already_matched(self, db):
        """Forcing an id that also auto-matches deletes it exactly once."""
        acct = _setup_account(db)
        pid = _add_txn(db, acct, "2026-04-09", "Reservation Kortköp WEBBSHOP X", -100.00, "pending")
        _add_txn(db, acct, "2026-04-10", "Kortköp 260409 WEBBSHOP X", -100.00, "settled")

        report = db.cleanup_pending(dry_run=False, force_ids=[pid])
        assert report["deleted"] == 1


class TestProbableCancellation:
    def test_old_pending_without_candidates_flagged(self, db):
        """An old pending with no same-merchant candidates at all is labelled
        a probable cancellation."""
        acct = _setup_account(db)
        _add_txn(db, acct, "2026-01-01", "Reservation Kortköp GONE SHOP", -99.00, "pending")

        report = db.cleanup_pending(dry_run=True)
        assert report["ghosts"] == []
        u = report["unresolved"][0]
        assert u["probable_cancelled"] is True
        assert u["candidates"] == []
