
SCHEMA = """
CREATE TABLE IF NOT EXISTS task (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS note (id TEXT PRIMARY KEY);
"""
def a(conn): insert(conn, "task", id="x"); 
def b(conn): conn.execute("SELECT * FROM task"); 
