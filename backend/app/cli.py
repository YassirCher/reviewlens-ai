from __future__ import annotations

import argparse
import getpass
import sys

from app.config import ProcessRole
from app.platform.health import (
    collect_health,
    process_dependencies_are_ready,
    scheduler_heartbeat_is_fresh,
    validate_process,
    worker_is_reachable,
)
from app.security import hash_password
from app.seed import seed_foundation


def _hash_password() -> int:
    password = getpass.getpass("Admin password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 2
    try:
        print(hash_password(password))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def _validate(role: ProcessRole) -> int:
    errors = validate_process(role)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"{role} configuration is valid")
    return 0


def _healthcheck(role: ProcessRole) -> int:
    errors = validate_process(role)
    if errors:
        return 1
    if role == "api":
        return 0 if collect_health().ready else 1
    if role == "worker":
        return 0 if process_dependencies_are_ready(role) and worker_is_reachable() else 1
    if role == "scheduler":
        return 0 if process_dependencies_are_ready(role) and scheduler_heartbeat_is_fresh() else 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ReviewLens platform commands")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("hash-password", help="Generate an Argon2id hash without echoing the password")
    subcommands.add_parser("seed", help="Idempotently seed the Phase 1 foundation")
    for command in ("validate", "healthcheck"):
        command_parser = subcommands.add_parser(command)
        command_parser.add_argument("role", choices=("api", "worker", "scheduler", "migrate"))
    args = parser.parse_args()

    if args.command == "hash-password":
        return _hash_password()
    if args.command == "seed":
        result = seed_foundation()
        print(result)
        return 0
    if args.command == "validate":
        return _validate(args.role)
    if args.command == "healthcheck":
        return _healthcheck(args.role)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
