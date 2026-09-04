# Incremental load, customer resolution, warehouse checks

SQL Server as the source, DuckDB as the warehouse, incremental loading, duplicate
customer resolution, and checks that say whether the warehouse is complete.

Reasoning and trade-offs are in `SOLUTION.md`.

## Requirements

- Docker
- Python 3.10+
- Microsoft ODBC driver for SQL Server (17 or 18)

## Run

```
pip install -r requirements.txt
make up
make all
```

`make up` starts SQL Server and waits for the healthcheck. The first run pulls the image.

`make all` is seed, load, dedupe, check. Each runs on its own too.

## Output

`make seed` prints row counts. Around 280 customers, 100 advances, 250 cards, 40k
transactions, 1.2k history rows, 500 scratch rows.

`make load` prints a line per table: strategy, rows read, inserted, updated, deleted, MB,
seconds. The scratch table shows as skipped.

Run it again with nothing changed and inserts and updates are zero. That is the
idempotency check.

`make dedupe` prints kept, merged, held for review, excluded, malformed contact counts,
and how many cards moved.

`make check` prints one line per check with observed and expected. All pass after a clean
`make all`.

## Failing check

```
make demo-failure
```

Removes 40 warehouse rows the source still has, then runs the checks.
`row_count_matches_source` and `no_gaps_in_key_range` both fail on transactions.

Two more:

```
make break-delete   # source rows deleted, warehouse still has them
make break-stale    # source changed without touching updated_at
```

`make load` repairs the first two. It cannot repair `break-stale` — that is the gap in the
watermark approach and `SOLUTION.md` covers it.

## Failed run

```
python src/load.py --fail-after-stage customers
```

Raises after staging, before the merge. Recorded as failed in `meta.run_log`, watermark
not advanced. Run `make load` again and it picks up where it stopped.

## Reset

```
make reset   # warehouse only
make down    # also drops the source database
```

## Seed data

`src/seed.py` was generated with AI assistance to get the synthetic dataset built quickly
and keep the planted defects organised. Same seed value every run, same data on every
machine.
