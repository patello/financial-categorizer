"""Transaction categorizer backed by SQLite match rules and manual overrides.

Supports regex, exact, and contains matching with priority ordering.
Categories form a tree — matching a leaf automatically includes all ancestors.
"""

import re


class Categorizer:
    """Categorizes transactions using rules and manual overrides from a DatabaseHandler."""

    def __init__(self, db_handler):
        """
        Args:
            db_handler: A connected DatabaseHandler instance.
        """
        self.db = db_handler

    # ------------------------------------------------------------------ #
    #  Core matching
    # ------------------------------------------------------------------ #

    def _match_description(self, pattern: str, match_type: str, description: str) -> bool:
        """Try to match *pattern* against *description* using the given *match_type*."""
        if match_type == "regex":
            return re.search(pattern, description, re.IGNORECASE) is not None
        elif match_type == "exact":
            return pattern.lower() == description.lower()
        elif match_type == "contains":
            return pattern.lower() in description.lower()
        return False

    def categorize(self, transaction_id: int) -> int | None:
        """Categorize a single transaction.

        Checks id_matches (manual override) first, then match_rules by priority.
        Updates the transaction's category_id and matched_rule_id.

        Returns the matched category_id, or None if no match.
        """
        cur = self.db.get_cursor()

        # 1. Check manual override — always wins, clears rule tracking
        cur.execute(
            "SELECT category_id FROM id_matches WHERE transaction_id = ?",
            (transaction_id,),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE transactions SET category_id = ?, matched_rule_id = NULL WHERE id = ?",
                (row[0], transaction_id),
            )
            self.db.commit()
            return row[0]

        # 2. Get the transaction description
        cur.execute(
            "SELECT description FROM transactions WHERE id = ?",
            (transaction_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        description = row[0]

        # 3. Apply match rules in priority order (highest priority first)
        cur.execute(
            "SELECT id, category_id, pattern, match_type FROM match_rules "
            "WHERE enabled = 1 ORDER BY priority DESC, id ASC"
        )
        for rule_id, category_id, pattern, match_type in cur.fetchall():
            if self._match_description(pattern, match_type, description):
                cur.execute(
                    "UPDATE transactions SET category_id = ?, matched_rule_id = ? WHERE id = ?",
                    (category_id, rule_id, transaction_id),
                )
                self.db.commit()
                return category_id

        # No match — clear any previous rule-based assignment
        cur.execute(
            "UPDATE transactions SET category_id = NULL, matched_rule_id = NULL WHERE id = ?",
            (transaction_id,),
        )
        self.db.commit()
        return None

    def categorize_new(self) -> dict:
        """Categorize only uncategorized transactions (category_id IS NULL).

        Skips transactions that already have a category (rule or manual).

        Returns a dict with counts: {'matched': int, 'unmatched': int}
        """
        cur = self.db.get_cursor()
        cur.execute(
            "SELECT id FROM transactions WHERE category_id IS NULL"
        )
        uncategorized = [row[0] for row in cur.fetchall()]

        matched = 0
        for txn_id in uncategorized:
            result = self.categorize(txn_id)
            if result is not None:
                matched += 1

        return {"matched": matched, "unmatched": len(uncategorized) - matched}

    def categorize_all(self) -> dict:
        """Re-categorize ALL transactions using current rules.

        Resets rule-based matches (matched_rule_id IS NOT NULL) first,
        then re-runs rules on every transaction. Manual overrides
        (id_matches) are preserved and always win.

        Returns a dict with counts: {'matched': int, 'unmatched': int}
        """
        cur = self.db.get_cursor()

        # Clear all rule-based category assignments
        cur.execute(
            "UPDATE transactions SET category_id = NULL, matched_rule_id = NULL "
            "WHERE id NOT IN (SELECT transaction_id FROM id_matches)"
        )
        self.db.commit()

        # Now categorize everything
        cur.execute("SELECT id FROM transactions")
        all_ids = [row[0] for row in cur.fetchall()]

        matched = 0
        for txn_id in all_ids:
            result = self.categorize(txn_id)
            if result is not None:
                matched += 1

        return {"matched": matched, "unmatched": len(all_ids) - matched}

    # ------------------------------------------------------------------ #
    #  Category hierarchy
    # ------------------------------------------------------------------ #

    def get_category_path(self, category_id: int) -> list[dict]:
        """Return the path from leaf to root for a category.

        Returns a list of dicts: [{'id': ..., 'name': ...}, ...]
        starting with the leaf category and ending with the root.
        """
        cur = self.db.get_cursor()
        path = []
        current_id = category_id
        visited = set()

        while current_id is not None and current_id not in visited:
            visited.add(current_id)
            cur.execute(
                "SELECT id, name, parent_id FROM categories WHERE id = ?",
                (current_id,),
            )
            row = cur.fetchone()
            if not row:
                break
            path.append({"id": row[0], "name": row[1]})
            current_id = row[2]

        return path

    def get_all_tags(self, transaction_id: int) -> list[str]:
        """Get all category names for a transaction, including ancestors.

        This replicates the old parent-propagation behavior: matching a
        child tag also includes all parent tags.
        """
        cur = self.db.get_cursor()
        cur.execute(
            "SELECT category_id FROM transactions WHERE id = ?",
            (transaction_id,),
        )
        row = cur.fetchone()
        if not row or row[0] is None:
            return []

        path = self.get_category_path(row[0])
        return [cat["name"] for cat in path]

    # ------------------------------------------------------------------ #
    #  Rule management
    # ------------------------------------------------------------------ #

    def add_rule(
        self,
        category_id: int,
        pattern: str,
        match_type: str = "regex",
        priority: int = 0,
    ) -> int:
        """Add a new match rule and re-categorize all transactions.

        Returns the rule id.
        """
        cur = self.db.get_cursor()
        cur.execute(
            "INSERT INTO match_rules (category_id, pattern, match_type, priority) "
            "VALUES (?, ?, ?, ?)",
            (category_id, pattern, match_type, priority),
        )
        self.db.commit()
        self.categorize_all()
        return cur.lastrowid

    def remove_rule(self, rule_id: int) -> bool:
        """Remove a match rule and re-categorize all transactions.

        Returns True if a rule was deleted.
        """
        cur = self.db.get_cursor()
        cur.execute("DELETE FROM match_rules WHERE id = ?", (rule_id,))
        deleted = cur.rowcount > 0
        self.db.commit()
        if deleted:
            self.categorize_all()
        return deleted

    def list_rules(self) -> list[dict]:
        """Return all match rules ordered by priority."""
        cur = self.db.get_cursor()
        cur.execute(
            "SELECT r.id, r.category_id, c.name, r.pattern, r.match_type, r.priority, r.enabled "
            "FROM match_rules r JOIN categories c ON r.category_id = c.id "
            "ORDER BY r.priority DESC, r.id ASC"
        )
        return [
            {
                "id": row[0],
                "category_id": row[1],
                "category_name": row[2],
                "pattern": row[3],
                "match_type": row[4],
                "priority": row[5],
                "enabled": bool(row[6]),
            }
            for row in cur.fetchall()
        ]

    def add_manual_match(self, transaction_id: int, category_id: int) -> int:
        """Add a one-off manual match (id_match). Returns the id_match id."""
        cur = self.db.get_cursor()
        cur.execute(
            "INSERT OR REPLACE INTO id_matches (transaction_id, category_id) VALUES (?, ?)",
            (transaction_id, category_id),
        )
        # Also update the transaction's category_id and clear rule tracking
        cur.execute(
            "UPDATE transactions SET category_id = ?, matched_rule_id = NULL WHERE id = ?",
            (category_id, transaction_id),
        )
        self.db.commit()
        return cur.lastrowid

    # ------------------------------------------------------------------ #
    #  Queries
    # ------------------------------------------------------------------ #

    def get_uncategorized(self) -> list[dict]:
        """Return all transactions without a category."""
        cur = self.db.get_cursor()
        cur.execute(
            "SELECT id, date, description, amount, account "
            "FROM transactions WHERE category_id IS NULL "
            "ORDER BY date DESC"
        )
        return [
            {
                "id": row[0],
                "date": row[1],
                "description": row[2],
                "amount": row[3],
                "account": row[4],
            }
            for row in cur.fetchall()
        ]

    def preview_rule(
        self, pattern: str, match_type: str = "regex", limit: int = 20
    ) -> list[dict]:
        """Show transactions that would match a rule (dry run).

        Returns a list of matching transaction dicts.
        """
        cur = self.db.get_cursor()
        cur.execute(
            "SELECT id, date, description, amount, account FROM transactions"
        )
        matches = []
        for row in cur.fetchall():
            txn = {
                "id": row[0],
                "date": row[1],
                "description": row[2],
                "amount": row[3],
                "account": row[4],
            }
            if self._match_description(pattern, match_type, txn["description"]):
                matches.append(txn)
                if len(matches) >= limit:
                    break
        return matches

    # ------------------------------------------------------------------ #
    #  Category management
    # ------------------------------------------------------------------ #

    def add_category(
        self, name: str, parent_id: int | None = None, description: str = None
    ) -> int:
        """Add a new category. Returns the category id."""
        cur = self.db.get_cursor()
        cur.execute(
            "INSERT INTO categories (name, parent_id, description) VALUES (?, ?, ?)",
            (name, parent_id, description),
        )
        self.db.commit()
        return cur.lastrowid

    def get_category_by_name(self, name: str) -> dict | None:
        """Look up a category by name."""
        cur = self.db.get_cursor()
        cur.execute(
            "SELECT id, name, parent_id, description FROM categories WHERE name = ?",
            (name,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "parent_id": row[2], "description": row[3]}

    def list_categories(self) -> list[dict]:
        """Return all categories."""
        cur = self.db.get_cursor()
        cur.execute("SELECT id, name, parent_id, description FROM categories ORDER BY name")
        return [
            {"id": row[0], "name": row[1], "parent_id": row[2], "description": row[3]}
            for row in cur.fetchall()
        ]

    def get_children(self, category_id: int) -> list[dict]:
        """Return direct children of a category."""
        cur = self.db.get_cursor()
        cur.execute(
            "SELECT id, name, parent_id, description FROM categories WHERE parent_id = ?",
            (category_id,),
        )
        return [
            {"id": row[0], "name": row[1], "parent_id": row[2], "description": row[3]}
            for row in cur.fetchall()
        ]
