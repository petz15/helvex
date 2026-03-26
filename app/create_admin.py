"""CLI tool to create or manage admin users.

Usage (via Docker Compose — see docker-compose.yml create-admin service):
    docker compose --profile create-admin run --rm create-admin \\
        create --email admin@example.com --password secret

    docker compose --profile create-admin run --rm create-admin list

Or directly (when running outside Docker):
    python -m app.create_admin create --email admin@example.com --password secret
    python -m app.create_admin list
    python -m app.create_admin set-password --email admin@example.com --password newpassword
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
    create.add_argument("--email", required=True, help="Login email address.")
    create.add_argument("--password", required=True, help="Login password (min 8 chars recommended).")
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
    setpw.add_argument("--email", required=True, help="Email of the user to update.")
    setpw.add_argument("--password", required=True, help="New password.")

    # ── list ──────────────────────────────────────────────────────────────────
    subparsers.add_parser("list", help="List all users with their tier and status.")

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    from app import crud

    with SessionLocal() as db:

        # ── create / upsert ───────────────────────────────────────────────────
        if args.mode == "create":
            if len(args.password) < 8:
                print("ERROR: password must be at least 8 characters.", file=sys.stderr)
                return 1

            existing = crud.get_user_by_email(db, args.email)
            if existing:
                existing.hashed_password = crud.hash_password(args.password)
                existing.tier = args.tier
                existing.is_superadmin = not args.no_superadmin
                db.commit()
                db.refresh(existing)
                print(f"Updated user (id={existing.id})")
                print(f"  email        : {existing.email}")
                print(f"  tier         : {existing.tier}")
                print(f"  is_superadmin: {existing.is_superadmin}")
            else:
                user = crud.create_user(
                    db,
                    email=args.email,
                    password=args.password,
                    is_active=True,
                    tier=args.tier,
                    is_superadmin=not args.no_superadmin,
                )
                # Admin-created users are considered verified
                user.email_verified = True
                db.commit()
                print(f"Created user (id={user.id})")
                print(f"  email        : {user.email}")
                print(f"  tier         : {user.tier}")
                print(f"  is_superadmin: {user.is_superadmin}")

            print("\nDone. You can now log in at /login")
            return 0

        # ── set-password ──────────────────────────────────────────────────────
        if args.mode == "set-password":
            if len(args.password) < 8:
                print("ERROR: password must be at least 8 characters.", file=sys.stderr)
                return 1

            user = crud.get_user_by_email(db, args.email)
            if not user:
                print(f"ERROR: user '{args.email}' not found.", file=sys.stderr)
                return 1

            user.hashed_password = crud.hash_password(args.password)
            db.commit()
            print(f"Password updated for '{user.email}'.")
            return 0

        # ── list ──────────────────────────────────────────────────────────────
        if args.mode == "list":
            users = crud.list_users(db)
            if not users:
                print("No users found.")
                return 0

            header = f"{'ID':<5} {'Email':<40} {'Tier':<14} {'Superadmin':<12} {'Active':<8} {'Org'}"
            print(header)
            print("-" * len(header))
            for u in users:
                print(
                    f"{u.id:<5} {u.email:<40} {u.tier:<14} "
                    f"{'yes' if u.is_superadmin else 'no':<12} "
                    f"{'yes' if u.is_active else 'no':<8} "
                    f"{u.org_id or '(none)'}"
                )
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
