"""The research list carries `processing_started_at`, and iOS decodes it.

WHY THIS EXISTS. `ResearchViewModel.applyClientSideTimeoutPass` used to age EVERY
`processing` row from `created_at` at 600 s — the same number as the server's
`RESEARCH_PIPELINE_TIMEOUT_SECONDS`, but the server counts from work START (after the
agent semaphore), so a report queued for two minutes was shown as failed while the
server was still generating it, and Retry charged a second 20 credits. The fix keys the
client on the server's own stamp, which needs the list endpoint to SELECT it, the schema
to CARRY it, and the Swift DTO to DECODE it. Three places; a source scan over each.
"""

import re
from pathlib import Path

from app.schemas.research import ResearchReportListItem

_REPO = Path(__file__).resolve().parents[2]
_ENDPOINT = _REPO / "backend" / "app" / "api" / "v1" / "endpoints" / "research.py"
_DTO = _REPO / "frontend" / "ios" / "ios" / "Core" / "Services" / "TaskPollingManager.swift"
_VM = _REPO / "frontend" / "ios" / "ios" / "ViewModels" / "ResearchViewModel.swift"
_MODEL = _REPO / "frontend" / "ios" / "ios" / "Models" / "ResearchModels.swift"


def _strip_swift_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", l) for l in src.splitlines())


def test_the_schema_carries_processing_started_at():
    assert "processing_started_at" in ResearchReportListItem.model_fields
    item = ResearchReportListItem(id="r", ticker="AAPL", investor_persona="warren_buffett",
                                  status="processing", created_at="2026-09-12T00:00:00Z")
    assert item.processing_started_at is None, "optional — old rows and queued reports have none"


def test_the_list_endpoint_selects_it():
    src = _ENDPOINT.read_text(encoding="utf-8")
    # The list handler's select string names created_at and processing_started_at together.
    m = re.search(r'current_step, created_at, processing_started_at, completed_at', src)
    assert m, "GET /research/reports no longer selects processing_started_at"


def test_the_ios_list_dto_decodes_it():
    src = _strip_swift_comments(_DTO.read_text(encoding="utf-8"))
    block = re.search(r"struct BackendReportListItem: Sendable \{(.*?)\n\}", src, re.S)
    assert block, "BackendReportListItem not found"
    assert "let processingStartedAt: String?" in block.group(1)
    assert 'case processingStartedAt = "processing_started_at"' in block.group(1)
    # The DTO has a HAND-WRITTEN decoder that assigns every stored property; a field added
    # to the struct but not to it is a compile error — pin the assignment so this test goes
    # red before Xcode does.
    init = re.search(r"extension BackendReportListItem: Decodable \{(.*?)\n\}", src, re.S)
    assert init and "decodeIfPresent(String.self, forKey: .processingStartedAt)" in init.group(1)


def test_the_timeout_pass_uses_two_clocks():
    src = _strip_swift_comments(_VM.read_text(encoding="utf-8"))
    body = re.search(r"private func applyClientSideTimeoutPass\(\) \{(.*?)\n    \}", src, re.S)
    assert body, "applyClientSideTimeoutPass not found"
    b = body.group(1)
    assert "report.processingStartedAt" in b and "startedTimeoutSeconds" in b
    assert "report.date" in b and "queuedTimeoutSeconds" in b
    assert "processingTimeoutSeconds" not in src, "the single 600 s created_at clock is back"
    started = re.search(r"startedTimeoutSeconds: TimeInterval = (\d+)", src)
    queued = re.search(r"queuedTimeoutSeconds: TimeInterval = (\d+)", src)
    assert started and queued
    from app.config import settings
    assert int(started.group(1)) > settings.RESEARCH_PIPELINE_TIMEOUT_SECONDS, (
        "the started clock must sit PAST the server's kill, or the client flips first")
    assert int(queued.group(1)) > int(started.group(1))


def test_the_model_threads_the_stamp_through():
    src = _strip_swift_comments(_MODEL.read_text(encoding="utf-8"))
    assert "var processingStartedAt: Date? = nil" in src
    assert "parseISO(item.processingStartedAt)" in src
    # withClientTimeout must preserve it, or a flipped card loses its clock on the next pass.
    body = re.search(r"func withClientTimeout\(\) -> AnalysisReport \{(.*?)\n    \}", src, re.S)
    assert body and "processingStartedAt: processingStartedAt" in body.group(1)
