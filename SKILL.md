---
name: financial-categorizer
description: "Process bank transaction CSV exports (Nordea, ICA), auto-categorize transactions using configurable rules, manage transaction links, and generate analytical database views."
---

# financial-categorizer

Process bank transaction CSV exports, auto-categorize transactions using configurable rules, manage transaction links, and generate analytical SQLite database views.

## Quick Start

Run the CLI tool from your terminal pointing to your database path:

```bash
# 1. Add your personal checking account
python cli.py --db ../data/finance.db add-account "Nordea Checking" --type personal --ownership 1.0

# 2. Add hierarchical categories
python cli.py --db ../data/finance.db add-category "Food"
python cli.py --db ../data/finance.db add-category "Groceries" --parent 1

# 3. Add auto-categorization rules
python cli.py --db ../data/finance.db add-rule 2 "ICA MAXI" --type contains
python cli.py --db ../data/finance.db add-rule 1 "^Hyra" --type regex

# 4. Import transactions from a bank CSV file
python cli.py --db ../data/finance.db import transactions.csv --account "Nordea Checking"

# 5. Run auto-categorization over uncategorized transactions
python cli.py --db ../data/finance.db categorize

# 6. View monthly summary statistics
python cli.py --db ../data/finance.db stats-summary
```

## Data Storage Pattern

**User data lives OUTSIDE the skill directory.** Recommended structure:

```
workspace-finance/
├── skills/financial-categorizer/   # Portable skill (shareable)
│   ├── SKILL.md
│   ├── cli.py
│   ├── setup.py
│   └── financial_categorizer/
└── data/                           # Your private data
    ├── finance.db
    └── exports/
        ├── Nordea_Checking.csv
        └── ICA_Shared.csv
```

The skill provides logic. Your data stays private and portable.

## CLI Reference

| Command | Description |
|---------|-------------|
| `import <files>` | Import bank CSV transactions |
| `accounts` | List all registered bank accounts |
| `add-account <name>` | Create a new bank account |
| `update-account <id>` | Update account ownership ratio, type, name, etc. |
| `delete-account <id>` | Delete a bank account |
| `categories` | List all categories in tree view |
| `add-category <name>` | Create a new category |
| `update-category <id>` | Update category parents or fields |
| `delete-category <id>` | Delete a category |
| `rules` | List all auto-categorization rules |
| `add-rule <cat_id> <pattern>` | Add a categorization rule (regex, contains, exact) |
| `remove-rule <id>` | Remove an auto-categorization rule |
| `preview <pattern>` | Preview which transactions match a pattern before adding a rule |
| `categorize [--all]` | Run auto-categorization rules |
| `uncategorized` | List all uncategorized transactions |
| `manual-match <txn_id> <cat_id>` | Manually assign a category override to a transaction |
| `stats-summary` | Monthly summary of income, expenses, and net |
| `stats-category <name>` | Category total with subcategory rollups |
| `stats-trend <name>` | Monthly trend for a category |
| `stats-top` | Top spending categories sorted by total expenses |
| `link <from_id> [to_id] --type`| Link transactions (e.g. transfers, reimbursements) |
| `unlink <id>` | Remove a link |
| `links` | List all transaction links |
| `recalculate` | Manually recalculate adjusted amounts for all transactions |
| `db-cleanup [--dry-run]` | Purge orphaned transaction links and rules (Integrity Cleanup) |

## Skill Contents

```
financial-categorizer/
├── SKILL.md                    # This file
├── requirements.txt            # pip dependencies
├── setup.py                    # setuptools configuration
├── cli.py                      # Main entrypoint
└── financial_categorizer/      # Package code
    ├── __init__.py
    ├── categorizer.py          # Auto-categorization & rule engine
    ├── db_handler.py           # Database CRUD & raw schema setup
    ├── importer.py             # CSV Parser (Nordea & ICA formats)
    └── stats.py                # SQL View registers and stats math
```

## SQLite Database Schema & Views

This skill utilizes a dynamic database schema. Analytical SQL views are registered dynamically to provide high performance and low-overhead querying for dashboards (e.g., Grafana):

1. **`v_effective_transactions`** — Joins transactions with accounts to factor in ownership ratios and transfer link adjustments.
2. **`v_monthly_summary`** — Calculates net income/expenses by month.
3. **`v_category_monthly`** — Calculates monthly spending by category.
4. **`v_daily_spending`** — Daily expense aggregation.
5. **`v_cumulative_spending_monthly`** — Running month-to-date daily cumulative spending.
6. **`v_daily_spending_moving_average`** — 30-day moving average of daily spending.
7. **`v_category_monthly_averages`** — Average monthly spending by category.
8. **`v_salary_period_summary`** — Expense/income summary grouped by salary periods (25th to 24th).
9. **`v_breakout_categories`** — Groups monthly spending into high-level categories (Groceries, Loans, Housing, Leisure, Car, etc.).
10. **`v_uncategorized_groups`** — Groups uncategorized transactions by normalized Swish/Card payment descriptions to identify potential new rules.

## Dependencies

- `pytest` - For testing suite
- Standard library modules: `sqlite3`, `csv`, `datetime`, `logging`, `re`, `argparse`, `os`

Install: `pip install -e .`
