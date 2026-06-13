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
