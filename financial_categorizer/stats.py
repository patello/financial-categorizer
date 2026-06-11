"""Stats module: SQL views and query functions for financial-categorizer.

Creates views for dashboard and CLI consumption.
All views use adjusted_amount directly — ownership_ratio, transfers,
and reimbursements are already baked in.
"""

from datetime import date


class Stats:
    """Query financial stats from pre-computed views."""

    def __init__(self, db_handler):
        self.db = db_handler
        self._ensure_views()

    def _ensure_views(self):
        """Create or replace SQL views."""
        cur = self.db.get_cursor()

        cur.execute("""
            CREATE VIEW IF NOT EXISTS v_effective_transactions AS
            SELECT t.id, t.date, t.description, t.amount, t.adjusted_amount,
                   t.account_id, t.category_id, t.status, t.comment,
                   a.name AS account_name, a.type AS account_type,
                   c.name AS category_name,
                   COALESCE(c.category_type, 'expense') AS category_type
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.adjusted_amount IS NOT NULL
        """)

        cur.execute("""
            CREATE VIEW IF NOT EXISTS v_monthly_summary AS
            SELECT strftime('%Y-%m', date) AS month,
                   ROUND(SUM(CASE WHEN adjusted_amount > 0 AND category_type != 'transfer' THEN adjusted_amount ELSE 0 END), 2) AS total_income,
                   ROUND(SUM(CASE WHEN adjusted_amount < 0 AND category_type != 'transfer' THEN adjusted_amount ELSE 0 END), 2) AS total_expenses,
                   ROUND(SUM(CASE WHEN category_type != 'transfer' THEN adjusted_amount ELSE 0 END), 2) AS net
            FROM v_effective_transactions
            GROUP BY strftime('%Y-%m', date)
            ORDER BY month
        """)

        cur.execute("""
            CREATE VIEW IF NOT EXISTS v_category_monthly AS
            SELECT COALESCE(c.name, 'Uncategorized') AS category_name,
                   t.category_id,
                   COALESCE(c.category_type, 'expense') AS category_type,
                   strftime('%Y-%m', t.date) AS month,
                   ROUND(SUM(t.adjusted_amount), 2) AS total,
                   COUNT(*) AS count
            FROM transactions t
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.adjusted_amount IS NOT NULL
            GROUP BY strftime('%Y-%m', t.date), t.category_id
            ORDER BY month, category_name
        """)

        cur.execute("""
            CREATE VIEW IF NOT EXISTS v_daily_spending AS
            SELECT date, adjusted_amount, COALESCE(c.name, 'Uncategorized') AS category_name,
                   COALESCE(c.category_type, 'expense') AS category_type
            FROM transactions t
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE adjusted_amount IS NOT NULL AND adjusted_amount < 0
            ORDER BY date
        """)

        self.db.commit()

    def monthly_summary(self, month: str = None) -> list[dict]:
        """Get monthly income/expenses/net.

        Args:
            month: Optional filter 'YYYY-MM'. If None, returns all months.

        Returns list of dicts with month, total_income, total_expenses, net.
        """
        cur = self.db.get_cursor()
        if month:
            cur.execute(
                "SELECT month, total_income, total_expenses, net "
                "FROM v_monthly_summary WHERE month = ?",
                (month,),
            )
        else:
            cur.execute(
                "SELECT month, total_income, total_expenses, net "
                "FROM v_monthly_summary"
            )
        return [
            {"month": r[0], "total_income": r[1], "total_expenses": r[2], "net": r[3]}
            for r in cur.fetchall()
        ]

    def category_total(self, category_id: int, month: str = None,
                       date_from: date = None, date_to: date = None) -> dict:
        """Get total for a category including all children.

        Args:
            category_id: The category to sum (includes subcategories).
            month: Optional 'YYYY-MM' filter.
            date_from/date_to: Optional date range.

        Returns dict with category_id, total, count.
        """
        # Gather all category IDs (self + subtree)
        from financial_categorizer.categorizer import Categorizer
        cat = Categorizer.__new__(Categorizer)
        cat.db = self.db

        ids = [category_id]
        subtree = cat.get_subtree(category_id)
        ids.extend(c["id"] for c in subtree)

        placeholders = ",".join("?" * len(ids))
        conditions = [f"category_id IN ({placeholders})"]
        params = list(ids)

        if month:
            conditions.append("strftime('%Y-%m', date) = ?")
            params.append(month)
        if date_from:
            conditions.append("date >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("date <= ?")
            params.append(date_to)

        where = " AND ".join(conditions)

        cur = self.db.get_cursor()
        cur.execute(
            f"SELECT ROUND(SUM(adjusted_amount), 2), COUNT(*) "
            f"FROM v_effective_transactions WHERE {where}",
            params,
        )
        row = cur.fetchone()
        return {
            "category_id": category_id,
            "total": row[0] if row[0] else 0.0,
            "count": row[1],
        }

    def top_spending(self, month: str = None, limit: int = 10) -> list[dict]:
        """Top spending categories by adjusted_amount.

        Args:
            month: Optional 'YYYY-MM' filter.
            limit: Max categories to return.

        Returns list of dicts sorted by total spending (most negative first).
        """
        cur = self.db.get_cursor()
        params = []
        conditions = ["total < 0"]
        if month:
            conditions.append("month = ?")
            params.append(month)

        where = " AND ".join(conditions)
        cur.execute(
            f"SELECT category_name, category_id, month, total, count "
            f"FROM v_category_monthly WHERE {where} "
            f"ORDER BY total ASC LIMIT ?",
            params + [limit],
        )
        return [
            {"category_name": r[0], "category_id": r[1], "month": r[2],
             "total": r[3], "count": r[4]}
            for r in cur.fetchall()
        ]

    def trend(self, category_id: int, date_from: date = None,
              date_to: date = None) -> list[dict]:
        """Monthly breakdown for a category (including children).

        Returns list of dicts with month, total, count.
        """
        from financial_categorizer.categorizer import Categorizer
        cat = Categorizer.__new__(Categorizer)
        cat.db = self.db

        ids = [category_id]
        subtree = cat.get_subtree(category_id)
        ids.extend(c["id"] for c in subtree)

        placeholders = ",".join("?" * len(ids))
        conditions = [f"category_id IN ({placeholders})"]
        params = list(ids)

        if date_from:
            conditions.append("month >= strftime('%Y-%m', ?)")
            params.append(date_from)
        if date_to:
            conditions.append("month <= strftime('%Y-%m', ?)")
            params.append(date_to)

        where = " AND ".join(conditions)

        cur = self.db.get_cursor()
        cur.execute(
            f"SELECT month, ROUND(SUM(total), 2), SUM(count) "
            f"FROM v_category_monthly WHERE {where} "
            f"GROUP BY month ORDER BY month",
            params,
        )
        return [
            {"month": r[0], "total": r[1] if r[1] else 0.0, "count": r[2]}
            for r in cur.fetchall()
        ]

    def compare(self, period: str = None, period_type: str = "calendar") -> list[dict]:
        """Month-over-month comparison.

        Args:
            period: 'YYYY-MM' for calendar or 'YYYY-MM' for salary period starting that month.
                    If None, compares last two periods.
            period_type: 'calendar' (1st-last) or 'salary' (25th-24th).

        Returns list of dicts with period, total_income, total_expenses, net,
                prev_ prefixed fields, and delta fields.
        """
        cur = self.db.get_cursor()

        if period_type == "salary":
            # Salary period: 25th of previous month to 24th of current month
            # Period '2026-03' means Feb 25 - Mar 24
            rows = self._salary_period_summary(cur)
        else:
            rows = self._calendar_month_summary(cur)

        if len(rows) < 2 and period is None:
            # Not enough data for comparison, just return what we have
            return rows

        # Find the target and previous periods
        if period:
            target = None
            for i, r in enumerate(rows):
                if r["period"] == period:
                    target = r
                    prev = rows[i - 1] if i > 0 else None
                    break
            if not target:
                return []
        else:
            target = rows[-1]
            prev = rows[-2]

        result = {
            "period": target["period"],
            "total_income": target["total_income"],
            "total_expenses": target["total_expenses"],
            "net": target["net"],
        }

        if prev:
            result["prev_period"] = prev["period"]
            result["prev_total_income"] = prev["total_income"]
            result["prev_total_expenses"] = prev["total_expenses"]
            result["prev_net"] = prev["net"]
            result["income_delta"] = round(target["total_income"] - prev["total_income"], 2)
            result["expense_delta"] = round(target["total_expenses"] - prev["total_expenses"], 2)
            result["net_delta"] = round(target["net"] - prev["net"], 2)
            # Percentage changes (handle zero prev)
            result["income_pct"] = round((result["income_delta"] / abs(prev["total_income"])) * 100, 1) if prev["total_income"] else None
            result["expense_pct"] = round((result["expense_delta"] / abs(prev["total_expenses"])) * 100, 1) if prev["total_expenses"] else None
            result["net_pct"] = round((result["net_delta"] / abs(prev["net"])) * 100, 1) if prev["net"] else None

        return result

    def _calendar_month_summary(self, cur) -> list[dict]:
        cur.execute(
            "SELECT month, total_income, total_expenses, net "
            "FROM v_monthly_summary ORDER BY month"
        )
        return [
            {"period": r[0], "total_income": r[1] or 0.0,
             "total_expenses": r[2] or 0.0, "net": r[3] or 0.0}
            for r in cur.fetchall()
        ]

    def _salary_period_summary(self, cur) -> list[dict]:
        """Aggregate by salary period (25th to 24th of next month).
        Period label is the month of the 24th, e.g. '2026-03' = Feb 25 - Mar 24.
        """
        cur.execute(
            "SELECT MIN(date), MAX(date) FROM v_effective_transactions "
            "WHERE category_type != 'transfer'"
        )
        date_range = cur.fetchone()
        if not date_range or not date_range[0]:
            return []

        min_date = date.fromisoformat(date_range[0]) if isinstance(date_range[0], str) else date_range[0]
        max_date = date.fromisoformat(date_range[1]) if isinstance(date_range[1], str) else date_range[1]

        # Generate all salary periods covering the data range
        # Start from the month before min_date
        results = []
        from datetime import timedelta

        # First period start: 25th of month before the earliest data
        year, month = min_date.year, min_date.month
        month -= 1
        if month < 1:
            month = 12
            year -= 1
        period_start = date(year, month, 25)

        while period_start <= max_date:
            # Period end: 24th of next month
            end_month = period_start.month + 1
            end_year = period_start.year
            if end_month > 12:
                end_month = 1
                end_year += 1
            period_end = date(end_year, end_month, 24)

            # Label: the month of period_end
            label = f"{period_end.year}-{period_end.month:02d}"

            cur.execute(
                "SELECT ROUND(SUM(CASE WHEN adjusted_amount > 0 THEN adjusted_amount ELSE 0 END), 2), "
                "ROUND(SUM(CASE WHEN adjusted_amount < 0 THEN adjusted_amount ELSE 0 END), 2), "
                "ROUND(SUM(adjusted_amount), 2) "
                "FROM v_effective_transactions "
                "WHERE category_type != 'transfer' AND date >= ? AND date <= ?",
                (period_start.isoformat(), period_end.isoformat()),
            )
            row = cur.fetchone()
            results.append({
                "period": label,
                "total_income": row[0] or 0.0,
                "total_expenses": row[1] or 0.0,
                "net": row[2] or 0.0,
            })

            # Advance to next period
            period_start = date(end_year, end_month, 25)

        return results
