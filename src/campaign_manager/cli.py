"""Administrative command-line interface."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from campaign_manager import __version__
from campaign_manager.auth import hash_password, normalize_email
from campaign_manager.config import Settings
from campaign_manager.database import configure_database, session_factory
from campaign_manager.models import User


def _directory_check(path: Path) -> dict[str, object]:
    exists = path.exists()
    return {
        "path": str(path),
        "exists": exists,
        "is_directory": path.is_dir() if exists else False,
        "writable": _is_writable_directory(path),
    }


def _is_writable_directory(path: Path) -> bool:
    candidate = path if path.exists() else path.parent
    return candidate.exists() and candidate.is_dir() and os_access(candidate)


def os_access(path: Path) -> bool:
    import os

    return os.access(path, os.W_OK)


def doctor(settings: Settings, check_database: bool = False) -> int:
    checks = {
        "version": __version__,
        "configuration": settings.safe_summary(),
        "artifact_storage": _directory_check(settings.artifact_root),
        "publish_storage": _directory_check(settings.publish_root),
    }
    checks["ok"] = all(
        check["writable"]
        for check in (checks["artifact_storage"], checks["publish_storage"])
    )
    if check_database:
        try:
            with session_factory()() as database:
                database.execute(text("SELECT 1"))
            checks["database"] = {"ok": True}
        except SQLAlchemyError as exc:
            checks["database"] = {"ok": False, "error": type(exc).__name__}
            checks["ok"] = False
    print(json.dumps(checks, indent=2))
    return 0 if checks["ok"] else 1


def migrate() -> int:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    return 0


def create_admin(email: str, display_name: str) -> int:
    normalized_email = normalize_email(email)
    first_password = getpass.getpass("Password: ")
    second_password = getpass.getpass("Confirm password: ")
    if first_password != second_password:
        print("Passwords do not match", file=sys.stderr)
        return 2
    password_hash = hash_password(first_password)
    with session_factory()() as database:
        existing = database.scalar(select(User).where(User.email == normalized_email))
        if existing is not None:
            print("A user with that email already exists", file=sys.stderr)
            return 2
        database.add(
            User(
                email=normalized_email,
                display_name=display_name.strip(),
                password_hash=password_hash,
                is_instance_admin=True,
            )
        )
        database.commit()
    print(f"Created administrator {normalized_email}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="campaignctl")
    result.add_argument("--version", action="version", version=__version__)
    subcommands = result.add_subparsers(dest="command", required=True)
    doctor_parser = subcommands.add_parser("doctor", help="validate configuration and storage")
    doctor_parser.add_argument("--database", action="store_true", help="also connect to the database")
    subcommands.add_parser("migrate", help="apply database migrations")
    admin_parser = subcommands.add_parser("create-admin", help="create the initial administrator")
    admin_parser.add_argument("--email", required=True)
    admin_parser.add_argument("--name", required=True, dest="display_name")
    return result


def main(argv: list[str] | None = None) -> None:
    arguments = parser().parse_args(argv)
    settings = Settings.from_environment()
    configure_database(settings.database_url)
    if arguments.command == "doctor":
        raise SystemExit(doctor(settings, check_database=arguments.database))
    if arguments.command == "migrate":
        raise SystemExit(migrate())
    if arguments.command == "create-admin":
        raise SystemExit(create_admin(arguments.email, arguments.display_name))
    raise SystemExit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
