# -*- coding: utf-8 -*-
"""
load_remote_db.py — One-click script to initialize and seed a remote Render PostgreSQL database.

Usage:
    python load_remote_db.py "postgresql://user:password@host/dbname"
"""

import sys
import os
import psycopg2

def load_db(conn_str: str):
    print("=" * 60)
    print("Connecting to remote database...")
    print("=" * 60)
    conn = psycopg2.connect(conn_str)
    conn.autocommit = True
    cur = conn.cursor()

    # 1. Schema
    schema_path = os.path.join(os.path.dirname(__file__), "app", "schema.sql")
    print(f"Applying schema from {schema_path}...")
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    cur.execute(schema_sql)
    print("✓ Schema applied successfully!")

    # 2. Seed data
    seed_path = os.path.join(os.path.dirname(__file__), "seed", "seed.sql")
    print(f"Loading production seed data from {seed_path} (~35 MB)...")
    with open(seed_path, "r", encoding="utf-8") as f:
        # Load seed statements
        seed_sql = f.read()
    cur.execute(seed_sql)
    print("✓ Seed data loaded successfully!")

    # Verify counts
    cur.execute("SELECT count(*) FROM applications")
    apps_count = cur.fetchone()[0]
    print(f"✓ Verification: {apps_count:,} applications now in remote database.")

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
