import pytest
import sys
from unittest.mock import patch
from financial_categorizer.db_handler import DatabaseHandler
from financial_categorizer.categorizer import Categorizer
from cli import main, confirm_action

@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_finance.db"
    handler = DatabaseHandler(str(db_file))
    handler.connect()
    yield handler
    handler.disconnect()

def test_confirm_action_yes():
    # Bypass confirmation
    assert confirm_action("Prompt", yes_flag=True) is True

def test_confirm_action_non_interactive():
    # If not interactive and yes_flag is False, should raise SystemExit(1)
    with patch("sys.stdin.isatty", return_value=False):
        with pytest.raises(SystemExit) as exc_info:
            confirm_action("Prompt", yes_flag=False)
        assert exc_info.value.code == 1

def test_confirm_action_interactive_yes():
    # Interactive 'yes'
    with patch("sys.stdin.isatty", return_value=True):
        with patch("builtins.input", return_value="y"):
            assert confirm_action("Prompt", yes_flag=False) is True
        with patch("builtins.input", return_value="yes"):
            assert confirm_action("Prompt", yes_flag=False) is True

def test_confirm_action_interactive_no():
    # Interactive 'no' should raise SystemExit(0)
    with patch("sys.stdin.isatty", return_value=True):
        with patch("builtins.input", return_value="n"):
            with pytest.raises(SystemExit) as exc_info:
                confirm_action("Prompt", yes_flag=False)
            assert exc_info.value.code == 0

def test_cli_delete_account_yes_flag(temp_db, monkeypatch, capsys):
    aid = temp_db.add_account("Test Account")
    test_args = ["cli.py", "--db", temp_db.db_file, "delete-account", str(aid), "--yes"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert f"Deleted account {aid}" in captured.out
    assert temp_db.get_account(aid) is None

def test_cli_delete_account_interactive_no(temp_db, monkeypatch, capsys):
    aid = temp_db.add_account("Test Account")
    test_args = ["cli.py", "--db", temp_db.db_file, "delete-account", str(aid)]
    monkeypatch.setattr(sys, "argv", test_args)
    with patch("sys.stdin.isatty", return_value=True):
        with patch("builtins.input", return_value="n"):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "Aborted." in captured.out
    assert temp_db.get_account(aid) is not None

def test_cli_delete_category_yes_flag(temp_db, monkeypatch, capsys):
    cat = Categorizer(temp_db)
    cid = cat.add_category("Test Category")
    test_args = ["cli.py", "--db", temp_db.db_file, "delete-category", str(cid), "--yes"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert f"Deleted category {cid}" in captured.out
    assert cat.get_category(cid) is None

def test_cli_remove_rule_yes_flag(temp_db, monkeypatch, capsys):
    cat = Categorizer(temp_db)
    cid = cat.add_category("Test Category")
    rid = cat.add_rule(cid, "ICA MAXI")
    test_args = ["cli.py", "--db", temp_db.db_file, "remove-rule", str(rid), "--yes"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert f"Removed rule {rid}" in captured.out

def test_cli_unlink_yes_flag(temp_db, monkeypatch, capsys):
    cur = temp_db.get_cursor()
    aid = temp_db.add_account("Checking")
    cur.execute("INSERT INTO transactions (account_id, date, description, amount) VALUES (?, '2026-06-01', 'Tx1', -100.0)", (aid,))
    t1 = cur.lastrowid
    cur.execute("INSERT INTO transactions (account_id, date, description, amount) VALUES (?, '2026-06-01', 'Tx2', 100.0)", (aid,))
    t2 = cur.lastrowid
    cur.execute("INSERT INTO transaction_links (from_transaction_id, to_transaction_id, link_type, ratio) VALUES (?, ?, 'internal_transfer', 1.0)", (t1, t2))
    lid = cur.lastrowid
    temp_db.commit()

    test_args = ["cli.py", "--db", temp_db.db_file, "unlink", str(lid), "--yes"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert f"Removed link {lid}" in captured.out

def test_cli_db_cleanup_yes_flag(temp_db, monkeypatch, capsys):
    # Temporarily disable foreign keys to insert orphaned record
    cur = temp_db.get_cursor()
    cur.execute("PRAGMA foreign_keys = OFF;")
    cur.execute("INSERT INTO id_matches (transaction_id, category_id) VALUES (99999, 1)")
    temp_db.commit()
    cur.execute("PRAGMA foreign_keys = ON;")

    test_args = ["cli.py", "--db", temp_db.db_file, "db-cleanup", "--yes"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert "Deleted 1 orphaned id_matches record(s)" in captured.out

def test_cli_remove_transfer_rule_yes_flag(temp_db, monkeypatch, capsys):
    rid = temp_db.add_transfer_rule("Test Pattern")
    test_args = ["cli.py", "--db", temp_db.db_file, "remove-transfer-rule", str(rid), "--yes"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert f"Removed transfer rule {rid}" in captured.out


def test_cli_auto_link_yes_flag(temp_db, monkeypatch, capsys):
    aid1 = temp_db.add_account("Checking")
    aid2 = temp_db.add_account("Savings")

    cur = temp_db.get_cursor()
    cur.execute(
        "INSERT INTO transactions (date, description, amount, account_id) VALUES ('2026-06-01', 'Transfer to savings', -150.0, ?)",
        (aid1,)
    )
    cur.execute(
        "INSERT INTO transactions (date, description, amount, account_id) VALUES ('2026-06-01', 'Transfer from checking', 150.0, ?)",
        (aid2,)
    )

    temp_db.add_transfer_rule("transfer")
    temp_db.commit()

    test_args = ["cli.py", "--db", temp_db.db_file, "auto-link", "--yes"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()

    captured = capsys.readouterr()
    assert "Linked 1 internal transfer(s):" in captured.out

    cur.execute("SELECT COUNT(*) FROM transaction_links")
    assert cur.fetchone()[0] == 1


def test_cli_auto_link_interactive_no(temp_db, monkeypatch, capsys):
    aid1 = temp_db.add_account("Checking")
    aid2 = temp_db.add_account("Savings")

    cur = temp_db.get_cursor()
    cur.execute(
        "INSERT INTO transactions (date, description, amount, account_id) VALUES ('2026-06-01', 'Transfer to savings', -150.0, ?)",
        (aid1,)
    )
    cur.execute(
        "INSERT INTO transactions (date, description, amount, account_id) VALUES ('2026-06-01', 'Transfer from checking', 150.0, ?)",
        (aid2,)
    )

    temp_db.add_transfer_rule("transfer")
    temp_db.commit()

    test_args = ["cli.py", "--db", temp_db.db_file, "auto-link"]
    monkeypatch.setattr(sys, "argv", test_args)
    with patch("sys.stdin.isatty", return_value=True):
        with patch("builtins.input", return_value="n"):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    captured = capsys.readouterr()
    assert "Auto-Link Preview:" in captured.out
    assert "Aborted." in captured.out

    cur.execute("SELECT COUNT(*) FROM transaction_links")
    assert cur.fetchone()[0] == 0


def test_cli_link_modes(temp_db, monkeypatch, capsys):
    aid = temp_db.add_account("Checking")
    cur = temp_db.get_cursor()
    cur.execute("UPDATE accounts SET ownership_ratio = 1.0 WHERE id = ?", (aid,))
    
    # Insert from transaction (salary) and to transaction (expense)
    cur.execute("INSERT INTO transactions (account_id, date, description, amount) VALUES (?, '2026-05-22', 'Salary', 57683.0)", (aid,))
    t_from = cur.lastrowid
    cur.execute("INSERT INTO transactions (account_id, date, description, amount) VALUES (?, '2026-05-25', 'First Card', -4981.23)", (aid,))
    t_to = cur.lastrowid
    temp_db.commit()

    # 1. Test dry-run with --ratio-to 1.0
    test_args = ["cli.py", "--db", temp_db.db_file, "link", str(t_from), str(t_to), "--type", "reimbursement", "--ratio-to", "1.0", "--dry-run"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    
    captured = capsys.readouterr()
    assert "Link Preview (DRY RUN - NO CHANGES MADE):" in captured.out
    assert "Calculated DB Ratio: 0.086355" in captured.out
    # Verify no links in DB
    cur.execute("SELECT COUNT(*) FROM transaction_links")
    assert cur.fetchone()[0] == 0

    # 2. Test actual link with --ratio-to 1.0
    test_args = ["cli.py", "--db", temp_db.db_file, "link", str(t_from), str(t_to), "--type", "reimbursement", "--ratio-to", "1.0"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    
    captured = capsys.readouterr()
    assert "Link Preview:" in captured.out
    assert "Created link" in captured.out
    
    # Verify link exists and ratio is correct in DB
    cur.execute("SELECT ratio FROM transaction_links")
    db_ratio = cur.fetchone()[0]
    assert abs(db_ratio - 0.08635525) < 1e-6

    # Verify transaction adjusted amounts recalculation
    cur.execute("SELECT adjusted_amount FROM transactions WHERE id = ?", (t_from,))
    assert abs(cur.fetchone()[0] - 52701.77) < 1e-2
    cur.execute("SELECT adjusted_amount FROM transactions WHERE id = ?", (t_to,))
    assert abs(cur.fetchone()[0] - 0.0) < 1e-2


def test_cli_link_amount_mode(temp_db, monkeypatch, capsys):
    aid = temp_db.add_account("Checking")
    cur = temp_db.get_cursor()
    cur.execute("UPDATE accounts SET ownership_ratio = 1.0 WHERE id = ?", (aid,))
    
    # Insert from transaction (salary) and to transaction (expense)
    cur.execute("INSERT INTO transactions (account_id, date, description, amount) VALUES (?, '2026-05-22', 'Salary', 57683.0)", (aid,))
    t_from = cur.lastrowid
    cur.execute("INSERT INTO transactions (account_id, date, description, amount) VALUES (?, '2026-05-25', 'First Card', -4981.23)", (aid,))
    t_to = cur.lastrowid
    temp_db.commit()

    # Link with exact amount
    test_args = ["cli.py", "--db", temp_db.db_file, "link", str(t_from), str(t_to), "--type", "reimbursement", "--amount", "4981.23"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    
    captured = capsys.readouterr()
    assert "Calculated DB Ratio: 0.086355" in captured.out
    
    cur.execute("SELECT ratio FROM transaction_links")
    db_ratio = cur.fetchone()[0]
    assert abs(db_ratio - 0.08635525) < 1e-6


def test_cli_link_mutually_exclusive(temp_db, monkeypatch, capsys):
    test_args = ["cli.py", "--db", temp_db.db_file, "link", "1", "2", "--type", "reimbursement", "--ratio", "1.0", "--amount", "100.0"]
    monkeypatch.setattr(sys, "argv", test_args)
    
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code in (2, 1)


def test_cli_transactions(temp_db, monkeypatch, capsys):
    aid = temp_db.add_account("Checking")
    cur = temp_db.get_cursor()
    
    cur.execute("INSERT INTO categories (name) VALUES ('Food')")
    cat_food = cur.lastrowid
    
    # Insert transactions
    cur.execute(
        "INSERT INTO transactions (account_id, date, description, amount, adjusted_amount, category_id) "
        "VALUES (?, '2026-05-22', 'ICA Kvantum', -100.0, -100.0, ?)",
        (aid, cat_food),
    )
    cur.execute(
        "INSERT INTO transactions (account_id, date, description, amount, adjusted_amount) "
        "VALUES (?, '2026-05-23', 'Uncat purchase', -50.0, -50.0)",
        (aid,),
    )
    temp_db.commit()
    
    # 1. Test basic listing
    test_args = ["cli.py", "--db", temp_db.db_file, "transactions", "--limit", "10"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert "Transactions (2):" in captured.out
    assert "ICA Kvantum" in captured.out
    assert "Uncat purchase" in captured.out

    # 2. Test filtering by category
    test_args = ["cli.py", "--db", temp_db.db_file, "transactions", "--category", "Food"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert "Transactions (1):" in captured.out
    assert "ICA Kvantum" in captured.out
    assert "Uncat purchase" not in captured.out

    # 3. Test filtering by uncategorized
    test_args = ["cli.py", "--db", temp_db.db_file, "transactions", "--uncategorized"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert "Transactions (1):" in captured.out
    assert "Uncat purchase" in captured.out
    assert "ICA Kvantum" not in captured.out


def test_cli_uncategorized_non_zero(temp_db, monkeypatch, capsys):
    aid = temp_db.add_account("Checking")
    cur = temp_db.get_cursor()
    
    # Insert uncategorized transactions, one non-zero and one zero adjusted_amount
    cur.execute(
        "INSERT INTO transactions (account_id, date, description, amount, adjusted_amount) "
        "VALUES (?, '2026-05-23', 'Flat Purchase', -50.0, -50.0)",
        (aid,),
    )
    cur.execute(
        "INSERT INTO transactions (account_id, date, description, amount, adjusted_amount) "
        "VALUES (?, '2026-05-24', 'Swish Reimbursed', 50.0, 0.0)",
        (aid,),
    )
    temp_db.commit()
    
    # 1. Test basic listing (shows both)
    test_args = ["cli.py", "--db", temp_db.db_file, "uncategorized"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert "Uncategorized transactions (2):" in captured.out
    assert "Flat Purchase" in captured.out
    assert "Swish Reimbursed" in captured.out
    
    # 2. Test --non-zero listing (shows only non-zero)
    test_args = ["cli.py", "--db", temp_db.db_file, "uncategorized", "--non-zero"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert "Uncategorized transactions (1):" in captured.out
    assert "Flat Purchase" in captured.out
    assert "Swish Reimbursed" not in captured.out

    # 3. Test grouped --non-zero listing
    test_args = ["cli.py", "--db", temp_db.db_file, "uncategorized", "--group", "--non-zero"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert "Uncategorized by description (1 groups):" in captured.out
    assert "Flat Purchase" in captured.out
    assert "Swish Reimbursed" not in captured.out


def test_cli_rules_with_transaction_id(temp_db, monkeypatch, capsys):
    aid = temp_db.add_account("Checking")
    cur = temp_db.get_cursor()
    
    cur.execute("INSERT INTO categories (name) VALUES ('Food')")
    cat_food = cur.lastrowid
    
    # Insert rule
    cur.execute(
        "INSERT INTO match_rules (category_id, pattern, match_type, enabled) "
        "VALUES (?, 'ICA', 'contains', 1)",
        (cat_food,),
    )
    rule_id = cur.lastrowid
    
    # Insert transactions
    # 1. Rule-categorized
    cur.execute(
        "INSERT INTO transactions (account_id, date, description, amount, category_id, matched_rule_id) "
        "VALUES (?, '2026-05-22', 'ICA Kvantum', -100.0, ?, ?)",
        (aid, cat_food, rule_id),
    )
    t_rule = cur.lastrowid
    
    # 2. Manually categorized
    cur.execute(
        "INSERT INTO transactions (account_id, date, description, amount, category_id) "
        "VALUES (?, '2026-05-23', 'Manual restaurant', -50.0, ?)",
        (aid, cat_food),
    )
    t_manual = cur.lastrowid
    cur.execute("INSERT INTO id_matches (transaction_id, category_id) VALUES (?, ?)", (t_manual, cat_food))
    
    # 3. Uncategorized
    cur.execute(
        "INSERT INTO transactions (account_id, date, description, amount) "
        "VALUES (?, '2026-05-24', 'Uncategorized txn', -10.0)",
        (aid,),
    )
    t_uncat = cur.lastrowid
    
    temp_db.commit()
    
    # A. Test rules general listing (shows rule)
    test_args = ["cli.py", "--db", temp_db.db_file, "rules"]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert "/ICA/" in captured.out
    
    # B. Test rules <txn_id> with rule match
    test_args = ["cli.py", "--db", temp_db.db_file, "rules", str(t_rule)]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert "ICA Kvantum" in captured.out
    assert f"Status: Categorized by Rule #{rule_id}" in captured.out
    assert "Pattern:    /ICA/" in captured.out
    assert "Match Type: contains" in captured.out
    
    # C. Test rules <txn_id> with manual override
    test_args = ["cli.py", "--db", temp_db.db_file, "rules", str(t_manual)]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert "Manual restaurant" in captured.out
    assert "Status: Manually categorized (Override)" in captured.out
    
    # D. Test rules <txn_id> with uncategorized
    test_args = ["cli.py", "--db", temp_db.db_file, "rules", str(t_uncat)]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert "Uncategorized txn" in captured.out
    assert "Status: Uncategorized" in captured.out
    
    # E. Test rules <txn_id> with invalid txn_id (should fail)
    test_args = ["cli.py", "--db", temp_db.db_file, "rules", "99999"]
    monkeypatch.setattr(sys, "argv", test_args)
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "Error: Transaction 99999 not found." in captured.err


def test_cli_manual_unmatch(temp_db, monkeypatch, capsys):
    aid = temp_db.add_account("Checking")
    cur = temp_db.get_cursor()
    
    cur.execute("INSERT INTO categories (name, category_type, associated_account_id) VALUES ('Savings', 'transfer', ?)", (aid,))
    cat_savings = cur.lastrowid
    
    cur.execute("INSERT INTO categories (name) VALUES ('Food')")
    cat_food = cur.lastrowid
    
    # 1. Manually matched transaction (transfer type)
    cur.execute(
        "INSERT INTO transactions (account_id, date, description, amount, category_id) "
        "VALUES (?, '2026-05-23', 'Transfer to savings', -500.0, ?)",
        (aid, cat_savings),
    )
    t_manual = cur.lastrowid
    cur.execute("INSERT INTO id_matches (transaction_id, category_id) VALUES (?, ?)", (t_manual, cat_savings))
    cur.execute(
        "INSERT INTO transaction_links (from_transaction_id, to_transaction_id, link_type, ratio, comment, to_account_id) "
        "VALUES (?, NULL, 'external_transfer', 1.0, 'auto-linked via categorize', ?)",
        (t_manual, aid),
    )
    
    # 2. Rule matched transaction
    cur.execute(
        "INSERT INTO match_rules (category_id, pattern, match_type, enabled) "
        "VALUES (?, 'ICA', 'contains', 1)",
        (cat_food,),
    )
    rule_id = cur.lastrowid

    cur.execute(
        "INSERT INTO transactions (account_id, date, description, amount, category_id, matched_rule_id) "
        "VALUES (?, '2026-05-22', 'ICA Kvantum', -100.0, ?, ?)",
        (aid, cat_food, rule_id),
    )
    t_rule = cur.lastrowid
    
    # 3. Uncategorized transaction
    cur.execute(
        "INSERT INTO transactions (account_id, date, description, amount) "
        "VALUES (?, '2026-05-24', 'Uncategorized txn', -10.0)",
        (aid,),
    )
    t_uncat = cur.lastrowid
    
    temp_db.commit()
    
    # A. Test manual-unmatch on rule-matched (should fail)
    test_args = ["cli.py", "--db", temp_db.db_file, "manual-unmatch", str(t_rule)]
    monkeypatch.setattr(sys, "argv", test_args)
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert f"Error: Transaction {t_rule} does not have a manual categorization override." in captured.err
    
    # B. Test manual-unmatch on uncategorized (should fail)
    test_args = ["cli.py", "--db", temp_db.db_file, "manual-unmatch", str(t_uncat)]
    monkeypatch.setattr(sys, "argv", test_args)
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert f"Error: Transaction {t_uncat} does not have a manual categorization override." in captured.err

    # C. Test manual-unmatch on manually matched (should succeed)
    test_args = ["cli.py", "--db", temp_db.db_file, "manual-unmatch", str(t_manual)]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert f"Removed manual categorization override for transaction {t_manual}." in captured.out
    
    # Verify DB changes
    cur.execute("SELECT category_id, matched_rule_id FROM transactions WHERE id = ?", (t_manual,))
    row = cur.fetchone()
    assert row[0] is None
    assert row[1] is None
    
    cur.execute("SELECT category_id FROM id_matches WHERE transaction_id = ?", (t_manual,))
    assert cur.fetchone() is None
    
    cur.execute("SELECT id FROM transaction_links WHERE from_transaction_id = ? AND link_type = 'external_transfer'", (t_manual,))
    assert cur.fetchone() is None


def test_cli_import_verbosity(temp_db, monkeypatch, capsys, tmp_path):
    # Setup category and rule
    cat = Categorizer(temp_db)
    cid = cat.add_category("Food")
    cat.add_rule(cid, "ICA", match_type="regex")

    # Create CSV file with 1 new matched, 1 new uncategorized, 1 error
    csv_file = tmp_path / "test_nordea.csv"
    csv_file.write_text(
        "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
        "2024-01-04;-500,00;1111;;;ICA Store;999,99;SEK\n"  # new matched (Food)
        "2024-01-04;-100,00;1111;;;Other store;999,99;SEK\n" # new uncategorized
        "invalid-row;abc;;;;;;\n"                            # error
    , encoding="utf-8-sig")

    # 1. Test Default Behavior (no verbosity flags)
    test_args = ["cli.py", "--db", temp_db.db_file, "import", str(csv_file)]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert "[NEW] 2024-01-04 | ICA Store | -500.00 SEK -> Food" in captured.out
    assert "[NEW] 2024-01-04 | Other store | -100.00 SEK -> [Uncategorized]" in captured.out
    assert "Total: 2 imported, 0 skipped, 1 errors" in captured.out
    assert "[ERROR] Row in test_nordea.csv" in captured.err

    # 2. Test Quiet Mode (-q)
    # We clear the transactions from db to test importing again
    cur = temp_db.get_cursor()
    cur.execute("DELETE FROM transactions")
    temp_db.commit()
    
    test_args = ["cli.py", "--db", temp_db.db_file, "import", "-q", str(csv_file)]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert captured.out == ""  # Absolutely silent on stdout
    assert "[ERROR] Row in test_nordea.csv" in captured.err  # Errors still print to stderr

    # 3. Test Compact Mode (-c)
    # Run import again without clearing. The rows will now be duplicates (skipped).
    test_args = ["cli.py", "--db", temp_db.db_file, "import", "-c", str(csv_file)]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    # Skip details should not be listed individually in compact mode
    assert "[SKIP]" not in captured.out
    assert "[NEW]" not in captured.out
    assert "Total: 0 imported, 2 skipped, 1 errors" in captured.out
    assert "[ERROR] Row in test_nordea.csv" in captured.err

    # 4. Test Verbose Mode (-v)
    test_args = ["cli.py", "--db", temp_db.db_file, "import", "-v", str(csv_file)]
    monkeypatch.setattr(sys, "argv", test_args)
    main()
    captured = capsys.readouterr()
    assert "[SKIP]" in captured.out
    assert "[ERROR]" not in captured.out  # errors are in stderr, not stdout
    assert "[ERROR] Row in test_nordea.csv" in captured.err
    assert "UNIQUE constraint" in captured.out
    assert "Total: 0 imported, 2 skipped, 1 errors" in captured.out







