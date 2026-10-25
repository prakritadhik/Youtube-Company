from __future__ import annotations

from app.storage import resolve_data_path, write_bytes_artifact, write_json_artifact  # noqa: F401


def make_file(name: str, content: bytes = b"real artifact content") -> str:
    return write_bytes_artifact("tests", name, content)


def file_evidence(name: str = "out.txt", content: bytes = b"real artifact content") -> dict:
    return {"kind": "file", "ref": make_file(name, content), "note": "deliverable"}


def verified_task(db, agent, worker: str, manager: str, title: str = "work", channel: str | None = None,
                  evidence_name: str | None = None):
    """Run a task through the real chain: manager assigns → worker submits evidence → manager verifies."""
    from app.services import tasks

    t = tasks.create(db, agent(manager), title=title, assigned_agent_slug=worker, channel_slug=channel)
    tasks.begin(db, agent(worker), t.id)
    tasks.submit(db, agent(worker), t.id, result={"done": True},
                 evidence_specs=[file_evidence(evidence_name or f"{title.replace(' ', '_')}-{t.id}.txt")])
    tasks.review(db, agent(manager), t.id, approve=True, notes="verified")
    return t
