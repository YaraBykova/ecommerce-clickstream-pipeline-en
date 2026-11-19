"""Split a source into reproducible batches. Draft prepared for joint review."""

import argparse
import csv
import os
import tempfile
from pathlib import Path

from load_raw import ARCHIVE_PATH, EXPECTED_COLUMNS, PROJECT_DIR, iter_source_rows


def split_source(source_path, output_dir, rows_per_batch=100_000):
    """Publish a batch directory after the whole source passes validation."""
    output_dir = Path(output_dir)
    if rows_per_batch <= 0:
        raise ValueError("Batch size must be positive")
    if output_dir.exists():
        raise FileExistsError(f"Directory already exists: {output_dir}. Choose a new batch directory.")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    batch_count = 0
    file = None
    with tempfile.TemporaryDirectory(prefix=".split-", dir=output_dir.parent) as temporary:
        try:
            for row_number, row in enumerate(iter_source_rows(source_path)):
                if row_number % rows_per_batch == 0:
                    if file is not None:
                        file.close()
                    batch_count += 1
                    file = (Path(temporary) / f"batch-{batch_count:04d}.csv").open(
                        "w", encoding="utf-8", newline=""
                    )
                    writer = csv.writer(file)
                    writer.writerow(EXPECTED_COLUMNS)
                (
                    _, event_time, event_type, product_id, category_id,
                    category_code, brand, price, user_id, user_session,
                ) = row
                writer.writerow((
                    event_time.strftime("%Y-%m-%d %H:%M:%S UTC"), event_type,
                    product_id, category_id, category_code, brand,
                    format(price, ".2f"), user_id, user_session,
                ))
        finally:
            if file is not None:
                file.close()
        # Renaming the directory avoids publishing a partially prepared file set.
        os.rename(temporary, output_dir)
    return sorted(output_dir.glob("batch-*.csv"))


def main():
    parser = argparse.ArgumentParser(description="Split CSV/ZIP into batches without changing the source")
    parser.add_argument("--source", type=Path, default=ARCHIVE_PATH)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_DIR / "data" / "batches")
    parser.add_argument("--rows-per-batch", type=int, default=100_000)
    args = parser.parse_args()
    paths = split_source(args.source, args.output_dir, args.rows_per_batch)
    print(f"Prepared batches: {len(paths)}. Directory: {args.output_dir}")


if __name__ == "__main__":
    main()
