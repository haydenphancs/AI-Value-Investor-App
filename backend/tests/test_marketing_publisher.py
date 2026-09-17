"""
`app/services/marketing/publisher_service.publish_cycle` — the claim-before-send discipline and the two
orderings that matter: dry-run is decided BEFORE the claim (a rehearsal touches no row), and
a ledger failure AFTER the outlet accepted the post must never flip it to `failed` (a retry
would double-post). Phase 1 ships no adapters; the tests register a fake one.
"""

from __future__ import annotations

import pytest

from app.services.marketing import publisher_service as pub


class _Ledger:
    def __init__(self, posts):
        self.posts = {p["id"]: dict(p) for p in posts}
        self.marks = []
        self.fail_mark = False

    async def list_posts(self, status, *, limit=50):
        return [dict(p) for p in self.posts.values() if p["status"] == status][:limit]

    async def claim_post(self, post_id):
        p = self.posts[post_id]
        if p["status"] != "approved":
            return None
        p["status"] = "queued"
        return dict(p)

    async def mark_post(self, post_id, status, **fields):
        if self.fail_mark:
            raise RuntimeError("ledger 520")
        self.marks.append((post_id, status, fields))
        self.posts[post_id].update({"status": status, **fields})
        return dict(self.posts[post_id])


def _post(pid, platform="x", **meta):
    return {"id": pid, "platform": platform, "format": "text", "status": "approved",
            "idempotency_key": f"2026-09-17:{platform}:text", "attempts": 0, "metadata": meta}


@pytest.fixture
def ledger(monkeypatch):
    led = _Ledger([_post("p1"), _post("p2", meta_dry=True)])
    led.posts["p2"]["metadata"] = {"dry_run": True}
    monkeypatch.setattr(pub, "get_marketing_run_service", lambda: led)
    monkeypatch.setattr(pub.settings, "MARKETING_DRY_RUN", False)
    monkeypatch.setattr(pub, "PUBLISHERS", {})
    return led


@pytest.mark.asyncio
async def test_no_adapters_means_observe_only(ledger):
    counters = await pub.publish_cycle()
    assert counters == {"approved_waiting": 2, "published": 0, "failed": 0, "skipped": 0}
    assert all(p["status"] == "approved" for p in ledger.posts.values())


@pytest.mark.asyncio
async def test_dry_run_is_decided_before_the_claim_and_touches_no_row(ledger, monkeypatch):
    calls = []

    async def adapter(post):
        calls.append(post["id"])
        return {"external_id": "x1"}

    monkeypatch.setattr(pub, "PUBLISHERS", {"x": adapter})
    monkeypatch.setattr(pub.settings, "MARKETING_DRY_RUN", True)
    counters = await pub.publish_cycle()
    assert calls == [] and counters["skipped"] == 2 and ledger.marks == []
    assert all(p["status"] == "approved" for p in ledger.posts.values())


@pytest.mark.asyncio
async def test_a_dry_run_ROW_is_skipped_even_when_the_web_switch_is_live(ledger, monkeypatch):
    calls = []

    async def adapter(post):
        calls.append(post["id"])
        return {"external_id": "x1", "cost_micros": 15000}

    monkeypatch.setattr(pub, "PUBLISHERS", {"x": adapter})
    counters = await pub.publish_cycle()
    assert calls == ["p1"]                       # p2 carried metadata.dry_run
    assert counters["published"] == 1 and counters["skipped"] == 1
    assert ledger.posts["p1"]["status"] == "published" and ledger.posts["p1"]["cost_micros"] == 15000
    assert ledger.posts["p2"]["status"] == "approved"


@pytest.mark.asyncio
async def test_adapter_failure_marks_failed_with_the_error(ledger, monkeypatch):
    async def adapter(post):
        raise RuntimeError("upload-post 503")

    monkeypatch.setattr(pub, "PUBLISHERS", {"x": adapter})
    counters = await pub.publish_cycle()
    assert counters["failed"] == 1
    p1 = ledger.posts["p1"]
    assert p1["status"] == "failed" and "upload-post 503" in p1["last_error"] and p1["attempts"] == 1


@pytest.mark.asyncio
async def test_ledger_failure_after_a_successful_publish_never_marks_failed(ledger, monkeypatch, caplog):
    """The outlet has the post. Flipping the row to `failed` would make a retry double-post."""
    import logging

    async def adapter(post):
        return {"external_id": "tw-123", "external_url": "https://x.com/i/status/123"}

    monkeypatch.setattr(pub, "PUBLISHERS", {"x": adapter})
    ledger.fail_mark = True
    with caplog.at_level(logging.ERROR):
        counters = await pub.publish_cycle()
    assert counters["published"] == 1 and counters["failed"] == 0
    assert ledger.posts["p1"]["status"] == "queued"          # left for reconcile, not failed
    assert any("PUBLISHED BUT LEDGER WRITE FAILED" in r.getMessage() and "tw-123" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_a_post_claimed_by_another_tick_is_skipped(ledger, monkeypatch):
    async def adapter(post):
        return {"external_id": "x"}

    monkeypatch.setattr(pub, "PUBLISHERS", {"x": adapter})
    ledger.posts["p1"]["status"] = "queued"   # someone else holds it… but list_posts filters approved
    ledger.posts["p2"]["metadata"] = {}
    counters = await pub.publish_cycle()
    assert counters["published"] == 1 and ledger.posts["p2"]["status"] == "published"
