import argparse
import sys
import time
from datetime import datetime, timezone

import pyarrow as pa
import pyodbc

import state
from config import WAREHOUSE_PATH, mssql_dsn
from tables import EXCLUDED, TABLES

BATCH = 20000


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def source_connection():
    return pyodbc.connect(mssql_dsn(), autocommit=True)


def fetch_arrow(cur, columns):
    rows = cur.fetchall()
    if not rows:
        return pa.table({c: pa.array([], type=pa.string()) for c in columns})
    cols = list(zip(*rows))
    return pa.table({c: pa.array(list(v)) for c, v in zip(columns, cols)})


def read_changes(src, table, cfg, watermark):
    cols = ", ".join(cfg["columns"])
    wm_col = cfg["watermark_column"]
    cur = src.cursor()

    if watermark is None:
        cur.execute(f"SELECT {cols} FROM dbo.{table} ORDER BY {wm_col}")
    elif cfg["strategy"] == "append":
        cur.execute(
            f"SELECT {cols} FROM dbo.{table} WHERE {wm_col} > ? ORDER BY {wm_col}",
            int(watermark),
        )
    else:
        cur.execute(
            f"SELECT {cols} FROM dbo.{table} WHERE {wm_col} > ? ORDER BY {wm_col}",
            datetime.fromisoformat(watermark),
        )
    return fetch_arrow(cur, cfg["columns"])


def next_watermark(src, table, cfg, current):
    wm_col = cfg["watermark_column"]
    cur = src.cursor()
    cur.execute(f"SELECT MAX({wm_col}) FROM dbo.{table}")
    value = cur.fetchone()[0]
    if value is None:
        return current
    return str(value)


def stage(wh, table, arrow_table):
    wh.register("incoming", arrow_table)
    wh.execute(f"CREATE OR REPLACE TABLE staging_{table} AS SELECT * FROM incoming")
    wh.unregister("incoming")


def ensure_target(wh, table, cfg):
    cols = ", ".join(cfg["columns"])
    wh.execute(
        f"CREATE TABLE IF NOT EXISTS main.{table} AS "
        f"SELECT {cols} FROM staging_{table} WHERE 1 = 0"
    )


def merge(wh, table, cfg):
    key = cfg["key"]
    on = " AND ".join(f"t.{k} = s.{k}" for k in key)
    updatable = [c for c in cfg["columns"] if c not in key]
    set_clause = ", ".join(f"{c} = s.{c}" for c in updatable)
    cols = ", ".join(cfg["columns"])

    updated = wh.execute(
        f"""
        SELECT COUNT(*) FROM main.{table} t
        JOIN staging_{table} s ON {on}
        """
    ).fetchone()[0]

    wh.execute(
        f"""
        UPDATE main.{table} AS t
           SET {set_clause}
          FROM staging_{table} AS s
         WHERE {on}
        """
    )

    inserted = wh.execute(
        f"""
        SELECT COUNT(*) FROM staging_{table} s
        WHERE NOT EXISTS (
            SELECT 1 FROM main.{table} t WHERE {on}
        )
        """
    ).fetchone()[0]

    wh.execute(
        f"""
        INSERT INTO main.{table} ({cols})
        SELECT {cols} FROM staging_{table} s
         WHERE NOT EXISTS (
            SELECT 1 FROM main.{table} t WHERE {on}
         )
        """
    )
    return inserted, updated


def append(wh, table, cfg):
    key = cfg["key"]
    on = " AND ".join(f"t.{k} = s.{k}" for k in key)
    cols = ", ".join(cfg["columns"])
    inserted = wh.execute(
        f"""
        SELECT COUNT(*) FROM staging_{table} s
        WHERE NOT EXISTS (SELECT 1 FROM main.{table} t WHERE {on})
        """
    ).fetchone()[0]
    wh.execute(
        f"""
        INSERT INTO main.{table} ({cols})
        SELECT {cols} FROM staging_{table} s
         WHERE NOT EXISTS (SELECT 1 FROM main.{table} t WHERE {on})
        """
    )
    return inserted, 0


def reconcile_deletes(src, wh, table, cfg):
    key = cfg["key"][0]
    cur = src.cursor()
    cur.execute(f"SELECT {key} FROM dbo.{table}")
    live = [r[0] for r in cur.fetchall()]

    wh.register("live_keys", pa.table({key: pa.array(live)}))
    wh.execute(
        f"CREATE OR REPLACE TABLE staging_{table}_keys AS SELECT * FROM live_keys"
    )
    wh.unregister("live_keys")

    removed = wh.execute(
        f"""
        SELECT COUNT(*) FROM main.{table} t
        WHERE NOT EXISTS (
            SELECT 1 FROM staging_{table}_keys k WHERE k.{key} = t.{key}
        )
        """
    ).fetchone()[0]

    wh.execute(
        f"""
        DELETE FROM main.{table}
         WHERE {key} NOT IN (SELECT {key} FROM staging_{table}_keys)
        """
    )
    return removed, len(live)


def load_table(src, wh, run_id, table, cfg, fail_after_stage):
    started = now()
    watermark = state.get_watermark(wh, table)
    state.begin(wh, run_id, table, cfg["strategy"], watermark, started)

    try:
        data = read_changes(src, table, cfg, watermark)
        rows_read = data.num_rows
        bytes_read = data.nbytes

        stage(wh, table, data)
        ensure_target(wh, table, cfg)

        if fail_after_stage == table:
            raise RuntimeError("injected failure after staging")

        wh.execute("BEGIN TRANSACTION")
        if cfg["strategy"] == "append":
            inserted, updated = append(wh, table, cfg)
        else:
            inserted, updated = merge(wh, table, cfg)
        wh.execute("COMMIT")

        deleted = 0
        if cfg["reconcile_keys"]:
            deleted, _ = reconcile_deletes(src, wh, table, cfg)

        wm = next_watermark(src, table, cfg, watermark)
        ended = now()
        counts = {
            "read": rows_read, "inserted": inserted,
            "updated": updated, "deleted": deleted,
        }
        state.finish(wh, run_id, table, wm, counts, bytes_read, ended)
        state.commit_watermark(wh, table, cfg["strategy"], wm, run_id, ended)
        return counts, bytes_read

    except Exception as exc:
        try:
            wh.execute("ROLLBACK")
        except Exception:
            pass
        state.fail(wh, run_id, table, exc, now())
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail-after-stage", default=None)
    parser.add_argument("--only", default=None)
    args = parser.parse_args()

    wh = state.connect(WAREHOUSE_PATH)
    src = source_connection()
    run_id = state.new_run_id(wh)

    selected = {args.only: TABLES[args.only]} if args.only else TABLES

    print(f"run {run_id}")
    print(f"{'table':<20}{'strategy':<10}{'read':>9}{'ins':>9}{'upd':>9}{'del':>7}{'MB':>9}{'sec':>8}")

    total_bytes = 0
    failed = False
    for table, cfg in selected.items():
        t0 = time.perf_counter()
        try:
            counts, nbytes = load_table(src, wh, run_id, table, cfg, args.fail_after_stage)
        except Exception as exc:
            print(f"{table:<20}{cfg['strategy']:<10}  FAILED: {exc}")
            failed = True
            break
        total_bytes += nbytes
        print(
            f"{table:<20}{cfg['strategy']:<10}"
            f"{counts['read']:>9,}{counts['inserted']:>9,}"
            f"{counts['updated']:>9,}{counts['deleted']:>7,}"
            f"{nbytes / 1048576:>9.2f}{time.perf_counter() - t0:>8.2f}"
        )

    for table, reason in EXCLUDED.items():
        print(f"{table:<20}{'skipped':<10}  {reason}")

    print(f"\ntransferred {total_bytes / 1048576:.2f} MB")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
