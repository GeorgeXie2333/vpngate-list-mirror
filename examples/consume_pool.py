"""Run the pool consumer from a public checkout; no credentials required."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mirror.pool_consumer import main

if __name__ == "__main__":
    raise SystemExit(main())
