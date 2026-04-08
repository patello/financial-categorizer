"""Stats module: SQL views and query functions for financial-categorizer.

Creates views optimized for Grafana and CLI consumption.
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
                   c.name AS category_name
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.adjusted_amount IS NOT NULL
        """)

        cur.execute("""
            CREATE VIEW IF NOT EXISTS v_monthly_summary AS
            SELECT strftime('%Y-%m', date) AS month,
                   ROUND(SUM(CASE WHEN adjusted_amount > 0 THEN adjusted_amount ELSE 0 END), 2) AS total_income,
                   ROUND(SUM(CASE WHEN adjusted_amount < 0 THEN adjusted_amount ELSE 0 END), 2) AS total_expenses,
                   ROUND(SUM(adjusted_amount), 2) AS net
            FROM v_effective_transactions
            GROUP BY strftime('%Y-%m', date)
            ORDER BY month
        """)

        cur.execute("""
            CREATE VIEW IF NOT EXISTS v_category_monthly AS
            SELECT COALESCE(c.name, 'Uncategorized') AS category_name,
                   t.category_id,
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
            SELECT date, adjusted_amount, COALESCE(c.name, 'Uncategorized') AS category_name
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
