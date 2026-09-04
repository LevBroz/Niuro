import argparse
import sys

import pyodbc

import state
from config import WAREHOUSE_PATH, mssql_dsn

SCENARIOS = {
    "silent_delete": "delete rows from the source without the pipeline running",
    "dropped_rows": "remove warehouse rows the source still has",
    "stale_update": "change the source without advancing the watermark",
}


def silent_delete(src, wh):
    cur = src.cursor()
    cur.execute("SELECT TOP 25 transaction_id FROM dbo.transactions ORDER BY transaction_id DESC")
    ids = [r[0] for r in cur.fetchall()]
    cur.execute(
        f"DELETE FROM dbo.transactions WHERE transaction_id IN ({','.join(map(str, ids))})"
    )
    return f"deleted {len(ids)} source transactions still present in the warehouse"


def dropped_rows(src, wh):
    wh.execute(
        """
        DELETE FROM main.transactions
         WHERE transaction_id IN (
            SELECT transaction_id FROM main.transactions
             ORDER BY transaction_id LIMIT 40
         )
        """
    )
    return "removed 40 warehouse transactions the source still has"


def stale_update(src, wh):
    cur = src.cursor()
    cur.execute(
        """
        UPDATE TOP (10) dbo.advances
           SET principal = principal + 100
         WHERE status = 'funded'
        """
    )
    return "changed 10 source advances without touching updated_at"


HANDLERS = {
    "silent_delete": silent_delete,
    "dropped_rows": dropped_rows,
    "stale_update": stale_update,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    args = parser.parse_args()

    wh = state.connect(WAREHOUSE_PATH)
    src = pyodbc.connect(mssql_dsn(), autocommit=True)

    message = HANDLERS[args.scenario](src, wh)
    print(f"{args.scenario}: {message}")
    print("run 'make check' to see the failing check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
