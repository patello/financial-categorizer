# Post-GitHub Tasks Checklist

This checklist tracks remaining tasks for the `financial-categorizer` project. Agents or developers working on this project should read and update this file as tasks are completed.

---

## 1. Recalculation Performance Optimization
* **Goal**: Prevent calling `self.db.recalculate_adjusted_amounts()` (which runs a full-table update) inside loops during batch categorization.
* **Details**:
  - In [categorizer.py](file:///c:/Users/patrk/.gemini/antigravity/scratch/financial-tracker/financial_categorizer/categorizer.py), `_link_external_transfer` triggers a recalculation whenever a transfer is linked.
  - During batch operations (like `categorize_all` or `categorize_new`), this causes O(N^2) DB updates.
  - **Fix**: Skip calling it inside `_link_external_transfer` during a batch loop, and call it exactly once at the end of the batch process.
* **Checklist**:
  - [x] Modify `categorizer.py` to support batch mode/deferred recalculation.
  - [x] Verify that all test cases still pass.

---

## 2. CLI Database Cleanup Utility
* **Goal**: Restore relational database integrity and fix orphaned records due to historically disabled foreign key constraints.
* **Details**:
  - Stale/orphaned foreign key rows exist in `id_matches` and `transaction_links` pointing to non-existent transaction IDs.
  - **Fix**: Add a `db-cleanup` command in [cli.py](file:///c:/Users/patrk/.gemini/antigravity/scratch/financial-tracker/cli.py) that detects and deletes these orphaned entries.
* **Checklist**:
  - [x] Implement query in `cli.py` to identify orphaned `transaction_links` and `id_matches`.
  - [x] Implement CLI command to run this purge query safely.
  - [x] Add unit/integration tests for the cleanup utility.

---

## 3. Grafana-Specific SQL Views
* **Goal**: Provide helper analytical SQL views dynamically so Grafana dashboards can run fast, lightweight queries.
* **Details**:
  - In [stats.py](file:///c:/Users/patrk/.gemini/antigravity/scratch/financial-tracker/financial_categorizer/stats.py), add:
    1. `v_cumulative_spending_monthly`: Running MTD daily spending.
    2. `v_daily_spending_moving_average`: 30-day moving average of daily spending.
    3. `v_category_monthly_averages`: Average monthly spending by category (replacing legacy static `average_tags.txt`).
* **Checklist**:
  - [x] Add view creation queries to `_ensure_views()` in `stats.py`.
  - [x] Write tests to verify views are correctly created and queryable.

---

## 4. Stale Database Tables Audit
* **Goal**: Inspect and drop physical tables `v_breakout_categories` and `v_uncategorized_groups` in the local `finance.db`.
* **Details**:
  - These are physical tables containing stale, static data with encoding issues, but they start with `v_` (typically used for views).
  - If Grafana dashboards query them, convert them to dynamic SQL views instead. If not, drop them.
* **Checklist**:
  - [ ] Audit Grafana queries to see if either table is used.
  - [ ] Safely drop or convert the tables to views.
