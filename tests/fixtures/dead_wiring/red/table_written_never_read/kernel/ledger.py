
SCHEMA = """
CREATE TABLE IF NOT EXISTS task (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS note (id TEXT PRIMARY KEY);
"""
def a(conn): insert(conn, "task", id="x"); insert(conn, "note", id="y")
def b(conn): conn.execute("SELECT * FROM task"); 
