def show(conn):
    conn.execute("SELECT * FROM invoice")
    conn.execute("SELECT * FROM audit")
