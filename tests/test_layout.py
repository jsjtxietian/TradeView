from __future__ import annotations

import csv
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from trenddeck.config import PROJECT_ROOT

MIGRATE = runpy.run_path(str(PROJECT_ROOT / "scripts" / "migrate-data.py"))["migrate_data"]


def run_python(cwd: Path, code: str, data_dir: str | None = None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env.pop("TRENDDECK_DATA_DIR", None)
    if data_dir is not None:
        env["TRENDDECK_DATA_DIR"] = data_dir
    return subprocess.run(
        [sys.executable, "-c", code], cwd=cwd, env=env, capture_output=True, text=True, check=True
    )


@pytest.mark.parametrize("override", [None, "isolated-data"])
def test_paths_do_not_depend_on_working_directory(tmp_path, override):
    result = run_python(
        tmp_path,
        "from trenddeck.config import DATA_DIR, STOCK_DIR, TRADE_DIR, ALERTS_SNAPSHOT_FILE, PROMPT_TEMPLATE_PATH; import json; print(json.dumps([str(p) for p in [DATA_DIR, STOCK_DIR, TRADE_DIR, ALERTS_SNAPSHOT_FILE, PROMPT_TEMPLATE_PATH]]))",
        override,
    )
    data, stock, trade, snapshot, prompt = map(Path, json.loads(result.stdout))
    assert data == PROJECT_ROOT / (override or "data")
    assert stock == data / "stock" and trade == data / "trade"
    assert snapshot == trade / "alerts_snapshot.json"
    assert prompt == PROJECT_ROOT / "templates" / "analysis_prompt.md" and prompt.exists()


def test_migration_preserves_bytes_and_is_repeatable(tmp_path):
    old_stock, old_trade = tmp_path / ".cache", tmp_path / ".trade"
    old_stock.mkdir()
    old_trade.mkdir()
    (old_stock / "NVDA_3y_history.csv").write_bytes(b"Date,Close\n2026-09-10,218.36\n")
    (old_stock / "alerts_snapshot.json").write_bytes(b'{"version":1}')
    (old_trade / "notes.json").write_bytes(b'{"NVDA":"keep this note"}')
    plan = MIGRATE(tmp_path, tmp_path / "data", dry_run=True)
    assert len(plan) == 3 and old_stock.exists() and not (tmp_path / "data").exists()
    expected = {target: source.read_bytes() for source, target in plan}
    MIGRATE(tmp_path, tmp_path / "data")
    assert all(target.read_bytes() == content for target, content in expected.items())
    assert (tmp_path / "data/trade/alerts_snapshot.json").is_file()
    assert not old_stock.exists() and not old_trade.exists()
    assert MIGRATE(tmp_path, tmp_path / "data") == []


def test_migration_conflicts_stop_before_any_move(tmp_path):
    old = tmp_path / ".trade"
    new = tmp_path / "data/trade"
    old.mkdir()
    new.mkdir(parents=True)
    (old / "notes.json").write_text("old note", encoding="utf-8")
    (old / "alerts.json").write_text("keep alerts", encoding="utf-8")
    (new / "notes.json").write_text("new note", encoding="utf-8")
    with pytest.raises(ValueError, match="nothing moved"):
        MIGRATE(tmp_path, tmp_path / "data")
    assert (old / "alerts.json").read_text() == "keep alerts"
    assert (old / "notes.json").read_text() == "old note"
    assert (new / "notes.json").read_text() == "new note"
    assert not (new / "alerts.json").exists()


def test_two_legacy_snapshots_cannot_overwrite_each_other(tmp_path):
    for directory, content in [(".cache", "one"), (".trade", "two")]:
        path = tmp_path / directory
        path.mkdir()
        (path / "alerts_snapshot.json").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="nothing moved"):
        MIGRATE(tmp_path, tmp_path / "data")
    assert not (tmp_path / "data").exists()


def test_identical_leftovers_after_git_move_are_merged(tmp_path):
    for directory in [".trade", "data/trade"]:
        path = tmp_path / directory
        path.mkdir(parents=True)
        (path / "notes.json").write_bytes(b"identical")
    MIGRATE(tmp_path, tmp_path / "data")
    assert (tmp_path / "data/trade/notes.json").read_bytes() == b"identical"
    assert not (tmp_path / ".trade").exists()


def test_import_and_refresh_scripts_share_external_data_root(tmp_path):
    data_dir = tmp_path / "external-data"
    input_csv = tmp_path / "input.csv"
    with input_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "Transaction History",
                "Header",
                "交易类型",
                "代码",
                "Price Currency",
                "数量",
                "价格",
                "日期",
                "总额",
                "佣金",
                "说明",
            ]
        )
        writer.writerow(
            [
                "Transaction History",
                "Data",
                "Buy",
                "NVDA",
                "USD",
                "10",
                "100",
                "2026-09-01",
                "-1000",
                "-1",
                "Buy",
            ]
        )
        writer.writerow(
            [
                "Transaction History",
                "Data",
                "Sell",
                "NVDA",
                "USD",
                "10",
                "110",
                "2026-09-02",
                "1100",
                "-1",
                "Sell",
            ]
        )
    importer = str(PROJECT_ROOT / "scripts/import-ibkr.py")
    for args in (
        ["convert", str(input_csv)],
        ["append", str(data_dir / "imports/ibkr-trades.json")],
        ["ledger", str(input_csv)],
    ):
        code = f"import runpy,sys; sys.argv={[importer, *args]!r}; runpy.run_path({importer!r},run_name='__main__')"
        run_python(tmp_path, code, str(data_dir))
    assert len(json.loads((data_dir / "trade/trades.json").read_text(encoding="utf-8"))) == 1
    assert len(json.loads((data_dir / "trade/ledger.json").read_text(encoding="utf-8"))["entries"]) == 2
    (data_dir / "trade/watchlist.json").write_text('{"watchlist":["AMD"]}', encoding="utf-8")
    refresh = str(PROJECT_ROOT / "scripts/refresh-cache.py")
    result = run_python(
        tmp_path,
        f"import runpy,json; print(json.dumps(runpy.run_path({refresh!r})['load_symbols']()))",
        str(data_dir),
    )
    assert json.loads(result.stdout) == ["AMD"]
    assert not (tmp_path / ".trade").exists() and not (tmp_path / "ibkr-trades.json").exists()


def test_new_data_root_is_created_for_notes_and_snapshot(tmp_path):
    data_dir = tmp_path / "new-data"
    run_python(
        tmp_path,
        "from trenddeck.storage import save_symbol_notes; from trenddeck.alerts import write_alert_snapshot; save_symbol_notes({'NVDA': {'text': 'note'}}); write_alert_snapshot({'NVDA': {'latestDate': '2026-09-10'}})",
        str(data_dir),
    )
    assert (data_dir / "trade/notes.json").is_file()
    assert (data_dir / "trade/alerts_snapshot.json").is_file()
    assert not (data_dir / "stock/alerts_snapshot.json").exists()
