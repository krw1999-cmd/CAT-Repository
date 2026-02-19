"""Create the first admin user.

Usage:
    python tools/cat_web/seed.py

Run once after first deploy. Safe to re-run — will not overwrite an existing user.
"""

import getpass
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent to path so we can import config without Flask context
sys.path.insert(0, str(Path(__file__).parent))
import config
import auth


def main():
    print("CAT Web — Create Admin User")
    print("-" * 30)

    username = input("Username: ").strip()
    if not username:
        print("Username cannot be blank.")
        sys.exit(1)

    password = getpass.getpass("Password: ")
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        sys.exit(1)

    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.")
        sys.exit(1)

    password_hash = auth.hash_password(password)
    now = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(config.DB_PATH)
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, password_hash, now),
        )
        conn.commit()
        print(f"\nUser '{username}' created successfully.")
    except sqlite3.IntegrityError:
        print(f"\nUser '{username}' already exists. No changes made.")
    finally:
        conn.close()


if __name__ == "__main__":
    # Ensure DB and schema exist before seeding
    import db as db_module
    db_module.init_db()
    main()
