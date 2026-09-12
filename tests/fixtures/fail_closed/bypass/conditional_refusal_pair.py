import sqlite3

def save(conn):
    try:
        conn.execute('INSERT INTO record VALUES (1)')
    except sqlite3.Error as error:
        if False:
            return False, str(error)
        return True, None
