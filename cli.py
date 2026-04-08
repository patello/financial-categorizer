#!/usr/bin/env python3
"""
CLI interface for financial-categorizer.

Provides command-line access to import, categorization, and category management.
"""

import argparse
import logging
import sys
from pathlib import Path

from financial_categorizer.db_handler import DatabaseHandler
from financial_categorizer.categorizer import Categorizer
from financial_categorizer.importer import CSVImporter


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def get_db(db_path: str) -> DatabaseHandler:
    handler = DatabaseHandler(db_path)
    handler.connect()
    return handler


def cmd_import(args):
    db = get_db(args.db)
    try:
        importer = CSVImporter(db)
        cat = Categorizer(db)

        total = {"imported": 0, "skipped": 0, "errors": 0, "settled_pending": 0}

        for path in args.files:
            result = importer.import_file(
                path, account_name=args.account,
                auto_create_account=not args.no_auto_account,
            )
            for k in total:
                total[k] += result.get(k, 0)
            logger.info(
                f"{Path(path).name}: {result['imported']} imported, "
                f"{result['skipped']} skipped, {result['errors']} errors"
            )

        if total["imported"] > 0:
            result = cat.categorize_new()
            logger.info(f"Categorized {result['matched']} new transactions "
                        f"({result['unmatched']} uncategorized)")

        if total["settled_pending"] > 0:
            logger.info(f"Settled {total['settled_pending']} pending transactions")

        print(f"Total: {total['imported']} imported, {total['skipped']} skipped, "
              f"{total['errors']} errors")
    finally:
        db.disconnect()


def cmd_categories(args):
    db = get_db(args.db)
    try:
        cat = Categorizer(db)
        categories = cat.list_categories()

        if not categories:
            print("No categories defined.")
            return

        # Build tree display
        children_map = {}
        roots = []
        for c in categories:
            pid = c["parent_id"]
            if pid is None:
                roots.append(c)
            else:
                children_map.setdefault(pid, []).append(c)

        def print_tree(node, indent=0):
            prefix = "  " * indent
            print(f"{prefix}- {node['name']} (id={node['id']})")
            for child in sorted(children_map.get(node["id"], []), key=lambda x: x["name"]):
                print_tree(child, indent + 1)

        for root in sorted(roots, key=lambda x: x["name"]):
            print_tree(root)
    finally:
        db.disconnect()


def cmd_add_category(args):
    db = get_db(args.db)
    try:
        cat = Categorizer(db)
        cid = cat.add_category(args.name, parent_id=args.parent, description=args.description)
        print(f"Created category '{args.name}' (id={cid})")
    finally:
        db.disconnect()


def cmd_update_category(args):
    db = get_db(args.db)
    try:
        cat = Categorizer(db)
        kwargs = {}
        if args.name is not None:
            kwargs["name"] = args.name
        if args.parent is not None:
            kwargs["parent_id"] = args.parent
        if args.description is not None:
            kwargs["description"] = args.description

        if not kwargs:
            print("Nothing to update. Specify --name, --parent, or --description.")
            return

        updated = cat.update_category(args.id, **kwargs)
        if updated:
            print(f"Updated category {args.id}")
        else:
            print(f"No changes made (category {args.id} not found or values unchanged)")
    finally:
        db.disconnect()


def cmd_delete_category(args):
    db = get_db(args.db)
    try:
        cat = Categorizer(db)
        deleted = cat.delete_category(args.id, reassign=args.reassign, force=args.force)
        if deleted:
            msg = f"Deleted category {args.id}"
            if args.reassign:
                msg += f" (reassigned to {args.reassign})"
            print(msg)
        else:
            print(f"Category {args.id} not found")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.disconnect()


def cmd_rules(args):
    db = get_db(args.db)
    try:
        cat = Categorizer(db)
        rules = cat.list_rules()

        if not rules:
            print("No rules defined.")
            return

        for r in rules:
            status = "enabled" if r["enabled"] else "disabled"
            print(f"  [{r['id']}] {r['category_name']:<20} "
                  f"{r['match_type']:<8} /{r['pattern']}/  "
                  f"priority={r['priority']} ({status})")
    finally:
        db.disconnect()


def cmd_add_rule(args):
    db = get_db(args.db)
    try:
        cat = Categorizer(db)
        rule_id = cat.add_rule(
            args.category, args.pattern,
            match_type=args.type, priority=args.priority
        )
        print(f"Added rule {rule_id} and re-categorized all transactions")
    finally:
        db.disconnect()


def cmd_remove_rule(args):
    db = get_db(args.db)
    try:
        cat = Categorizer(db)
        removed = cat.remove_rule(args.id)
        if removed:
            print(f"Removed rule {args.id} and re-categorized all transactions")
        else:
            print(f"Rule {args.id} not found")
    finally:
        db.disconnect()


def cmd_preview(args):
    db = get_db(args.db)
    try:
        cat = Categorizer(db)
        matches = cat.preview_rule(args.pattern, match_type=args.type, limit=args.limit)

        if not matches:
            print("No matches found.")
            return

        print(f"Matches ({len(matches)}):")
        for m in matches:
            print(f"  [{m['id']}] {m['date']}  {m['amount']:>10.2f}  {m['description']}")
    finally:
        db.disconnect()


def cmd_categorize(args):
    db = get_db(args.db)
    try:
        cat = Categorizer(db)
        if args.all:
            result = cat.categorize_all()
            print(f"Re-categorized all: {result['matched']} matched, "
                  f"{result['unmatched']} unmatched")
        else:
            result = cat.categorize_new()
            print(f"Categorized new: {result['matched']} matched, "
                  f"{result['unmatched']} unmatched")
    finally:
        db.disconnect()


def cmd_uncategorized(args):
    db = get_db(args.db)
    try:
        cat = Categorizer(db)
        uncategorized = cat.get_uncategorized()

        if not uncategorized:
            print("All transactions are categorized.")
            return

        print(f"Uncategorized transactions ({len(uncategorized)}):")
        for t in uncategorized:
            print(f"  [{t['id']}] {t['date']}  {t['amount']:>10.2f}  {t['description']}")
    finally:
        db.disconnect()


def cmd_accounts(args):
    db = get_db(args.db)
    try:
        accounts = db.list_accounts()
        if not accounts:
            print("No accounts defined.")
            return
        for a in accounts:
            print(f"  [{a['id']}] {a['name']:<20} type={a['type']:<10} "
                  f"ownership={a['ownership_ratio']:.2f}  {a['currency']}"
                  f"{('  ' + a['description']) if a['description'] else ''}")
    finally:
        db.disconnect()


def cmd_add_account(args):
    db = get_db(args.db)
    try:
        aid = db.add_account(
            args.name, type=args.type,
            ownership_ratio=args.ownership,
            currency=args.currency,
            description=args.description,
        )
        print(f"Created account '{args.name}' (id={aid})")
    finally:
        db.disconnect()


def cmd_update_account(args):
    db = get_db(args.db)
    try:
        kwargs = {}
        if args.name is not None:
            kwargs["name"] = args.name
        if args.type is not None:
            kwargs["type"] = args.type
        if args.ownership is not None:
            kwargs["ownership_ratio"] = args.ownership
        if args.currency is not None:
            kwargs["currency"] = args.currency
        if args.description is not None:
            kwargs["description"] = args.description
        if not kwargs:
            print("Nothing to update.")
            return
        updated = db.update_account(args.id, **kwargs)
        print(f"Updated account {args.id}" if updated else f"Account {args.id} not found")
    finally:
        db.disconnect()


def cmd_delete_account(args):
    db = get_db(args.db)
    try:
        deleted = db.delete_account(args.id)
        if deleted:
            print(f"Deleted account {args.id}")
        else:
            print(f"Account {args.id} not found")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.disconnect()


def cmd_manual_match(args):
    db = get_db(args.db)
    try:
        cat = Categorizer(db)
        cat.add_manual_match(args.transaction, args.category)
        print(f"Manually matched transaction {args.transaction} -> category {args.category}")
    finally:
        db.disconnect()


def main():
    parser = argparse.ArgumentParser(
        prog="financial-categorizer",
        description="Transaction categorization tool backed by SQLite.",
    )
    parser.add_argument("--db", default="finance.db", help="Path to SQLite database (default: finance.db)")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # import
    p_import = subparsers.add_parser("import", help="Import CSV transactions")
    p_import.add_argument("files", nargs="+", help="CSV files to import")
    p_import.add_argument("--account", help="Account name (default: derived from filename)")
    p_import.add_argument("--no-auto-account", action="store_true",
                          help="Don't auto-create account if missing (raises error)")
    p_import.set_defaults(func=cmd_import)

    # accounts
    p_accounts = subparsers.add_parser("accounts", help="List all accounts")
    p_accounts.set_defaults(func=cmd_accounts)

    # add-account
    p_add_acct = subparsers.add_parser("add-account", help="Add a new account")
    p_add_acct.add_argument("name", help="Account name")
    p_add_acct.add_argument("--type", default="personal",
                            choices=["personal", "shared", "savings", "external"],
                            help="Account type (default: personal)")
    p_add_acct.add_argument("--ownership", type=float, default=1.0,
                            help="Ownership ratio 0.0-1.0 (default: 1.0)")
    p_add_acct.add_argument("--currency", default="SEK", help="Currency (default: SEK)")
    p_add_acct.add_argument("--description", help="Account description")
    p_add_acct.set_defaults(func=cmd_add_account)

    # update-account
    p_upd_acct = subparsers.add_parser("update-account", help="Update an account")
    p_upd_acct.add_argument("id", type=int, help="Account ID")
    p_upd_acct.add_argument("--name", help="New name")
    p_upd_acct.add_argument("--type", choices=["personal", "shared", "savings", "external"],
                            help="New type")
    p_upd_acct.add_argument("--ownership", type=float, help="New ownership ratio")
    p_upd_acct.add_argument("--currency", help="New currency")
    p_upd_acct.add_argument("--description", help="New description")
    p_upd_acct.set_defaults(func=cmd_update_account)

    # delete-account
    p_del_acct = subparsers.add_parser("delete-account", help="Delete an account")
    p_del_acct.add_argument("id", type=int, help="Account ID")
    p_del_acct.set_defaults(func=cmd_delete_account)

    # categories
    p_cats = subparsers.add_parser("categories", help="List all categories")
    p_cats.set_defaults(func=cmd_categories)

    # add-category
    p_add_cat = subparsers.add_parser("add-category", help="Add a new category")
    p_add_cat.add_argument("name", help="Category name")
    p_add_cat.add_argument("--parent", type=int, help="Parent category ID")
    p_add_cat.add_argument("--description", help="Category description")
    p_add_cat.set_defaults(func=cmd_add_category)

    # update-category
    p_upd_cat = subparsers.add_parser("update-category", help="Update a category")
    p_upd_cat.add_argument("id", type=int, help="Category ID")
    p_upd_cat.add_argument("--name", help="New name")
    p_upd_cat.add_argument("--parent", type=int, help="New parent category ID")
    p_upd_cat.add_argument("--description", help="New description")
    p_upd_cat.set_defaults(func=cmd_update_category)

    # delete-category
    p_del_cat = subparsers.add_parser("delete-category", help="Delete a category")
    p_del_cat.add_argument("id", type=int, help="Category ID")
    p_del_cat.add_argument("--reassign", type=int, help="Reassign children/rules/matches to this category")
    p_del_cat.add_argument("--force", action="store_true", help="Force deletion without reassign (children still require --reassign)")
    p_del_cat.set_defaults(func=cmd_delete_category)

    # rules
    p_rules = subparsers.add_parser("rules", help="List all match rules")
    p_rules.set_defaults(func=cmd_rules)

    # add-rule
    p_add_rule = subparsers.add_parser("add-rule", help="Add a categorization rule")
    p_add_rule.add_argument("category", type=int, help="Category ID to match")
    p_add_rule.add_argument("pattern", help="Pattern to match")
    p_add_rule.add_argument("--type", default="regex", choices=["regex", "exact", "contains"],
                            help="Match type (default: regex)")
    p_add_rule.add_argument("--priority", type=int, default=0, help="Rule priority (default: 0)")
    p_add_rule.set_defaults(func=cmd_add_rule)

    # remove-rule
    p_rem_rule = subparsers.add_parser("remove-rule", help="Remove a match rule")
    p_rem_rule.add_argument("id", type=int, help="Rule ID")
    p_rem_rule.set_defaults(func=cmd_remove_rule)

    # preview
    p_preview = subparsers.add_parser("preview", help="Preview what a rule would match")
    p_preview.add_argument("pattern", help="Pattern to test")
    p_preview.add_argument("--type", default="regex", choices=["regex", "exact", "contains"],
                           help="Match type (default: regex)")
    p_preview.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    p_preview.set_defaults(func=cmd_preview)

    # categorize
    p_cat = subparsers.add_parser("categorize", help="Categorize transactions")
    p_cat.add_argument("--all", action="store_true", help="Re-categorize all transactions")
    p_cat.set_defaults(func=cmd_categorize)

    # uncategorized
    p_uncat = subparsers.add_parser("uncategorized", help="Show uncategorized transactions")
    p_uncat.set_defaults(func=cmd_uncategorized)

    # manual-match
    p_manual = subparsers.add_parser("manual-match", help="Manually match a transaction to a category")
    p_manual.add_argument("transaction", type=int, help="Transaction ID")
    p_manual.add_argument("category", type=int, help="Category ID")
    p_manual.set_defaults(func=cmd_manual_match)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
