import os

MSSQL = {
    "host": os.getenv("MSSQL_HOST", "localhost"),
    "port": os.getenv("MSSQL_PORT", "1433"),
    "user": os.getenv("MSSQL_USER", "sa"),
    "password": os.getenv("MSSQL_SA_PASSWORD", "Str0ng!Passw0rd"),
    "database": os.getenv("MSSQL_DB", "opsdb"),
}

WAREHOUSE_PATH = os.getenv("WAREHOUSE_PATH", "warehouse/wh.duckdb")

SEED = 20240917

COMPANY_DOMAINS = {"fundo.com"}


def mssql_dsn(database=None):
    drivers = [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
    ]
    import pyodbc

    available = set(pyodbc.drivers())
    driver = next((d for d in drivers if d in available), None)
    if driver is None:
        raise RuntimeError(
            "No SQL Server ODBC driver found. Install msodbcsql18."
        )
    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={MSSQL['host']},{MSSQL['port']};"
        f"DATABASE={database or MSSQL['database']};"
        f"UID={MSSQL['user']};PWD={MSSQL['password']};"
        "TrustServerCertificate=yes;Encrypt=yes"
    )
