"""CLI tool to create or manage admin users.

Usage (via Docker Compose — see docker-compose.yml create-admin service):
    docker compose --profile create-admin run --rm create-admin \\
        create --username admin --password secret --email admin@example.com

    docker compose --profile create-admin run --rm create-admin list

Or directly (when running outside Docker):
    python -m app.create_admin create --username admin --password secret
    python -m app.create_admin list
    python -m app.create_admin set-password --username admin --password newpassword
"""

import argparse
import sys

from app.database import SessionLocal


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and manage admin users for the Zefix Analyzer."
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    # ── create ────────────────────────────────────────────────────────────────
    create = subparsers.add_parser(
        "create",
        help="Create a new superadmin user, or update an existing one.",
    )
    create.add_argument("--username", required=True, help="Login username.")
    create.add_argument("--password", required=True, help="Login password (min 8 chars recommended).")
    create.add_argument("--email", default=None, help="Email address (optional).")
    create.add_argument(
        "--tier",
        default="enterprise",
        choices=["free", "starter", "professional", "enterprise"],
        help="User tier (default: enterprise).",
    )
    create.add_argument(
        "--no-superadmin",
        action="store_true",
        help="Create as a regular user without superadmin privileges.",
    )

    # ── set-password ──────────────────────────────────────────────────────────
    setpw = subparsers.add_parser("set-password", help="Reset the password for an existing user.")
    setpw.add_argument("--username", required=True, help="Username to update.")
    setpw.add_argument("--password", required=True, help="New password.")

    # ── list ──────────────────────────────────────────────────────────────────
    subparsers.add_parser("list", help="List all users with their tier and status.")

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    # Import here so the Python 3.12 patch in main.py runs first when invoked
    # via the web process, but also works standalone.
    from app import crud

    with SessionLocal() as db:

        # ── create / upsert ───────────────────────────────────────────────────
        if args.mode == "create":
            if len(args.password) < 8:
                print("ERROR: password must be at least 8 characters.", file=sys.stderr)
                return 1

            existing = crud.get_user_by_username(db, args.username)
            if existing:
                # Update in-place
                existing.hashed_password = crud.hash_password(args.password)
                existing.tier = args.tier
                existing.is_superadmin = not args.no_superadmin
                if args.email:
                    existing.email = args.email
                db.commit()
                db.refresh(existing)
                print(f"Updated user '{existing.username}' (id={existing.id})")
                print(f"  tier         : {existing.tier}")
                print(f"  is_superadmin: {existing.is_superadmin}")
                print(f"  email        : {existing.email or '(not set)'}")
            else:
                user = crud.create_user(
                    db,
                    username=args.username,
                    password=args.password,
                    is_active=True,
                    email=args.email,
                    tier=args.tier,
                    is_superadmin=not args.no_superadmin,
                )
                print(f"Created user '{user.username}' (id={user.id})")
                print(f"  tier         : {user.tier}")
                print(f"  is_superadmin: {user.is_superadmin}")
                print(f"  email        : {user.email or '(not set)'}")

            print("\nDone. You can now log in at /login")
            return 0

        # ── set-password ──────────────────────────────────────────────────────
        if args.mode == "set-password":
            if len(args.password) < 8:
                print("ERROR: password must be at least 8 characters.", file=sys.stderr)
                return 1

            user = crud.get_user_by_username(db, args.username)
            if not user:
                print(f"ERROR: user '{args.username}' not found.", file=sys.stderr)
                return 1

            user.hashed_password = crud.hash_password(args.password)
            db.commit()
            print(f"Password updated for '{user.username}'.")
            return 0

        # ── list ──────────────────────────────────────────────────────────────
        if args.mode == "list":
            users = crud.list_users(db)
            if not users:
                print("No users found.")
                return 0

            header = f"{'ID':<5} {'Username':<24} {'Tier':<14} {'Superadmin':<12} {'Active':<8} {'Email'}"
            print(header)
            print("-" * len(header))
            for u in users:
                print(
                    f"{u.id:<5} {u.username:<24} {u.tier:<14} "
                    f"{'yes' if u.is_superadmin else 'no':<12} "
                    f"{'yes' if u.is_active else 'no':<8} "
                    f"{u.email or '(not set)'}"
                )
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
