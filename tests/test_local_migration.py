import runpy

import pytest

from trenddeck.config import PROJECT_ROOT

migrate = runpy.run_path(str(PROJECT_ROOT / "scripts/migrate-local.py"))["migrate_local"]


def test_local_migration_preserves_secrets_books_and_artifacts(tmp_path):
    files = {
        ".env": b"TRENDDECK_MCP_TOKEN=test-secret\n",
        ".streamlit/secrets.toml": b'TIINGO_API_KEY="test-key"\n',
        ".streamlit/sshpassword": b"example-password",
        "books/notes/book.pdf": b"example-book",
        "ref/article.md": b"reference-note",
        "todo.md": b"private-local-notes",
        "tmp/figure.png": b"example-image",
    }
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    plan = migrate(tmp_path, dry_run=True)
    assert len(plan) == 7 and not (tmp_path / "local").exists()
    expected = {target: source.read_bytes() for source, target in plan}
    migrate(tmp_path)
    assert all(p.read_bytes() == contents for p, contents in expected.items())
    assert (tmp_path / "local/ref/books/notes/book.pdf").read_bytes() == b"example-book"
    assert (tmp_path / "local/ref/article.md").read_bytes() == b"reference-note"
    assert (tmp_path / "local/todo.md").read_bytes() == b"private-local-notes"
    assert not (tmp_path / "ref").exists()
    assert not (tmp_path / ".streamlit").exists() and not (tmp_path / "books").exists()
    assert migrate(tmp_path) == []


def test_migration_conflict_keeps_all_original_files(tmp_path):
    (tmp_path / ".env").write_text("old")
    (tmp_path / "local").mkdir()
    (tmp_path / "local/.env").write_text("new")
    with pytest.raises(ValueError, match="nothing moved"):
        migrate(tmp_path)
    assert (tmp_path / ".env").read_text() == "old"
    assert (tmp_path / "local/.env").read_text() == "new"


def test_local_secrets_override_legacy_and_process_environment_wins(tmp_path, monkeypatch):
    from trenddeck import market

    monkeypatch.setattr(market, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(market, "SECRETS_FILE", tmp_path / "local/secrets.toml")
    for rel, key in [(".streamlit/secrets.toml", "legacy"), ("local/secrets.toml", "current")]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True)
        p.write_text(f'TIINGO_API_KEY="{key}"')
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    assert market.get_tiingo_api_key() == "current"
    monkeypatch.setenv("TIINGO_API_KEY", "process")
    assert market.get_tiingo_api_key() == "process"


def test_two_old_secret_files_cannot_overwrite_one_target(tmp_path):
    (tmp_path / ".env").write_text("first")
    (tmp_path / ".streamlit").mkdir()
    (tmp_path / ".streamlit/.env").write_text("second")
    with pytest.raises(ValueError, match="nothing moved"):
        migrate(tmp_path)
    assert not (tmp_path / "local").exists()
    assert (tmp_path / ".env").read_text() == "first"
