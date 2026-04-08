# financial-categorizer

Personal finance transaction categorization with a SQLite backend.

Imports bank CSV files, auto-categorizes transactions using configurable rules, and provides SQL views for dashboards and analysis.

## Features

- **Multi-account support** — personal, shared, savings, external accounts with ownership ratios
- **Auto-categorization** — regex, exact, and contains match rules with priority ordering and manual overrides
- **Transaction linking** — mark transfers and reimbursements between transactions; adjusted amounts are pre-computed
- **SQL views** — ready-to-query views for monthly summaries, category breakdowns, and daily spending
- **CSV import** — auto-detects Nordea and ICA formats, handles pending transactions
- **CLI** — full command-line interface for all operations

## Install

```bash
pip install -e .
```

Requires Python 3.10+.

## Quick Start

```bash
# Import transactions from a CSV
financial-categorizer import transactions.csv --account "Nordea Checking"

# Add categorization rules
financial-categorizer add-rule 3 "ICA MAXI" --type contains
financial-categorizer add-rule 4 "^Hyra" --type regex

# Categorize uncategorized transactions
financial-categorizer categorize

# View stats
financial-categorizer stats-summary --month 2026-04
financial-categorizer stats-top --limit 10
financial-categorizer stats-category Food --month 2026-04

# Link a transfer between accounts
financial-categorizer link 1 2 --type internal_transfer

# Recalculate adjusted amounts
financial-categorizer recalculate
```

## Architecture

All data lives in a single SQLite database (`finance.db` by default).

### Core tables
- **accounts** — bank accounts with type and ownership ratio
- **transactions** — imported transactions with `adjusted_amount` (pre-computed)
- **categories** — hierarchical categories (parent/child)
- **match_rules** — patterns for auto-categorization
- **transaction_links** — connects transfers and reimbursements

### Views
- `v_effective_transactions` — all transactions with adjusted amounts
- `v_monthly_summary` — income, expenses, net per month
- `v_category_monthly` — category totals per month
- `v_daily_spending` — daily spending breakdown

### Adjusted amounts

`adjusted_amount` is a denormalized column on every transaction, pre-computed from:

1. `amount * account.ownership_ratio` (base)
2. Transaction link adjustments (transfers neutralize both sides, external transfers zero out)

Stats and views read this column directly — no JOINs at query time. Run `recalculate` to refresh after any manual changes.

## CLI Reference

| Command | Description |
|---|---|
| `import <files>` | Import CSV transactions |
| `accounts` | List accounts |
| `add-account <name>` | Create an account |
| `update-account <id>` | Update account fields |
| `delete-account <id>` | Delete an account |
| `categories` | List categories (tree view) |
| `add-category <name>` | Create a category |
| `update-category <id>` | Update category fields |
| `delete-category <id>` | Delete a category |
| `rules` | List match rules |
| `add-rule <cat> <pattern>` | Add a categorization rule |
| `remove-rule <id>` | Remove a rule |
| `preview <pattern>` | Preview what a rule would match |
| `categorize [--all]` | Run auto-categorization |
| `uncategorized` | Show uncategorized transactions |
| `manual-match <txn> <cat>` | Manually assign a category |
| `stats-summary` | Monthly income/expenses/net |
| `stats-category <name>` | Category total with subcategory rollup |
| `stats-trend <name>` | Monthly breakdown for a category |
| `stats-top` | Top spending categories |
| `link <from> [to] --type` | Link transactions |
| `unlink <id>` | Remove a link |
| `links` | List transaction links |
| `recalculate` | Refresh adjusted_amount |

## Testing

```bash
pip install pytest
pytest tests/
```
