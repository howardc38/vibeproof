"""Deliberately ignores stdin: a changing answer is not a correlated read."""
import sqlite3

with sqlite3.connect("notes.db") as conn:
    conn.execute("CREATE TABLE IF NOT EXISTS notes (run_id TEXT)")
    print(conn.execute("SELECT count(*) FROM notes").fetchone()[0])
