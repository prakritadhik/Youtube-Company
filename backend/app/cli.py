"""Command-line entry point: ``python -m app.cli <command>``."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
from pathlib import Path

from pydantic import ValidationError
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
    from app.ops import schedule

    with session_scope() as db:
        print("Seeded:", seed_organization(db))
        made = schedule.seed_defaults(db)
        if made:
            print("Schedules created:", ", ".join(made))
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
    from app.services import credentials as credentials_svc
    from app.services import registry
    from app.worker.runner import Runner

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    redaction.register_secrets(get_settings().secret_values())
    credentials_svc.register_all()
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


def cmd_export_org(args: argparse.Namespace) -> int:
    from app.agents.docs import render_hierarchy_markdown

    out = Path(args.path)
    out.write_text(render_hierarchy_markdown(), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


def cmd_demo_production(args: argparse.Namespace) -> int:
    from app.production import demo
    from app.services import registry

    registry.load()
    print("Running the real production pipeline on a clearly-labelled DEMO video (never publishable)...")
    res = demo.run(args.width, args.height, args.preset)
    print(json.dumps(res, indent=2))
    print("\nOpen the dashboard → the Happy Birthday Songs channel → the DEMO video to watch it and read its QA and rights reports.")
    return 0 if res["qa"]["passed"] else 1


def cmd_ops_status(_: argparse.Namespace) -> int:
    from app.db import session_scope
    from app.ops import alerts, health, schedule
    from app.services import registry

    registry.load()
    with session_scope(write=False) as db:
        w = health.worker_status(db)
        print(f"Worker: {'running' if w['alive'] else 'NOT RUNNING'}" + "".join(f"  [{x['worker_id']} beat {x['seconds_since_beat']}s ago]" for x in w["workers"][:3]))
        for s in db.scalars(select(schedule.Schedule).order_by(schedule.Schedule.id)):
            v = schedule.serialize(db, s)
            print(f"  {v['slug']:<14} {'on ' if v['enabled'] else 'OFF'} next {v['next_run'] or '-'}{'  (DUE NOW)' if v['due_now'] else ''}")
        open_ = alerts.list_alerts(db)
        print(f"Alerts: {len(open_)} open")
        for a in open_[:20]:
            print(f"  [{a.severity}] {a.title}  (x{a.count}, {a.status.lower()})")
    return 0


def cmd_youtube_status(_: argparse.Namespace) -> int:
    from app.db import session_scope
    from app.services import registry
    from app.youtube import connection as conn_svc
    from app.youtube import quota

    registry.load()
    with session_scope(write=False) as db:
        st = conn_svc.status(db)
        print(f"Google OAuth client configured: {'yes' if st['oauth_client_configured'] else 'NO (see docs/YOUTUBE.md)'}")
        for ch in st["channels"]:
            c = ch["connection"]
            extra = f" -> {c.get('google_channel_title')} ({c.get('google_channel_id')}), made for kids: {c.get('made_for_kids')}" if c["status"] != "NOT_CONNECTED" else ""
            print(f"  {ch['channel']}: {c['status']}{extra}")
        q = quota.summary(db)
        print(f"Quota used today (this app's own count): {q['used_today']} of {q['daily_limit']}; resets {q['resets_at']}")
    return 0


def cmd_youtube_selftest(args: argparse.Namespace) -> int:
    from app.services import registry
    from app.youtube import selftest

    registry.load()
    if not args.yes_upload_a_private_test_video:
        print("This uploads a few seconds of generated test video to your YouTube channel as PRIVATE, then deletes it.\n"
              "Add --yes-upload-a-private-test-video to confirm. Your operating mode must not be DEVELOPMENT (Settings).")
        return 2
    try:
        report = selftest.run(args.channel, keep=args.keep)
    except (selftest.SelfTestError, Exception) as e:  # noqa: BLE001 - say why, plainly
        print(f"Self-test stopped: {e}")
        return 1
    print(json.dumps(report, indent=2))
    return 0 if report["result"] in ("VERIFIED", "PRIVACY_LOCKED") else 1


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
    ep = sub.add_parser("export-org", help="write the agent hierarchy and permission matrix as Markdown")
    ep.add_argument("path", nargs="?", default=str(BACKEND_DIR.parent / "docs" / "AGENT_HIERARCHY.md"))
    ep.set_defaults(fn=cmd_export_org)
    dp = sub.add_parser("demo-production", help="produce a labelled demonstration video through the real pipeline (needs FFmpeg)")
    dp.add_argument("--width", type=int, default=1280)
    dp.add_argument("--height", type=int, default=720)
    dp.add_argument("--preset", default="veryfast", help="x264 speed preset (ultrafast … slow)")
    dp.set_defaults(fn=cmd_demo_production)
    sub.add_parser("ops-status", help="worker, schedules and open alerts").set_defaults(fn=cmd_ops_status)
    ys = sub.add_parser("youtube-status", help="show YouTube connections and today's quota use")
    ys.set_defaults(fn=cmd_youtube_status)
    yt_ = sub.add_parser("youtube-selftest", help="upload a tiny PRIVATE test video to a connected channel, read it back, then delete it")
    yt_.add_argument("--channel", required=True, help="channel slug, e.g. happy-birthday-songs")
    yt_.add_argument("--keep", action="store_true", help="do not delete the test video afterwards")
    yt_.add_argument("--yes-upload-a-private-test-video", action="store_true")
    yt_.set_defaults(fn=cmd_youtube_selftest)
    rp = sub.add_parser("restore", help="restore a backup (stop the app first)")
    rp.add_argument("path")
    rp.set_defaults(fn=cmd_restore)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except ValidationError as e:  # a bad value in .env or the environment: explain it, no traceback
        print("There is a problem with your configuration (.env or environment variables):")
        for err in e.errors():
            print(f"  - {'.'.join(str(x) for x in err['loc'])}: {err['msg']}")
        print("Tip: put comments on their own line in .env, never after a value.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
