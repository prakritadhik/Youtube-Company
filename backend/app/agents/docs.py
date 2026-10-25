"""Render the organisation and permission model from the code that enforces it (so docs cannot drift)."""

from __future__ import annotations

from app.agents.catalog import CHANNELS, DEPARTMENTS, ROLES
from app.security.permissions import (
    AGENT_GRANTABLE,
    LEVEL_PERMISSIONS,
    OWNER_ONLY,
    ROLE_PERMISSIONS,
    SYSTEM_PERMISSIONS,
)


def _cell(items) -> str:
    return ", ".join(f"`{i}`" for i in sorted(items)) or "—"


def render_hierarchy_markdown() -> str:
    names = {r.slug: r.name for r in ROLES}
    out = ["# Agent hierarchy and permissions", "",
           "_Generated from `backend/app/agents/catalog.py` and `backend/app/security/permissions.py` by "
           "`python -m app.cli export-org`. Do not edit by hand._", "",
           "```", "OWNER / HUMAN DIRECTOR", "└── AI CEO"]
    out.append("    └── GENERAL MANAGER / COO")
    for slug, name, _ in DEPARTMENTS:
        mgr = next(r for r in ROLES if r.level == "manager" and r.department == slug)
        out.append(f"        ├── {name.upper()}  (manager: {mgr.name})")
        workers = [r for r in ROLES if r.level == "worker" and r.department == slug]
        for i, w in enumerate(workers):
            out.append(f"        │   {'└──' if i == len(workers) - 1 else '├──'} {w.name}")
    out += ["```", "", f"{len(ROLES)} roles: 1 CEO, 1 COO, "
            f"{sum(r.level == 'manager' for r in ROLES)} department managers, "
            f"{sum(r.level == 'worker' for r in ROLES)} specialists.", "",
            "## Permission model", "",
            "Permissions are code constants. An agent row in the database can only add the permissions in the "
            "grantable allowlist; owner-only permissions can never be granted to an agent.", "",
            "| Actor | Permissions |", "|---|---|"]
    for role, perms in ROLE_PERMISSIONS.items():
        out.append(f"| User: **{role}** | {_cell(perms)} |")
    for level, perms in LEVEL_PERMISSIONS.items():
        out.append(f"| Agent level: **{level}** | {_cell(perms)} |")
    out.append(f"| System (queue runner / scheduler) | {_cell(SYSTEM_PERMISSIONS)} |")
    out += ["", f"Owner-only (never grantable to any agent): {_cell(OWNER_ONLY)}", "",
            f"Extra permissions an agent row may carry: {_cell(AGENT_GRANTABLE)}", "", "## Roles", ""]
    for r in ROLES:
        out += [f"### {r.name}", "", f"*Level:* {r.level} · *Reports to:* {names.get(r.reports_to or '', 'Owner')} · "
                f"*Uses an AI model:* {'yes' if r.ai else 'no'}", "", r.responsibilities, "",
                f"- **Inputs:** {'; '.join(r.required_inputs) or '—'}",
                f"- **Output:** {'; '.join(r.output_format) or '—'}",
                f"- **Acceptance criteria:** {'; '.join(r.acceptance) or '—'}",
                f"- **Allowed tools:** {_cell(r.tools)}",
                f"- **Extra permissions:** {_cell(r.extra_permissions)}", ""]
    out += ["## Channels and hard policies", ""]
    for c in CHANNELS:
        out += [f"### {c['name']}", "", c["objective"], "", f"Policy: `{c['policy']}`", ""]
    return "\n".join(out)
