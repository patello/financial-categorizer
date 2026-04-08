"""CSV importer for financial-categorizer.

Auto-detects bank CSV format (Nordea, ICA) and imports transactions
into the SQLite database with deduplication. Handles pending transactions
("Reserverat" in Nordea) by updating them when the settled version appears.
"""

import csv
import datetime
import os
import logging

logger = logging.getLogger(__name__)


# Known CSV formats. Detection uses a unique header combo per format.
CSV_FORMATS = {
    "nordea": {
        "detect_headers": ["Bokföringsdag", "Rubrik"],
        "date_col": "Bokföringsdag",
        "amount_col": "Belopp",
        "desc_col": "Rubrik",
    },
    "ica": {
        "detect_headers": ["Datum", "Text", "Typ"],
        "date_col": "Datum",
        "amount_col": "Belopp",
        "desc_col": "Text",
    },
}


def parse_date(date_string: str) -> datetime.date:
    """Parse a date string in common Swedish bank formats.

    Supports: YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD
    """
    date_string = date_string.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.datetime.strptime(date_string, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unable to parse date: {date_string!r}")


def parse_amount(amount_string: str) -> float:
    """Parse a Swedish-format amount string to float.

    Handles comma decimals, strips 'kr' and spaces. Keeps sign as-is.
    """
    s = amount_string.strip()
    s = s.replace("kr", "")
    s = s.replace(" ", "")
    s = s.replace("\xa0", "")  # non-breaking space
    s = s.replace(",", ".")
    return float(s)


def detect_format(header_row: list[str]) -> str | None:
    """Detect the CSV format from the header row.

    Returns the format name ('nordea', 'ica') or None if unrecognized.
    """
    header_set = set(header_row)
    for fmt_name, fmt_def in CSV_FORMATS.items():
        if all(h in header_set for h in fmt_def["detect_headers"]):
            return fmt_name
    return None


class CSVImporter:
    """Imports bank CSV files into the transactions table."""

    def __init__(self, db_handler):
        """
        Args:
            db_handler: A connected DatabaseHandler instance.
        """
        self.db = db_handler

    def import_file(self, file_path: str, account_name: str = None, auto_create_account: bool = True) -> dict:
        """Import a CSV file into the database.

        Args:
            file_path: Path to the CSV file.
            account_name: Override account name. If None, derived from filename.
            auto_create_account: If True, create the account if it doesn't exist.

        Returns:
            dict with 'imported', 'skipped' (duplicates), 'errors' counts.
        """
        if account_name is None:
            account_name = os.path.basename(file_path).split(".")[0]

        if auto_create_account:
            account_id = self.db.ensure_account(account_name)
        else:
            acct = self.db.get_account_by_name(account_name)
            if not acct:
                raise ValueError(f"Account '{account_name}' not found. Create it first or use auto_create_account=True.")
            account_id = acct["id"]

        imported = 0
        skipped = 0
        errors = 0
        settled_pending = 0

        with open(file_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f, delimiter=";")
            header_row = next(reader)

            fmt_name = detect_format(header_row)
            if fmt_name is None:
                raise ValueError(
                    f"Unrecognized CSV format. Header: {header_row}"
                )

            fmt = CSV_FORMATS[fmt_name]
            date_idx = header_row.index(fmt["date_col"])
            amount_idx = header_row.index(fmt["amount_col"])
            desc_idx = header_row.index(fmt["desc_col"])

            cur = self.db.get_cursor()

            for row in reader:
                if not row or all(cell.strip() == "" for cell in row):
                    continue

                try:
                    raw_date = row[date_idx].strip()
                    is_pending = raw_date.lower() == "reserverat"
                    if is_pending:
                        txn_date = datetime.date.today()
                    else:
                        txn_date = parse_date(raw_date)
                    amount = parse_amount(row[amount_idx])
                    description = row[desc_idx].strip()
                except (ValueError, IndexError) as e:
                    errors += 1
                    continue

                status = "pending" if is_pending else "settled"

                # For settled transactions, check if a pending one exists
                # with the same description and account. If so, update it.
                if status == "settled":
                    cur.execute(
                        "SELECT id FROM transactions "
                        "WHERE description = ? AND account_id = ? AND status = 'pending'",
                        (description, account_id),
                    )
                    pending_row = cur.fetchone()
                    if pending_row:
                        cur.execute(
                            "UPDATE transactions SET date = ?, amount = ?, "
                            "adjusted_amount = ? * "
                            "(SELECT ownership_ratio FROM accounts WHERE accounts.id = account_id), "
                            "status = 'settled', source_file = ? WHERE id = ?",
                            (txn_date, amount, amount, file_path, pending_row[0]),
                        )
                        settled_pending += 1
                        imported += 1
                        continue

                try:
                    cur.execute(
                        "INSERT INTO transactions (date, description, amount, account_id, source_file, status, adjusted_amount) "
                        "VALUES (?, ?, ?, ?, ?, ?, ? * "
                        "(SELECT ownership_ratio FROM accounts WHERE accounts.id = ?))",
                        (txn_date, description, amount, account_id, file_path, status, amount, account_id),
                    )
                    imported += 1
                except Exception:
                    skipped += 1

            self.db.commit()

        return {
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "settled_pending": settled_pending,
        }
