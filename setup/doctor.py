#!/usr/bin/env python3
"""Run the doctor check. Same command as `python -m core doctor`."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["doctor", *sys.argv[1:]]))
