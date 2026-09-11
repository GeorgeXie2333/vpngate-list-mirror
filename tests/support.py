import csv
import io
from pathlib import Path

from mirror.snapshot import build_snapshot, make_index

FIXTURES = Path(__file__).parent / "fixtures"
TIME = "2026-09-01T00:00:00.000Z"
RUN_URL = "https://github.com/GeorgeXie2333/vpngate-list-mirror/actions/runs/1"


def fixture(name="normal.csv"):
    return (FIXTURES / name).read_bytes()


def modified(**values):
    rows = list(csv.reader(io.StringIO(fixture().decode(), newline="")))
    keys = [value.removeprefix("#") for value in rows[1]]
    for key, value in values.items():
        rows[2][keys.index(key)] = value
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\r\n").writerows(rows)
    return output.getvalue().encode()


def example(body=None, commit="a" * 40, fetched_at=TIME):
    snapshot = build_snapshot(body or fixture())
    return make_index(snapshot, commit, fetched_at, fetched_at, fetched_at, RUN_URL), snapshot.files
