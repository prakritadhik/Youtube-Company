"""Research and packaging as queue tasks: assigned to the owning role, verified by its manager, evidence on file."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.db import session_scope
from app.enums import TaskStatus as S
from app.models.research import MetadataSet, ResearchNote
from app.models.tasks import Task
from app.models.videos import Video
from app.production import pipeline
from app.research import seo, workflow
from app.research.fetch import SafeFetcher
from app.security.permissions import PermissionDenied
from app.services import org, tasks
from app.storage import resolve_data_path
from app.worker.runner import HANDLERS, Runner
from tests.prod_helpers import BDAY, MED, add_brief, add_lyrics, add_music, make_video

PAGE = "<html><head><title>Aspirin</title></head><body><p>Low-dose aspirin is used in some patients.</p></body></html>"


def fake_web(monkeypatch, handler):
    monkeypatch.setattr(workflow, "fetcher_factory", lambda: SafeFetcher(transport=httpx.MockTransport(handler), resolver=lambda h, p: ["93.184.216.34"]))


def page_handler(status=200, ctype="text/html", body=PAGE, robots=404):
    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(robots)
        return httpx.Response(status, text=body, headers={"content-type": ctype})
    return h


def fetch_task(db, agent, url="https://www.cdc.gov/aspirin", video=None, role="research-specialist", **refs):
    t = tasks.create(db, agent("production-manager"), title="Fetch a source", task_type="research.fetch_source", assigned_agent_slug=role,
                     video_id=video.id if video else None, channel_slug=MED,
                     input_refs={"url": url, "kind": "source", "supports": "Aspirin facts", **refs})
    db.commit()
    return t.id


def run_one():
    return Runner("t", HANDLERS).run_once()


def status_of(task_id):
    with session_scope(write=False) as s:
        t = s.get(Task, task_id)
        return t.status, t.error, t.blocked_reason


def test_a_fetch_task_stores_the_page_as_untrusted_research_and_the_manager_verifies_it(db, agent, monkeypatch):
    v = make_video(db, agent, channel=MED, title="How aspirin works")
    fake_web(monkeypatch, page_handler())
    tid = fetch_task(db, agent, video=v)
    assert run_one() == tid and status_of(tid)[0] == S.SUBMITTED.value
    with session_scope() as s:
        n = s.scalar(select(ResearchNote).where(ResearchNote.video_id == v.id))
        assert n.method == "fetched" and n.source_class == "authoritative" and "Low-dose aspirin" in n.excerpt and n.untrusted
        assert resolve_data_path(n.snapshot_ref).exists()
        tasks.review(s, org.actor_for_slug(s, "production-manager"), tid, approve=True, notes="snapshot hash re-checked")
    assert status_of(tid)[0] == S.COMPLETED.value


def test_a_role_without_the_web_tool_cannot_fetch(db, agent, monkeypatch):
    fake_web(monkeypatch, page_handler())
    tid = fetch_task(db, agent, role="script-lyrics-writer")  # no web.research tool
    run_one()
    assert status_of(tid)[0] != S.SUBMITTED.value
    with session_scope(write=False) as s:
        assert s.scalars(select(ResearchNote)).all() == []


def test_fetch_failures_are_classified_for_the_queue(db, agent, monkeypatch):
    fake_web(monkeypatch, page_handler(robots=200))  # robots.txt exists but is empty: allowed
    ok = fetch_task(db, agent, url="https://www.cdc.gov/a")
    assert run_one() == ok and status_of(ok)[0] == S.SUBMITTED.value

    fake_web(monkeypatch, page_handler(status=404))
    gone = fetch_task(db, agent, url="https://www.cdc.gov/missing")
    run_one()
    st, err, _ = status_of(gone)
    assert st == S.FAILED.value and "http_error" in err["message"]  # a 404 will not get better by retrying

    fake_web(monkeypatch, page_handler(status=503))
    flaky = fetch_task(db, agent, url="https://www.cdc.gov/flaky")
    run_one()
    assert status_of(flaky)[0] == S.RETRY_SCHEDULED.value  # a 503 might; the queue retries with backoff

    fake_web(monkeypatch, lambda req: httpx.Response(200, text="User-agent: *\nDisallow: /\n") if req.url.path == "/robots.txt" else httpx.Response(200, text=PAGE))
    blocked = fetch_task(db, agent, url="https://www.cdc.gov/secret")
    run_one()
    st, err, _ = status_of(blocked)
    assert st == S.FAILED.value and "robots_disallowed" in err["message"]


def test_a_fetch_task_with_nowhere_to_file_the_note_fails_instead_of_retrying(db, agent, monkeypatch):
    fake_web(monkeypatch, page_handler())
    t = tasks.create(db, agent("production-manager"), title="Fetch with no scope", task_type="research.fetch_source", assigned_agent_slug="research-specialist",
                     input_refs={"url": "https://www.cdc.gov/aspirin", "kind": "source", "supports": "x"})
    db.commit()
    run_one()
    st, err, _ = status_of(t.id)
    assert st == S.FAILED.value and "belongs to a channel" in err["message"]


def test_internal_addresses_are_refused_even_when_a_task_asks(db, agent, monkeypatch):
    fake_web(monkeypatch, page_handler())
    tid = fetch_task(db, agent, url="http://169.254.169.254/latest/meta-data/")
    run_one()
    st, err, _ = status_of(tid)
    assert st == S.FAILED.value and "blocked_host" in err["message"]


def test_a_paused_company_fetches_nothing(db, agent, owner, monkeypatch):
    from app.services import control
    fake_web(monkeypatch, page_handler())
    tid = fetch_task(db, agent)
    control.pause(db, owner, "test")
    db.commit()
    run_one()
    assert status_of(tid)[0] != S.SUBMITTED.value
    with session_scope(write=False) as s:
        assert s.scalars(select(ResearchNote)).all() == []


# --- packaging ------------------------------------------------------------------------------------------------------


def prepared_video(db, agent):
    v = make_video(db, agent)
    add_brief(db, agent, v, main_concept="A morning garden celebration with bells", style_tags=["garden", "sunrise"], tone_and_style="warm, playful")
    add_lyrics(db, agent, v)
    add_music(db, agent, v)
    db.commit()
    return v


def test_the_metadata_task_is_owned_by_the_seo_specialist_and_created_by_its_own_department(db, agent, owner):
    v = prepared_video(db, agent)
    with pytest.raises(PermissionDenied):  # the production manager cannot assign work to another department's specialist
        workflow.start_packaging(db, agent("production-manager"), v.id)
    t = workflow.start_packaging(db, agent("research-manager"), v.id)
    assert t.assigned_agent.slug == "keyword-seo-specialist" and t.task_type == "seo.prepare_metadata"
    assert workflow.start_packaging(db, owner, v.id).id == t.id  # idempotent


def test_the_metadata_task_waits_for_the_script_and_the_soundtrack(db, agent):
    pm = agent("production-manager")
    v = make_video(db, agent)
    chain = pipeline.start_production(db, pm, v.id, title="Garden Morning Song", description="An original instrumental demo.")
    t = workflow.start_packaging(db, agent("research-manager"), v.id)
    db.commit()
    assert {chain["script"].id, chain["music"].id} <= set(_dep_ids(db, t.id))
    assert t.status != S.QUEUED.value  # not runnable until both are done


def _dep_ids(db, task_id):
    from app.models.tasks import TaskDependency
    return [d.depends_on_id for d in db.scalars(select(TaskDependency).where(TaskDependency.task_id == task_id))]


def test_preparing_metadata_creates_a_checked_set_but_changes_nothing_until_it_is_approved(db, agent):
    v = prepared_video(db, agent)
    t = workflow.start_packaging(db, agent("research-manager"), v.id)
    db.commit()
    assert run_one() == t.id and status_of(t.id)[0] == S.SUBMITTED.value
    with session_scope() as s:
        ms = s.scalar(select(MetadataSet).where(MetadataSet.video_id == v.id))
        assert ms.status == "CHECKED" and ms.task_id == t.id and ms.created_by == "agent:keyword-seo-specialist"
        assert s.get(Video, v.id).metadata_json.get("metadata_set_id") is None  # nothing applied yet
        tasks.review(s, org.actor_for_slug(s, "research-manager"), t.id, approve=True, notes="file hash and record re-checked")
        seo.approve(s, org.actor_for_slug(s, "research-manager"), ms.id, note="accurate to the brief and lyrics")
        assert s.get(Video, v.id).metadata_json["metadata_set_id"] == ms.id
    assert status_of(t.id)[0] == S.COMPLETED.value


def test_a_video_without_a_title_makes_the_task_fail_clearly(db, agent):
    v = prepared_video(db, agent)
    v.working_title = ""
    t = workflow.start_packaging(db, agent("research-manager"), v.id)
    db.commit()
    run_one()
    st, err, _ = status_of(t.id)
    assert st in (S.FAILED.value, S.RETRY_SCHEDULED.value, S.BLOCKED.value) and st != S.SUBMITTED.value


def test_source_based_channels_must_record_sources_before_the_brief(db, agent):
    pm = agent("production-manager")
    v = make_video(db, agent, channel=MED, title="How aspirin works")
    med = pipeline.start_production(db, pm, v.id, title="How aspirin works", description="x")
    assert "research" in med and med["research"].assigned_agent.slug == "research-specialist" and med["research"].task_type == "manual.research"
    assert med["research"].id in _dep_ids(db, med["brief"].id)
    bday = pipeline.start_production(db, pm, make_video(db, agent, channel=BDAY, title="Another").id, title="Another", description="x")
    assert "research" not in bday


# --- reporting ------------------------------------------------------------------------------------------------------


def test_the_research_manager_report_carries_verifiable_counts_and_flags_what_is_unavailable(db, agent):
    from datetime import timedelta

    from app.research import ideas
    from app.services import reports
    from app.timeutil import utcnow

    cs, mgr = agent("content-strategy-specialist"), agent("research-manager")
    a = ideas.propose(db, cs, BDAY, title="Lantern Parade Birthday", audience="Children", concept="A gentle original piece about a lantern parade through a quiet village.")
    ideas.decide(db, mgr, a.id, approve=True, note="distinct and feasible")
    b = ideas.propose(db, cs, BDAY, title="Another rejected idea here", audience="Children", concept="A concept the research manager does not want to pursue right now.")
    ideas.decide(db, mgr, b.id, approve=False, note="off-brand")
    ideas.propose(db, cs, BDAY, title="Needs singing to work", audience="Children", concept="A duet about sharing a cake which depends on real sung vocals.", requires=["sung_vocals"])
    end = utcnow() + timedelta(minutes=1)
    start = end - timedelta(days=1)
    rep = reports.build_department_report(db, mgr, "research-growth", start, end)
    claims = {c["text"]: c for c in rep.content["claims"]}
    proposed = next(c for t, c in claims.items() if "idea(s) were proposed" in t)
    assert proposed["value"] == 3 and proposed["check"]["entity"] == "content_ideas" and proposed["cls"] == "verified_fact"
    assert next(c for t, c in claims.items() if "were approved" in t)["value"] == 1
    assert next(c for t, c in claims.items() if "were rejected" in t)["value"] == 1
    unavailable = [c for c in rep.content["claims"] if c["cls"] == "unavailable"]
    # two distinct statements: YouTube performance data, and keyword/trend data that no source provides
    assert [c["check"] for c in unavailable] == [{"type": "source_not_connected", "source": "youtube"}, {"type": "source_not_connected", "source": "keyword_data"}]


def test_the_ceo_rechecks_research_claims_and_rejects_an_inflated_one(db, agent):
    from datetime import timedelta

    from app.enums import ClaimClass
    from app.research import ideas
    from app.services import reports
    from app.services.reports import Claim
    from app.timeutil import utcnow

    cs = agent("content-strategy-specialist")
    ideas.propose(db, cs, BDAY, title="Lantern Parade Birthday", audience="Children", concept="A gentle original piece about a lantern parade through a quiet village.")
    end = utcnow() + timedelta(minutes=1)
    start = end - timedelta(days=1)
    inflated = Claim("Research proposed 12 ideas this period.", ClaimClass.VERIFIED_FACT, 12,
                     {"type": "count", "entity": "content_ideas", "where": {}, "expected": 12,
                      "between": {"column": "created_at", "start": start.isoformat(), "end": end.isoformat()}}, "research manager", "Research Manager")
    reports.build_department_report(db, agent("research-manager"), "research-growth", start, end, [inflated])
    ex = reports.verify_and_publish(db, agent("ceo"), reports.consolidate(db, agent("coo"), start, end).id)
    rejected = ex.content["sections"]["rejected_claims"]
    assert any(r["reported_value"] == 12 and r["database_value"] == 1 for r in rejected)
    assert any("1 content idea(s) were proposed" in f["text"] for f in ex.content["sections"]["verified_facts"])
