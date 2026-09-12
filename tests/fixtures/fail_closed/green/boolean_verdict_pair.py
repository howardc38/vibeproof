import sqlite3

def save(conn):
    try:
        conn.execute('INSERT INTO record VALUES (1)')
    except sqlite3.Error as error:
        return False, f'Could not store the record: {error}'
    return True, None
