def show(conn):
    conn.execute("SELECT * FROM invoice")
