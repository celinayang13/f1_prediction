import sqlite3

conn = sqlite3.connect("data/f1.db")

print("=== Raw sessions by year ===")
for row in conn.execute("SELECT year, COUNT(*) FROM raw_sessions GROUP BY year ORDER BY year").fetchall():
    print(f"  {row[0]}: {row[1]} sessions")

print()
print("=== features_prerace by year ===")
for row in conn.execute("SELECT rs.year, COUNT(*) FROM features_prerace fp JOIN raw_sessions rs ON fp.session_key = rs.session_key GROUP BY rs.year ORDER BY rs.year").fetchall():
    print(f"  {row[0]}: {row[1]} rows")

print()
print("=== features_inrace by year ===")
for row in conn.execute("SELECT rs.year, COUNT(*) FROM features_inrace fi JOIN raw_sessions rs ON fi.session_key = rs.session_key GROUP BY rs.year ORDER BY rs.year").fetchall():
    print(f"  {row[0]}: {row[1]} rows")

print()
print("=== Train/test split preview ===")
by_year = dict(conn.execute("SELECT rs.year, COUNT(*) FROM features_prerace fp JOIN raw_sessions rs ON fp.session_key = rs.session_key GROUP BY rs.year").fetchall())
train = sum(v for k, v in by_year.items() if k <= 2024)
test = sum(v for k, v in by_year.items() if k == 2025)
print(f"  Train (<=2024): {train} prerace rows")
print(f"  Test  (2025):   {test} prerace rows")
if train == 0:
    print("  WARNING: No training data — run backfill for 2022-2024")
if test == 0:
    print("  WARNING: No test data — run backfill for 2025")
if train > 0 and test > 0:
    print("  READY: Run python3.11 scripts/train.py")
