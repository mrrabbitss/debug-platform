import argparse
import sys
from pathlib import Path

from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.db import SessionLocal  # noqa: E402
from app.core.migrations import run_database_migrations  # noqa: E402
from app.core.utils import new_id, utcnow  # noqa: E402
from app.models import AccessToken, UserAccount  # noqa: E402
from app.services.access_control import VALID_ROLES, issue_access_token  # noqa: E402


def _user(db, username: str) -> UserAccount:
    user = db.scalar(select(UserAccount).where(UserAccount.username == username.lower()))
    if not user:
        raise ValueError(f"User not found: {username}")
    return user


def create_user(args) -> None:
    with SessionLocal() as db:
        username = args.username.lower()
        if db.scalar(select(UserAccount.id).where(UserAccount.username == username)):
            raise ValueError(f"Username already exists: {username}")
        user = UserAccount(
            id=new_id("USR"),
            username=username,
            display_name=args.display_name,
            role=args.role,
        )
        db.add(user)
        db.commit()
        token, raw_token = issue_access_token(
            db,
            user,
            name=args.token_name,
            expires_days=args.expires_days,
        )
        print(f"Created {user.username} ({user.role}), token {token.token_hint}")
        print("Copy this token now. It will not be shown again:")
        print(raw_token)


def issue_token(args) -> None:
    with SessionLocal() as db:
        user = _user(db, args.username)
        token, raw_token = issue_access_token(
            db,
            user,
            name=args.token_name,
            expires_days=args.expires_days,
        )
        print(f"Issued token {token.token_hint} for {user.username}")
        print("Copy this token now. It will not be shown again:")
        print(raw_token)


def list_users(args) -> None:
    with SessionLocal() as db:
        users = db.scalars(select(UserAccount).order_by(UserAccount.username)).all()
        if not users:
            print("No users configured.")
            return
        for user in users:
            active_tokens = len(db.scalars(select(AccessToken).where(
                AccessToken.user_id == user.id,
                AccessToken.revoked_at.is_(None),
            )).all())
            print(f"{user.username}\t{user.role}\tactive={user.active}\ttokens={active_tokens}")


def deactivate_user(args) -> None:
    with SessionLocal() as db:
        user = _user(db, args.username)
        if user.role == "ADMIN":
            other_admin = db.scalar(select(UserAccount.id).where(
                UserAccount.id != user.id,
                UserAccount.role == "ADMIN",
                UserAccount.active.is_(True),
            ).limit(1))
            if not other_admin:
                raise ValueError("Refusing to deactivate the last active administrator")
        user.active = False
        for token in db.scalars(select(AccessToken).where(
            AccessToken.user_id == user.id,
            AccessToken.revoked_at.is_(None),
        )).all():
            token.revoked_at = utcnow()
        db.commit()
        print(f"Deactivated {user.username} and revoked active tokens.")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Manage local RBAC users and one-time access tokens.")
    commands = result.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="Create a user and issue the initial token")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--role", choices=sorted(VALID_ROLES), default="VIEWER")
    create.add_argument("--token-name", default="initial")
    create.add_argument("--expires-days", type=int, default=90)
    create.set_defaults(handler=create_user)

    issue = commands.add_parser("issue-token", help="Issue another token for an existing user")
    issue.add_argument("--username", required=True)
    issue.add_argument("--token-name", default="replacement")
    issue.add_argument("--expires-days", type=int, default=90)
    issue.set_defaults(handler=issue_token)

    listing = commands.add_parser("list", help="List users without revealing token values")
    listing.set_defaults(handler=list_users)

    deactivate = commands.add_parser("deactivate", help="Deactivate a user and revoke active tokens")
    deactivate.add_argument("--username", required=True)
    deactivate.set_defaults(handler=deactivate_user)
    return result


def main() -> int:
    args = parser().parse_args()
    if getattr(args, "expires_days", 1) is not None and getattr(args, "expires_days", 1) < 1:
        raise ValueError("--expires-days must be at least 1")
    run_database_migrations()
    args.handler(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
