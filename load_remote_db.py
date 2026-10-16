# -*- coding: utf-8 -*-
"""
load_remote_db.py — One-click script to initialize and seed a remote Render PostgreSQL database.

Usage:
    python load_remote_db.py "postgresql://user:password@host/dbname"
"""

import sys
import os
import io
import time
import psycopg2

def load_db(conn_str: str):
    print("=" * 60)
    print("Connecting to remote database...")
    print("=" * 60)
    conn = psycopg2.connect(conn_str)
    conn.autocommit = True
    cur = conn.cursor()

    # 1. Apply Schema
    schema_path = os.path.join(os.path.dirname(__file__), "app", "schema.sql")
    print(f"Applying schema from {schema_path}...")
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    cur.execute(schema_sql)
    print("[OK] Schema applied successfully!")

    # 2. Apply Seed Data via COPY protocol
    seed_path = os.path.join(os.path.dirname(__file__), "seed", "seed.sql")
    print(f"Streaming production seed data from {seed_path}...")
    
    t0 = time.time()
    with open(seed_path, "r", encoding="utf-8") as f:
        current_copy_sql = None
        current_buffer = []
        for line in f:
            if line.startswith("COPY "):
                current_copy_sql = line.strip().rstrip(";")
                current_buffer = []
            elif line.strip() == r"\.":
                if current_copy_sql:
                    table_name = current_copy_sql.split()[1]
                    print(f"  -> Ingesting {len(current_buffer):,} rows into {table_name}...")
                    stream = io.StringIO("".join(current_buffer))
                    cur.copy_expert(f"{current_copy_sql};", stream)
                    print(f"  [OK] {table_name} loaded ({len(current_buffer):,} records)")
                    current_copy_sql = None
                    current_buffer = []
            elif current_copy_sql:
                current_buffer.append(line)
            elif line.startswith("SELECT setval"):
                cur.execute(line)

    elapsed = round(time.time() - t0, 1)
    print(f"[OK] All seed data loaded successfully in {elapsed}s!")

    # 3. Verify counts
    cur.execute("SELECT count(*) FROM applications")
    apps_count = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM portal_users")
    users_count = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM audit_log")
    audit_count = cur.fetchone()[0]

    print("\nVerification Checklist:")
    print(f"  [OK] applications: {apps_count:,} rows")
    print(f"  [OK] portal_users: {users_count:,} rows")
    print(f"  [OK] audit_log:    {audit_count:,} rows")

    cur.close()
    conn.close()
    print("=" * 60)
    print("Remote database setup complete! Your deployment is ready.")
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python load_remote_db.py <DATABASE_URL>")
        print("Example: python load_remote_db.py 'postgresql://sewasetu:password@dpg-...render.com/sewasetu'")
        sys.exit(1)
    load_db(sys.argv[1])
