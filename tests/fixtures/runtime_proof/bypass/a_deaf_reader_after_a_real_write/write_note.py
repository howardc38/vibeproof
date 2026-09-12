import sqlite3

with sqlite3.connect("notes.db") as conn:
    conn.execute("CREATE TABLE IF NOT EXISTS notes (run_id TEXT)")
    conn.execute("INSERT INTO notes VALUES ('fixed-id-not-the-current-run')")
