import sqlite3

class QueryFailure(str):
    pass

def fetch(conn):
    try:
        return conn.execute('SELECT * FROM record').fetchall(), None
    except sqlite3.Error as error:
        return None, QueryFailure(str(error))
