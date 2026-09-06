SCHEMA = """
CREATE TABLE IF NOT EXISTS invoice (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS audit (id TEXT PRIMARY KEY);
"""


def write(conn):
    insert(conn, "invoice", id="x")
    insert(conn, "audit", id="y")
