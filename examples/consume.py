#!/usr/bin/env python3
"""Run from a checkout; the shared consumer uses only Python's standard library."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mirror.consumer import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
