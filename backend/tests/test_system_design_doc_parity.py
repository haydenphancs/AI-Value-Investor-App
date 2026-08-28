"""Pins `documents/System Design/SYSTEM_DESIGN_GUIDELINES.md` to the code it describes.

Why this exists
---------------
Version 1.x of that document carried ~770 lines of illustrative Swift/Python that were never
reconciled with the codebase, and readers took them as descriptions. It asserted `APIService`,
`CacheManager`, `PersistenceManager`, `ResearchRepository`, `RetryPolicy`, Core Data, a
`{success, data, meta}` envelope and a `deep_research_reports` table — none of which have ever
existed. Nothing caught it for eight months, because appending a correction blockquote was always
cheaper than fixing the body.

So this module asserts the two classes of claim that actually rotted:

  A. NEGATIVE claims — "there is no Core Data / Redis / BackgroundTasks / ORM". A hit is always a
     real finding, so this family has no false positives by construction. It is also the family
     that inverted: the document said Core Data was a *pending task* while the iOS guide said it
     would never ship.
  B. POINTERS — every `path/to/file.ext` and `file.ext::symbol` the document names must resolve.
     Symbols, not line numbers: a line number decays silently inside an 800-line file.

Deliberately NOT asserted: a snake_case → table-name scanner. `user_message`, `error_code`,
`page`, `status`, `total`, `used`, `data`, `meta` and ~180 others in that document are backticked
snake_case that are not tables. That scanner is a false-positive machine; tables are covered by the
curated list below plus one inverse assertion that keeps the list honest.

Per .claude/rules/testing.md: comment-stripped, brace-bounded where it matters, anti-vacuity
sentinels, and mutation-tested by hand once (see MUTATION_LOG at the bottom).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_DOC = _REPO / "documents" / "System Design" / "SYSTEM_DESIGN_GUIDELINES.md"
_BACKEND = _REPO / "backend"
_IOS = _REPO / "frontend" / "ios" / "ios"
_SNAPSHOT = _BACKEND / "database" / "schema_snapshot.sql"


# ─────────────────────────────────────────────────────────────────────────────
# Source helpers
# ─────────────────────────────────────────────────────────────────────────────

def _doc_text() -> str:
    assert _DOC.exists(), f"missing {_DOC}"
    return _DOC.read_text(encoding="utf-8")


def _doc_prose() -> str:
    """The document with fenced blocks removed.

    Only inline `backticked` spans are ever parsed. Fenced blocks are diagrams and would
    contribute box-drawing noise, and the whole point of version 2.0 is that there is no longer
    any fenced *code* to check.
    """
    return re.sub(r"```.*?```", "", _doc_text(), flags=re.S)


def _strip_comments(src: str, swift: bool) -> str:
    """Remove comments so prose about a fix is never mistaken for the fix.

    This is testing.md rule 1, and it is not hypothetical here: the codebase is full of comments
    that quote the exact wrong shape they replaced (`// NOT BackgroundTasks`, `// never the
    Keychain`). Scanning un-stripped source would let a revert stay green on its own changelog.
    """
    if swift:
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        return "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"'''(?:.|\n)*?'''", "", src)
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))


def _sources(root: Path, suffix: str) -> list[tuple[Path, str]]:
    out = []
    for p in sorted(root.rglob(f"*{suffix}")):
        if any(part in {".venv", "venv", "venv_clone", "build", ".build", "DerivedData"}
               for part in p.parts):
            continue
        try:
            out.append((p, _strip_comments(p.read_text(encoding="utf-8", errors="ignore"),
                                           swift=(suffix == ".swift"))))
        except OSError:
            continue
    return out


_BACKEND_PY = None
_IOS_SWIFT = None


def _backend_py() -> list[tuple[Path, str]]:
    global _BACKEND_PY
    if _BACKEND_PY is None:
        _BACKEND_PY = _sources(_BACKEND / "app", ".py")
    return _BACKEND_PY


def _ios_swift() -> list[tuple[Path, str]]:
    global _IOS_SWIFT
    if _IOS_SWIFT is None:
        _IOS_SWIFT = _sources(_IOS, ".swift")
    return _IOS_SWIFT


# ─────────────────────────────────────────────────────────────────────────────
# A. Negative claims — the document asserts these do NOT exist
# ─────────────────────────────────────────────────────────────────────────────
# token -> (tree, section that would become false, human explanation)
_ABSENT_IOS = {
    "NSCache":          ("§7.1/§7.2", "the client cache is one plain [String: CacheEntry] dict"),
    "import CoreData":  ("§7.1/§9.2/§10", "there is no local database, and none is planned"),
    "import SwiftData": ("§7.1/§9.2/§10", "there is no local database, and none is planned"),
    "@Bindable":        ("§4.2", "ViewModels are ObservableObject + @Published, never @Bindable"),
    "class APIService": ("§3.2/App A", "the network entry point is `actor APIClient`"),
    "class CacheManager": ("§3.2", "no such type — the cache lives inside StockRepository"),
    "class PersistenceManager": ("§9.2", "persistence is Keychain + UserDefaults only"),
    "struct RetryPolicy": ("§6.4", "retry is a fixed 1s delay, not a policy object"),
}
_ABSENT_BACKEND = {
    "BackgroundTasks":  ("§5.3", "work is dispatched via asyncio.create_task through _spawn"),
    "from celery":      ("§5.3/App B", "there is no task queue"),
    "import celery":    ("§5.3/App B", "there is no task queue"),
    "import redis":     ("§7.1/§10", "Tier 1 dict + Tier 2 Supabase; no Redis"),
    "from redis":       ("§7.1/§10", "Tier 1 dict + Tier 2 Supabase; no Redis"),
    "sqlalchemy":       ("App A / CLAUDE.md inv. #5", "no ORM, ever"),
    "Accept-Version":   ("§8.2", "URL-path versioning only"),
    "X-API-Version":    ("§8.2", "URL-path versioning only"),
    "deep_research_reports": ("§5.3", "the table is `research_reports`"),
}
# X-RateLimit-* is READ from FMP upstream; §8.3 says we never EMIT it. Only that one file may
# mention it, and it must not appear on a response we build.
_FMP_ONLY = {"X-RateLimit-Limit", "X-RateLimit-Remaining"}


@pytest.mark.parametrize("token", sorted(_ABSENT_IOS))
def test_ios_negative_claims_still_hold(token: str) -> None:
    section, why = _ABSENT_IOS[token]
    hits = [str(p.relative_to(_REPO)) for p, src in _ios_swift() if token in src]
    assert not hits, (
        f"{section} of SYSTEM_DESIGN_GUIDELINES.md says {why}, so `{token}` should not appear "
        f"in the iOS tree. Found it in: {hits[:5]}. Either revert the code or update that section "
        f"— the document is currently telling readers something false."
    )


@pytest.mark.parametrize("token", sorted(_ABSENT_BACKEND))
def test_backend_negative_claims_still_hold(token: str) -> None:
    section, why = _ABSENT_BACKEND[token]
    needle = token.lower()
    hits = [str(p.relative_to(_REPO)) for p, src in _backend_py() if needle in src.lower()]
    assert not hits, (
        f"{section} of SYSTEM_DESIGN_GUIDELINES.md says {why}, so `{token}` should not appear "
        f"in backend/app/. Found it in: {hits[:5]}."
    )


def test_no_core_data_model_files() -> None:
    found = [str(p) for p in (_REPO / "frontend").rglob("*.xcdatamodeld")]
    assert not found, f"§7.1 and §9.2 say there is no Core Data; found model files: {found}"


def test_rate_limit_headers_are_read_from_fmp_not_emitted_by_us() -> None:
    """§8.3: X-RateLimit-* are FMP's, never ours."""
    for token in sorted(_FMP_ONLY):
        hits = {p.name for p, src in _backend_py() if token in src}
        assert hits <= {"fmp.py"}, (
            f"§8.3 says `{token}` is only ever READ from FMP's response, never emitted on ours. "
            f"It now appears in {sorted(hits)}. If we started emitting it, §8.3 needs rewriting."
        )


# ─────────────────────────────────────────────────────────────────────────────
# B. Pointer resolution — every path the document names must resolve
# ─────────────────────────────────────────────────────────────────────────────
# Paths the document names precisely BECAUSE they do not exist ("there is no app/tasks/").
# Asserting their absence turns a would-be false positive into a real assertion.
_ASSERTED_ABSENT_PATHS = {
    "app/agents/", "app/tasks/", "app/core/middleware.py",
    "App/", "Features/", "SharedUI/", "Models/{Domain,DTO}/",
    "tests/{unit,integration,e2e}",
}
# Spans that look like paths but are not repo files.
_NOT_A_PATH = re.compile(
    r"""^(
        (GET|POST|PUT|PATCH|DELETE)\s      # HTTP routes
      | /api/                              # route prefixes
      | /(auth|billing|users|research|stocks|home|admin|overview|list-error-codes)
      | \.claude/rules/\*                  # globs
      | .*[*{}]                            # any glob / brace expansion
    )""",
    re.X,
)
_PATHLIKE = re.compile(r"^[\w./{}\-*]+$")


def _doc_path_spans() -> set[str]:
    spans = set(re.findall(r"`([^`\n]+)`", _doc_prose()))
    out = set()
    for s in spans:
        if "::" in s:
            s = s.split("::", 1)[0]
        if not _PATHLIKE.match(s):
            continue
        if _NOT_A_PATH.match(s):
            continue
        if not ("/" in s or re.search(r"\.(py|swift|sql|sh|md|json|html|svg)$", s)):
            continue
        out.add(s)
    return out


_RESOLVE_ROOTS = [
    _REPO,
    _BACKEND,
    _BACKEND / "app",
    _BACKEND / "app" / "services",
    _BACKEND / "app" / "api" / "v1" / "endpoints",
    _BACKEND / "app" / "services" / "agents",
    _BACKEND / "tests",
    _REPO / "frontend" / "ios",
    _IOS,
    _IOS / "Core",
    _REPO / "documents" / "System Design",
]


def _exists_case_sensitively(p: Path) -> bool:
    """`p.exists()` is NOT enough on macOS.

    APFS is case-insensitive by default, so `backend/App` "exists" because `backend/app` does —
    which silently resolved the document's `App/` (a directory it states does NOT exist) and made
    test_paths_the_doc_says_are_absent_really_are fail for a filesystem reason rather than a real
    one. Walk the parts and require an exact name match at every level.
    """
    if not p.exists():
        return False
    cur = p
    parts = []
    while cur != cur.parent and cur not in _RESOLVE_ROOTS and cur != _REPO:
        parts.append(cur.name)
        cur = cur.parent
    for name in reversed(parts):
        try:
            if name not in {c.name for c in cur.iterdir()}:
                return False
        except OSError:
            return False
        cur = cur / name
    return True


def _resolve(span: str) -> Path | None:
    """Resolve a doc path span against the roots the document writes relative to."""
    for r in _RESOLVE_ROOTS:
        c = r / span
        if _exists_case_sensitively(c):
            return c
    return None


def test_every_path_the_doc_names_resolves() -> None:
    unresolved = sorted(
        s for s in _doc_path_spans()
        if s not in _ASSERTED_ABSENT_PATHS and _resolve(s) is None
    )
    assert not unresolved, (
        "SYSTEM_DESIGN_GUIDELINES.md names these paths, but they do not exist:\n  "
        + "\n  ".join(unresolved)
        + "\n\nEither the file moved (update the document) or the document is describing something "
          "that was never built. If the document names it precisely BECAUSE it does not exist, add "
          "it to _ASSERTED_ABSENT_PATHS."
    )


def test_paths_the_doc_says_are_absent_really_are() -> None:
    present = sorted(p for p in _ASSERTED_ABSENT_PATHS
                     if "*" not in p and "{" not in p and _resolve(p) is not None
                     and not (p == "app/models/"))
    assert not present, (
        f"The document states these do NOT exist, but they now do: {present}. "
        "Update the document before adding them back."
    )


# ─────────────────────────────────────────────────────────────────────────────
# file::symbol pointers
# ─────────────────────────────────────────────────────────────────────────────
# NOTE the trailing \b on every alternative. Without it these are VACUOUS: `actor APIClientZZ`
# contains the substring `actor APIClient`, so renaming the type left the guard green. That was
# caught by mutation M1 below and is exactly the failure .claude/rules/testing.md warns about.
_PY_DEF = r"\bdef {sym}\b|\bclass {sym}\b|^{sym}\s*[:=]"
_SWIFT_DEF = r"\b(?:class|struct|enum|actor|protocol|func|var|let)\s+{sym}\b"


def _defines(src: str, sym: str, swift: bool) -> bool:
    pat = (_SWIFT_DEF if swift else _PY_DEF).format(sym=re.escape(sym))
    return re.search(pat, src, flags=re.M) is not None


def test_file_symbol_pointers_resolve() -> None:
    """`path/to/file.ext::symbol` must name a symbol defined in that file."""
    bad = []
    for span in sorted(set(re.findall(r"`([^`\n]+)`", _doc_prose()))):
        if "::" not in span:
            continue
        left, sym = span.split("::", 1)
        if not re.search(r"\.(py|swift)$", left):
            continue          # `Class::method` shorthand is checked by the curated list instead
        target = _resolve(left)
        if target is None:
            bad.append(f"{span}  (file not found)")
            continue
        swift = target.suffix == ".swift"
        src = _strip_comments(target.read_text(encoding="utf-8", errors="ignore"), swift=swift)
        if not _defines(src, sym, swift):
            bad.append(f"{span}  (file exists, symbol not defined in it)")
    assert not bad, "Broken file::symbol pointers in SYSTEM_DESIGN_GUIDELINES.md:\n  " + "\n  ".join(bad)


# ─────────────────────────────────────────────────────────────────────────────
# C. Curated presence — the named replacements for the fictional types
# ─────────────────────────────────────────────────────────────────────────────
# (symbol, repo-relative file). Curated, never extracted: extraction is what produces false
# positives. Each entry is a name the document offers as "the REAL one".
_CURATED_SWIFT = [
    ("APIClient",                 "frontend/ios/ios/Core/Services/APIClient.swift"),
    ("TaskPollingManager",        "frontend/ios/ios/Core/Services/TaskPollingManager.swift"),
    ("LivePriceWebSocketManager", "frontend/ios/ios/Core/Services/LivePriceWebSocketManager.swift"),
    ("NetworkMonitor",            "frontend/ios/ios/Core/Services/NetworkMonitor.swift"),
    ("KeychainService",           "frontend/ios/ios/Core/Services/AuthService.swift"),
    ("StockRepository",           "frontend/ios/ios/Core/Repositories/StockRepository.swift"),
    ("HomeRepository",            "frontend/ios/ios/Core/Repositories/HomeRepository.swift"),
    ("AppState",                  "frontend/ios/ios/Core/State/AppState.swift"),
    ("UserState",                 "frontend/ios/ios/Core/State/AppState.swift"),
    ("AuthState",                 "frontend/ios/ios/Core/State/AppState.swift"),
    ("WatchlistState",            "frontend/ios/ios/Core/State/AppState.swift"),
    ("ResearchState",             "frontend/ios/ios/Core/State/AppState.swift"),
    ("AppError",                  "frontend/ios/ios/Core/Utilities/AppError.swift"),
]
_CURATED_PY = [
    ("ErrorCode",                    "backend/app/api/error_response.py"),
    ("make_error_body",              "backend/app/api/error_response.py"),
    ("make_error_response",          "backend/app/api/error_response.py"),
    ("auth_error",                   "backend/app/api/error_response.py"),
    ("classify_exception",           "backend/app/api/error_response.py"),
    ("error_response_from_exception", "backend/app/api/error_response.py"),
    ("_spawn",                       "backend/app/main.py"),
    ("RateLimitChecker",             "backend/app/dependencies.py"),
    ("guest_user_id_for",            "backend/app/dependencies.py"),
]


@pytest.mark.parametrize("sym,rel", _CURATED_SWIFT, ids=[f"{s}" for s, _ in _CURATED_SWIFT])
def test_curated_swift_symbols_exist(sym: str, rel: str) -> None:
    p = _REPO / rel
    assert p.exists(), f"{rel} is named by SYSTEM_DESIGN_GUIDELINES.md but does not exist"
    src = _strip_comments(p.read_text(encoding="utf-8"), swift=True)
    assert _defines(src, sym, swift=True), (
        f"SYSTEM_DESIGN_GUIDELINES.md names `{sym}` as the real type in {rel}, "
        f"but nothing declares it there any more."
    )


@pytest.mark.parametrize("sym,rel", _CURATED_PY, ids=[f"{s}" for s, _ in _CURATED_PY])
def test_curated_python_symbols_exist(sym: str, rel: str) -> None:
    p = _REPO / rel
    assert p.exists(), f"{rel} is named by SYSTEM_DESIGN_GUIDELINES.md but does not exist"
    src = _strip_comments(p.read_text(encoding="utf-8"), swift=False)
    assert _defines(src, sym, swift=False), (
        f"SYSTEM_DESIGN_GUIDELINES.md names `{sym}` in {rel}, but nothing defines it there."
    )


# ─────────────────────────────────────────────────────────────────────────────
# D. Tables — curated, plus one inverse assertion that keeps the list honest
# ─────────────────────────────────────────────────────────────────────────────
_CURATED_TABLES = {
    "research_reports", "ticker_report_cache", "ticker_data_cache", "user_credits",
    "credit_transactions", "credit_purchases", "credit_packs", "plan_credits",
    "sector_benchmarks", "chat_sessions", "chat_messages", "chat_usage_budget",
    "notification_events", "trending_themes", "user_investor_profile", "user_memory_facts",
    "watchlist_items", "whale_trades", "snapshot_cache", "etf_snapshot_cache",
    "news_articles", "signals_cache", "guest_report_budget",
}


def _snapshot_tables() -> set[str]:
    txt = _SNAPSHOT.read_text(encoding="utf-8")
    return set(re.findall(r"^CREATE TABLE public\.(\w+)", txt, flags=re.M))


def test_curated_tables_exist_in_the_schema() -> None:
    tables = _snapshot_tables()
    missing = sorted(t for t in _CURATED_TABLES if t not in tables)
    assert not missing, (
        f"SYSTEM_DESIGN_GUIDELINES.md names these tables; they are not in schema_snapshot.sql: "
        f"{missing}. Either they were dropped, or the snapshot needs regenerating "
        f"(backend/scripts/dump_schema.sh)."
    )


def test_every_real_table_the_doc_mentions_is_curated() -> None:
    """Inverse assertion: keeps _CURATED_TABLES honest without a false-positive-prone scanner.

    We do not try to GUESS which backticked snake_case spans are tables — ~180 of them are not
    (`user_message`, `page`, `status`, `total`, `data`, ...). We only ask: of the spans that ARE
    real tables, is each one in the curated list? That has no false positives by construction.
    """
    tables = _snapshot_tables()
    spans = {s for s in re.findall(r"`([^`\n]+)`", _doc_prose())
             if re.fullmatch(r"[a-z][a-z0-9_]+", s)}
    uncurated = sorted((spans & tables) - _CURATED_TABLES)
    assert not uncurated, (
        f"The document names these real tables but they are not in _CURATED_TABLES: {uncurated}. "
        f"Add them, so a later DROP fails this test instead of silently orphaning the prose."
    )


# ─────────────────────────────────────────────────────────────────────────────
# E. Structural invariants the document states as counts
# ─────────────────────────────────────────────────────────────────────────────
# Only counts that are genuine INVARIANTS live here. Non-invariant counts ("155 atoms") are
# deliberately absent from the document: they are guaranteed future falsehoods with no
# compensating value. Both frontend/ios/ios_structure.txt and iOS_ARCHITECTURE_GUIDE.md proved
# that — every one of their file counts had drifted 20-70% before this pass.
EXPECTED = {
    "middleware_registered": 4,   # CORS, GZip, cap_json_body, add_process_time  (§8.3, §10)
    "integrations": 11,           # app/integrations/*.py excluding __init__     (§2)
}


def test_middleware_count_matches_the_doc() -> None:
    src = _strip_comments((_BACKEND / "app" / "main.py").read_text(encoding="utf-8"), swift=False)
    n = len(re.findall(r"app\.add_middleware\(", src)) + len(re.findall(r"@app\.middleware\(", src))
    assert n == EXPECTED["middleware_registered"], (
        f"§10 says the middleware stack is exactly {EXPECTED['middleware_registered']} entries "
        f"(CORS, GZip, cap_json_body, add_process_time); main.py now registers {n}. "
        f"If that is intentional, update §10 and this number together."
    )


def test_integration_count_matches_the_doc() -> None:
    n = len([p for p in (_BACKEND / "app" / "integrations").glob("*.py")
             if p.name != "__init__.py"])
    assert n == EXPECTED["integrations"], (
        f"§2 says there are {EXPECTED['integrations']} integrations; found {n}. "
        f"Update the diagram note in §2 and this number together."
    )


# ─────────────────────────────────────────────────────────────────────────────
# F. Anti-vacuity — a broken path constant must go RED, not silently green
# ─────────────────────────────────────────────────────────────────────────────

def test_anti_vacuity_sentinels() -> None:
    """If these two stop behaving, every other assertion above is meaningless.

    testing.md: a source-scan guard that stops matching turns green, not red. These sentinels
    prove the scanners are actually reading source.
    """
    swift = _ios_swift()
    py = _backend_py()
    assert len(swift) > 300, f"iOS scan found only {len(swift)} Swift files — path constant broken?"
    assert len(py) > 100, f"backend scan found only {len(py)} Python files — path constant broken?"

    # KNOWN-PRESENT: `actor APIClient` is the network entry point the document names.
    assert any(re.search(r"\bactor APIClient\b", s) for _, s in swift), \
        "sentinel failed: `actor APIClient` not found — the Swift scanner is not reading source"
    # KNOWN-ABSENT: a string that must never appear, proving the scanner can also return False.
    assert not any("ZZ_SENTINEL_MUST_NOT_EXIST" in s for _, s in swift), \
        "sentinel failed: the Swift scanner matches everything"

    assert any(re.search(r"\bdef _spawn\b", s) for _, s in py), \
        "sentinel failed: `def _spawn` not found — the Python scanner is not reading source"

    doc = _doc_prose()
    assert len(doc) > 20_000, "sentinel failed: the doc parsed to almost nothing"
    assert "```" not in doc, "sentinel failed: fenced blocks were not stripped"


def test_doc_does_not_reintroduce_code_samples() -> None:
    """§0: 'If you are about to add a code sample here, it belongs in a rule file instead.'

    Version 1.x rotted because 770 lines of fenced Swift/Python could not be checked by anything.
    Diagrams (unlabelled fences) are fine and are what the remaining blocks are.
    """
    langs = re.findall(r"^```(\w+)", _doc_text(), flags=re.M)
    banned = sorted({l for l in langs if l.lower() in {"swift", "python", "py", "json", "javascript"}})
    assert not banned, (
        f"SYSTEM_DESIGN_GUIDELINES.md has regained language-tagged code fences: {banned}. "
        f"Per §0, prescriptive code belongs in .claude/rules/*.md, not here — that duplication is "
        f"exactly what let version 1.x drift for eight months."
    )


# MUTATION_LOG — hand-verified once, 2026-08-27 (testing.md rule 3):
#   * renamed `actor APIClient` -> `actor APIClientZZ`     => GREEN (!) => substring match, no \b
#       -> fixed _SWIFT_DEF/_PY_DEF to require word boundaries; re-ran => 2 tests RED => restored
#   * added `import CoreData` to a scratch .swift file     => 1 test  RED  => file deleted
#   * added ```swift fence to the doc                      => 1 test  RED  => reverted
#   * broke _IOS path constant                             => sentinel RED => restored
