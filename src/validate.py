import argparse
import sys
from datetime import datetime, timezone

import pyodbc

import state
from config import WAREHOUSE_PATH, mssql_dsn
from tables import TABLES

RESULT_DDL = """
CREATE SCHEMA IF NOT EXISTS dq;

CREATE TABLE IF NOT EXISTS dq.check_results (
    checked_at    TIMESTAMP,
    check_name    VARCHAR,
    scope         VARCHAR,
    status        VARCHAR,
    observed      VARCHAR,
    expected      VARCHAR,
    detail        VARCHAR
);
"""


def source_counts(src, table, cfg):
    cur = src.cursor()
    if cfg["soft_delete_column"]:
        cur.execute(f"SELECT COUNT(*) FROM dbo.{table} WHERE is_deleted = 0")
    else:
        cur.execute(f"SELECT COUNT(*) FROM dbo.{table}")
    return cur.fetchone()[0]


def source_key_bounds(src, table, cfg):
    key = cfg["key"][0]
    cur = src.cursor()
    cur.execute(f"SELECT MIN({key}), MAX({key}), COUNT(*) FROM dbo.{table}")
    return cur.fetchone()


def check_row_counts(src, wh, results):
    for table, cfg in TABLES.items():
        expected = source_counts(src, table, cfg)
        if cfg["soft_delete_column"]:
            observed = wh.execute(
                f"SELECT COUNT(*) FROM main.{table} WHERE NOT is_deleted"
            ).fetchone()[0]
        else:
            observed = wh.execute(f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]
        status = "pass" if observed == expected else "fail"
        results.append(
            ("row_count_matches_source", table, status, str(observed), str(expected),
             f"delta={observed - expected}")
        )


def check_no_orphan_keys(src, wh, results):
    for table, cfg in TABLES.items():
        if not cfg["reconcile_keys"]:
            continue
        key = cfg["key"][0]
        cur = src.cursor()
        cur.execute(f"SELECT {key} FROM dbo.{table}")
        live = {r[0] for r in cur.fetchall()}
        wh_keys = {
            r[0] for r in wh.execute(f"SELECT {key} FROM main.{table}").fetchall()
        }
        extra = wh_keys - live
        status = "pass" if not extra else "fail"
        sample = ",".join(str(k) for k in sorted(extra)[:5])
        results.append(
            ("no_rows_deleted_upstream", table, status, str(len(extra)), "0",
             f"sample={sample}" if sample else "")
        )


def check_key_gaps(src, wh, results):
    for table, cfg in TABLES.items():
        if cfg["strategy"] != "append":
            continue
        key = cfg["key"][0]
        lo, hi, total = source_key_bounds(src, table, cfg)
        if lo is None:
            continue
        observed = wh.execute(
            f"SELECT COUNT(*) FROM main.{table} WHERE {key} BETWEEN ? AND ?",
            [lo, hi],
        ).fetchone()[0]
        status = "pass" if observed == total else "fail"
        results.append(
            ("no_gaps_in_key_range", table, status, str(observed), str(total),
             f"range={lo}..{hi}")
        )


def check_primary_key_unique(wh, results):
    for table, cfg in TABLES.items():
        key = cfg["key"][0]
        dupes = wh.execute(
            f"""
            SELECT COUNT(*) FROM (
                SELECT {key} FROM main.{table}
                 GROUP BY {key} HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        status = "pass" if dupes == 0 else "fail"
        results.append(
            ("primary_key_is_unique", table, status, str(dupes), "0", "")
        )


def check_referential_integrity(wh, results):
    checks = [
        ("advances", "customer_id", "customers", "customer_id"),
        ("cards", "customer_id", "customers", "customer_id"),
        ("transactions", "card_id", "cards", "card_id"),
        ("transactions", "customer_id", "customers", "customer_id"),
    ]
    for child, fk, parent, pk in checks:
        broken = wh.execute(
            f"""
            SELECT COUNT(*) FROM main.{child} c
             WHERE NOT EXISTS (
                SELECT 1 FROM main.{parent} p WHERE p.{pk} = c.{fk}
             )
            """
        ).fetchone()[0]
        status = "pass" if broken == 0 else "fail"
        results.append(
            ("referential_integrity", f"{child}.{fk}->{parent}", status,
             str(broken), "0", "")
        )


def check_sum_reconciliation(src, wh, results):
    cur = src.cursor()
    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM dbo.transactions")
    expected = float(cur.fetchone()[0])
    observed = float(
        wh.execute("SELECT COALESCE(SUM(amount), 0) FROM main.transactions").fetchone()[0]
    )
    status = "pass" if abs(observed - expected) < 0.01 else "fail"
    results.append(
        ("transaction_amount_reconciles", "transactions", status,
         f"{observed:.2f}", f"{expected:.2f}", f"delta={observed - expected:.2f}")
    )


def check_no_protected_customer_merged(wh, results):
    if not table_exists(wh, "dq", "customer_resolution"):
        return
    violations = wh.execute(
        """
        SELECT COUNT(*) FROM dq.customer_resolution
         WHERE protected AND action = 'merge'
        """
    ).fetchone()[0]
    status = "pass" if violations == 0 else "fail"
    results.append(
        ("protected_customer_never_merged", "dq.customer_resolution", status,
         str(violations), "0", "")
    )


def check_every_card_has_owner(wh, results):
    if not table_exists(wh, "dq", "card_ownership"):
        return
    orphans = wh.execute(
        """
        SELECT COUNT(*) FROM dq.card_ownership o
         WHERE NOT EXISTS (
            SELECT 1 FROM dq.customer_golden g WHERE g.customer_id = o.customer_id
         )
        """
    ).fetchone()[0]
    status = "pass" if orphans == 0 else "fail"
    results.append(
        ("every_card_has_surviving_owner", "dq.card_ownership", status,
         str(orphans), "0", "")
    )


def table_exists(wh, schema, name):
    return wh.execute(
        """
        SELECT COUNT(*) FROM information_schema.tables
         WHERE table_schema = ? AND table_name = ?
        """,
        [schema, name],
    ).fetchone()[0] > 0


def run_all(src, wh):
    results = []
    check_row_counts(src, wh, results)
    check_no_orphan_keys(src, wh, results)
    check_key_gaps(src, wh, results)
    check_primary_key_unique(wh, results)
    check_referential_integrity(wh, results)
    check_sum_reconciliation(src, wh, results)
    check_no_protected_customer_merged(wh, results)
    check_every_card_has_owner(wh, results)
    return results


def persist(wh, results):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    wh.executemany(
        "INSERT INTO dq.check_results VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(now, r[0], r[1], r[2], r[3], r[4], r[5]) for r in results],
    )


def render(results):
    width = max(len(r[0]) for r in results) + 2
    scope_width = max(len(r[1]) for r in results) + 2
    print(f"{'check':<{width}}{'scope':<{scope_width}}{'status':<8}{'observed':>12}{'expected':>12}  detail")
    for name, scope, status, observed, expected, detail in results:
        mark = "PASS" if status == "pass" else "FAIL"
        print(f"{name:<{width}}{scope:<{scope_width}}{mark:<8}{observed:>12}{expected:>12}  {detail}")

    failed = [r for r in results if r[2] == "fail"]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return failed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    wh = state.connect(WAREHOUSE_PATH)
    wh.execute(RESULT_DDL)
    src = pyodbc.connect(mssql_dsn(), autocommit=True)

    results = run_all(src, wh)
    persist(wh, results)
    failed = render(results)

    if failed and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
