"""Relocate legacy local files without overwriting secrets or reference material."""

from __future__ import annotations

import argparse
from pathlib import Path


def migrate_local(root: Path, dry_run: bool = False) -> list[tuple[Path, Path]]:
    root = root.resolve()
    pairs = [
        (".env", "local/.env"),
        (".streamlit", "local"),
        ("todo.md", "local/todo.md"),
        ("books", "local/ref/books"),
        ("ref", "local/ref"),
        ("tmp", "local/artifacts"),
    ]
    plan, directories, destinations = [], [], {}
    for old, new in pairs:
        source, target = root / old, root / new
        if not source.exists():
            continue
        files = sorted(source.rglob("*")) if source.is_dir() else [source]
        if source.is_symlink():
            raise ValueError("Unsafe migration path; nothing moved")
        if source.is_dir():
            directories += [source, *(p for p in files if p.is_dir())]
        for path in files:
            destination = target / path.relative_to(source) if source.is_dir() else target
            if (
                path.is_symlink()
                or not path.resolve().is_relative_to(root)
                or not destination.resolve().is_relative_to(root)
            ):
                raise ValueError("Unsafe migration path; nothing moved")
            if not path.is_file():
                continue
            if any(p.is_symlink() for p in [destination, *destination.parents] if p.is_relative_to(root)):
                raise ValueError("Unsafe destination path; nothing moved")
            if destination.exists() and (
                not destination.is_file() or path.read_bytes() != destination.read_bytes()
            ):
                raise ValueError(f"Conflicting destination {destination.relative_to(root)}; nothing moved")
            if destination in destinations and path.read_bytes() != destinations[destination].read_bytes():
                raise ValueError("Multiple sources conflict; nothing moved")
            destinations[destination] = path
            if any(
                p.exists() and not p.is_dir()
                for p in destination.parents
                if p != root and p.is_relative_to(root)
            ):
                raise ValueError("Conflicting parent directory; nothing moved")
            plan.append((path, destination))
    if dry_run:
        return plan
    for source, target in plan:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            source.unlink()  # Preflight proved identical bytes.
        else:
            source.rename(target)
        if target.name in {".env", "secrets.toml", "sshpassword"}:
            target.chmod(0o600)
    for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        if directory.exists() and not any(directory.iterdir()):
            directory.rmdir()
    return plan


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    moves = migrate_local(Path(__file__).resolve().parents[1], args.dry_run)
    print(
        f"{'Planned' if args.dry_run else 'Migrated'} {len(moves)} local files; secret values are never printed."
    )
