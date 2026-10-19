import importlib.util
import os
import sys
from pathlib import Path

import pytest

from app import db, repo

_spec = importlib.util.spec_from_file_location(
    "import_register_csv", Path(__file__).parent.parent / "scripts" / "import_register_csv.py"
)
importer = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = importer  # dataclasses look the module up while executing it
_spec.loader.exec_module(importer)

HEADER = '"Date","Balance","Amount","Type","Status","Name","Category","Number or code","Note"\n'


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "reconcile.db"
    conn = db.connect(path)
    db.migrate(conn)
    repo.setup(conn, "Checking", 69529, "2026-08-01")
    conn.close()
    return path


def write_csv(tmp_path, *rows):
    path = tmp_path / "export.csv"
    path.write_text(HEADER + "".join(rows))
    return path


ROWS = (
    '"20261019","122.72","25.00","Deposit","Unverified","Deposit","Default","",""\n',
    '"20261013","620.40","-70.00","Payment","Unverified","  Cleaning Lady ","Default","2270",""\n',
    '"20260807","117.58","-24.22","Payment","Verified","Macbook cloth cover","Default",""," a note "\n',
)


def test_parse_rows(tmp_path):
    rows = importer.parse_csv(write_csv(tmp_path, *ROWS))
    assert rows[1] == importer.Row("2026-10-13", "Cleaning Lady", -7000, "2270", None, "unverified")
    assert rows[2].note == "a note" and rows[2].status == "verified"


def test_dry_run_changes_nothing(tmp_path, db_path):
    plan = importer.run(write_csv(tmp_path, *ROWS), db_path, apply=False)
    assert len(plan.to_import) == 3
    conn = db.connect(db_path)
    assert len(repo.list_txns(conn, show_reconciled=True)) == 1


def test_apply_imports_and_projects_totals(tmp_path, db_path):
    plan = importer.run(write_csv(tmp_path, *ROWS), db_path, apply=True)
    assert plan.balance_after == 69529 + 2500 - 7000 - 2422
    assert plan.verified_after == 69529 - 2422
    conn = db.connect(db_path)
    assert repo.totals(conn) == (plan.balance_after, plan.verified_after)
    [t] = repo.list_txns(conn, q="Cleaning")
    assert (t["payee"], t["number"], t["status"]) == ("Cleaning Lady", "2270", "unverified")


def test_rerun_skips_rows_already_present(tmp_path, db_path):
    csv_path = write_csv(tmp_path, *ROWS)
    importer.run(csv_path, db_path, apply=True)
    plan = importer.run(csv_path, db_path, apply=True)
    assert plan.to_import == [] and len(plan.already_present) == 3
    assert len(repo.list_txns(db.connect(db_path), show_reconciled=True)) == 4


def test_identical_rows_in_csv_both_import(tmp_path, db_path):
    row = '"20261001","0","-6.80","Payment","Unverified","Starbucks","Default","",""\n'
    plan = importer.run(write_csv(tmp_path, row, row), db_path, apply=True)
    assert len(plan.to_import) == 2


def test_refuses_db_without_opening_balance(tmp_path):
    path = tmp_path / "empty.db"
    conn = db.connect(path)
    db.migrate(conn)
    conn.close()
    with pytest.raises(SystemExit, match="opening balance"):
        importer.run(write_csv(tmp_path, *ROWS), path, apply=False)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unreachable_db_reports_permission(tmp_path, db_path):
    locked = tmp_path / "volume"
    locked.mkdir()
    db_path = db_path.rename(locked / db_path.name)
    csv_path = write_csv(tmp_path, *ROWS)
    locked.chmod(0o600)  # listable but not traversable, like /var/lib/docker for non-root
    try:
        with pytest.raises(SystemExit, match="permission denied"):
            importer.run(csv_path, db_path, apply=False)
    finally:
        locked.chmod(0o700)


def test_rejects_bad_amount(tmp_path):
    bad = '"20261001","0","abc","Payment","Unverified","X","Default","",""\n'
    with pytest.raises(SystemExit, match="line 2"):
        importer.parse_csv(write_csv(tmp_path, bad))
