"""Change a PostgreSQL user's password without needing psql.exe.

Usage:
    cd backend
    .venv\\Scripts\\activate         (Windows)
    python scripts/change_password.py

The script connects using your CURRENT credentials (from .env or prompt),
then runs ALTER USER to set a new password.
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2


def main():
    print("=" * 60)
    print("PostgreSQL Password Changer")
    print("=" * 60)
    print()

    # Connection info
    host     = input("Host [localhost]: ").strip() or "localhost"
    port     = input("Port [5432]: ").strip() or "5432"
    database = input("Database name [careAidb]: ").strip() or "careAidb"
    username = input("Username to change password for [careAidb]: ").strip() or "careAidb"

    current_pw = getpass.getpass(f"Current password for {username}: ")
    new_pw     = getpass.getpass("New password: ")
    confirm    = getpass.getpass("Confirm new password: ")

    if new_pw != confirm:
        print("\n❌ Passwords don't match.")
        return

    if not new_pw:
        print("\n❌ Password cannot be empty.")
        return

    # Warn about special chars
    SPECIAL = set("@:/?#&%")
    if any(c in SPECIAL for c in new_pw):
        print(f"\n⚠ WARNING: Your new password contains special characters ({SPECIAL}).")
        print("   You'll need to URL-encode them in DATABASE_URL.")
        print("   Recommendation: use letters and digits only for simpler config.")
        if input("   Continue anyway? (yes/no): ").strip().lower() != "yes":
            return

    print()
    print(f"Connecting to {username}@{host}:{port}/{database}…")

    try:
        conn = psycopg2.connect(
            host=host, port=port, database=database,
            user=username, password=current_pw,
        )
    except psycopg2.OperationalError as e:
        print(f"\n❌ Could not connect: {e}")
        print("\nCheck:")
        print("  - Is Postgres running? (Windows: services.msc → look for 'postgresql')")
        print("  - Did you type the current password correctly?")
        print("  - Does the user/database exist?")
        return

    conn.autocommit = True
    try:
        cur = conn.cursor()
        # Use parameterized identifier to avoid SQL injection.
        # ALTER USER requires the password as a literal — quote_ident is for the username.
        from psycopg2 import sql
        cur.execute(
            sql.SQL("ALTER USER {} WITH PASSWORD %s").format(sql.Identifier(username)),
            [new_pw],
        )
        print()
        print(f"✅ Password changed successfully for user '{username}'")
        print()
        print("Now update your backend/.env DATABASE_URL with the new password:")
        print()
        print(f"   DATABASE_URL=postgresql+psycopg2://{username}:{new_pw}@{host}:{port}/{database}")
        print()
        if any(c in SPECIAL for c in new_pw):
            from urllib.parse import quote
            encoded = quote(new_pw, safe="")
            print(f"⚠ Or with URL-encoded password (because of special chars):")
            print(f"   DATABASE_URL=postgresql+psycopg2://{username}:{encoded}@{host}:{port}/{database}")
            print()

    except Exception as e:
        print(f"\n❌ Failed to change password: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
