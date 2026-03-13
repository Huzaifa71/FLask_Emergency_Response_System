# export_sqlite.py
import sqlite3

def export_sqlite_to_sql(sqlite_db_path, output_sql_file):
    conn = sqlite3.connect(sqlite_db_path)
    with open(output_sql_file, 'w') as f:
        for line in conn.iterdump():
            f.write('%s\n' % line)
    print(f"Data exported to {output_sql_file}")
    conn.close()

export_sqlite_to_sql('responses.db', 'database_dump.sql')