"""Compatibility CLI; import implementation lives in trenddeck.importers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trenddeck.importers.ibkr import main  # noqa: E402

if __name__ == "__main__":
    main()
