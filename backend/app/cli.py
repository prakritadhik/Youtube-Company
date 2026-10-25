"""Command-line entry point: ``python -m app.cli <command>``."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().db_url)
    return cfg


def cmd_migrate(_: argparse.Namespace) -> int:
    from alembic import command

    command.upgrade(_alembic_config(), "head")
    print("Database is up to date.")
    return 0


def cmd_seed(_: argparse.Namespace) -> int:
    from app.agents.seed import seed_organization
    from app.db import session_scope

    with session_scope() as db:
        print("Seeded:", seed_organization(db))
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    cmd_migrate(args)
    return cmd_seed(args)


def _read_password(args: argparse.Namespace) -> str:
    if args.password_env:
        pw = os.environ.get(args.password_env)
        if not pw:
            raise SystemExit(f"Environment variable {args.password_env} is empty.")
        return pw
    pw = getpass.getpass("New password: ")
    if pw != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return pw


def cmd_create_owner(args: argparse.Namespace) -> int:
    from app.db import session_scope
    from app.enums import Role
    from app.models.auth import User
    from app.security import passwords
    from app.security.permissions import Actor
    from app.services import audit

    pw = _read_password(args)
    try:
        passwords.validate_password_strength(pw)
    except passwords.WeakPassword as e:
        raise SystemExit(str(e)) from e
    with session_scope() as db:
        if db.scalar(select(User).where(User.username == args.username.lower())):
            raise SystemExit("That user already exists. Use reset-password.")
        user = User(username=args.username.lower(), password_hash=passwords.hash_password(pw), role=Role.OWNER.value)
        db.add(user)
        db.flush()
        audit.record(db, Actor.system("cli"), "auth.owner_created", "user", user.id, {"username": user.username, "via": "cli"})
    print(f"Owner '{args.username.lower()}' created.")
    return 0


def cmd_reset_password(args: argparse.Namespace) -> int:
    from app.db import session_scope
    from app.models.auth import User
    from app.security import passwords, sessions
    from app.security.permissions import Actor
    from app.services import audit

    pw = _read_password(args)
    try:
        passwords.validate_password_strength(pw)
    except passwords.WeakPassword as e:
        raise SystemExit(str(e)) from e
    with session_scope() as db:
        u = db.scalar(select(User).where(User.username == args.username.lower()))
        if u is None:
            raise SystemExit("No such user.")
        u.password_hash = passwords.hash_password(pw)
        n = sessions.revoke_all_for_user(db, u.id)
        audit.record(db, Actor.system("cli"), "auth.password_reset", "user", u.id, {"via": "cli", "sessions_revoked": n})
    print("Password updated; all sessions were signed out.")
    return 0


def cmd_api(_: argparse.Namespace) -> int:
    import uvicorn

    s = get_settings()
    uvicorn.run("app.main:asgi", factory=True, host=s.host, port=s.port, log_level="info")
    return 0


def cmd_worker(_: argparse.Namespace) -> int:
    from app.security import redaction
    from app.services import registry
    from app.worker.runner import Runner

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    redaction.register_secrets(get_settings().secret_values())
    redaction.install_log_redaction()
    registry.load()
    try:
        Runner().run_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    from app import envcheck
    from app.db import session_scope
    from app.models.auth import User
    from app.models.org import Agent
    from app.services import audit

    info = envcheck.inspect()
    print(json.dumps(info, indent=2))
    try:
        with session_scope(write=False) as db:
            print("\nDatabase:", json.dumps({
                "users": db.scalar(select(func.count()).select_from(User)),
                "agents": db.scalar(select(func.count()).select_from(Agent)),
                "audit_chain": audit.verify_chain(db),
            }))
    except Exception as e:  # noqa: BLE001
        print(f"\nDatabase: not ready ({type(e).__name__}). Run: python -m app.cli setup")
    problems = []
    if not info["tools"]["ffmpeg"]["ok"]:
        problems.append("FFmpeg not found (needed from Phase 2 for video rendering).")
    if info["disk_free_gib"] < 10:
        problems.append("Less than 10 GiB free disk space.")
    for p in problems:
        print("WARNING:", p)
    return 0


def cmd_backup(_: argparse.Namespace) -> int:
    from app.services import backup

    print(json.dumps(backup.create_backup("manual"), indent=2, default=str))
    return 0


def cmd_list_backups(_: argparse.Namespace) -> int:
    from app.services import backup

    for b in backup.list_backups():
        print(f"{b['name']}  {b['bytes']:>12,} bytes")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    from app.services import backup

    print("Make sure the API and worker are stopped before restoring.")
    print(json.dumps(backup.restore_backup(Path(args.path)), indent=2))
    return 0


def cmd_verify_audit(_: argparse.Namespace) -> int:
    from app.db import session_scope
    from app.services import audit

    with session_scope(write=False) as db:
        res = audit.verify_chain(db)
    print(json.dumps(res))
    return 0 if res["ok"] else 1


def cmd_generate_report(_: argparse.Namespace) -> int:
    from app.db import session_scope
    from app.services import registry, reports

    registry.load()
    with session_scope() as db:
        rep = reports.run_reporting_cycle(db)
        print(f"Executive report #{rep.id}: {rep.summary_text}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="app.cli", description="AI Media Company control commands")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn, help_ in (
        ("migrate", cmd_migrate, "apply database migrations"),
        ("seed", cmd_seed, "create departments, channels and agent roles (idempotent)"),
        ("setup", cmd_setup, "migrate + seed"),
        ("api", cmd_api, "run the API server"),
        ("worker", cmd_worker, "run the task queue worker"),
        ("doctor", cmd_doctor, "inspect this computer and the installation"),
        ("backup", cmd_backup, "create a verified database backup"),
        ("list-backups", cmd_list_backups, "list backups"),
        ("verify-audit", cmd_verify_audit, "verify the audit log hash chain"),
        ("generate-report", cmd_generate_report, "build and verify an executive report now"),
    ):
        sub.add_parser(name, help=help_).set_defaults(fn=fn)
    for name, fn, help_ in (("create-owner", cmd_create_owner, "create the owner account"),
                            ("reset-password", cmd_reset_password, "set a new password for a user")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--username", default="owner")
        sp.add_argument("--password-env", help="read the password from this environment variable (non-interactive)")
        sp.set_defaults(fn=fn)
    rp = sub.add_parser("restore", help="restore a backup (stop the app first)")
    rp.add_argument("path")
    rp.set_defaults(fn=cmd_restore)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
