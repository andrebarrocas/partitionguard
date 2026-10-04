"""Create synthetic tutorial data only. Refuses to overwrite an existing database."""

import argparse
from pathlib import Path

import duckdb


def create(path):
    path = Path(path)
    if path.exists():
        raise ValueError(f"Refusing to overwrite {path}")
    with duckdb.connect(str(path)) as db:
        db.execute(
            "CREATE TABLE shipment_events(event_id BIGINT NOT NULL, event_date DATE NOT NULL, carrier_code VARCHAR, amount DOUBLE NOT NULL)"
        )
        db.executemany(
            "INSERT INTO shipment_events VALUES (?, ?, ?, ?)",
            [
                (1, "2026-01-01", "10", 10.5),
                (2, "2026-01-01", "20", 20.0),
                (3, "2026-01-02", "10", 30.0),
                (4, "2026-01-02", "30", 40.5),
                (5, "2026-01-03", "20", 50.0),
                (6, "2026-01-03", "30", 60.0),
                (7, "2025-12-31", "legacy", 70.0),
                (8, "2026-01-04", "future", 80.0),
            ],
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    create(args.database)
    print("Created 8 synthetic rows; 6 rows fall inside [2026-01-01, 2026-01-04).")
