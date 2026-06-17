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

        cur.execute("""
            CREATE VIEW IF NOT EXISTS v_cumulative_spending_monthly AS
            WITH daily_spending AS (
                SELECT
                    date,
                    strftime('%Y-%m', date) AS month,
                    ROUND(SUM(adjusted_amount), 2) AS daily_amount
                FROM transactions t
                LEFT JOIN categories c ON c.id = t.category_id
                WHERE adjusted_amount IS NOT NULL AND adjusted_amount < 0
                  AND (category_id IS NULL OR COALESCE(c.category_type, 'expense') != 'transfer')
                GROUP BY date
            )
            SELECT
                date,
                month,
                daily_amount,
                ROUND(SUM(daily_amount) OVER (
                    PARTITION BY month
                    ORDER BY date
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ), 2) AS cumulative_amount
            FROM daily_spending
        """)

        cur.execute("""
            CREATE VIEW IF NOT EXISTS v_daily_spending_moving_average AS
            WITH RECURSIVE date_range(date) AS (
                SELECT MIN(date) FROM transactions WHERE date IS NOT NULL
                UNION ALL
                SELECT date(date, '+1 day') FROM date_range WHERE date < (SELECT MAX(date) FROM transactions)
            ),
            daily_spending AS (
                SELECT
                    date,
                    SUM(adjusted_amount) AS daily_amount
                FROM transactions t
                LEFT JOIN categories c ON c.id = t.category_id
                WHERE adjusted_amount IS NOT NULL AND adjusted_amount < 0
                  AND (category_id IS NULL OR COALESCE(c.category_type, 'expense') != 'transfer')
                GROUP BY date
            )
            SELECT
                dr.date,
                COALESCE(ds.daily_amount, 0.0) AS daily_amount,
                ROUND(AVG(COALESCE(ds.daily_amount, 0.0)) OVER (
                    ORDER BY dr.date
                    ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
                ), 2) AS moving_average
            FROM date_range dr
            LEFT JOIN daily_spending ds ON dr.date = ds.date
        """)

        cur.execute("""
            CREATE VIEW IF NOT EXISTS v_category_monthly_averages AS
            SELECT
                category_name,
                category_id,
                category_type,
                ROUND(AVG(total), 2) AS average_monthly_spending,
                ROUND(AVG(count), 1) AS average_monthly_count
            FROM v_category_monthly
            GROUP BY category_id
        """)

        cur.execute("DROP VIEW IF EXISTS v_salary_periods")
        cur.execute("DROP VIEW IF EXISTS v_salary_period_summary")

        cur.execute("""
            CREATE VIEW v_salary_periods AS
            WITH settings AS (
                SELECT 
                    COALESCE((SELECT value FROM metadata WHERE key = 'salary_period_mode'), 'fixed') AS mode,
                    COALESCE((SELECT CAST(value AS INTEGER) FROM metadata WHERE key = 'salary_period_fixed_day'), 25) AS fixed_day,
                    COALESCE((SELECT value FROM metadata WHERE key = 'salary_period_category_name'), 'Salary') AS salary_cat_name
            ),
            primary_salary_dates AS (
                SELECT date
                FROM (
                    SELECT 
                        t.date,
                        ROW_NUMBER() OVER (
                            PARTITION BY strftime('%Y-%m', t.date) 
                            ORDER BY t.amount DESC, t.date ASC
                        ) AS rank
                    FROM transactions t
                    JOIN categories c ON t.category_id = c.id
                    WHERE c.name = (SELECT salary_cat_name FROM settings)
                      AND t.amount > 0
                )
                WHERE rank = 1
            )
            SELECT 
                t.id AS transaction_id,
                t.date,
                CASE 
                    WHEN (SELECT mode FROM settings) = 'salary' THEN
                        strftime('%Y-%m', COALESCE(
                            (SELECT MAX(s.date) FROM primary_salary_dates s WHERE s.date <= t.date),
                            t.date
                        ))
                    ELSE
                        CASE 
                            WHEN CAST(strftime('%d', t.date) AS INTEGER) >= (SELECT fixed_day FROM settings)
                                THEN strftime('%Y-%m', date(t.date, 'start of month', '+1 month'))
                            ELSE strftime('%Y-%m', t.date)
                        END
                END AS period
            FROM transactions t
        """)

        cur.execute("""
            CREATE VIEW v_salary_period_summary AS
            WITH txn_with_period AS (
                SELECT
                    t.adjusted_amount,
                    COALESCE(c.category_type, 'expense') AS category_type,
                    p.period
                FROM transactions t
                JOIN v_salary_periods p ON t.id = p.transaction_id
                JOIN accounts a ON a.id = t.account_id
                LEFT JOIN categories c ON c.id = t.category_id
                WHERE t.adjusted_amount IS NOT NULL AND COALESCE(c.category_type, 'expense') != 'transfer'
            )
            SELECT
                period,
                ROUND(SUM(CASE WHEN adjusted_amount > 0 THEN adjusted_amount ELSE 0 END), 2) AS total_income,
                ROUND(SUM(CASE WHEN adjusted_amount < 0 THEN adjusted_amount ELSE 0 END), 2) AS total_expenses,
                ROUND(SUM(adjusted_amount), 2) AS net
            FROM txn_with_period
            GROUP BY period
            ORDER BY period
        """)

        cur.execute("""
            CREATE VIEW IF NOT EXISTS v_breakout_categories AS
            WITH breakout_mapping AS (
                SELECT id,
                       CASE
                           WHEN id IN (4, 50) THEN 'Groceries'
                           WHEN id = 6 THEN 'Loans'
                           WHEN id IN (7, 8, 48) THEN 'Restaurants'
                           WHEN id IN (14, 31) THEN 'Housing'
                           WHEN id IN (26, 27, 28, 29, 52) THEN 'Car'
                           WHEN id IN (12, 15, 35, 41) THEN 'Shopping'
                           WHEN id IN (17, 19, 23, 24, 25, 42, 43, 44, 49) THEN 'Leisure'
                           WHEN id IN (5, 11, 13, 22, 40, 45, 46, 47) THEN 'Household Other'
                           WHEN id IN (10, 16, 30, 36, 37, 38, 39, 51) THEN 'Other'
                           ELSE NULL
                       END AS display_name
                FROM categories
            )
            SELECT
                COALESCE(bm.display_name, 'Uncategorized') AS display_name,
                CASE
                    WHEN COALESCE(bm.display_name, 'Uncategorized') = 'Groceries' THEN '4,50'
                    WHEN COALESCE(bm.display_name, 'Uncategorized') = 'Loans' THEN '6'
                    WHEN COALESCE(bm.display_name, 'Uncategorized') = 'Restaurants' THEN '7,8,48'
                    WHEN COALESCE(bm.display_name, 'Uncategorized') = 'Housing' THEN '14,31'
                    WHEN COALESCE(bm.display_name, 'Uncategorized') = 'Car' THEN '26,27,28,29,52'
                    WHEN COALESCE(bm.display_name, 'Uncategorized') = 'Shopping' THEN '12,15,35,41'
                    WHEN COALESCE(bm.display_name, 'Uncategorized') = 'Leisure' THEN '17,19,23,24,25,42,43,44,49'
                    WHEN COALESCE(bm.display_name, 'Uncategorized') = 'Household Other' THEN '5,11,13,22,40,45,46,47'
                    WHEN COALESCE(bm.display_name, 'Uncategorized') = 'Other' THEN '10,16,30,36,37,38,39,51'
                    ELSE ''
                END AS category_ids,
                strftime('%Y-%m', t.date) AS month,
                ROUND(SUM(t.adjusted_amount), 2) AS total
            FROM transactions t
            LEFT JOIN breakout_mapping bm ON t.category_id = bm.id
            WHERE t.adjusted_amount IS NOT NULL 
              AND t.adjusted_amount < 0
              AND (t.category_id IS NULL OR t.category_id NOT IN (SELECT id FROM categories WHERE category_type = 'transfer'))
            GROUP BY COALESCE(bm.display_name, 'Uncategorized'), month
        """)

        cur.execute("""
            CREATE VIEW IF NOT EXISTS v_uncategorized_groups AS
            WITH cleaned_txns AS (
                SELECT
                    t.id,
                    t.date,
                    t.amount,
                    t.description AS latest_full_text,
                    a.name AS account_name,
                    CASE
                        WHEN t.description LIKE 'Swish betalning %' THEN TRIM(SUBSTR(t.description, 16))
                        WHEN t.description LIKE 'Swish inbetalning %' THEN TRIM(SUBSTR(t.description, 18))
                        WHEN (t.description LIKE 'Kortk_p %' OR t.description LIKE 'Kortköp %') 
                             THEN TRIM(SUBSTR(t.description, 16))
                        ELSE t.description
                    END AS group_key
                FROM transactions t
                JOIN accounts a ON a.id = t.account_id
                WHERE t.category_id IS NULL
            ),
            grouped_txns AS (
                SELECT
                    group_key,
                    COUNT(*) AS match_count,
                    ROUND(SUM(amount), 2) AS total_amount,
                    MAX(date) AS latest_date
                FROM cleaned_txns
                GROUP BY group_key
            )
            SELECT
                g.group_key,
                g.match_count,
                g.total_amount,
                g.latest_date,
                (
                    SELECT c.amount
                    FROM cleaned_txns c
                    WHERE c.group_key = g.group_key AND c.date = g.latest_date
                    LIMIT 1
                ) AS latest_amount,
                (
                    SELECT c.latest_full_text
                    FROM cleaned_txns c
                    WHERE c.group_key = g.group_key AND c.date = g.latest_date
                    LIMIT 1
                ) AS latest_full_text,
                (
                    SELECT group_concat(DISTINCT c.account_name)
                    FROM cleaned_txns c
                    WHERE c.group_key = g.group_key
                ) AS account_names
            FROM grouped_txns g
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
            "SELECT period, total_income, total_expenses, net "
            "FROM v_salary_period_summary ORDER BY period"
        )
        return [
            {"period": r[0], "total_income": r[1] or 0.0,
             "total_expenses": r[2] or 0.0, "net": r[3] or 0.0}
            for r in cur.fetchall()
        ]
