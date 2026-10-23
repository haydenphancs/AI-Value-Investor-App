"""Wire shapes of the narrator worker's internal API (`/api/v1/internal/learn/*`, plan "Wiser weekly
content engine" §1.8).

ONE client reads and writes these: the standalone `learn_narrator` Railway service (no `app.*`
imports, no Supabase key). iOS never sees them — the article itself reaches iOS as the
`money_move_articles.content` passthrough (`schemas/money_moves.py`), after the owner approves it.

Deliberately loose where the server checks precisely: a timing value is a plain `float` here (NaN
and ±inf included — Starlette's JSON parser accepts them), so `narration.validate_timings` can refuse
it with LEARN_NARRATOR_REQUEST_INVALID naming the block and sentence, instead of a generic schema
422. Unknown keys are ignored (a newer worker may send more), never an error. Request size is
bounded here (list lengths, string lengths) so a runaway body is refused before any work.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

#: A Deep Dive is ~60-120 blocks (1,400-1,900 words, ≤ 75 words a block); 2,000 is a runaway.
MAX_TIMING_BLOCKS = 2000
MAX_SENTENCES_PER_BLOCK = 200


class _Wire(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ── claim ─────────────────────────────────────────────────────────────────────────────────────


class JobClaimRequest(_Wire):
    #: The engines this worker image can render. The server offers only jobs whose FROZEN engine is
    #: listed (an unknown name is dropped with a WARNING, never a 422 — a newer worker must not lose
    #: every claim to an older web).
    engines: List[str] = Field(..., min_length=1, max_length=8)
    worker_version: str = Field(..., min_length=1, max_length=80)


class NarrationBlockWire(_Wire):
    """One block the worker renders — never its server-side `dest`."""
    i: int
    text: str
    role: str


class NarrationJobWire(_Wire):
    id: str
    round: int
    engine: str
    #: The job's frozen voice parameters (Chatterbox: exaggeration / cfg_weight / target_wpm / ref;
    #: Kokoro: voice). The worker renders with THESE, never its environment's.
    voice: Dict[str, Any]
    pipeline_version: str
    #: The claim: present `X-Learn-Claim: <attempts>.<claim_nonce>` on every later call.
    attempts: int
    claim_nonce: str
    lease_until: Optional[str] = None
    blocks: List[NarrationBlockWire]


class JobClaimResponse(_Wire):
    job: NarrationJobWire


# ── heartbeat ─────────────────────────────────────────────────────────────────────────────────


class HeartbeatResponse(_Wire):
    ok: bool = True
    lease_until: Optional[str] = None


# ── upload ────────────────────────────────────────────────────────────────────────────────────


class UploadRequest(_Wire):
    content_type: str = Field(..., max_length=100)      # must be "audio/mp4" (checked by the service)
    bytes: int
    sha256: str = Field(..., max_length=64)


class UploadResponse(_Wire):
    #: A signed upload URL (PUT, a write credential — the worker must never log it), or None when the
    #: content-addressed object is already in the bucket with the same size (nothing to upload).
    upload_url: Optional[str] = None
    path: str
    content_type: str = "audio/mp4"
    already_uploaded: bool = False


# ── complete ──────────────────────────────────────────────────────────────────────────────────


class TimingSentence(_Wire):
    text: str = Field(..., max_length=4000)
    start: float
    end: float


class TimingBlock(_Wire):
    i: int
    start: float
    end: float
    sentences: List[TimingSentence] = Field(..., max_length=MAX_SENTENCES_PER_BLOCK)


class CompleteRequest(_Wire):
    path: str = Field(..., max_length=512)
    bytes: int
    duration_seconds: float
    engine: str = Field(..., max_length=32)
    pipeline_version: str = Field(..., max_length=80)
    #: One entry per narration block, in block order; sentence texts = the server's split.
    blocks: List[TimingBlock] = Field(..., max_length=MAX_TIMING_BLOCKS)
    #: The worker's own gate results (per-block words/s, alignment coverage, voice bands…), stored on
    #: the job as `checks.worker` for the review header. ≤ 64,000 characters of JSON.
    checks: Dict[str, Any] = Field(default_factory=dict)


class CompleteResponse(_Wire):
    ok: bool = True
    #: "ready", or "cancelled" when the job was cancelled before the completion landed.
    status: str
    #: True when this completion had already landed under the same claim (a retried call).
    replay: bool = False
    #: True when the piece moved to ready_for_review with this audio merged.
    piece_updated: bool = False


# ── fail ──────────────────────────────────────────────────────────────────────────────────────


class FailRequest(_Wire):
    code: str = Field(..., min_length=1, max_length=64)
    detail: str = Field("", max_length=2000)


class FailResponse(_Wire):
    ok: bool = True
    #: "queued" (re-queued, under the worker-failure cap), "failed" (at the cap) or "cancelled".
    status: str
    worker_failures: Optional[int] = None
