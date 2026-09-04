import duckdb

DDL = """
CREATE SCHEMA IF NOT EXISTS meta;

CREATE TABLE IF NOT EXISTS meta.load_state (
    table_name        VARCHAR PRIMARY KEY,
    strategy          VARCHAR NOT NULL,
    watermark_value   VARCHAR,
    last_run_id       BIGINT,
    last_success_at   TIMESTAMP
);

CREATE SEQUENCE IF NOT EXISTS meta.run_seq START 1;

CREATE TABLE IF NOT EXISTS meta.run_log (
    run_id            BIGINT,
    table_name        VARCHAR,
    strategy          VARCHAR,
    status            VARCHAR,
    watermark_from    VARCHAR,
    watermark_to      VARCHAR,
    rows_read         BIGINT,
    rows_inserted     BIGINT,
    rows_updated      BIGINT,
    rows_deleted      BIGINT,
    bytes_read        BIGINT,
    started_at        TIMESTAMP,
    ended_at          TIMESTAMP,
    error_text        VARCHAR
);
"""


def connect(path):
    con = duckdb.connect(path)
    con.execute(DDL)
    return con


def new_run_id(con):
    return con.execute("SELECT nextval('meta.run_seq')").fetchone()[0]


def get_watermark(con, table):
    row = con.execute(
        "SELECT watermark_value FROM meta.load_state WHERE table_name = ?",
        [table],
    ).fetchone()
    return row[0] if row else None


def begin(con, run_id, table, strategy, wm_from, started_at):
    con.execute(
        """
        INSERT INTO meta.run_log
            (run_id, table_name, strategy, status, watermark_from, started_at)
        VALUES (?, ?, ?, 'running', ?, ?)
        """,
        [run_id, table, strategy, wm_from, started_at],
    )


def finish(con, run_id, table, wm_to, counts, bytes_read, ended_at):
    con.execute(
        """
        UPDATE meta.run_log
           SET status = 'success',
               watermark_to = ?,
               rows_read = ?,
               rows_inserted = ?,
               rows_updated = ?,
               rows_deleted = ?,
               bytes_read = ?,
               ended_at = ?
         WHERE run_id = ? AND table_name = ?
        """,
        [
            wm_to, counts["read"], counts["inserted"], counts["updated"],
            counts["deleted"], bytes_read, ended_at, run_id, table,
        ],
    )


def fail(con, run_id, table, message, ended_at):
    con.execute(
        """
        UPDATE meta.run_log
           SET status = 'failed', error_text = ?, ended_at = ?
         WHERE run_id = ? AND table_name = ?
        """,
        [str(message)[:500], ended_at, run_id, table],
    )


def commit_watermark(con, table, strategy, watermark, run_id, ended_at):
    con.execute(
        """
        INSERT INTO meta.load_state
            (table_name, strategy, watermark_value, last_run_id, last_success_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (table_name) DO UPDATE SET
            strategy = excluded.strategy,
            watermark_value = excluded.watermark_value,
            last_run_id = excluded.last_run_id,
            last_success_at = excluded.last_success_at
        """,
        [table, strategy, watermark, run_id, ended_at],
    )
