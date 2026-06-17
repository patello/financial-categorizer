---
name: financial-categorizer
description: "Process bank transaction CSV exports (Nordea, ICA), auto-categorize transactions using configurable rules, manage transaction links, and generate analytical database views."
---

# financial-categorizer

Process bank transaction CSV exports, auto-categorize transactions using configurable rules, manage transaction links, and generate analytical SQLite database views.

## Quick Start

Run the CLI tool from your terminal pointing to your database path:

```bash
# 1. Add your main checking account
python cli.py --db ../data/finance.db add-account "Nordea Checking" --type tracked --ownership 1.0

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

## Security & Data Integrity

This tool modifies your local SQLite database. To prevent accidental data loss, please observe the following guidelines:

> [!WARNING]
> Always make a backup of your database before performing database cleanup, auto-linking, or destructive operations:
> ```bash
> # Simple file copy backup
> cp data/finance.db data/finance.db.bak
> 
> # Safe SQLite backup command
> sqlite3 data/finance.db ".backup data/finance.db.bak"
> ```

### Destructive Operations & Confirmation Prompts
Destructive commands require interactive confirmation `[y/N]` when run in a terminal (TTY). If you are running these commands in automated scripts or non-interactive shells, you must pass the `--yes` or `-y` flag to bypass the prompt; otherwise, the command will abort with an error.

The following commands require confirmation:
- `delete-account <id> [--yes]`
- `delete-category <id> [--yes] [--reassign <id>] [--force]`
- `remove-rule <id> [--yes]`
- `unlink <id> [--yes]`
- `db-cleanup [--yes] [--dry-run]`
- `remove-transfer-rule <id> [--yes]`
- `auto-link [--yes] [--dry-run]`

## CLI Reference

| Command | Description |
|---------|-------------|
| `import <files>` | Import bank CSV transactions |
| `accounts` | List all registered bank accounts |
| `add-account <name>` | Create a new bank account |
| `update-account <id>` | Update account ownership ratio, type, name, etc. |
| `delete-account <id> [--yes]` | Delete a bank account (requires confirmation or `-y`) |
| `categories` | List all categories in tree view |
| `add-category <name> [--associated-account <name_or_id>]` | Create a new category, optionally associated with an external account |
| `update-category <id> [--associated-account <name_or_id>]` | Update category parents or fields (use `none` to clear association) |
| `delete-category <id> [--yes]` | Delete a category (requires confirmation or `-y`) |
| `rules` | List all auto-categorization rules |
| `add-rule <cat_id> <pattern>` | Add a categorization rule (regex, contains, exact) |
| `remove-rule <id> [--yes]` | Remove an auto-categorization rule (requires confirmation or `-y`) |
| `preview <pattern>` | Preview which transactions match a pattern before adding a rule |
| `categorize [--all]` | Run auto-categorization rules |
| `uncategorized` | List all uncategorized transactions |
| `manual-match <txn_id> <cat_id>` | Manually assign a category override to a transaction |
| `stats-summary [--period-type <type>]` | Monthly summary of income, expenses, and net |
| `stats-category <name> [--period-type <type>]` | Category total with subcategory rollups |
| `stats-trend <name> [--period-type <type>]` | Monthly trend for a category |
| `stats-top [--period-type <type>]` | Top spending categories sorted by total expenses |
| `stats-transfers [--month <YYYY-MM>] [--period-type <type>]` | Net capital transfers to external accounts |
| `stats-cashflow [--month <YYYY-MM>] [--period-type <type>]` | Monthly cash flow summary (Operating, Transfers, Net) |
| `link <from_id> [to_id] --type [--to-account <name_or_id>]` | Link transactions (specify `--to-account` for external transfers) |
| `unlink <id> [--yes]` | Remove a link (requires confirmation or `-y`) |
| `links` | List all transaction links |
| `auto-link [--dry-run] [--yes]` | Auto-detect and link internal transfers using transfer rules (requires confirmation or `-y` when not running dry-run) |
| `recalculate` | Manually recalculate adjusted amounts for all transactions |
| `db-cleanup [--dry-run] [--yes]` | Purge orphaned transaction links and rules (Integrity Cleanup) (requires confirmation or `-y` when not running dry-run) |
| `remove-transfer-rule <id> [--yes]` | Remove a transfer detection rule (requires confirmation or `-y`) |
| `salary-config` | Show current salary period configuration |
| `set-salary-mode <mode>` | Set the salary period mode (`calendar`, `fixed`, `salary`) |
| `set-salary-day <day>` | Set the fixed boundary day of the month (1-28) |
| `set-salary-category <name>` | Set the category name used to scan for salary paydays |

## Configuring Salary Periods

By default, the salary period boundary is fixed to the 25th of the month. You can customize this grouping behavior using the salary configuration commands.

### Available Modes:
1. **`calendar`**: Group transactions by calendar months (1st to the last day).
2. **`fixed`**: Group transactions by a static day of the month (e.g., the 25th). Transactions on or after this day are grouped into the next month's period.
3. **`salary`**: Group transactions by automatically detecting the primary salary deposit date in each month (the transaction under the salary category with the largest positive amount).

### CLI Configuration Commands:
```bash
# View current configuration
python cli.py salary-config

# Change mode to salary (automatic payday detection)
python cli.py set-salary-mode salary

# Set the category name used to search for paydays (default is "Salary")
python cli.py set-salary-category "Salary"

# Change mode to a fixed day of the month (e.g. 27th)
python cli.py set-salary-mode fixed
python cli.py set-salary-day 27
```

> [!WARNING]
> If you choose the **`fixed`** day mode, be aware that bank deposits and transactions can shift early or late due to weekends and holidays.
> - Ensure your fixed day is configured early or late enough so that fluctuations in actual payday do not cause two salary deposits to fall into the same period (which would result in one month showing double income and the next showing zero income).
> - Alternatively, use the **`salary`** mode, which automatically detects the actual deposit transaction dates and shifts the boundaries dynamically.

### Querying Statistics by Salary Period
All statistics and breakdown commands support the `--period-type` parameter:
* `calendar` — Force standard calendar month boundaries.
* `salary` — Force salary period boundaries (using the active `salary-config` settings).
* `default` — Dynamically resolve to your active `salary-config` mode:
  - If mode is `calendar`, defaults to calendar months.
  - If mode is `fixed` or `salary`, defaults to salary periods.

For example, to query your housing category spending using the active salary period:
```bash
python cli.py stats-category Housing --period-type salary --month 2026-06
```

If you do not specify a `--period-type` flag, it will automatically default to the setting configured via `set-salary-mode`.

## Common Workflows

### Handling Shared-Expense Reimbursements

If you make a shared purchase (e.g., from the `Gemensamt` account, 50% ownership) and get reimbursed by an external person (e.g., via Swish to your `Personligt` account, 100% ownership) and subsequently transfer the payback to the shared account:

1. **Reimburse the shared expense**: Link the reimbursement transaction (the Swish inflow) directly to the original expense transaction (the shared purchase):
   ```bash
   python cli.py --db data/finance.db link <swish_transaction_id> <expense_transaction_id> --type reimbursement --ratio 1.0
   ```
   * *Effect*: The Swish transaction is fully neutralized to `0.00` adjusted amount, and the credit to the expense transaction is automatically scaled by the shared account's ownership ratio (e.g., 50%), reducing your net cost correctly.
   * *Note*: The credit is scaled by the target account's ownership ratio (e.g., 50%) because the benefit of the payback is shared between the joint account owners.

2. **Link the account transfer**: Link the outflow from your main account to the inflow on your joint account as an internal transfer:
   ```bash
   python cli.py --db data/finance.db link <transfer_out_id> <transfer_in_id> --type internal_transfer
   ```
   * *Effect*: Both sides of the transfer are neutralized to `0.00`, ensuring no false income or outflows are recorded.
   * *Note*: This step is skipped if the transfer has already been auto-linked.

## Tracking External Accounts

You can track capital transfers from your tracked accounts to untracked external accounts (such as savings or stock brokerage accounts).

### Setup and Workflow:
1. **Create the External Account**:
   ```bash
   python cli.py add-account "Avanza Brokerage" --type external
   ```
2. **Associate a Category**:
   Create a category of type `transfer` associated with this external account:
   ```bash
   python cli.py add-category "Brokerage Transfer" --type transfer --associated-account "Avanza Brokerage"
   ```
3. **Add a Categorization Rule**:
   Add a match rule to auto-categorize transfers:
   ```bash
   python cli.py add-rule <category_id> "AVANZA" --type contains
   ```
4. **Auto-linking**:
   When transactions are categorized (via `categorize` or manual overrides), if they match a transfer category linked to an external account, an `external_transfer` link is created automatically.

### Manual Linking:
For one-off transfers, you can link a transaction directly to an external account:
```bash
python cli.py link <transaction_id> --type external_transfer --to-account "Avanza Brokerage"
```

### Querying Statistics:
Use the `stats-transfers` command to view net capital movements per external account:
```bash
python cli.py stats-transfers --month 2026-06
```

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
8. **`v_salary_period_summary`** — Expense/income summary grouped by salary periods (using the active salary config: fixed or salary).
9. **`v_breakout_categories`** — Groups monthly spending into high-level categories (Groceries, Loans, Housing, Leisure, Car, etc.).
10. **`v_uncategorized_groups`** — Groups uncategorized transactions by normalized Swish/Card payment descriptions to identify potential new rules.

## Dependencies

- `pytest` - For testing suite
- Standard library modules: `sqlite3`, `csv`, `datetime`, `logging`, `re`, `argparse`, `os`

Install: `pip install -e .`
