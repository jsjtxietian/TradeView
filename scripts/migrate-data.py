#!/usr/bin/env python3
"""Move legacy .cache/.trade files into data without overwriting different files."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trenddeck.config import DATA_DIR  # noqa: E402


def migrate_data(project_root: Path, data_dir: Path, dry_run: bool = False) -> list[tuple[Path, Path]]:
    project_root, data_dir = project_root.resolve(), data_dir.resolve()
    moves: list[tuple[Path, Path]] = []
    legacy_dirs = [project_root / ".cache", project_root / ".trade"]
    conflicts = []
    planned_targets: dict[Path, Path] = {}
    for legacy in legacy_dirs:
        if legacy.is_symlink():
            raise ValueError(f"Refusing a symlinked legacy directory: {legacy}")
        if not legacy.exists():
            continue
        if not legacy.is_dir():
            raise ValueError(f"Expected a directory: {legacy}")
        for source in sorted(legacy.rglob("*")):
            if source.is_symlink():
                raise ValueError(f"Refusing a symlinked legacy entry: {source}")
            if not source.is_file():
                continue
            relative = source.relative_to(legacy)
            if legacy.name == ".cache" and relative.as_posix() == "alerts_snapshot.json":
                target = data_dir / "trade" / relative
            else:
                target = data_dir / ("stock" if legacy.name == ".cache" else "trade") / relative
            # Resolve and verify every computed destination before moving files.
            if not source.resolve().is_relative_to(legacy.resolve()):
                raise ValueError(f"Legacy entry escapes its directory: {source}")
            if not target.resolve().is_relative_to(data_dir):
                raise ValueError(f"Destination escapes the data directory: {target}")
            if any(target.resolve().is_relative_to(old) for old in legacy_dirs):
                raise ValueError(f"Destination overlaps a legacy directory: {target}")
            if target.is_symlink():
                raise ValueError(f"Refusing a symlinked destination: {target}")
            if target.exists() and (not target.is_file() or source.read_bytes() != target.read_bytes()):
                conflicts.append(str(target))
            earlier = planned_targets.get(target.resolve())
            if earlier is not None and earlier.read_bytes() != source.read_bytes():
                conflicts.append(str(target))
            planned_targets[target.resolve()] = source
            moves.append((source, target))
    # Check the entire migration before changing anything.
    if conflicts:
        raise ValueError(
            "Different files already exist at the destination; nothing moved:\n" + "\n".join(conflicts)
        )
    if dry_run:
        return moves
    for source, target in moves:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if not target.is_file() or source.read_bytes() != target.read_bytes():
                raise FileExistsError(f"Destination changed during migration; source retained: {target}")
            source.unlink()  # Identical duplicate verified during preflight.
        else:
            shutil.move(str(source), str(target))
    for legacy in legacy_dirs:
        if legacy.is_dir():
            for directory in sorted(
                (p for p in legacy.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
            ):
                directory.rmdir()  # Only remove directories that are empty.
            legacy.rmdir()
    return moves


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=DATA_DIR, help="Destination data root (default: project data/)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and show moves without writing")
    args = parser.parse_args()
    try:
        moves = migrate_data(ROOT, args.data_dir, args.dry_run)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.dry_run:
        for source, target in moves:
            print(f"{source} -> {target}")
    print(
        f"{'Would migrate' if args.dry_run else 'Migrated'} {len(moves)} files into {args.data_dir.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
