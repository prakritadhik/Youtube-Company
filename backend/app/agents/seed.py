"""Idempotent seeding of departments, channels and agent roles from the catalog."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.catalog import CHANNELS, DEPARTMENTS, ROLES
from app.models.org import Agent, Channel, Department
from app.security.permissions import Actor
from app.services import audit


class OrgIntegrityError(Exception):
    pass


def seed_organization(db: Session) -> dict[str, int]:
    created = {"departments": 0, "channels": 0, "agents": 0}

    for i, (slug, name, desc) in enumerate(DEPARTMENTS):
        row = db.scalar(select(Department).where(Department.slug == slug))
        if row is None:
            db.add(Department(slug=slug, name=name, description=desc, sort_order=i))
            created["departments"] += 1
        else:
            row.name, row.description, row.sort_order = name, desc, i
    db.flush()

    for spec in CHANNELS:
        row = db.scalar(select(Channel).where(Channel.slug == spec["slug"]))
        if row is None:
            db.add(Channel(**spec))
            created["channels"] += 1
        else:  # keep owner-edited fields (brand, youtube id); refresh hard policy and kind
            row.kind = spec["kind"]
            row.policy = spec["policy"]
    db.flush()

    depts = {d.slug: d for d in db.scalars(select(Department))}
    by_slug: dict[str, Agent] = {a.slug: a for a in db.scalars(select(Agent))}

    # Create all rows first, then link managers (roles may reference each other in any order).
    for i, spec in enumerate(ROLES):
        row = by_slug.get(spec.slug)
        if row is None:
            row = Agent(slug=spec.slug, name=spec.name, level=spec.level, responsibilities=spec.responsibilities)
            db.add(row)
            by_slug[spec.slug] = row
            created["agents"] += 1
        row.name = spec.name
        row.level = spec.level
        row.responsibilities = spec.responsibilities
        row.required_inputs = list(spec.required_inputs)
        row.output_format = list(spec.output_format)
        row.acceptance_criteria = list(spec.acceptance)
        row.allowed_tools = list(spec.tools)
        row.extra_permissions = list(spec.extra_permissions)
        row.execution_limits = dict(spec.limits)
        row.uses_ai_model = spec.ai
        row.department_id = depts[spec.department].id if spec.department else None
        row.sort_order = i
    db.flush()
    for spec in ROLES:
        by_slug[spec.slug].reports_to_id = by_slug[spec.reports_to].id if spec.reports_to else None
    db.flush()

    validate_hierarchy(db)
    if any(created.values()):
        audit.record(db, Actor.system("seed"), "org.seeded", "organization", None, created)
    return created


def validate_hierarchy(db: Session) -> None:
    """The structure must be a real tree: workers → manager (same dept) → COO → CEO."""
    agents = {a.id: a for a in db.scalars(select(Agent))}
    problems: list[str] = []
    ceos = [a for a in agents.values() if a.level == "ceo"]
    if len(ceos) != 1:
        problems.append(f"expected exactly one CEO, found {len(ceos)}")
    for a in agents.values():
        mgr = agents.get(a.reports_to_id) if a.reports_to_id else None
        if a.level == "ceo":
            if mgr is not None:
                problems.append("CEO must report only to the owner")
        elif mgr is None:
            problems.append(f"{a.slug} has no manager")
        elif a.level == "coo" and mgr.level != "ceo":
            problems.append("COO must report to the CEO")
        elif a.level == "manager" and mgr.level != "coo":
            problems.append(f"{a.slug} must report to the COO")
        elif a.level == "worker":
            if mgr.level != "manager":
                problems.append(f"{a.slug} must report to a department manager")
            elif mgr.department_id != a.department_id:
                problems.append(f"{a.slug} reports to a manager of a different department")
        # cycle check
        seen, cur = set(), a
        while cur.reports_to_id:
            if cur.id in seen:
                problems.append(f"reporting cycle at {a.slug}")
                break
            seen.add(cur.id)
            cur = agents[cur.reports_to_id]
    if problems:
        raise OrgIntegrityError("; ".join(problems))
