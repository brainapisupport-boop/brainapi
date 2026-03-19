import sqlite3

conn = sqlite3.connect("brainapi.db")
cursor = conn.cursor()

print("Tables:")
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
print(cursor.fetchall())

print("\nAPI Keys:")
cursor.execute("SELECT * FROM api_keys")
rows = cursor.fetchall()

for r in rows:
    print(r)
    

      