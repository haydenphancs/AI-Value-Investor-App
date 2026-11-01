#!/usr/bin/env python
"""Publish one Wiser weekly REHEARSAL — the owner's decision of 2026-11-01 for the W44 Deep Dive.

⚠️ WRITES PRODUCTION (`learn_weekly_pieces`, `learn_narration_jobs`, the private `money-moves-media`
bucket) with the service-role key in backend/.env. Without `--yes` it only prints what it would do.

    cd backend && ./venv/bin/python scripts/learn_weekly_promote.py --period 2026-W44:deep_dive
    cd backend && ./venv/bin/python scripts/learn_weekly_promote.py --period 2026-W44:deep_dive \\
        [--publish-at 2026-11-02T18:00:00-05:00] [--pad 0.45] --yes

WHY IT EXISTS
-------------
A dry-run piece ends `rehearsed` and can never go live (`ledger.transition` refuses to leave it). The
owner approved W44 "Semiconductor Giants and AI Infrastructure" during the dry run and decided on
2026-11-01 to publish it ("W44 only") — and, the same day, that weekly pieces carry "no
"AI-assisted" label". W44 was written before that: its byline said "AI-assisted · Editor-reviewed"
and its last block, a closing callout that the narration SPEAKS, began "AI-assisted: this article was
researched and written with AI…". So promoting it also strips that label from the text AND the audio.

WHAT IT DOES
------------
1. Checks (pure, before any write): the piece is `rehearsed` with `dry_run` true; its READY narration
   job is the one it points at; its last block is exactly a legacy disclosure (nothing else says
   "AI-assisted"); the job's timing table ends on that block; the publish time is ≥ 10 min away.
2. Trims the audio at the end of the previous sentence + `--pad` (never into the disclosure): ffmpeg
   STREAM COPY (no re-encode), then ffprobe and the narration's bytes-per-second band.
3. Uploads the trimmed file content-addressed (`narration.weekly_audio_path`, upsert off) — the old
   object is kept.
4. Repoints the job (`ledger.repoint_ready_audio_sync`) BEFORE the piece moves: in the other order a
   promoted piece whose repoint failed would publish the new text over audio that speaks the label.
5. Promotes the piece (`ledger.promote_rehearsed_sync`): rehearsed → approved, `dry_run` false, the
   new draft (catalog byline, closing removed, new audio fields) and its sha, the new schedule.
6. Best effort: sends the owner a Telegram line with the 🗑 withdraw button (the dry-run Approve sent
   none) and records its message id so the button works.

COVER ART + TAG LABEL (owner 2026-11-01: "W44 now + every weekly piece", and the doubled
"FEATURED DEEP DIVE / FEATURE DEEP DIVE" label). W44 was built before the art step, so its draft has
no `imageUrl` (the app draws only the gradient) and its tagLabel came from the old prompt's example.
Before step 3 the script stores the cover art in the PUBLIC `money-moves-images` bucket
(`cover_art.store_plates` / `make_cover_art`, `articles/<slug>.{hero,card}.jpg`, never overwriting) and
the promoted draft carries `imageUrl` / `imageCardUrl`; a tagLabel that repeats the app's header
(`validators.GENERIC_TAG_RE`) becomes `--tag-label` (default CASE STUDY). Neither is narrated, so the
audio still matches the text. The art comes from, in order: the ready-made plates in `--art-dir`
(`<slug>.hero.jpg` + `.card.jpg`, checked against `<slug>.manifest.json` when present — no model call);
a pair already in the bucket (reused); `--draw-art` (ONE paid image call). With none of them `--yes`
refuses unless `--no-art` says to publish artless.

`--backfill-art` does the same for a piece that is ALREADY live (promoted before this step existed):
`approved` → the piece's draft gains the art and the tag (one fenced write; the publisher takes the new
draft); `published` → the live `money_move_articles` row (`image_url` + content `imageUrl` /
`imageCardUrl` / `tagLabel`, fenced on `updated_at`), then the piece's draft. The app shows a published
change after the Money Moves cache refreshes (its TTL, ≤ 1 h, or a restart).

The deployed publisher takes the piece live at `--publish-at` (default: now + 15 min). `--yes`
refuses until the deployed Terms page no longer promises an "AI-assisted" label (deploy the web
first; `--allow-before-deploy` overrides) and while the Telegram review bot is not configured here (`--no-withdraw-button` overrides:
the deployed publisher's Published reply still carries 🗑 Unpublish).
A lost withdraw button exits 2; `--resend-withdraw` sends it again. The old closing's second sentence
("It is educational only and not investment advice.") goes with it: splicing it out of the middle of
an AAC file is riskier than the line is worth, and the 23 catalog articles carry no closing either.
Each step is safe to stop after; a rerun detects a repointed job and continues from step 5. The new
closing line ("educational only…") is NOT added: it is not in the audio, and the transcript must match
the audio.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

MIN_LEAD = timedelta(minutes=10)
DEFAULT_LEAD = timedelta(minutes=15)
DEFAULT_PAD = 0.45
#: Never cut closer than this to the first spoken word of the disclosure.
DISCLOSURE_GUARD = 0.05
_FFMPEG_TIMEOUT = 120
#: The tag a header-repeating tagLabel ("FEATURE DEEP DIVE") becomes when `--tag-label` is not given.
DEFAULT_TAG_LABEL = "CASE STUDY"
#: Where ready-made weekly plates live (one-off plates made with the catalog art script).
DEFAULT_ART_DIR = _BACKEND / "data" / "learn_weekly_art"
_IMAGE_KEYS = ("imageUrl", "imageCardUrl")


def _parse_when(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise SystemExit("--publish-at needs a timezone offset (e.g. -05:00)")
    return parsed.astimezone(timezone.utc)


def _last_block(draft: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    sections = draft.get("sections") if isinstance(draft, Mapping) else None
    if not isinstance(sections, list) or not sections or not isinstance(sections[-1], Mapping):
        return None
    blocks = sections[-1].get("content")
    if not isinstance(blocks, list) or not blocks or not isinstance(blocks[-1], Mapping):
        return None
    return blocks[-1]


def plan_promote(piece: Mapping[str, Any], job: Optional[Mapping[str, Any]], *, now: datetime,
                 publish_at: datetime, hold_hours: int) -> Dict[str, Any]:
    """{"publish_at", "hold_until", "legacy_text"} — or SystemExit naming why it must not run. Pure."""
    from app.services.learn_weekly import contracts as C
    from app.services.learn_weekly import publisher

    key = piece.get("period_key")
    if piece.get("status") != C.REHEARSED:
        raise SystemExit(f"{key} is {piece.get('status')!r} — only a rehearsed piece can be promoted")
    if piece.get("dry_run") is not True:
        raise SystemExit(f"{key} has dry_run={piece.get('dry_run')!r} — only a dry-run rehearsal is promoted")
    jid = piece.get("narration_job_id")
    if not jid or job is None or str(job.get("id")) != str(jid):
        raise SystemExit(f"{key}: its narration job {jid!r} was not found")
    if job.get("status") != C.JOB_READY or str(job.get("piece_id")) != str(piece.get("id")):
        raise SystemExit(f"{key}: narration job {jid} is {job.get('status')!r} for piece {job.get('piece_id')} — "
                         "not this piece's ready audio")
    if not publisher.is_weekly_audio_path(job.get("audio_path")):
        raise SystemExit(f"{key}: narration job {jid} has audio_path {job.get('audio_path')!r}")
    draft = piece.get("draft")
    last = _last_block(draft) if isinstance(draft, Mapping) else None
    if last is None or last.get("type") != "callout" or last.get("text") not in C.LEGACY_DISCLOSURE_TEXTS:
        raise SystemExit(f"{key}: the draft does not end on a legacy AI disclosure — nothing to strip "
                         "(was it promoted already?)")
    rest = copy.deepcopy(dict(draft))
    rest["sections"][-1]["content"].pop()
    rest.pop("author", None)
    from app.services.learn_weekly import article_schema as A
    if "ai-assisted" in A.canonical_json(rest).casefold():
        raise SystemExit(f"{key}: the draft says AI-assisted outside the byline and the closing — refusing "
                         "to publish a half-stripped label")
    if publish_at.tzinfo is None:
        raise SystemExit("publish time must be timezone-aware")
    if publish_at < now + MIN_LEAD:
        raise SystemExit(f"--publish-at must be at least {int(MIN_LEAD.total_seconds() // 60)} min from now "
                         "(the publisher runs every few minutes)")
    return {"publish_at": publish_at, "hold_until": publish_at + timedelta(hours=hold_hours),
            "legacy_text": last.get("text")}


def plan_cut(timings: Any, draft: Mapping[str, Any], *, pad: float = DEFAULT_PAD) -> Dict[str, float]:
    """Where to cut the audio: {"cut", "prev_end", "disclosure_start"}. The timing table must end on
    the disclosure block and the entry before it on the block before it (the narration order).
    cut = min(prev_end + pad, disclosure_start − DISCLOSURE_GUARD); SystemExit when there is no gap."""
    from app.services.learn_weekly import narration

    blocks = narration.narration_blocks(draft)
    if len(blocks) < 2:
        raise SystemExit("the draft narrates fewer than two blocks")
    if not isinstance(timings, list) or len(timings) < 2 or not all(isinstance(t, Mapping) for t in timings):
        raise SystemExit("the job's timing table is unreadable")
    last, prev = timings[-1], timings[-2]
    if last.get("i") != blocks[-1].i or prev.get("i") != blocks[-2].i:
        raise SystemExit(f"the timing table ends on blocks {prev.get('i')}, {last.get('i')} — the draft's last "
                         f"two are {blocks[-2].i}, {blocks[-1].i}")
    if blocks[-1].dest[0] != "block":
        raise SystemExit("the draft's last narrated block is not the closing callout")

    def _times(entry: Mapping[str, Any], key: str) -> List[float]:
        out = [s.get(key) for s in entry.get("sentences") or [] if isinstance(s, Mapping)]
        out.append(entry.get(key))
        return [float(v) for v in out if isinstance(v, (int, float)) and not isinstance(v, bool)]

    ends, starts = _times(prev, "end"), _times(last, "start")
    if not ends or not starts:
        raise SystemExit("the timing table has no end/start for the last two blocks")
    prev_end, disclosure_start = max(ends), min(starts)
    if not pad >= 0:
        raise SystemExit("--pad must be ≥ 0")
    cut = min(prev_end + pad, disclosure_start - DISCLOSURE_GUARD)
    if cut <= prev_end:
        raise SystemExit(f"no gap to cut in: the previous block ends at {prev_end:.3f}s, the disclosure "
                         f"starts at {disclosure_start:.3f}s")
    return {"cut": round(cut, 3), "prev_end": prev_end, "disclosure_start": disclosure_start}


def fixed_tag_label(draft: Mapping[str, Any], override: Optional[str] = None) -> Optional[str]:
    """The tagLabel the live draft must carry instead of its own, or None to keep it. Pure.

    `override` (the owner's `--tag-label`) is upper-cased and must be 1-4 words / ≤ 32 characters (the
    writer's caps), never repeat the app's "Featured Deep Dive" header and never say AI-assisted —
    SystemExit otherwise. Without one, a tagLabel that is missing or repeats the header
    (`validators.GENERIC_TAG_RE`: W44's old-prompt "FEATURE DEEP DIVE") becomes DEFAULT_TAG_LABEL."""
    from app.services.learn_weekly import validators as V
    from app.services.learn_weekly import writer as W

    current = draft.get("tagLabel") if isinstance(draft, Mapping) else None
    if override is not None:
        tag = " ".join(str(override).split()).upper()
        if not (1 <= len(tag.split()) <= W.TAG_LABEL_MAX_WORDS) or len(tag) > W.TAG_LABEL_MAX_CHARS:
            raise SystemExit(f"--tag-label {override!r} must be 1-{W.TAG_LABEL_MAX_WORDS} words and at most "
                             f"{W.TAG_LABEL_MAX_CHARS} characters")
        if V.GENERIC_TAG_RE.search(tag):
            raise SystemExit(f"--tag-label {override!r} repeats the app's own \"Featured Deep Dive\" header")
        if "AI-ASSISTED" in tag.replace(" ", "-"):
            raise SystemExit(f"--tag-label {override!r} says AI-assisted")
        return None if tag == current else tag
    if isinstance(current, str) and current.strip() and not V.GENERIC_TAG_RE.search(current):
        return None
    return DEFAULT_TAG_LABEL


def with_cover(draft: Mapping[str, Any], *, art: Optional[Mapping[str, Any]] = None,
               tag_label: Optional[str] = None) -> Dict[str, Any]:
    """A copy of `draft` with the cover art's `imageUrl` / `imageCardUrl` and the new tagLabel (each
    only when given). Neither is narrated, so the narration — and the audio — is unchanged. SystemExit
    for art that is not exactly the two http(s) URLs. Pure."""
    new = copy.deepcopy(dict(draft))
    if art is not None:
        if not isinstance(art, Mapping) or set(art) != set(_IMAGE_KEYS) or not all(
                isinstance(art[k], str) and art[k].startswith(("https://", "http://")) and len(art[k]) <= 1000
                for k in _IMAGE_KEYS):
            raise SystemExit(f"the cover art is not exactly {list(_IMAGE_KEYS)} as http(s) URLs: {art!r}")
        new.update({k: art[k] for k in _IMAGE_KEYS})
    if tag_label is not None:
        new["tagLabel"] = tag_label
    return new


def promoted_draft(draft: Mapping[str, Any], job_script: Any, *, audio_url: str,
                   duration_seconds: float, art: Optional[Mapping[str, Any]] = None,
                   tag_label: Optional[str] = None) -> Dict[str, Any]:
    """The draft as it goes live: the catalog byline, the legacy closing removed, the trimmed audio's
    URL and length, and (when given) the cover art and the new tagLabel (`with_cover`). Its narration
    must be exactly the job's script minus the closing — proof the text matches the audio — and its
    tagLabel must not repeat the app's header. SystemExit otherwise. Pure."""
    from app.services.learn_weekly import article_schema as A
    from app.services.learn_weekly import contracts as C
    from app.services.learn_weekly import narration
    from app.services.learn_weekly import validators as V

    new = with_cover(draft, art=art, tag_label=tag_label)
    tag = new.get("tagLabel")
    if isinstance(tag, str) and V.GENERIC_TAG_RE.search(tag):
        raise SystemExit(f"the promoted tagLabel {tag!r} repeats the app's \"Featured Deep Dive\" header")
    last = _last_block(new)
    if last is None or last.get("text") not in C.LEGACY_DISCLOSURE_TEXTS:
        raise SystemExit("the draft does not end on a legacy AI disclosure")
    new["sections"][-1]["content"].pop()
    if not new["sections"][-1]["content"]:
        raise SystemExit("removing the closing would leave the last section empty")
    new["author"] = dict(C.AUTHOR)
    new["audioUrl"] = audio_url
    new["hasAudioVersion"] = True
    new["audioDurationSeconds"] = max(1, int(round(duration_seconds)))
    if not isinstance(job_script, list) or not job_script:
        raise SystemExit("the job's script is unreadable")
    if narration.script_json(narration.narration_blocks(new)) != job_script[:-1]:
        raise SystemExit("the promoted text is not the job's script minus its last block — the audio would "
                         "not match the text")
    if "ai-assisted" in A.canonical_json(new).casefold():
        raise SystemExit("the promoted draft still says AI-assisted")
    return new


def trim_argv(src: str, dst: str, cut: float) -> List[str]:
    """Stream copy (no re-encode) of the first `cut` seconds; bitexact so a rerun on this machine
    writes the same bytes (and so the same content-addressed path)."""
    return ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src, "-map", "0:a:0", "-c", "copy",
            "-t", f"{cut:.3f}", "-fflags", "+bitexact", "-movflags", "+faststart", dst]


def probe_argv(path: str) -> List[str]:
    return ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
            "default=noprint_wrappers=1:nokey=1", path]


# ── cover art (pure) ─────────────────────────────────────────────────────────────────────────


def local_plate_problems(local: Mapping[str, Any], slug: str) -> List[str]:
    """Why the ready-made plates read from `--art-dir` cannot be this piece's cover art, or []: the
    shipping sizes and format (`cover_art.plate_problems`) and, when a manifest sits beside them, its
    slug and each file's sha256 (a re-rolled or swapped file is not the plate the manifest records)."""
    from app.services.learn_weekly import cover_art

    out = list(cover_art.plate_problems(local.get("plates")))
    manifest = local.get("manifest")
    if manifest is None:
        return out
    if not isinstance(manifest, Mapping):
        return out + ["the manifest is not a JSON object"]
    if manifest.get("slug") != slug:
        out.append(f"the manifest is for {manifest.get('slug')!r}, not {slug!r}")
    masters = manifest.get("masters") if isinstance(manifest.get("masters"), Mapping) else {}
    for name, data in (local.get("plates") or {}).items():
        want = (masters.get(name) or {}).get("sha256") if isinstance(masters.get(name), Mapping) else None
        if want is not None and isinstance(data, (bytes, bytearray)) and hashlib.sha256(data).hexdigest() != want:
            out.append(f"{name}: the file is not the plate its manifest records (sha256 differs)")
    return out


def plan_art(*, local: Optional[Mapping[str, Any]], bucket: Mapping[str, Any], draw: bool,
             no_art: bool) -> Dict[str, str]:
    """{"source", "why"} — where this piece's cover art comes from. Pure. In order: `--no-art` (none);
    the ready-made plates (local); a whole pair already in the bucket (bucket — reused, no model call);
    `--draw-art` (draw — ONE paid image call). Nothing → "missing", which `--yes` refuses."""
    if draw and no_art:
        raise SystemExit("--draw-art and --no-art contradict each other")
    if no_art:
        return {"source": "none", "why": "--no-art: published without cover art (the app draws the gradient)"}
    if local is not None:
        return {"source": "local", "why": f"the ready-made plates in {local.get('dir')}"}
    if bucket and all(bucket.get(name) is not None for name in ("hero", "card")):
        return {"source": "bucket", "why": "the pair already in the image bucket (reused, no model call)"}
    if draw:
        return {"source": "draw", "why": "--draw-art: one paid image call"}
    return {"source": "missing", "why": "no ready-made plates, no pair in the bucket and no --draw-art"}


def would_be_art(slug: str, plan: Mapping[str, str]) -> Optional[Dict[str, str]]:
    """The image fields the draft will carry for this plan (the URLs are a function of the slug), or
    None for an artless plan. Pure."""
    from app.services.learn_weekly import cover_art

    if plan.get("source") in ("none", "missing"):
        return None
    paths = cover_art.art_paths(slug)
    return {"imageUrl": cover_art.public_url(paths["hero"]), "imageCardUrl": cover_art.public_url(paths["card"])}


def _missing_art_message(slug: str, art_dir: Path) -> str:
    return (f"no cover art for {slug}: put {slug}.hero.jpg + {slug}.card.jpg in {art_dir} (--art-dir), or "
            "pass --draw-art (one paid image call), or --no-art to publish without (owner 2026-11-01: "
            "\"W44 now + every weekly piece\")")


# ── I/O seams (tests replace these) ──────────────────────────────────────────────────────────


def _run(argv: Sequence[str]) -> str:
    done = subprocess.run(list(argv), capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT, check=False)
    if done.returncode != 0:
        raise SystemExit(f"{argv[0]} failed ({done.returncode}): {done.stderr.strip()[:500]}")
    return done.stdout


def _download(bucket: str, path: str) -> bytes:
    from app.database import get_supabase

    data = get_supabase().storage.from_(bucket).download(path)
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise SystemExit(f"downloading {bucket}/{path} returned nothing")
    return bytes(data)


def _upload(bucket: str, path: str, data: bytes, content_type: str) -> None:
    from app.database import get_supabase

    get_supabase().storage.from_(bucket).upload(path, data, {"content-type": content_type, "upsert": "false"})


def _stat(bucket: str, path: str) -> Optional[Dict[str, Any]]:
    from app.services.learn_weekly import narration

    return narration._object_stat_sync(bucket, path)


def _read_local_plates(art_dir: Path, slug: str) -> Optional[Dict[str, Any]]:
    """{"plates": {"hero", "card"}, "manifest", "dir"} from `art_dir`, or None when neither plate is
    there. SystemExit when only one is (half a picture) or the manifest is unreadable."""
    files = {name: Path(art_dir) / f"{slug}.{name}.jpg" for name in ("hero", "card")}
    present = [name for name, p in files.items() if p.is_file()]
    if not present:
        return None
    if len(present) != len(files):
        raise SystemExit(f"only the {present[0]} plate is in {art_dir} — both {slug}.hero.jpg and "
                         f"{slug}.card.jpg are needed (one picture)")
    manifest = None
    mpath = Path(art_dir) / f"{slug}.manifest.json"
    if mpath.is_file():
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise SystemExit(f"{mpath} is unreadable ({type(e).__name__}: {e})") from e
    return {"plates": {name: p.read_bytes() for name, p in files.items()}, "manifest": manifest,
            "dir": str(art_dir)}


def _art_bucket_pair(slug: str) -> Dict[str, Optional[Dict[str, Any]]]:
    """{"hero": stat or None, "card": stat or None} of the slug's plates in the PUBLIC image bucket
    (a read). A failed listing raises — never read as "absent"."""
    from app.services.learn_weekly import cover_art

    paths = cover_art.art_paths(slug)

    async def _go() -> Dict[str, Optional[Dict[str, Any]]]:
        sb = cover_art._sb()
        return {name: await cover_art._stat(sb, path) for name, path in paths.items()}

    try:
        return asyncio.run(_go())
    except cover_art.CoverArtError as e:
        raise SystemExit(f"could not read the image bucket for {slug}: {e}") from e


def _store_plates(slug: str, plates: Mapping[str, bytes], piece_id: Any) -> Dict[str, Any]:
    from app.services.learn_weekly import cover_art

    return asyncio.run(cover_art.store_plates(slug, plates, piece_id=piece_id))


def _draw_art(piece: Mapping[str, Any], draft: Mapping[str, Any]) -> Dict[str, Any]:
    from app.services.learn_weekly import cover_art

    validation = piece.get("validation") if isinstance(piece.get("validation"), Mapping) else {}
    return asyncio.run(cover_art.make_cover_art(
        draft, subjects=piece.get("subjects") or [], piece_id=piece.get("id"),
        previous=validation.get("cover_art") if isinstance(validation.get("cover_art"), Mapping) else None))


def apply_art(piece: Mapping[str, Any], draft: Mapping[str, Any], plan: Mapping[str, str],
              local: Optional[Mapping[str, Any]]) -> Tuple[Optional[Dict[str, str]], Dict[str, Any]]:
    """WRITES the public image bucket (never overwriting): (the image fields, the record for
    `validation.cover_art`). An artless plan writes nothing. SystemExit on any cover-art failure —
    before any ledger write, so the piece is untouched."""
    from app.services.learn_weekly import cover_art

    slug = piece.get("slug")
    source = plan.get("source")
    if source == "none":
        return None, {"status": cover_art.STATUS_SKIPPED, "error": "published with --no-art"}
    if source == "missing":
        raise SystemExit(_missing_art_message(str(slug), Path(plan.get("dir") or DEFAULT_ART_DIR)))
    try:
        if source == "local":
            art = _store_plates(slug, (local or {}).get("plates") or {}, piece.get("id"))
        elif source == "draw":
            art = _draw_art(piece, draft)
        else:                                   # "bucket": reuse the stored pair, no model call
            art = {**(would_be_art(slug, plan) or {}),
                   "coverArt": {"status": cover_art.STATUS_OK, "reused": True, "source": "image bucket"}}
    except cover_art.CoverArtError as e:
        raise SystemExit(f"cover art failed — nothing else written: {type(e).__name__}: {e}") from e
    fields = {k: art[k] for k in _IMAGE_KEYS}
    if fields != would_be_art(slug, plan):
        raise SystemExit(f"the stored cover art {fields} is not at the slug's own paths — refusing")
    record = dict(art.get("coverArt") or {})
    manifest = (local or {}).get("manifest") if source == "local" else None
    if isinstance(manifest, Mapping):
        for key, src in (("subject", "subject"), ("treatment", "treatment"), ("model", "art_model"),
                         ("prompt_sha1", "art_prompt_sha1")):
            if isinstance(manifest.get(src), str) and manifest[src].strip():
                record.setdefault(key, manifest[src])
    record["imageUrl"] = fields["imageUrl"]
    return fields, record


def _read_live_article_sync(slug: str) -> Optional[Dict[str, Any]]:
    from app.database import get_supabase
    from app.services.learn_weekly import publisher

    res = (get_supabase().table(publisher.ARTICLES).select("id,slug,source,image_url,content,updated_at")
           .eq("slug", slug).limit(1).execute())
    data = getattr(res, "data", None) or []
    if isinstance(data, dict):
        data = [data]
    return data[0] if data and isinstance(data[0], dict) else None


def _update_live_article_sync(slug: str, *, image_url: Optional[str], content: Mapping[str, Any],
                              observed_updated_at: str, now: datetime) -> Optional[Dict[str, Any]]:
    """One conditional UPDATE of the live weekly row: matched on its slug, `source = 'weekly'`, its own
    id and the `updated_at` read before (a concurrent change wins and this returns None)."""
    from app.database import get_supabase
    from app.services.learn_weekly import publisher

    res = (get_supabase().table(publisher.ARTICLES)
           .update({"image_url": image_url, "content": dict(content), "updated_at": publisher._iso(now)})
           .eq("slug", slug).eq("source", publisher.SOURCE_WEEKLY).eq("id", publisher.article_id(slug))
           .eq("updated_at", observed_updated_at).execute())
    data = getattr(res, "data", None) or []
    return data[0] if data and isinstance(data[0], dict) else None


def live_article_patch(live: Mapping[str, Any], wanted: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """{"image_url", "content"} to bring the live row in line with the draft `wanted` (its image fields
    and tagLabel only — the rest of the published content stays byte for byte), or None when it already
    matches. Pure."""
    content = copy.deepcopy(dict(live.get("content") or {}))
    for key in (*_IMAGE_KEYS, "tagLabel"):
        if key in wanted:
            content[key] = wanted[key]
    image_url = wanted.get("imageUrl", live.get("image_url"))
    if content == live.get("content") and image_url == live.get("image_url"):
        return None
    return {"image_url": image_url, "content": content}


def _taken(piece: Mapping[str, Any]) -> List[str]:
    """Why the piece's slug or title is already used — in `money_move_articles` (every row, paged)
    or by another piece still in flight. [] = free."""
    from app.services.learn_weekly import build
    from app.services.learn_weekly import contracts as C
    from app.services.learn_weekly import ledger

    slug = piece.get("slug")
    title = " ".join(str(piece.get("title") or "").split()).casefold()
    out: List[str] = []
    for row in build._article_keys_sync():
        if row.get("slug") == slug:
            out.append(f"money_move_articles already has slug {slug!r}")
        if " ".join(str(row.get("title") or "").split()).casefold() == title:
            out.append(f"money_move_articles already has the title {piece.get('title')!r}")
    live = sorted(set(C.PIECE_STATUSES) - set(C.TERMINAL_STATUSES))
    for other in ledger.list_pieces_sync(statuses=live, limit=200):
        if other.get("id") == piece.get("id"):
            continue
        if other.get("slug") == slug or " ".join(str(other.get("title") or "").split()).casefold() == title:
            out.append(f"piece {other.get('period_key')} ({other.get('status')}) uses the same slug or title")
    return out


def _bot_configured() -> bool:
    from app.services.marketing import review_service

    return review_service.is_configured() and review_service.review_chat_id() is not None


def _live_terms_text() -> str:
    """The DEPLOYED Terms page (the public site the app links to). Raises on a failed fetch."""
    import httpx
    from app.config import settings

    url = f"{str(settings.MARKETING_PUBLIC_BASE_URL).rstrip('/')}/terms"
    resp = httpx.get(url, timeout=15.0, follow_redirects=False)
    resp.raise_for_status()
    return resp.text


def deploy_problem() -> Optional[str]:
    """Why the web deploy that drops the "AI-assisted" promise is not live yet, or None. Promoting
    before it would publish an unlabelled piece while the live Terms still say such pieces are marked
    (review 2026-11-01). A fetch failure is a problem too — never assume the deploy landed."""
    try:
        text = _live_terms_text()
    except Exception as e:  # noqa: BLE001 — reported, never swallowed
        return f"could not read the live Terms page ({type(e).__name__}: {e})"
    if "AI-assisted" in text:
        return "the live Terms page still promises an \"AI-assisted\" label — deploy the web first"
    return None


async def _notify(row: Mapping[str, Any], publish_at: datetime) -> bool:
    """Send the owner the 🗑 withdraw button and record its message id (the button only works on a
    recorded message). True only when the id is in the piece's `review_message_ids` afterwards. Never
    raises: a failure is printed, and the caller says how to re-send."""
    from app.integrations import telegram
    from app.services.learn_weekly import ledger, review
    from app.services.marketing import review_service, telegram_pacing

    chat_id = review_service.review_chat_id()
    if not _bot_configured() or chat_id is None:
        print("  Telegram: the review bot is not configured here — no withdraw button")
        return False
    line = (f"✅ {row.get('title')} — promoted from the dry run (owner, 2026-11-01): goes live "
            f"{review._et(publish_at)} · 🗑 withdraws the approval until then, unpublishes after")
    try:
        await telegram_pacing.PACER.wait()
        env = await telegram.send_message(chat_id, line, reply_markup=review.approved_keyboard(str(row["id"])))
        mid = review.message_id_of(env)
        await review.remember_message_id(row, mid)
        current = await ledger.get_piece_by_id(row["id"])
        ids = (current or {}).get("review_message_ids") or []
        if mid is not None and mid in ids:
            print("  Telegram: withdraw button sent and recorded")
            return True
        print(f"  Telegram: the message went out (id {mid}) but it is not recorded on the piece — its button "
              "will answer \"out of date\"")
    except Exception as e:  # noqa: BLE001 — reported to the caller through the return value
        print(f"  Telegram: could not send the withdraw button ({type(e).__name__}: {e})")
    return False


def _promoted_already(piece: Mapping[str, Any]) -> bool:
    from app.services.learn_weekly import contracts as C

    v = piece.get("validation") if isinstance(piece.get("validation"), Mapping) else {}
    return (piece.get("status") in (C.APPROVED, C.PUBLISHING, C.PUBLISHED) and piece.get("dry_run") is False
            and isinstance(v.get("promote"), Mapping))


# ── the cover step (promote and backfill) ───────────────────────────────────────────────────


def plan_cover(piece: Mapping[str, Any], draft: Mapping[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    """{"tag", "local", "plan", "would", "art_dir"} — what the cover step will do. Reads only (the local
    plates, and the bucket when there are none); SystemExit for refused local plates or a bucket that
    cannot be read."""
    slug = str(piece.get("slug") or draft.get("slug") or "")
    art_dir = Path(args.art_dir)
    tag = fixed_tag_label(draft, args.tag_label)
    local = None if args.no_art else _read_local_plates(art_dir, slug)
    if local is not None:
        problems = local_plate_problems(local, slug)
        if problems:
            raise SystemExit(f"the ready-made plates in {art_dir} are refused: " + "; ".join(problems))
    bucket = {} if (args.no_art or local is not None) else _art_bucket_pair(slug)
    plan = plan_art(local=local, bucket=bucket, draw=args.draw_art, no_art=args.no_art)
    return {"tag": tag, "local": local, "plan": plan, "would": would_be_art(slug, plan), "art_dir": art_dir}


def _print_cover(cover: Mapping[str, Any], draft: Mapping[str, Any]) -> None:
    plan = cover["plan"]
    print(f"  cover art: {plan['source']} — {plan['why']}")
    if plan["source"] == "missing":
        print(f"  ⚠️  {_missing_art_message(str(draft.get('slug')), cover['art_dir'])} — --yes refuses")
    if cover["tag"] is not None:
        print(f"  tagLabel {draft.get('tagLabel')!r} → {cover['tag']!r} (the app already shows \"Featured Deep "
              "Dive\" above it)")
    else:
        print(f"  tagLabel kept: {draft.get('tagLabel')!r}")


def _cover_notes(cover: Mapping[str, Any], draft: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"cover_art": cover["plan"]["source"]}
    if cover["tag"] is not None:
        out["tag_label"] = {"from": draft.get("tagLabel"), "to": cover["tag"]}
    return out


def backfill_art(piece: Mapping[str, Any], args: argparse.Namespace) -> int:
    """`--backfill-art`: give a piece that is ALREADY live (promoted before the cover step existed) its
    cover art and a non-generic tagLabel (module docstring). Writes only with `--yes`."""
    from app.services.learn_weekly import contracts as C
    from app.services.learn_weekly import ledger, narration, publisher

    key, status = piece.get("period_key"), piece.get("status")
    if status == C.PUBLISHING:
        print(f"{key} is being published right now — rerun in a minute")
        return 1
    if status not in (C.APPROVED, C.PUBLISHED) or piece.get("dry_run") is not False:
        raise SystemExit(f"--backfill-art is for a LIVE piece that is approved or published; {key} is "
                         f"{status!r} (dry_run={piece.get('dry_run')!r}). A rehearsal gets its cover art "
                         "through the promote itself (run without --backfill-art)")
    draft = piece.get("draft")
    slug = piece.get("slug")
    if not isinstance(draft, Mapping) or not isinstance(slug, str) or draft.get("slug") != slug:
        raise SystemExit(f"{key}: the draft is unreadable or names another slug — nothing done")
    cover = plan_cover(piece, draft, args)
    wanted = with_cover(draft, art=cover["would"], tag_label=cover["tag"])
    if narration.script_json(narration.narration_blocks(wanted)) != \
            narration.script_json(narration.narration_blocks(draft)):
        raise SystemExit("the new draft would narrate differently — the audio would not match; nothing done")
    live = None
    if status == C.PUBLISHED:
        live = _read_live_article_sync(slug)
        if not live or live.get("source") != publisher.SOURCE_WEEKLY or \
                str(live.get("id")) != publisher.article_id(slug):
            raise SystemExit(f"{key} is published but money_move_articles holds no weekly row of its own for "
                             f"{slug!r} — nothing done")
        if not isinstance(live.get("content"), Mapping) or not live.get("updated_at"):
            # Never write a three-key `content` over an unreadable one, nor an unfenced update.
            raise SystemExit(f"{key}: the live row's content or updated_at is unreadable — nothing done")
    patch = live_article_patch(live, wanted) if live is not None else None
    print(f"{key}: {piece.get('title')!r} — {status} (live): cover art backfill")
    _print_cover(cover, draft)
    if wanted == draft and patch is None:
        print("  nothing to change — the draft (and the live row) already carry this art and tag")
        return 0
    if cover["plan"]["source"] == "missing":
        if not args.yes:
            print("dry run — nothing written (add --yes)")
            return 0
        raise SystemExit(_missing_art_message(slug, cover["art_dir"]))
    if status == C.PUBLISHED:
        print(f"  live row: {'image_url + content imageUrl/imageCardUrl/tagLabel' if patch else 'already matches'}")
    print(f"  piece draft: {'gains the art/tag' if wanted != draft else 'already matches'}")
    if not args.yes:
        print("dry run — nothing written (add --yes)")
        return 0

    fields, record = apply_art(piece, draft, cover["plan"], cover["local"])
    new_draft = with_cover(draft, art=fields, tag_label=cover["tag"])
    now = datetime.now(timezone.utc)
    if live is not None:
        patch = live_article_patch(live, new_draft)
        if patch is not None:
            updated = _update_live_article_sync(slug, image_url=patch["image_url"], content=patch["content"],
                                                observed_updated_at=live["updated_at"], now=now)
            if updated is None:
                print("the live row changed while this ran (lost compare-and-swap) — nothing else written; rerun")
                return 1
            print("  live row updated — the app shows it once the Money Moves cache refreshes (≤ 1 h, or a "
                  "restart)")
    if new_draft != draft:
        validation = dict(piece.get("validation") or {})
        if fields is not None or not isinstance(validation.get("cover_art"), Mapping):
            validation["cover_art"] = record        # a tag-only run never overwrites a real art record
        validation["backfill_art"] = {"at": now.isoformat(), "by": "owner (2026-11-01: \"W44 now\")",
                                      **_cover_notes(cover, draft)}
        row = ledger.transition_sync(
            piece["id"], from_statuses=[status], to_status=status,
            fields={"draft": new_draft, "draft_sha256": narration.draft_sha256(new_draft),
                    "validation": validation},
            fence={"draft_sha256": piece.get("draft_sha256"), "review_round": piece.get("review_round"),
                   "dry_run": False})
        if row is None:
            print("the piece moved on while this ran (lost compare-and-swap) — its draft is unchanged; rerun "
                  "(a piece published meanwhile takes the published path)")
            return 1
        print(f"  piece draft updated ({status})")
    print(f"BACKFILLED: {key} — cover art {cover['plan']['source']}"
          + (f", tagLabel {cover['tag']!r}" if cover["tag"] else ""))
    return 0


# ── main ─────────────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Publish one Wiser weekly rehearsal (WRITES PRODUCTION).")
    p.add_argument("--period", required=True, help="e.g. 2026-W44:deep_dive")
    p.add_argument("--publish-at", default=None, help="publish instant with offset (default: now + 15 min)")
    p.add_argument("--pad", type=float, default=DEFAULT_PAD, help="seconds kept after the last body word")
    p.add_argument("--yes", action="store_true", help="actually write (default: print only)")
    p.add_argument("--allow-before-deploy", action="store_true",
                   help="promote even though the live Terms page still promises an AI-assisted label")
    p.add_argument("--no-withdraw-button", action="store_true",
                   help="promote without the pre-publish 🗑 (no review bot here); the deployed publisher's "
                        "Published reply still carries 🗑 Unpublish")
    p.add_argument("--resend-withdraw", action="store_true",
                   help="only re-send the 🗑 withdraw button for a piece this script already promoted")
    p.add_argument("--art-dir", default=str(DEFAULT_ART_DIR),
                   help="where the ready-made <slug>.hero.jpg / .card.jpg (+ .manifest.json) plates are")
    p.add_argument("--draw-art", action="store_true",
                   help="with no ready-made plates and none in the bucket, draw the cover (ONE paid image call)")
    p.add_argument("--no-art", action="store_true", help="publish without cover art (the app draws the gradient)")
    p.add_argument("--tag-label", default=None,
                   help=f"the tagLabel to publish (default: keep it, or {DEFAULT_TAG_LABEL} when it repeats the "
                        "app's \"Featured Deep Dive\" header)")
    p.add_argument("--backfill-art", action="store_true",
                   help="give an ALREADY live (approved / published) piece its cover art and tagLabel")
    args = p.parse_args(argv)

    from app.config import settings
    from app.services.learn_weekly import contracts as C
    from app.services.learn_weekly import ledger, narration, publisher

    piece = ledger.get_piece_sync(args.period)
    if piece is None:
        print(f"no learn_weekly_pieces row for {args.period}")
        return 1
    if args.resend_withdraw:
        if not _promoted_already(piece):
            raise SystemExit(f"{args.period} is {piece.get('status')!r} and was not promoted by this script")
        when = publisher._ts(piece.get("scheduled_publish_at")) or datetime.now(timezone.utc)
        return 0 if asyncio.run(_notify(piece, when)) else 2
    if args.backfill_art:
        return backfill_art(piece, args)
    job = ledger.get_job_sync(piece["narration_job_id"]) if piece.get("narration_job_id") else None
    now = datetime.now(timezone.utc)
    publish_at = _parse_when(args.publish_at) if args.publish_at else now + DEFAULT_LEAD
    plan = plan_promote(piece, job, now=now, publish_at=publish_at, hold_hours=settings.LEARN_REVIEW_HOLD_HOURS)
    cut = plan_cut(job.get("timings"), piece["draft"], pad=args.pad)
    checks = job.get("checks") if isinstance(job.get("checks"), dict) else {}
    repointed = isinstance(checks.get("promote"), dict)
    source_path = checks["promote"].get("from_path") if repointed else job["audio_path"]
    if repointed:
        # Trust the marker only when the job really holds a cut file: another path, and a length that
        # ends before the old disclosure starts.
        dur = job.get("duration_seconds")
        if job["audio_path"] == source_path or not isinstance(dur, (int, float)) or isinstance(dur, bool) \
                or not (cut["prev_end"] + 0.1 <= float(dur) <= cut["disclosure_start"] - 0.02):
            raise SystemExit(f"the job carries checks.promote but its audio ({job['audio_path']}, {dur!r}s) is "
                             "not a trimmed file — refusing")

    problems = _taken(piece)
    if problems:
        raise SystemExit("refusing: " + "; ".join(problems))
    if _stat(narration.AUDIO_BUCKET, job["audio_path"]) is None:
        raise SystemExit(f"the job's audio {job['audio_path']} is not in the bucket")

    print(f"{args.period}: {piece.get('title')!r} — rehearsed (dry run) → approved (LIVE)")
    print(f"  publish at {plan['publish_at'].isoformat()}  hold until {plan['hold_until'].isoformat()}")
    print(f"  audio: cut at {cut['cut']:.3f}s (body ends {cut['prev_end']:.3f}s, old AI disclosure starts "
          f"{cut['disclosure_start']:.3f}s of {float(job.get('duration_seconds') or 0):.1f}s)")
    print(f"  byline → {C.AUTHOR['name']} · {C.AUTHOR['title']}; closing callout removed")
    if repointed:
        print(f"  the job already points at the trimmed file {job['audio_path']} (from {source_path}) — "
              "continuing from the promote step")
    cover = plan_cover(piece, piece["draft"], args)
    _print_cover(cover, piece["draft"])

    # The row the publisher will write, checked now (pure).
    preview_job = dict(job, duration_seconds=cut["cut"]) if not repointed else job
    preview = promoted_draft(piece["draft"], job.get("script"),
                             audio_url=publisher.audio_public_url(preview_job["audio_path"]),
                             duration_seconds=float(preview_job["duration_seconds"]),
                             art=cover["would"], tag_label=cover["tag"])
    _, problem = publisher.build_article_row(dict(piece, draft=preview), preview_job, now=plan["publish_at"])
    if problem:
        raise SystemExit(f"the publisher would refuse it: {problem}")
    if not args.yes:
        print("dry run — nothing written (add --yes)")
        return 0
    deploy = deploy_problem()
    if deploy and not args.allow_before_deploy:
        raise SystemExit(f"refusing --yes: {deploy} (or pass --allow-before-deploy)")
    if not _bot_configured() and not args.no_withdraw_button:
        raise SystemExit("refusing --yes: the Telegram review bot is not configured in this environment, so "
                         "the owner would get no 🗑 withdraw button before it goes live (the dry-run Approve "
                         "left none). Set the MARKETING_TELEGRAM_* values here, or pass --no-withdraw-button "
                         "(after publishing, the deployed publisher's reply carries 🗑 Unpublish)")
    if cover["plan"]["source"] == "missing":
        raise SystemExit(f"refusing --yes: {_missing_art_message(piece['slug'], cover['art_dir'])}")

    # The cover art first: idempotent (a stored pair is reused), and a failure stops before any ledger write.
    art_fields, art_record = apply_art(piece, piece["draft"], cover["plan"], cover["local"])
    if art_fields is not None:
        print(f"  cover art stored: {art_fields['imageUrl']}")

    if not repointed:
        with tempfile.TemporaryDirectory(prefix="learn-promote-") as tmp:
            src, dst = Path(tmp) / "in.m4a", Path(tmp) / "out.m4a"
            src.write_bytes(_download(narration.AUDIO_BUCKET, source_path))
            _run(trim_argv(str(src), str(dst), cut["cut"]))
            data = dst.read_bytes()
            duration = float(_run(probe_argv(str(dst))).strip())
        if not (cut["prev_end"] + 0.1 <= duration <= cut["disclosure_start"] - 0.02):
            raise SystemExit(f"the trimmed file is {duration:.3f}s — outside ({cut['prev_end'] + 0.1:.3f}, "
                             f"{cut['disclosure_start'] - 0.02:.3f}); nothing uploaded")
        band = narration.bytes_per_second_problem(job.get("engine"), len(data), duration)
        if band:
            raise SystemExit(f"the trimmed file fails the voice band: {band}; nothing uploaded")
        new_path = narration.weekly_audio_path(piece["slug"], hashlib.sha256(data).hexdigest())
        stat = _stat(narration.AUDIO_BUCKET, new_path)
        if stat is None:
            _upload(narration.AUDIO_BUCKET, new_path, data, narration.AUDIO_CONTENT_TYPE)
            stat = _stat(narration.AUDIO_BUCKET, new_path)
            if stat is None:
                raise SystemExit(f"uploaded {new_path} but Storage does not list it — stopping before the repoint")
        mismatch = narration.object_problems(stat, want_size=len(data), want_type=narration.AUDIO_CONTENT_TYPE,
                                             path=new_path)
        if mismatch:
            raise SystemExit(f"{new_path} in the bucket does not match the trimmed file: {mismatch}")
        print(f"  uploaded {new_path} ({len(data)} bytes, {duration:.3f}s)")
        job = ledger.repoint_ready_audio_sync(
            job["id"], piece_id=piece["id"], old_audio_path=source_path, new_audio_path=new_path,
            new_audio_bytes=len(data), new_duration_seconds=duration,
            note={"from_path": source_path, "from_bytes": job.get("audio_bytes"),
                  "from_duration_seconds": job.get("duration_seconds"), "cut_at": cut["cut"],
                  "at": now.isoformat(), "reason": "owner 2026-11-01: publish the rehearsal without the "
                                                   "AI-assisted closing"})
        if job is None:
            print("the narration job moved on while this ran — the piece is untouched (still rehearsed)")
            return 1
        print(f"  narration job {job['id']} now points at the trimmed file")

    new_draft = promoted_draft(piece["draft"], job.get("script"),
                               audio_url=publisher.audio_public_url(job["audio_path"]),
                               duration_seconds=float(job["duration_seconds"]),
                               art=art_fields, tag_label=cover["tag"])
    validation = dict(piece.get("validation") or {})
    validation["cover_art"] = art_record
    validation["promote"] = {"from_status": "rehearsed", "at": now.isoformat(), "by": "owner (2026-11-01)",
                             "legacy_closing_removed": True, "audio_path": job["audio_path"],
                             **_cover_notes(cover, piece["draft"])}
    row = ledger.promote_rehearsed_sync(
        piece["id"], review_round=piece.get("review_round"), old_draft_sha256=piece.get("draft_sha256"),
        narration_job_id=job["id"], observed_updated_at=piece.get("updated_at"), draft=new_draft,
        draft_sha256=narration.draft_sha256(new_draft), scheduled_publish_at=plan["publish_at"],
        hold_until=plan["hold_until"], validation=validation, now=datetime.now(timezone.utc))
    if row is None:
        print("the piece moved on while this ran (lost compare-and-swap) — not promoted. The job keeps the "
              "trimmed audio; a rerun continues from here.")
        return 1
    print(f"PROMOTED: {row.get('period_key')} is approved and goes live {plan['publish_at'].isoformat()} "
          "(the deployed publisher runs it). After it publishes: scripts/check_money_moves_published.py")
    if args.no_withdraw_button and not _bot_configured():
        print("⚠️  No pre-publish withdraw button (--no-withdraw-button). After it publishes, the deployed "
              "publisher's Published reply carries 🗑 Unpublish.")
        return 0
    if not asyncio.run(_notify(row, plan["publish_at"])):
        print(f"⚠️  NO WORKING WITHDRAW BUTTON. Re-send it: scripts/learn_weekly_promote.py --period "
              f"{args.period} --resend-withdraw")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
