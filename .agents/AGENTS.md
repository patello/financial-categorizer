# Workspace Agent Rules

## Handling Reimbursements & Composite Transactions

- **Salary as Reimbursement Source:** If the user states that an expense (such as a credit card bill or a purchase) was reimbursed on their salary (or is "baked into" their salary), you **can and should** link the salary transaction (positive inflow) to the expense transaction (negative outflow).
- **No Manual Adjustments:** Never manually adjust transaction values (e.g., setting `adjusted_amount = 0` manually without a link). Always use transaction links (`link` command) to handle adjustments semantically.
- **Composite Transaction Separation:** Never assume a transaction labeled "Salary" is purely labor income if the user states a reimbursement is included. The system is designed to handle composite transactions by adjusting the effective amounts using the link ratio.
- **Using the Link Command:** 
  - Do not tell the user that linking is "not possible" because there is no separate reimbursement transaction.
  - Instead, recommend linking the salary transaction to the expense transaction.
  - Recommend using the `--ratio-to 1.0` flag (or specifying the exact `--amount`) to automatically calculate the correct fractional ratio. This will cleanly split the salary into its income portion and reimbursement portion without inflating your reported gross numbers.
