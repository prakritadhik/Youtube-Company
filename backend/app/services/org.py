"""Organisation queries and agent actors."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.org import Agent, Channel, Department
from app.security.permissions import Actor


class UnknownAgent(LookupError):
    pass


def get_agent(db: Session, slug: str) -> Agent:
    a = db.scalar(select(Agent).where(Agent.slug == slug))
    if a is None:
        raise UnknownAgent(slug)
    return a


def actor_for(agent: Agent) -> Actor:
    return Actor.for_agent(agent.id, agent.slug, agent.level, agent.extra_permissions, agent.allowed_tools)


def actor_for_slug(db: Session, slug: str) -> Actor:
    return actor_for(get_agent(db, slug))


def get_channel(db: Session, slug: str) -> Channel:
    c = db.scalar(select(Channel).where(Channel.slug == slug))
    if c is None:
        raise LookupError(f"unknown channel '{slug}'")
    return c


def chain_of_command(db: Session, agent: Agent) -> list[Agent]:
    """Managers above ``agent``, nearest first (manager, COO, CEO)."""
    out: list[Agent] = []
    cur = agent
    while cur.reports_to_id:
        cur = db.get(Agent, cur.reports_to_id)  # type: ignore[assignment]
        out.append(cur)
    return out


def is_superior_of(db: Session, actor: Actor, agent: Agent) -> bool:
    """True when ``actor`` is an agent above ``agent`` in its reporting line."""
    if actor.agent_id is None:
        return False
    return any(m.id == actor.agent_id for m in chain_of_command(db, agent))


def organization_tree(db: Session) -> dict[str, Any]:
    depts = {d.id: d for d in db.scalars(select(Department).order_by(Department.sort_order))}
    agents = list(db.scalars(select(Agent).order_by(Agent.sort_order)))

    def node(a: Agent) -> dict[str, Any]:
        return {
            "slug": a.slug,
            "name": a.name,
            "level": a.level,
            "department": depts[a.department_id].slug if a.department_id else None,
            "responsibilities": a.responsibilities,
            "uses_ai_model": a.uses_ai_model,
            "required_inputs": a.required_inputs,
            "output_format": a.output_format,
            "acceptance_criteria": a.acceptance_criteria,
            "allowed_tools": a.allowed_tools,
            "reports_to": next((x.slug for x in agents if x.id == a.reports_to_id), None),
        }

    nodes = [node(a) for a in agents]
    return {
        "owner": {"name": "Owner / Human Director"},
        "departments": [
            {"slug": d.slug, "name": d.name, "description": d.description} for d in depts.values()
        ],
        "agents": nodes,
    }
