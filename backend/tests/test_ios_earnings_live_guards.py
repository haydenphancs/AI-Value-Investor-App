"""iOS source-scan guards for live earnings results (owner request 2026-10-29).

There is no XCTest target, so the iOS half of the feature is pinned here, the way every other
screen's contract is:
  * the wire: `EarningsLiveResponse` and its nested DTOs mirror the backend models key for
    key; `AlertDTO` and `TrackingFeedResponse` carry the new feed keys, and each
    reaches the card; every property decodes from its own key, never another field's; a null
    the backend may send, or a key an older backend leaves out, never fails a decode; each
    status decodes from the backend's own word;
  * the route: `APIEndpoint.getEarningsLive` is the backend's path, signed-in only;
  * the honesty rule: LIVE is drawn from the SERVER's status, never from date math on the
    device (`LiveNewsHeader`: nothing live on screen, no "Live"), and only on the live card;
  * the poll: it stops when the server stops asking, its starter cancels before re-arming (two
    pollers would double the traffic), it never reads the status, and a failed first load retries
    by itself, bounded. The Tracking tab's 30 s poll builds the whole feed only while a price on
    it can move; otherwise it asks each LIVE card's own route, which costs no FMP call per
    holding, and a card takes an answer only when it settles that card's own report: one the
    server could not settle leaves the card LIVE, and a moved or unlisted report is the feed's
    to redraw;
  * the route: "See live results" is offered only while the endpoint serves that report;
  * the sheet: its header follows the card's own answer, it claims nothing before the first
    answer, it says "Waiting…" only while the report is live, it stops saying LIVE when the
    device stops asking, and a held figure reads the same on the card and the sheet;
  * the previews: sample figures, no request, and no way into a production path;
  * the formatting defects the card shipped with ("$-0.25", "$0.0B") stay fixed;
  * the date parser reads the whole seconds the server sends;
  * the figures: each sits under its own label (the card's rows and year-ago lines, the chips,
    the results sentence), either one landing counts as a result, a beat and a miss never swap
    in anything that draws them (nor in the outcome that stores them), a beat or a miss never
    prints its two figures alike, VoiceOver says how far it beat or missed, a past quarter's
    verdict is in words as well as colour, and a preliminary figure is marked in words, with its
    New York filing time and a link that opens its filing.
Every scan is comment-stripped first, so a doc comment naming a removed token cannot satisfy
or fail it.
"""
from __future__ import annotations

import calendar
import functools
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import get_args

import pytest

from app.integrations.fmp import FMPUnavailableException
from app.schemas.notifications import NotificationEventResponse
from app.schemas.tracking import (
    AlertResponse,
    EarningsLiveFilingResponse,
    EarningsLiveMetricResponse,
    EarningsLiveQuarterResponse,
    EarningsLiveResponse,
    EarningsLiveYearAgoResponse,
    TrackingFeedResponse,
)
from app.services._earnings_common import AFTER_CLOSE, BEFORE_OPEN
from app.services.earnings_live_service import _quarter_result
from app.utils.market_hours import ET, previous_trading_day

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"


def _strip_comments(src: str) -> str:
    """Block comments, whole-line comments and a `//` tail, spaced or glued to the code. The
    look-behind keeps the `//` of a URL in a string."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"(?<!:)//.*$", "", line))
    return "\n".join(out)


def _code(rel: str) -> str:
    path = _IOS / rel
    assert path.exists(), f"missing: {path}"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _norm(code: str) -> str:
    """One space between tokens, so a pin does not depend on where a call wraps."""
    return re.sub(r"\s+", " ", code)


def _struct_block(src: str, name: str) -> str:
    found = re.search(rf"\bstruct {re.escape(name)}\b", src)
    assert found, f"no struct {name}"
    start = found.start()
    return src[start: src.index("\n}", start)]


def _bracketed(src: str, marker: str, opener: str = "{", closer: str = "}") -> str:
    """What the first `opener` after `marker` encloses, found by counting, so a nested type's body
    and a sibling's never leak into the scan."""
    start = src.index(opener, src.index(marker))
    depth = 0
    for at in range(start, len(src)):
        if src[at] == opener:
            depth += 1
        elif src[at] == closer:
            depth -= 1
            if depth == 0:
                return src[start + 1: at]
    raise AssertionError(f"no closing {closer} after {marker}")


def _coding_keys(src: str, name: str) -> dict:
    """`{property: wire key}` from the struct's own `CodingKeys`, read case by case. A key SET
    stays whole when two cases trade keys (`case estimate = "actual"`, `case actual =
    "estimate"`) while every figure decodes into the other's field."""
    enum = _bracketed(_struct_block(src, name), "enum CodingKeys")
    keys: dict = {}
    for statement in re.findall(r"^\s*case\s+(.+?)\s*$", enum, re.M):
        for part in statement.split(","):
            prop, eq, raw = (piece.strip() for piece in part.partition("="))
            assert re.fullmatch(r"\w+", prop), f"{name}: unreadable case {part!r}"
            if eq:
                quoted = re.fullmatch(r'"(\w+)"', raw)
                assert quoted, f"{name}: unreadable raw value {raw!r}"
                raw = quoted.group(1)
            else:
                raw = prop
            assert prop not in keys, f"{name}: {prop} is declared twice"
            keys[prop] = raw
    assert keys, f"{name} declares no CodingKeys"
    return keys


def _snake(prop: str) -> str:
    """`revenueGrowthPct` -> `revenue_growth_pct`: the backend's name for a Swift property."""
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", prop).lower()


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(word.capitalize() for word in rest)


def _decodes_from_its_own_key(keys: dict) -> dict:
    """The properties that decode from another name's key: none, in every DTO here."""
    return {prop: key for prop, key in keys.items() if key != _snake(prop)}


def _body(src: str, opener: str) -> str:
    """The brace-balanced body of the declaration `opener` introduces, which ends at its `{`."""
    start = src.index(opener) + len(opener) - 1
    assert src[start] == "{", f"{opener!r} must end at its opening brace"
    depth = 0
    for i in range(start, len(src)):
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start: i + 1]
    raise AssertionError(f"unbalanced braces after {opener!r}")


_DEBUG_BLOCK = re.compile(r"^[ \t]*#if DEBUG\n(.*?)^[ \t]*#endif[^\n]*\n?", re.S | re.M)


def _debug_only(src: str) -> str:
    return "\n".join(_DEBUG_BLOCK.findall(src))


def _release(src: str) -> str:
    """What a Release build compiles: the source with every `#if DEBUG` block gone."""
    return _DEBUG_BLOCK.sub("", src)


_COLOUR_MODIFIER = re.compile(r"\.(?:foregroundColor|foregroundStyle|tint)\(")


def _colours(code: str) -> list:
    """The argument of every colour modifier in `code`, in source order, parentheses counted so
    a `Color(...)` argument is read whole. A pin on one modifier misses a second one added on a
    view inside it, and the innermost colour is the one drawn."""
    found = []
    for match in _COLOUR_MODIFIER.finditer(code):
        depth, at = 1, match.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(code[at], 0)
            at += 1
        found.append(_norm(code[match.end(): at - 1]).strip())
    return found


def _in_order(code: str, *parts: str) -> None:
    """Each part is in `code`, after the one before it."""
    at = -1
    for part in parts:
        found = code.find(part, at + 1)
        assert found > at, f"{part!r} is missing or out of order"
        at = found


_MODELS = "Models/EarningsLiveModels.swift"
_TRACKING_MODELS = "Models/TrackingModels.swift"
_NOTIFICATION_MODELS = "Models/NotificationModels.swift"
_TRACKING_VM = "ViewModels/TrackingViewModel.swift"
_ACTIVITY_ROW = "Views/Molecules/ActivityRow.swift"
_ALERT_CARD = "Views/Molecules/AlertCardView.swift"
_DESTINATIONS = "Models/AlertDestination.swift"
_HOURS = "Core/Utilities/MarketHoursUtil.swift"
_VM = "ViewModels/EarningsLiveViewModel.swift"
_CARD = "Views/Organisms/EarningsLiveResultsCard.swift"
_SCREEN = "Views/Screens/EarningsLiveScreen.swift"
_DETAIL = "Views/Screens/AlertDetailView.swift"
_CHIPS = "Views/Molecules/EarningsResultChips.swift"
_UPDATED = "func updated(with live: EarningsLiveResponse) -> AppAlert.EarningsData {"
_EPS_FIGURE = ("private nonisolated static func epsFigure(_ value: Double, "
               "extraDecimals extra: Int) -> String {")
_REVENUE_FIGURE = ("private nonisolated static func revenueFigure(_ value: Double, "
                   "extraDecimals extra: Int) -> String {")
_CARD_INIT = ("init(ticker: String, reportDate: String?, fallback: AppAlert.EarningsData? = nil,\n"
              "         onAnswer: ((EarningsLiveResponse) -> Void)? = nil,\n"
              "         onPollEnded: ((Bool) -> Void)? = nil) {")


def test_a_comment_cannot_satisfy_a_scan_however_it_is_glued_on():
    assert _strip_comments("if a {// token") == "if a {"
    assert _strip_comments("if a { // token") == "if a { "
    assert _strip_comments("    // token\nlet b = 1") == "let b = 1"
    assert _strip_comments("let a = 1 /* token */ + 2") == "let a = 1  + 2"
    url = 'let u = "https://example.test/a"'
    assert _strip_comments(url) == url, "a URL in a string is code"
    assert _strip_comments(url + "// token") == url


@pytest.mark.parametrize("swift_name,model", [
    ("EarningsLiveResponse", EarningsLiveResponse),
    ("EarningsLiveMetricDTO", EarningsLiveMetricResponse),
    ("EarningsLiveQuarterDTO", EarningsLiveQuarterResponse),
    ("EarningsLiveYearAgoDTO", EarningsLiveYearAgoResponse),
    ("EarningsLiveFilingDTO", EarningsLiveFilingResponse),
])
def test_the_live_dtos_mirror_the_backend_models_key_for_key(swift_name, model):
    """Field by field, not as a set: two cases that trade keys keep the set whole while the
    sheet prints the actual under "Estimate" with the beat in red, or the EPS row's figures
    under Revenue. Each property decodes from its own name in the backend's snake_case."""
    keys = _coding_keys(_code(_MODELS), swift_name)
    crossed = _decodes_from_its_own_key(keys)
    assert not crossed, f"{swift_name} decodes these from another field's key: {crossed}"
    ios = set(keys.values())
    backend = set(model.model_fields)
    assert ios == backend, (
        f"{swift_name} vs {model.__name__}: iOS-only {sorted(ios - backend)}, "
        f"backend-only {sorted(backend - ios)}"
    )


def test_the_alert_dto_decodes_every_live_card_key():
    """Every key the earnings card reads, each into its own field: crossed estimates read "EPS
    Est: $16600000000.00", and a `reportTime` case with no raw value reads every report time
    as nil."""
    keys = _coding_keys(_code(_TRACKING_MODELS), "AlertDTO")
    card_keys = {
        "ticker", "company_name", "day", "month", "report_time", "eps_estimate",
        "revenue_estimate",
        "earnings_status", "report_date", "eps_actual", "revenue_actual", "eps_surprise_pct",
        "revenue_surprise_pct", "eps_basis", "results_preliminary", "results_checked_at",
        "eps_checking", "revenue_checking",
    }
    assert card_keys <= set(AlertResponse.model_fields), "backend dropped a key the card reads"
    for key in sorted(card_keys):
        assert keys.get(_camel(key)) == key, f"{key} is decoded into the wrong field"
    crossed = _decodes_from_its_own_key(keys)
    assert not crossed, f"AlertDTO decodes these from another field's key: {crossed}"


# Every response this feature's Swift DTOs decode, by struct.
_DTO_PAIRS = (
    ("AlertDTO", _TRACKING_MODELS, AlertResponse),
    ("TrackingFeedResponse", _TRACKING_MODELS, TrackingFeedResponse),
    ("EarningsLiveResponse", _MODELS, EarningsLiveResponse),
    ("EarningsLiveMetricDTO", _MODELS, EarningsLiveMetricResponse),
    ("EarningsLiveQuarterDTO", _MODELS, EarningsLiveQuarterResponse),
    ("EarningsLiveYearAgoDTO", _MODELS, EarningsLiveYearAgoResponse),
    ("EarningsLiveFilingDTO", _MODELS, EarningsLiveFilingResponse),
    ("NotificationEventDTO", _NOTIFICATION_MODELS, NotificationEventResponse),
)

#: The keys this feature added to responses an installed build already decodes. A backend from
#: before them (not yet deployed, or rolled back) sends none, so each must decode when absent.
#: The new route's own DTOs are not here: a backend without the route answers 404, not a body.
_ADDED_TO_SHIPPED_RESPONSES = {
    "AlertDTO": ("earnings_status", "report_date", "eps_actual", "revenue_actual",
                 "eps_surprise_pct", "revenue_surprise_pct", "eps_basis", "results_preliminary",
                 "results_checked_at", "eps_checking", "revenue_checking"),
    "TrackingFeedResponse": ("recent_earnings_alerts",),
    "NotificationEventDTO": ("sent_at",),
}

_SWIFT_SCALARS = {"String": (str,), "Int": (int,), "Double": (float, int), "Bool": (bool,)}


def _stored(src: str, name: str) -> dict:
    """`{property: Swift type}` for the struct's own stored `let`s, read at its own indent, so a
    nested type's properties and a function's locals never count."""
    return dict(re.findall(r"^ {4}let (\w+): ([^=\n]+?)\s*$", _struct_block(src, name), re.M))


def _may_be_null(field) -> bool:
    """The backend can send `null`: the annotation admits None. Not `is_required()`: a defaulted
    `checking: bool = False` is always on the wire and never null, so `Bool` decodes it."""
    ann = field.annotation
    return ann is type(None) or type(None) in get_args(ann)


@functools.lru_cache(maxsize=1)
def _swift_sources() -> dict:
    """`{path under ios/: comment-stripped code}` for every Swift file of the app, read once."""
    return {str(path.relative_to(_IOS)): _strip_comments(path.read_text(encoding="utf-8"))
            for path in sorted(_IOS.rglob("*.swift"))}


_NESTED_TYPE = re.compile(r"\b(?:struct|enum|class|actor|extension)\s+\w+[^{};]*\{")
_DECODER = re.compile(r"\binit\s*\(\s*from\b")
_DECODER_OPENER = re.compile(
    r"init\s*\(\s*from\s+(?:\w+\s+)?\w+\s*:\s*(?:any\s+)?Decoder\s*\)\s*throws\s*\{")
_DECODE_CALL = re.compile(
    r"(try\??)\s+\w+\.(decodeIfPresent|decode)\([^()]*?,\s*forKey:\s*\.(\w+)\)")


def _own_members(body: str) -> str:
    """A type's body with every nested type's body taken out: what is left are its own members.
    A nested type decodes something else (`NotificationEventDTO.RouteScalar` decodes one route
    value), with a decoder of its own."""
    kept, at = [], 0
    for nested in _NESTED_TYPE.finditer(body):
        if nested.start() < at:
            continue
        kept.append(body[at: nested.start()])
        at = nested.end() - 1 + len(_body(body[nested.end() - 1:], "{"))
    return "".join(kept) + body[at:]


def _declarations(src: str, keyword: str, name: str) -> list:
    """The own members of each `struct Name` (`keyword` "struct") or `extension Name` in `src`,
    braces counted, whatever attributes, modifiers, conformances or `where` clause it is declared
    with. Never `extension Name.Nested`: that one extends another type."""
    opener = re.compile(
        rf"^[ \t]*(?:@\w+(?:\([^)]*\))?\s+|\w+\s+)*{keyword}\s+{re.escape(name)}"
        rf"(?=\s*[:{{]|\s+where\b)[^{{]*\{{",
        re.M)
    return [_own_members(_body(src[found.end() - 1:], "{")[1:-1])
            for found in opener.finditer(src)]


def _decoder_bodies(src: str, name: str, rel: str, others: dict) -> tuple:
    """`(bodies, problems)`: every `init(from:)` the type declares for itself, and anything that
    hides one from this scan. In its own body, or in an `extension` of it in its own file (`src`,
    as the caller has it) or in any other (`others`, by path): a decoder written in an extension
    replaces the synthesized one all the same, and this app writes some of its decoders there.
    An opener this scan cannot read is a problem, never a decoder skipped."""
    own = _declarations(src, "struct", name)
    assert len(own) == 1, f"{name}: {len(own)} declarations in {rel}"
    members = own + _declarations(src, "extension", name)
    for path, code in others.items():
        if path != rel and name in code:
            members += _declarations(code, "extension", name)
    bodies, problems = [], []
    for text in members:
        for found in _DECODER.finditer(text):
            opener = _DECODER_OPENER.match(text, found.start())
            if opener is None:
                problems.append(f"{name}: a decoder this scan cannot read: "
                                f"{_norm(text[found.start(): found.start() + 80])!r}")
                continue
            bodies.append(_body(text[opener.end() - 1:], "{"))
    return bodies, problems


def _decoder_reads(src: str, name: str, rel: str, others: dict) -> tuple:
    """`({property: [tolerant, ...]}, problems)`: one entry per read in the type's own
    `init(from:)`, wherever it is declared, or `{}` when it has none and the synthesized decoder
    reads each property. Tolerant is `decodeIfPresent` or `try?`: a plain `decode` throws on a
    null and on an absent key. A decoder whose reads this scan cannot see is a problem, never a
    pass."""
    bodies, problems = _decoder_bodies(src, name, rel, others)
    reads: dict = {}
    for body in bodies:
        found = _DECODE_CALL.findall(body)
        if not found:
            problems.append(f"{name}: an init(from:) whose reads this scan cannot see")
        for tried, how, prop in found:
            reads.setdefault(prop, []).append(how == "decodeIfPresent" or tried == "try?")
    return reads, problems


def _null_safety_problems(src: str, swift_name: str, model, rel: str,
                          others: dict = None) -> list:
    """Every way the type declared in `src`, the file at `rel`, fails to decode something `model`
    may send. `others` is every other Swift file by path: the app's own, unless a test hands in a
    mutant."""
    props = _stored(src, swift_name)
    keys = _coding_keys(src, swift_name)
    assert props and set(props) == set(keys), (
        f"{swift_name}: stored properties and CodingKeys disagree: {sorted(set(props) ^ set(keys))}"
    )
    fields = model.model_fields
    reads, problems = _decoder_reads(src, swift_name, rel,
                                     _swift_sources() if others is None else others)
    added = set(_ADDED_TO_SHIPPED_RESPONSES.get(swift_name, ()))
    for prop, swift_type in props.items():
        key = keys[prop]
        if key not in fields:
            problems.append(f"{swift_name}.{prop} decodes `{key}`, which {model.__name__} never sends")
            continue
        optional = swift_type.endswith("?")
        nullable = _may_be_null(fields[key])
        if nullable and not optional:
            problems.append(f"`{key}` may be null, but {swift_name}.{prop} is `{swift_type}`: "
                            "one null fails the whole response")
        if reads:
            if not reads.get(prop):
                problems.append(f"{swift_name}.{prop}: its own init(from:) never reads `{key}`")
            elif (optional or nullable or key in added) and not all(reads[prop]):
                problems.append(f"{swift_name}.{prop} reads `{key}` with a plain decode(), which "
                                "throws on a null and on an absent key")
        elif key in added and not optional:
            problems.append(f"`{key}` is new on a response a shipped build decodes, so an older "
                            f"backend leaves it out: {swift_name}.{prop} must be optional")
        want = _SWIFT_SCALARS.get(swift_type.rstrip("?"))
        if want is not None:
            ann = fields[key].annotation
            wire = next((a for a in get_args(ann) if a is not type(None)), ann)
            if wire not in want:
                problems.append(f"`{key}` is {getattr(wire, '__name__', wire)} on the backend, "
                                f"`{swift_type}` in {swift_name}")
    return problems


@pytest.mark.parametrize("swift_name,rel,model", _DTO_PAIRS, ids=[p[0] for p in _DTO_PAIRS])
def test_a_null_or_a_missing_key_never_fails_a_decode(swift_name, rel, model):
    """Each of these lists is decoded whole, so one row that fails takes every row with it: a
    non-optional `earningsStatus` stops the Tracking feed on the first insider roll-up (which
    sends `"earnings_status": null`), a plain `decode` of `sent_at` fails the inbox page holding a
    held row, and a non-optional `checkedAt` makes every upcoming answer read "Couldn't load the
    latest results". All three compile. A field that may be null must be optional, a key added
    to a shipped response must decode when an older backend leaves it out, and a type with its
    own decoder must read both kinds without a plain `decode`, wherever that decoder is written:
    in an `extension`, in this file or another, it replaces the synthesized one all the same."""
    problems = _null_safety_problems(_code(rel), swift_name, model, rel)
    assert not problems, "\n".join(problems)


def _in_struct(swift_name: str, *changes: tuple):
    """A mutant made inside the struct's own block, each `(old, new)` once: `let surprisePct:
    Double?` is also declared by `EarningsMetricOutcome` and the metric DTO above the quarter
    DTO."""
    def mutate(src: str) -> tuple:
        block = _struct_block(src, swift_name)
        changed = block
        for old, new in changes:
            assert changed.count(old) == 1, f"mutation anchor {old!r} is gone from {swift_name}"
            changed = changed.replace(old, new, 1)
        return src.replace(block, changed, 1), {}
    return mutate


def _extension_decoder(swift_name: str, src: str, plain: str = "") -> str:
    """`extension <swift_name>` holding a hand-written decoder that reads every stored property:
    tolerantly where it is optional, with a plain `decode` for the rest and for `plain`."""
    reads = []
    for prop, swift_type in _stored(src, swift_name).items():
        how = "decodeIfPresent" if swift_type.endswith("?") and prop != plain else "decode"
        reads.append(f"        {prop} = try c.{how}({swift_type.rstrip('?')}.self, "
                     f"forKey: .{prop})")
    return (f"\nextension {swift_name} {{\n    init(from decoder: Decoder) throws {{\n"
            "        let c = try decoder.container(keyedBy: CodingKeys.self)\n"
            + "\n".join(reads) + "\n    }\n}\n")


def _decoder_added(swift_name: str, plain: str = "", elsewhere: bool = False):
    """A mutant that gives the struct a hand-written decoder in an `extension`: at the end of its
    own file, or in a file of its own."""
    def mutate(src: str) -> tuple:
        decoder = _extension_decoder(swift_name, src, plain)
        if elsewhere:
            return src, {f"Models/{swift_name}+Decoding.swift": decoder}
        return src + decoder, {}
    return mutate


def _decoder_moved_out(swift_name: str, old: str = "", new: str = ""):
    """A mutant that moves the struct's own decoder into an `extension` at the end of its file,
    with `old` changed to `new` on the way."""
    opener = "init(from decoder: Decoder) throws {"

    def mutate(src: str) -> tuple:
        block = _struct_block(src, swift_name)
        decoder = opener[:-1] + _body(block, opener)
        assert block.count("    " + decoder) == 1, f"{swift_name} has no decoder of its own"
        if old:
            assert decoder.count(old) == 1, f"mutation anchor {old!r} is gone from {swift_name}"
        moved = decoder.replace(old, new, 1) if old else decoder
        kept = src.replace(block, block.replace("    " + decoder, "", 1), 1)
        return kept + f"\nextension {swift_name} {{\n    {moved}\n}}\n", {}
    return mutate


_SENT_AT_TOLERANT = "sentAt = try c.decodeIfPresent(String.self, forKey: .sentAt)"
_SENT_AT_PLAIN = "sentAt = try c.decode(String.self, forKey: .sentAt)"

#: Each way a decode that throws on a null can come back with every other test green. The
#: decoders written in an `extension` were invisible to this scan once: it read the struct's
#: own block, with one spelling of the opener, and so read `RouteScalar`'s decoder in place of
#: one respelled `any Decoder`.
_NULL_SAFETY_MUTANTS = {
    "a_null_status_on_the_feed": (
        "AlertDTO", _TRACKING_MODELS,
        _in_struct("AlertDTO", ("let earningsStatus: String?", "let earningsStatus: String"))),
    "a_plain_sent_at": (
        "NotificationEventDTO", _NOTIFICATION_MODELS,
        _in_struct("NotificationEventDTO", (_SENT_AT_TOLERANT, _SENT_AT_PLAIN))),
    "a_null_checked_at": (
        "EarningsLiveResponse", _MODELS,
        _in_struct("EarningsLiveResponse", ("let checkedAt: String?", "let checkedAt: String"))),
    "an_older_feed_without_the_recent_list": (
        "TrackingFeedResponse", _TRACKING_MODELS,
        _in_struct("TrackingFeedResponse", ("let recentEarningsAlerts: [AlertDTO]?",
                                            "let recentEarningsAlerts: [AlertDTO]"))),
    "a_null_quarter_surprise": (
        "EarningsLiveQuarterDTO", _MODELS,
        _in_struct("EarningsLiveQuarterDTO", ("let surprisePct: Double?", "let surprisePct: Double"))),
    "a_plain_status_in_an_extension": (
        "AlertDTO", _TRACKING_MODELS, _decoder_added("AlertDTO", plain="earningsStatus")),
    "a_plain_status_in_an_extension_in_another_file": (
        "AlertDTO", _TRACKING_MODELS,
        _decoder_added("AlertDTO", plain="earningsStatus", elsewhere=True)),
    "a_plain_checked_at_in_an_extension": (
        "EarningsLiveResponse", _MODELS, _decoder_added("EarningsLiveResponse", plain="checkedAt")),
    "a_plain_sent_at_under_an_any_decoder_opener": (
        "NotificationEventDTO", _NOTIFICATION_MODELS,
        _in_struct("NotificationEventDTO",
                   ("init(from decoder: Decoder) throws {\n"
                    "        let c = try decoder.container(keyedBy: CodingKeys.self)",
                    "init(from decoder: any Decoder) throws {\n"
                    "        let c = try decoder.container(keyedBy: CodingKeys.self)"),
                   (_SENT_AT_TOLERANT, _SENT_AT_PLAIN))),
    "a_plain_sent_at_in_a_decoder_moved_to_an_extension": (
        "NotificationEventDTO", _NOTIFICATION_MODELS,
        _decoder_moved_out("NotificationEventDTO", _SENT_AT_TOLERANT, _SENT_AT_PLAIN)),
    "a_decoder_whose_reads_the_scan_cannot_see": (
        "AlertDTO", _TRACKING_MODELS,
        lambda src: (src + "\nextension AlertDTO {\n    init(from decoder: Decoder) throws {\n"
                           "        self = try AlertDTO.tolerant(decoder)\n    }\n}\n", {})),
}


@pytest.mark.parametrize("swift_name,rel,mutate", list(_NULL_SAFETY_MUTANTS.values()),
                         ids=list(_NULL_SAFETY_MUTANTS))
def test_the_null_safety_scan_bites(swift_name, rel, mutate):
    """The source as it is gives no problem, and each mutant above, made on a copy of it (and of
    the other files, for one in another file), gives at least one."""
    src = _code(rel)
    model = next(m for n, _, m in _DTO_PAIRS if n == swift_name)
    assert not _null_safety_problems(src, swift_name, model, rel)
    mutant, extra = mutate(src)
    assert _null_safety_problems(mutant, swift_name, model, rel, {**_swift_sources(), **extra}), (
        "the scan cannot see this mutant"
    )


@pytest.mark.parametrize("swift_name,rel,mutate", [
    ("AlertDTO", _TRACKING_MODELS, _decoder_added("AlertDTO")),
    ("AlertDTO", _TRACKING_MODELS, _decoder_added("AlertDTO", elsewhere=True)),
    ("EarningsLiveResponse", _MODELS, _decoder_added("EarningsLiveResponse")),
    ("NotificationEventDTO", _NOTIFICATION_MODELS, _decoder_moved_out("NotificationEventDTO")),
], ids=["in_its_file", "in_another_file", "on_the_live_answer", "moved_out_whole"])
def test_a_tolerant_decoder_in_an_extension_passes_the_scan(swift_name, rel, mutate):
    """The twins of the mutants above: the scan reads a decoder written in an extension the way it
    reads one in the struct, so the mutants fail on their one plain `decode`, never on where the
    decoder is written."""
    model = next(m for n, _, m in _DTO_PAIRS if n == swift_name)
    honest, extra = mutate(_code(rel))
    problems = _null_safety_problems(honest, swift_name, model, rel, {**_swift_sources(), **extra})
    assert not problems, "\n".join(problems)


def test_every_key_added_to_a_shipped_response_is_on_both_sides():
    """A key listed here that neither side declares would be checked by nothing."""
    for swift_name, added in _ADDED_TO_SHIPPED_RESPONSES.items():
        rel, model = next((r, m) for n, r, m in _DTO_PAIRS if n == swift_name)
        assert set(added) <= set(model.model_fields), swift_name
        assert set(added) <= set(_coding_keys(_code(rel), swift_name).values()), swift_name


def test_the_feed_row_reaches_the_card_field_by_field():
    """A decoded key proves nothing until it reaches the card. A `report_date` lost here takes the
    results card off the sheet with no error, and a lost status reads every report as upcoming.
    The whole arm, the statements above the call included: the call passes `consensus` and
    `reportTime` as locals built there, so a pin on the call alone misses a consensus that lost
    its revenue estimate and a before-open report told as after the close. The time tokens are
    the backend's own (`_earnings_common`): one renamed on either side reads every time as nil."""
    tracking = _code(_TRACKING_MODELS)
    convert = _body(tracking, "func toAppAlert() -> AppAlert {")
    arm = _norm(convert[convert.index('case "earnings":'): convert.index('case "whale_trade":')])
    assert arm.strip() == (
        'case "earnings": let mappedTime: AppAlert.EarningsReportTime? switch reportTime { '
        'case "' + BEFORE_OPEN + '": mappedTime = .beforeOpen '
        'case "' + AFTER_CLOSE + '": mappedTime = .afterClose '
        "default: mappedTime = nil } "
        "let consensus = AppAlert.EarningsData.consensusText(epsEstimate: epsEstimate, "
        "revenueEstimate: revenueEstimate) "
        'return .earnings(AppAlert.EarningsData( ticker: ticker ?? "", '
        'companyName: companyName ?? "", reportTime: mappedTime, consensus: consensus, '
        'day: day ?? 0, month: month ?? "", status: EarningsLiveStatus(wire: earningsStatus), '
        'reportDate: reportDate, epsEstimate: epsEstimate, revenueEstimate: revenueEstimate, '
        'epsActual: epsActual, revenueActual: revenueActual, epsSurprisePct: epsSurprisePct, '
        'revenueSurprisePct: revenueSurprisePct, epsIsGAAP: epsBasis == "gaap", '
        'isPreliminary: resultsPreliminary ?? false, '
        'checkedAt: EarningsLiveDates.parse(resultsCheckedAt), '
        'epsChecking: epsChecking ?? false, revenueChecking: revenueChecking ?? false ))'
    )
    times = _norm(_bracketed(_bracketed(tracking, "enum EarningsReportTime"), "var displayText: String"))
    assert ('case .beforeOpen: return "before market open" '
            'case .afterClose: return "after market close"') in times


def test_the_feed_response_decodes_the_recent_earnings_list():
    """The previous trading day's cards reach the app only through `recent_earnings_alerts`, so
    the morning after an after-close report its card and results come from this list alone.
    The merge is pinned whole, inside the load that makes it: a token anywhere in the file stays
    when `self.alerts = current` drops the list it decoded. Every whole replacement of the list
    bumps `alertsRevision`, which a LIVE card's own late answer checks before writing."""
    keys = _coding_keys(_code(_TRACKING_MODELS), "TrackingFeedResponse")
    assert not _decodes_from_its_own_key(keys)
    assert set(keys.values()) == set(TrackingFeedResponse.model_fields)
    feed = _norm(_body(_code(_TRACKING_VM), "private func loadTrackingFeed() async -> Bool {"))
    assert ("let recent = (feed.recentEarningsAlerts ?? []).map { $0.toAppAlert() } "
            "let current = feed.alerts.map { $0.toAppAlert() } "
            "let currentIds = Set(current.map(\\.id)) "
            "self.alerts = recent.filter { !currentIds.contains($0.id) } + current "
            "self.alertsRevision &+= 1 ") in feed, "the previous day's cards are decoded and dropped"
    assert feed.count("self.alerts = ") == 2, "the merged list is replaced again in the same load"
    assert "self.alerts = [] self.alertsRevision &+= 1 " in feed, (
        "a refusal empties the list without telling a LIVE card's answer in flight"
    )
    assert _code(_TRACKING_VM).count("alertsRevision &+= 1") == 2


def test_the_endpoint_is_the_backend_route_and_sign_in_only():
    src = _code("Core/Services/APIEndpoint.swift")
    assert 'return "/api/v1/tracking/earnings/\\(ticker)/live"' in src
    assert '["report_date": reportDate]' in src
    policy = src[src.index("var authPolicy: AuthPolicy"):]
    # The arm that holds the Tracking feed itself: from its `case` line to its `return`.
    at = policy.index(".getTrackingAssets")
    arm_start = policy.rfind("case ", 0, at)
    arm = policy[arm_start: policy.index("return ", at)]
    tail = policy[policy.index("return ", at):].split("\n", 1)[0]
    assert ".getEarningsLive" in arm, "the live endpoint is not in the Tracking feed's arm"
    assert tail.strip() == "return .signInRequired", tail


def test_live_is_drawn_from_the_server_status_only():
    """The whole getter: a substring stays green under `|| data.status == .awaiting`, which
    draws LIVE above "results aren't in yet" and keeps the Tracking poll running until noon ET
    on the next trading day."""
    body = _norm(_body(_code(_TRACKING_MODELS), "var isLiveEarnings: Bool {"))
    assert body == "{ if case .earnings(let data) = self { return data.status == .live } return false }"
    for token in ("Date()", "Calendar", "isMarketActive", "hour"):
        assert token not in body, f"LIVE is inferred on the device again ({token})"
    card = _code(_ALERT_CARD)
    assert "leadingBadge: alert.isLiveEarnings ? .live : nil" in card
    assert "ActivityRow(" in card


def test_the_live_badge_is_drawn_on_the_live_card_alone():
    """`ActivityRow` is the row of every notification, digest card and price rule, and only the
    live earnings card asks it for the badge. The arm that draws it, and the words VoiceOver
    reads, are pinned whole: `leadingBadge == nil` draws LIVE on every other row and never on the
    live one, and a deleted overlay or "Live. " drops the owner's label with every guard green.
    Across the app the badge is drawn only there, in the sheet's header and in its own preview,
    and only `AlertCardView` asks a row for it."""
    row = _struct_block(_code(_ACTIVITY_ROW), "ActivityRow")
    assert ".overlay(alignment: .bottom) {" in row, "no row draws the LIVE label any more"
    assert _norm(_bracketed(row, ".overlay(alignment: .bottom) {")).strip() == (
        "if leadingBadge == .live { LiveBadge() .offset(y: 9) }"
    )
    assert row.count("LiveBadge(") == 1
    assert _norm(_bracketed(row, ".accessibilityLabel", "(", ")")).strip() == (
        '"\\(isNew ? "New. " : "")\\(leadingBadge == .live ? "Live. " : "")\\(title). \\(subtitle)"'
    ), "VoiceOver must say Live on the live card, and only there"

    drawn: dict = {}
    asked: dict = {}
    for path in sorted(_IOS.rglob("*.swift")):
        rel = str(path.relative_to(_IOS))
        code = _code(rel)
        uses = len(re.findall(r"\bLiveBadge\(", code))
        if uses:
            drawn[rel] = uses
        passed = re.findall(r"\bleadingBadge:\s*([^,\n)]+?)\s*(?:,|\n|\))", code)
        if passed:
            asked[rel] = passed
    assert drawn == {_ACTIVITY_ROW: 1, _CARD: 1, "Views/Atoms/LiveBadge.swift": 2}, drawn
    atom = _code("Views/Atoms/LiveBadge.swift")
    assert "LiveBadge(" not in atom[: atom.index("#Preview")], "the atom draws itself outside its preview"
    assert asked == {
        _ACTIVITY_ROW: ["ActivityRowBadge? = nil", "ActivityRowBadge? = nil", "leadingBadge"],
        _ALERT_CARD: ["alert.isLiveEarnings ? .live : nil"],
    }, asked


def test_the_status_decoder_falls_back_to_upcoming():
    """An older backend sends no status; that is exactly the card this build used to draw. The
    whole initializer, inside its own enum: every status from the server goes through it, the
    feed's and the sheet's, so a line after the fallback (`if self == .live { self = .upcoming
    }`) reads every LIVE report as upcoming, with no LIVE label and no late poll, while the
    fallback itself still reads as pinned."""
    status = _bracketed(_code(_MODELS), "enum EarningsLiveStatus: String")
    assert _norm(_body(status, "init(wire: String?) {")) == (
        '{ self = EarningsLiveStatus(rawValue: (wire ?? "").lowercased()) ?? .upcoming }'
    )
    assert len(re.findall(r"\binit\s*\(", status)) == 1, "a second way to decode a status"
    assert 'case notScheduled = "none"' in status, "a case named `none` collides with Optional"


def _string_enum_cases(src: str, name: str) -> dict:
    """`{case: raw value}` of a `String` enum, read case by case: a case with no `= "..."`
    stands for its own name."""
    body = _bracketed(src, f"enum {name}: String")
    cases = body[: body.index("init(")] if "init(" in body else body
    raw: dict = {}
    for statement in re.findall(r"^\s*case\s+(.+?)\s*$", cases, re.M):
        for part in statement.split(","):
            case, eq, value = (piece.strip() for piece in part.partition("="))
            assert re.fullmatch(r"\w+", case), f"{name}: unreadable case {part!r}"
            if eq:
                quoted = re.fullmatch(r'"([^"]*)"', value)
                assert quoted, f"{name}: unreadable raw value {value!r}"
                value = quoted.group(1)
            else:
                value = case
            assert case not in raw, f"{name}: {case} is declared twice"
            raw[case] = value
    assert raw, f"{name} declares no cases"
    return raw


def test_each_status_decodes_from_the_backends_own_word():
    """A word renamed on either side falls through `init(wire:)` to `.upcoming`, with no error:
    a live report then reads "INTC reports earnings on OCT 29 after market close." beside its
    own result chips, with no LIVE label and no late poll. Case by case, never as a set of
    words: `live` and `reported` trading words keep the set whole while a finished report draws
    the red LIVE label. Every status the backend sends has its case, and the answer's status is
    read through that one decoder."""
    import app.services.earnings_live_service as els

    assert _string_enum_cases(_code(_MODELS), "EarningsLiveStatus") == {
        "upcoming": els.STATUS_UPCOMING,
        "live": els.STATUS_LIVE,
        "reported": els.STATUS_REPORTED,
        "awaiting": els.STATUS_AWAITING,
        "notScheduled": els.STATUS_NONE,
    }
    sent = {value for name, value in vars(els).items() if name.startswith("STATUS_")}
    assert sent == {els.STATUS_UPCOMING, els.STATUS_LIVE, els.STATUS_REPORTED, els.STATUS_AWAITING,
                    els.STATUS_NONE}, f"a status iOS has no case for reads as upcoming: {sorted(sent)}"
    answer = _bracketed(_code(_MODELS), "extension EarningsLiveResponse")
    assert _norm(_body(answer, "var liveStatus: EarningsLiveStatus {")) == (
        "{ EarningsLiveStatus(wire: status) }")


def test_the_sheet_poll_stops_when_the_server_stops_asking():
    vm = _code(_VM)
    start = vm[vm.index("func start()"): vm.index("func stop()")]
    assert start.index("pollTask?.cancel()") < start.index("pollTask = Task"), (
        "re-arming leaves the previous poller running"
    )
    assert "pollTask?.cancel()" in _body(vm, "func stop() {")
    assert "guard let seconds = self.nextPollSeconds else {" in start
    loop = _body(start, "while !Task.isCancelled {")
    assert loop.index("guard !Task.isCancelled else { break }") < loop.index("self.nextPollSeconds"), (
        "a cancelled load is no answer: it may not end the poll that replaced it"
    )
    assert "let wait = UInt64(max(seconds, Self.minimumPollSeconds)) * 1_000_000_000" in loop, (
        "`minimumPollSeconds` is a floor: a `min` makes it a ceiling"
    )
    assert "private static let minimumPollSeconds = 15" in vm, "a malformed cadence hammers the route"
    assert "try? await Task.sleep(nanoseconds: wait)" in loop, "the retry fires with no wait"
    next_poll = _norm(_body(vm, "private var nextPollSeconds: Int? {"))
    assert ("if let live { guard let seconds = live.pollSeconds, seconds > 0 else { return nil } "
            "return seconds }") in next_poll, (
        "poll_seconds null must end the poll, and any other value is the cadence: 120 s while "
        "FMP confirms a reported report must not turn into the retry's 30 s"
    )
    assert "tatus" not in vm, (
        "the server's cadence alone decides: an `upcoming` or `awaiting` answer on a day nobody "
        "has read carries a poll, and a view model that reads the status would stop on it"
    )
    card = _norm(_code(_CARD))
    for hook in (".onAppear { viewModel.start() }", ".onDisappear { viewModel.stop() }",
                 "willResignActiveNotification)) { _ in viewModel.stop() }",
                 "didBecomeActiveNotification)) { _ in viewModel.start() }"):
        assert hook in card, f"the live card lost its lifecycle hook: {hook}"


def test_the_sheet_hosts_the_live_card_without_changing_its_own_signature():
    detail = _code("Views/Screens/AlertDetailView.swift")
    assert "EarningsLiveResultsCard(ticker: data.ticker, reportDate: data.reportDate," in detail
    assert "var onOpen: (AlertDestination) -> Void" in detail


def test_the_sheet_opens_on_the_tap_figures_and_the_live_answer_then_wins():
    """The sheet is never blank while the first answer loads, and from that answer on nothing
    on it may go on reading the copy taken at the tap."""
    detail = _norm(_body(_code(_DETAIL),
                         "private func earningsDetail(_ data: AppAlert.EarningsData) -> some View {"))
    assert ("fallback: data, onAnswer: { liveAnswer = $0 }, onPollEnded: { liveCheckEnded = $0 })"
            in detail)
    card = _code(_CARD)
    init = _body(card, _CARD_INIT)
    for stored in ("self.fallback = fallback", "self.onAnswer = onAnswer",
                   "self.onPollEnded = onPollEnded"):
        assert stored in init, f"the card drops what its host gave it: {stored}"
    for opener, order in (
        ("private var status: EarningsLiveStatus {",
         "live?.liveStatus ?? fallback?.status ?? .upcoming"),
        ("private var eps: EarningsMetricOutcome {", "live?.epsOutcome ?? fallback?.epsOutcome"),
        ("private var revenue: EarningsMetricOutcome {",
         "live?.revenueOutcome ?? fallback?.revenueOutcome"),
        ("private var isPreliminary: Bool {",
         "live?.preliminary ?? fallback?.isPreliminary ?? false"),
        ("private var checkedAt: Date? {", "live?.checkedDate ?? fallback?.checkedAt"),
        ("private var companyName: String {", 'live?.companyName ?? fallback?.companyName ?? ""'),
    ):
        assert order in _norm(_body(card, opener)), f"the answer must win: {order}"


def test_the_report_date_reaches_the_endpoint_from_both_doors():
    """Drop it anywhere on the way and the server picks a report itself: a Friday report opened
    on Monday with no held record reads next quarter's estimates."""
    screen = _body(_code(_SCREEN), "init(ticker: String, reportDate: String?) {")
    assert "self.card = EarningsLiveResultsCard(ticker: ticker, reportDate: reportDate)" in screen
    assert ("StateObject(wrappedValue: EarningsLiveViewModel(ticker: ticker, reportDate: reportDate))"
            in _norm(_body(_code(_CARD), _CARD_INIT)))
    vm = _code(_VM)
    assert "self.reportDate = reportDate" in _body(
        vm, "init(ticker: String, reportDate: String?, apiClient: APIClient = .shared) {")
    assert "endpoint: .getEarningsLive(ticker: ticker, reportDate: reportDate)," in _body(
        vm, "private func load() async {")
    assert 'guard let reportDate, !reportDate.isEmpty else { return nil }' in _code(
        "Core/Services/APIEndpoint.swift")


def test_the_push_offers_live_results_first_and_the_cover_opens_them():
    dest = _code("Models/AlertDestination.swift")
    assert "case earningsLive(ticker: String, reportDate: String?)" in dest
    assert 'label: "See live results"' in dest
    first = dest.index('label: "See live results"')
    assert first < dest.index('label: "Open \\(symbol)"'), "live results must lead"
    cover = _code("Views/Modifiers/AlertDestinationCover.swift")
    assert "EarningsLiveScreen(ticker: ticker, reportDate: reportDate)" in cover


def test_the_consensus_formatting_defects_stay_fixed():
    """The whole formatter, not just its two strings: its callers pass both estimates (pinned
    where they call it), and one `if let` here reading the other estimate prints "Rev Est: $0"
    beside a $16.6B consensus, or "EPS Est: $16600000000.00"."""
    fmt = _code(_MODELS)
    assert _norm(_body(fmt, "static func consensusText(epsEstimate: Double?, "
                            "revenueEstimate: Double?) -> String {")) == (
        "{ var parts: [String] = [] "
        'if let eps = epsEstimate { parts.append("EPS Est: \\(EarningsFigureFormat.eps(eps))") } '
        "if let revenue = revenueEstimate { "
        'parts.append("Rev Est: \\(EarningsFigureFormat.revenue(revenue))") } '
        'return parts.joined(separator: " · ") }'
    ), "each estimate under its own label, a loss as -$0.25 and $23.5M as $23.5M"
    for code in (fmt, _code(_TRACKING_MODELS)):
        assert 'String(format: "EPS Est: $%.2f"' not in code
        assert 'String(format: "Rev Est: $%.1fB"' not in code
    figures = _norm(_bracketed(fmt, "enum EarningsFigureFormat"))
    eps = _body(figures, _EPS_FIGURE)
    assert 'let sign = value < 0 ? "-" : ""' in eps, "the EPS sign goes after the $ again"
    assert 'return sign + String(format: "$%.\\(2 + extra)f", abs(value))' in eps
    assert 'String(format: "$%.\\(1 + extra)fM", v / 1_000_000)' in _body(figures, _REVENUE_FIGURE)


def test_a_figure_the_server_gave_no_verdict_is_never_coloured():
    """A figure with no surprise beside its consensus is one the server refused to judge: a
    print that covers the consensus, from either source, or a consensus under a cent. Comparing
    the raw values would paint a verdict the card, the sheet and the push cannot share: the card
    only has the report-wide preliminary flag, so the rule reads the server's answer and no
    source. A surprise of 0.0 IS an answer, so a beat that rounds to 0.00 stays a beat."""
    block = _struct_block(_code(_MODELS), "EarningsMetricOutcome")
    body = block[block.index("var result: EarningsQuarterResult"):block.index("var color: Color")]
    rule = "if surprisePct == nil { return .noEstimate }"
    assert body.index("guard !isGAAP, let estimate else") < body.index(rule)
    assert body.index(rule) < body.index("actual > estimate")
    assert "surprisePct == 0" not in body
    assert "isPreliminary" not in body, "the card and the sheet pass this flag differently"


@pytest.mark.parametrize("rel,scope,outcome,passed", [
    (_MODELS, "extension EarningsLiveResponse", "var epsOutcome", "eps.surprisePct"),
    (_MODELS, "extension EarningsLiveResponse", "var revenueOutcome", "revenue.surprisePct"),
    ("Models/TrackingModels.swift", "struct EarningsData", "var epsOutcome", "epsSurprisePct"),
    ("Models/TrackingModels.swift", "struct EarningsData", "var revenueOutcome",
     "revenueSurprisePct"),
])
def test_each_outcome_is_handed_its_own_surprise(rel, scope, outcome, passed):
    """`EarningsMetricOutcome.result` reads the surprise as the server's answer, so a nil here turns
    every figure of that surface into a grey "In", with nothing else to show it."""
    body = _bracketed(_bracketed(_code(rel), scope), outcome)
    assert re.search(rf"\bsurprisePct:\s*{re.escape(passed)}\s*[,)]", body), body


@pytest.mark.parametrize("label,dto_field", [
    ("epsSurprisePct", "epsSurprisePct"),
    ("revenueSurprisePct", "revenueSurprisePct"),
])
def test_the_feed_hands_the_card_each_surprise_it_decoded(label, dto_field):
    models = _code("Models/TrackingModels.swift")
    call = _bracketed(models, ".earnings(AppAlert.EarningsData", "(", ")")
    assert re.search(rf"\b{label}:\s*{dto_field}\s*,", call), call


def test_the_date_parser_reads_the_whole_seconds_the_server_sends():
    """`iso_utc` sends "2026-10-29T20:05:00Z". A formatter asking for fractional seconds refuses
    that, the date comes back nil and "updated N min ago" goes, with no crash and no log."""
    block = _bracketed(_code(_MODELS), "enum EarningsLiveDates")
    plain = _bracketed(block, "private static let plain")
    assert "f.formatOptions = [.withInternetDateTime]" in plain
    assert re.search(r"return\s+plain\.date\(from:\s*raw\)", _bracketed(block, "func parse("))






def test_the_colour_word_and_arrow_all_come_from_the_one_verdict():
    block = _struct_block(_code(_MODELS), "EarningsMetricOutcome")
    for name, end in (("var color: Color", "var word: String?"),
                      ("var word: String?", "var surpriseText: String?"),
                      ("var arrowSymbol: String?", None)):
        start = block.index(name)
        body = block[start: block.index(end) if end else len(block)]
        assert "switch result {" in body, f"{name} works its own verdict out"
        assert "actual" not in body and "estimate" not in body, f"{name} compares raw figures"


def test_the_poll_builds_the_feed_only_while_a_price_can_move():
    """Every feed build pays FMP for each holding (sparklines, the extended print, insider rows,
    quotes off the screener): about 777 calls and 19 MB an hour for 21 holdings at 30 s. A LIVE
    card used to keep that running after 20:00 ET, after a half day's close and on a closed day,
    when no price can move and only the reporting company's card can change (the owner's rule of
    2026-11-11: live earnings cost only for the watched companies, on their report day). The
    feed is built only while `shouldRefreshPrices` says a price can move; otherwise the poll asks
    each LIVE card on screen (`filteredAlerts`: a card for another group is not on screen)
    through its own route, every 30 s, so a LIVE card still keeps up with the server."""
    timer = _norm(_body(_code(_TRACKING_VM), "func startPriceRefreshTimer() {"))
    assert timer == (
        "{ priceRefreshTask?.cancel() priceRefreshTask = Task { [weak self] in "
        "while !Task.isCancelled { try? await Task.sleep(nanoseconds: 30_000_000_000) "
        "guard !Task.isCancelled else { break } guard let self = self else { break } "
        "let assets = self.trackedAssets "
        "if MarketHoursUtil.shouldRefreshPrices( assetTypes: assets.map(\\.assetType), "
        "symbols: assets.map(\\.ticker) ) { await self.loadTrackingFeed() } "
        "else if self.filteredAlerts.contains(where: \\.isLiveEarnings) { "
        "await self.refreshLiveEarningsCards() } } } }"
    )


_REFRESH_LIVE = "private func refreshLiveEarningsCards() async {"


def test_a_live_card_is_asked_about_through_its_own_route_alone():
    """The round outside price hours: each LIVE card on screen asks its own route (a memo read on
    the server). What the answer does is `liveCardStep`'s to say (pinned below): an answer that
    settles the card's own report is written the way the sheet's header takes it
    (`updated(with:)`), one the server could not settle yet leaves the card as it is, still
    LIVE, and a moved or unlisted report is the feed's to redraw. Never a feed build otherwise,
    but for a refusal that asking again cannot change (a 404 from a backend without the route, a
    400 for a date outside its window), where the feed says how the card stands. An answer that
    lands after an identity change, or after a feed answer replaced the list, writes nothing: the
    list may already hold a newer copy of the card. At most `maxLiveCardsPerRound` asks a round,
    more cards taking turns: the route shares the per-user limit of 60 a minute with every other
    screen, the sheet's poll included."""
    vm = _code(_TRACKING_VM)
    refresh = _norm(_body(vm, _REFRESH_LIVE))
    assert refresh == (
        "{ let generation = loadGeneration let revision = alertsRevision "
        "let reports: [LiveCardReport] = filteredAlerts.compactMap { Self.liveCardReport($0) } "
        "guard !reports.isEmpty else { return } "
        "let start: Int = liveCardCursor % reports.count "
        "let rotated: [LiveCardReport] = Array(reports[start...]) + Array(reports[..<start]) "
        "let batch: [LiveCardReport] = Array(rotated.prefix(Self.maxLiveCardsPerRound)) "
        "liveCardCursor = start + batch.count "
        "for report in batch { do { let answer: EarningsLiveResponse = try await apiClient.request( "
        "endpoint: .getEarningsLive(ticker: report.ticker, reportDate: report.reportDate), "
        "responseType: EarningsLiveResponse.self ) "
        "guard !Task.isCancelled, generation == loadGeneration, revision == alertsRevision else { return } "
        "switch Self.liveCardStep(answer, about: report) { "
        "case .write: guard let index = alerts.firstIndex(where: { $0.id == report.id }), "
        "case .earnings(let data) = alerts[index] else { continue } "
        "alerts[index] = .earnings(data.updated(with: answer)) "
        "case .keep: continue "
        "case .askTheFeed: logLiveCardHandedToTheFeed(answer, report: report) "
        "await loadTrackingFeed() return } "
        "} catch { "
        "guard !Task.isCancelled, generation == loadGeneration, revision == alertsRevision else { return } "
        "let appError = AppError.from(error) "
        "logLiveCardFailure(error, as: appError, report: report) "
        "if case .notFound = appError { await loadTrackingFeed() return } "
        "if case .validationFailed = appError { await loadTrackingFeed() return } } } }"
    )
    assert _norm(_body(vm, "private static func liveCardReport(_ alert: AppAlert) -> LiveCardReport? {")) == (
        "{ guard case .earnings(let data) = alert, data.status == .live, "
        "let date = data.reportDate, !date.isEmpty, !data.ticker.isEmpty else { return nil } "
        "return LiveCardReport(id: alert.id, ticker: data.ticker, reportDate: date) }"
    ), "only a LIVE card with a report date is asked about, by its own ticker and date"
    bound = re.search(r"private static let maxLiveCardsPerRound = (\d+)\b", vm)
    assert bound and 1 < int(bound.group(1)) <= 12, (
        "two rounds a minute must leave the sheet's poll and the other screens well inside 60"
    )
    for declared in ("private var alertsRevision = 0", "private var liveCardCursor = 0"):
        assert declared in vm, declared


def test_a_failed_live_card_ask_is_logged_with_its_report_and_the_raw_error():
    """The round runs where nobody watches it: the ticker, the report date, the mapped code and the
    raw error, all public, at `.error` for an answer that did not decode (backend↔iOS drift) and
    `.warning` for the handled rest."""
    vm = _code(_TRACKING_VM)
    helper = _body(vm, "private func logLiveCardFailure(_ error: Error, as appError: AppError, "
                       "report: LiveCardReport) {")
    for statement in ("let symbol: String = report.ticker", "let date: String = report.reportDate",
                      "let code: String = appError.analyticsCode",
                      'let detail: String = "\\(String(describing: type(of: error))): '
                      '\\(String(describing: error))"'):
        assert statement in helper, statement
    branch = helper.index("if let apiError = error as? APIError, case .decodingError = apiError {")
    assert (branch < helper.index("Self.log.error(") < helper.index("} else {")
            < helper.index("Self.log.warning(")), "a decode failure is the error; the rest warn"
    for level in ("Self.log.error(", "Self.log.warning("):
        message = _bracketed(helper, level, "(", ")")
        for value in ("symbol", "date", "code", "detail"):
            assert f"\\({value}, privacy: .public)" in message, f"{level} drops {value}"
        assert message.count("\\(") == message.count("privacy: .public)"), (
            f"{level} logs a value as <private>"
        )


_STEP = ("private static func liveCardStep(_ answer: EarningsLiveResponse, "
         "about report: LiveCardReport) -> LiveCardStep {")


def test_a_live_cards_answer_is_written_only_when_it_settles_its_report():
    """Which answers a LIVE card takes in the round, whole. A card is asked by its own date, so an
    answer about another date says the report moved (written in place, the card took the new date
    and with it the id of the card the feed already lists there: two cards with one id in
    `ForEach(alerts)`), and `none` says it left the calendar (written, the card said "no report in
    the next two weeks" under its own date, where the feed draws no card): both are the feed's to
    redraw. `live` and `reported` settle the report. Any other status that still asks to be asked
    again is a read the server could not settle yet, a restart whose first calendar read failed:
    written, it turned a card holding figures into "reports earnings … after market close" with no
    LIVE label, so nothing asked about it again. The port below is this function, case by case."""
    vm = _norm(_code(_TRACKING_VM))
    assert _norm(_body(vm, _STEP)) == (
        "{ let status: EarningsLiveStatus = answer.liveStatus "
        "if answer.reportDate != report.reportDate || status == .notScheduled { return .askTheFeed } "
        "if status == .live || status == .reported { return .write } "
        "if let seconds = answer.pollSeconds, seconds > 0 { return .keep } "
        "return .write }"
    )
    steps = _bracketed(vm, "private enum LiveCardStep")
    assert sorted(re.findall(r"\bcase (\w+)", steps)) == ["askTheFeed", "keep", "write"], steps

    asked = "2026-11-17"
    table = [
        (("live", asked, 30), "write"),
        (("reported", asked, None), "write"),
        (("reported", asked, 120), "write"),     # a preliminary figure FMP can still confirm
        (("awaiting", asked, None), "write"),    # 22:00 ET with nothing in
        (("upcoming", asked, None), "write"),    # the live loop off: the feed draws the same
        (("upcoming", asked, 30), "keep"),       # nobody has read the day yet
        (("awaiting", asked, 30), "keep"),
        (("awaiting", asked, 0), "write"),       # a zero asks nothing, as on the sheet
        (("upcoming", "2026-11-20", None), "askTheFeed"),
        (("live", "2026-11-20", 30), "askTheFeed"),
        (("none", asked, None), "askTheFeed"),
        (("NONE", asked, None), "askTheFeed"),   # read through `init(wire:)`, lower-cased
        (("unheard_of", asked, 30), "keep"),     # an unknown word reads as upcoming
    ]
    for (status, report_date, poll), step in table:
        answer = EarningsLiveResponse(ticker="XYZ", report_date=report_date, status=status,
                                      eps={}, revenue={}, poll_seconds=poll)
        assert _live_card_step(answer, asked) == step, (status, report_date, poll)


def _wire_status(word) -> str:
    """`EarningsLiveStatus(wire:)` by its raw value, as pinned in
    `test_the_status_decoder_falls_back_to_upcoming` and
    `test_each_status_decodes_from_the_backends_own_word`."""
    word = (word or "").lower()
    return word if word in ("upcoming", "live", "reported", "awaiting", "none") else "upcoming"


def _live_card_step(answer, asked_date: str) -> str:
    """`liveCardStep(_:about:)`, as pinned in
    `test_a_live_cards_answer_is_written_only_when_it_settles_its_report`."""
    status = _wire_status(answer.status)
    if answer.report_date != asked_date or status == "none":
        return "askTheFeed"
    if status in ("live", "reported"):
        return "write"
    if answer.poll_seconds is not None and answer.poll_seconds > 0:
        return "keep"
    return "write"


def test_a_card_handed_to_the_feed_is_logged_with_both_dates():
    """A hand-off pays for one feed build, so it says why where nobody watches the round: the
    ticker, the date asked, and the status and date answered, all public."""
    vm = _code(_TRACKING_VM)
    helper = _body(_norm(vm), "private func logLiveCardHandedToTheFeed(_ answer: EarningsLiveResponse, "
                              "report: LiveCardReport) {")
    for statement in ("let symbol: String = report.ticker", "let asked: String = report.reportDate",
                      "let status: String = answer.status",
                      "let answered: String = answer.reportDate"):
        assert statement in helper, statement
    message = _bracketed(helper, "Self.log.info(", "(", ")")
    for value in ("symbol", "asked", "status", "answered"):
        assert f"\\({value}, privacy: .public)" in message, f"the hand-off line drops {value}"
    assert message.count("\\(") == message.count("privacy: .public)"), "a value logged as <private>"
    assert helper.count("Self.log.") == 1


# ── The round, against the real route and feed ─────────────────────────────────

_ROUND_DAY = date(2026, 11, 17)                  # a Tuesday
_MOVED_TO = _ROUND_DAY + timedelta(days=3)


class _FrozenClock:
    """`datetime` for `tracking_service`: `now()` reads one instant, the rest is the real class."""

    def __init__(self, at: datetime):
        self._at = at

    def now(self, tz=None):
        return self._at.astimezone(tz) if tz else self._at.replace(tzinfo=None)

    def __getattr__(self, name):
        return getattr(datetime, name)

    def __call__(self, *args, **kwargs):
        return datetime(*args, **kwargs)


def _calendar_row(symbol: str, day: date, **figures) -> dict:
    row = {"symbol": symbol, "date": day.isoformat(), "epsEstimated": 0.41,
           "revenueEstimated": 16.6e9, "epsActual": None, "revenueActual": None,
           "companyName": f"{symbol} Corp", "time": "amc"}
    row.update(figures)
    return row


class _Calendar:
    """FMP's earnings calendar, asked one day at a time as the memo asks it; `down` is an outage."""

    def __init__(self):
        self.rows: list = []
        self.down = False

    async def get_earnings_calendar(self, from_date=None, to_date=None):
        if self.down:
            raise FMPUnavailableException("earnings-calendar: 503")
        return [dict(row) for row in self.rows if from_date <= row["date"] <= to_date]


def _card_id(card: dict) -> str:
    """`AppAlert.id`, the earnings arm."""
    return f"earnings_{card['ticker']}_{card['month']}{card['day']}"


def _card(alert) -> dict:
    """`AlertDTO.toAppAlert()`'s earnings arm (pinned in
    `test_the_feed_row_reaches_the_card_field_by_field`), on the fields the card's id, its LIVE
    label and its figures read."""
    return {"ticker": alert.ticker or "", "day": alert.day or 0, "month": alert.month or "",
            "status": _wire_status(alert.earnings_status), "report_date": alert.report_date,
            "eps_actual": alert.eps_actual, "revenue_actual": alert.revenue_actual}


def _updated(card: dict, answer) -> dict:
    """`AppAlert.EarningsData.updated(with:)` (pinned in
    `test_an_answer_about_another_report_never_repaints_this_ones_figures` and
    `test_the_header_is_rebuilt_from_every_field_it_reads`), on the same fields."""
    status = _wire_status(answer.status)
    same = answer.report_date == card["report_date"]
    if (card["eps_actual"] is not None or card["revenue_actual"] is not None) and (
            status == "none" or not same):
        return card
    if status == "none":
        return {**card, "status": "none"}
    day, month = card["day"], card["month"]
    if not same:
        _, number, day = (int(part) for part in answer.report_date.split("-"))
        month = calendar.month_abbr[number].upper()
    return {**card, "day": day, "month": month, "status": status,
            "report_date": answer.report_date, "eps_actual": answer.eps.actual,
            "revenue_actual": answer.revenue.actual}


class _Round:
    """The real Tracking memo, live records, feed builder and route over one fake calendar, with
    New York's clock in the test's hands: what the card and its own route say at each moment."""

    def __init__(self, monkeypatch):
        import app.services.tracking_service as ts

        self._monkeypatch = monkeypatch
        self.calendar = _Calendar()
        self.service = ts.TrackingService.__new__(ts.TrackingService)
        self.service.fmp = self.calendar

    def at(self, hour: int, minute: int = 0) -> None:
        import app.services.tracking_service as ts

        at = datetime(_ROUND_DAY.year, _ROUND_DAY.month, _ROUND_DAY.day, hour, minute, tzinfo=ET)
        self._monkeypatch.setattr(ts, "datetime", _FrozenClock(at))

    async def cards(self, ticker: str) -> list:
        return [_card(alert) for alert in await self.service._get_earnings_alerts([ticker])]

    async def ask(self, card: dict):
        return await self.service.get_earnings_live(card["ticker"],
                                                    date.fromisoformat(card["report_date"]))

    @staticmethod
    def forget() -> None:
        """A restart, or the loop ending a record FMP stopped listing, and the memo's next
        round: no record left, and the next read asks FMP again."""
        import app.services.earnings_live_service as els
        import app.services.tracking_service as ts

        els.get_earnings_live_service().reset()
        ts.reset_earnings_calendar_cache()

    @staticmethod
    def next_memo_round() -> None:
        """The memo's next round: the next read asks FMP again, the records stay."""
        import app.services.tracking_service as ts

        ts.reset_earnings_calendar_cache()


@pytest.fixture
def live_round(monkeypatch):
    import app.services.earnings_live_service as els
    import app.services.tracking_service as ts
    from app.config import settings

    async def _no_extras(self, symbol, report_day, *, loader=None):
        return None

    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    monkeypatch.setattr(ts, "_et_today", lambda: _ROUND_DAY)
    monkeypatch.setattr(els.EarningsLiveService, "history_extras", _no_extras)
    _Round.forget()
    yield _Round(monkeypatch)
    _Round.forget()


@pytest.mark.asyncio
async def test_a_report_moved_under_a_live_card_is_redrawn_by_the_feed(live_round):
    """FMP lists XYZ today (a stale row) beside its new date, so the last feed of the evening
    draws two cards, LIVE today and upcoming three days out (a recorded residual). FMP then drops
    the stale row, the loop ends its record, and the LIVE card's own route answers the new date.
    Written in place, that answer moved the card onto the date the feed already lists: two cards
    with one id, neither LIVE, so nothing asked about either until the next morning's feed. The
    round hands it to the feed, which draws the one card it lists."""
    world = live_round
    world.calendar.rows = [_calendar_row("XYZ", _ROUND_DAY), _calendar_row("XYZ", _MOVED_TO),
                           _calendar_row("ZZZZ", _ROUND_DAY)]
    world.at(19, 59)
    card, later = await world.cards("XYZ")
    assert (card["status"], later["status"]) == ("live", "upcoming")

    world.calendar.rows = [_calendar_row("XYZ", _MOVED_TO), _calendar_row("ZZZZ", _ROUND_DAY)]
    world.forget()
    world.at(20, 30)
    answer = await world.ask(card)
    assert (answer.status, answer.report_date) == ("upcoming", _MOVED_TO.isoformat())
    assert _card_id(_updated(card, answer)) == _card_id(later), "the hazard this test is about"
    assert _live_card_step(answer, card["report_date"]) == "askTheFeed"
    assert [_card_id(c) for c in await world.cards("XYZ")] == [_card_id(later)]


@pytest.mark.asyncio
async def test_a_report_gone_from_the_calendar_is_redrawn_by_the_feed(live_round):
    """FMP moves XYZ past the two-week window: the route answers `none` for the card's own date.
    Written in place, the card said "XYZ has no earnings report in the next two weeks." beside a
    date badge for today, where the feed draws no card at all."""
    world = live_round
    world.calendar.rows = [_calendar_row("XYZ", _ROUND_DAY), _calendar_row("ZZZZ", _ROUND_DAY)]
    world.at(19, 59)
    (card,) = await world.cards("XYZ")
    assert card["status"] == "live"

    world.calendar.rows = [_calendar_row("XYZ", _ROUND_DAY + timedelta(days=30)),
                           _calendar_row("ZZZZ", _ROUND_DAY)]
    world.forget()
    world.at(20, 30)
    answer = await world.ask(card)
    assert (answer.status, answer.report_date) == ("none", _ROUND_DAY.isoformat())
    assert _updated(card, answer)["status"] == "none", "the hazard this test is about"
    assert _live_card_step(answer, card["report_date"]) == "askTheFeed"
    assert await world.cards("XYZ") == []


@pytest.mark.asyncio
async def test_an_answer_the_server_could_not_settle_leaves_a_live_card_as_it_is(live_round):
    """A restart after 20:00 ET whose first calendar read fails (an FMP outage): the route cannot
    tell, and answers `upcoming` for the card's own date, with no figures and a 30 s recheck.
    Written in place, a card holding its revenue read "XYZ reports earnings on NOV 17 after market
    close." with no LIVE label, so nothing asked about it again; the feed, asked instead, draws no
    card at all in an outage. The round leaves the card as it is, still LIVE, and the next answer
    once FMP is back is written."""
    world = live_round
    world.calendar.rows = [_calendar_row("XYZ", _ROUND_DAY, revenueActual=17.1e9),
                           _calendar_row("ZZZZ", _ROUND_DAY)]
    world.at(20, 0)
    (card,) = await world.cards("XYZ")
    assert (card["status"], card["revenue_actual"]) == ("live", 17.1e9)

    world.forget()
    world.calendar.down = True
    world.at(20, 30)
    answer = await world.ask(card)
    assert (answer.status, answer.report_date, answer.poll_seconds, answer.revenue.actual) == (
        "upcoming", _ROUND_DAY.isoformat(), 30, None)
    assert _updated(card, answer)["status"] == "upcoming", "the hazard this test is about"
    assert _live_card_step(answer, card["report_date"]) == "keep"
    assert await world.cards("XYZ") == [], "the feed draws nothing in the outage"

    world.calendar.down = False
    world.next_memo_round()      # past the memo's wait after a failed round
    world.at(20, 31)
    answer = await world.ask(card)
    assert (answer.status, answer.revenue.actual) == ("live", 17.1e9)
    assert _live_card_step(answer, card["report_date"]) == "write"


@pytest.mark.asyncio
@pytest.mark.parametrize("before,after,asked_at,status", [
    ({"revenueActual": 17.1e9}, None, (20, 30), "live"),
    ({"revenueActual": 17.1e9}, {"revenueActual": 17.1e9, "epsActual": 0.44}, (20, 30), "reported"),
    ({}, None, (22, 30), "awaiting"),
], ids=["still_live", "complete", "the_window_closed_with_nothing_in"])
async def test_an_answer_that_settles_the_cards_own_report_is_written_as_the_feed_draws_it(
        live_round, before, after, asked_at, status):
    """The card takes the answer for its own report, with the id it had, and reads what a feed
    build at the same moment draws: its status (LIVE goes when the server stops saying it, and
    with it the round's asks) and its figures."""
    world = live_round
    world.calendar.rows = [_calendar_row("XYZ", _ROUND_DAY, **before),
                           _calendar_row("ZZZZ", _ROUND_DAY)]
    world.at(19, 59)
    (card,) = await world.cards("XYZ")
    assert card["status"] == "live"
    if after is not None:
        world.calendar.rows = [_calendar_row("XYZ", _ROUND_DAY, **after),
                               _calendar_row("ZZZZ", _ROUND_DAY)]
        world.next_memo_round()
    world.at(*asked_at)
    answer = await world.ask(card)
    assert (answer.status, answer.report_date) == (status, _ROUND_DAY.isoformat())
    assert _live_card_step(answer, card["report_date"]) == "write"
    written = _updated(card, answer)
    (drawn,) = await world.cards("XYZ")
    assert _card_id(written) == _card_id(card)
    assert written == drawn


@pytest.mark.asyncio
async def test_asking_a_live_cards_route_again_costs_no_fmp_call(monkeypatch):
    """What makes the poll's round cheap: the route reads the shared Tracking memo and the live
    record, so asking again inside the memo's own pace makes no FMP call at all, whatever the user
    holds. Asked as the round asks: after 20:00 ET on the report day, about a report the server
    calls LIVE, and every answer the same as the first. The fake FMP client offers the calendar
    alone, so any other call fails the test."""
    import app.services.earnings_live_service as els
    import app.services.tracking_service as ts

    day = date(2026, 11, 17)
    calls: list = []

    class _CalendarOnly:
        async def get_earnings_calendar(self, from_date=None, to_date=None):
            calls.append((from_date, to_date))
            row = {"symbol": "INTC", "date": day.isoformat(), "epsEstimated": 0.41,
                   "revenueEstimated": 16.6e9, "epsActual": None, "revenueActual": 17.1e9,
                   "companyName": "Intel Corporation"}
            return [row] if from_date <= row["date"] <= to_date else []

    async def _no_extras(self, symbol, report_day, *, loader=None):
        return None

    # One client, as in production (`get_fmp_client` is a singleton): the memo is the client's.
    fmp = _CalendarOnly()
    monkeypatch.setattr(ts, "_et_today", lambda: day)
    monkeypatch.setattr(ts, "datetime", _FrozenClock(datetime(2026, 11, 17, 20, 30, tzinfo=ET)))
    monkeypatch.setattr(ts, "get_fmp_client", lambda: fmp)
    monkeypatch.setattr(els.EarningsLiveService, "history_extras", _no_extras)
    els.get_earnings_live_service().reset()
    ts.reset_earnings_calendar_cache()
    try:
        first = await ts.TrackingService().get_earnings_live("INTC", day)
        assert (first.status, first.report_date, first.poll_seconds) == (
            "live", day.isoformat(), 30), "the round asks only about a LIVE card"
        assert (first.company_name, first.revenue.actual) == ("Intel Corporation", 17.1e9)
        warmed = len(calls)
        assert warmed >= 1, "the first ask reads the memo"
        for _ in range(10):
            again = await ts.TrackingService().get_earnings_live("INTC", day)
            assert again == first
        assert len(calls) == warmed, f"asking again called FMP {len(calls) - warmed} more times"
    finally:
        els.get_earnings_live_service().reset()
        ts.reset_earnings_calendar_cache()


_SHOULD_REFRESH = ("static func shouldRefreshPrices(assetTypes: [String], "
                   "symbols: [String] = []) -> Bool {")
_AROUND_THE_CLOCK = "nonisolated static func symbolTradesAroundTheClock(_ symbol: String) -> Bool {"


def _swift_string_set(code: str, declaration: str) -> set:
    """The quoted members of the `Set<String>` literal that `declaration` opens."""
    return set(re.findall(r'"([^"]*)"', _bracketed(code, declaration, "[", "]")))


def test_the_feed_is_built_outside_the_session_only_for_a_holding_that_trades_then():
    """The bound on the Tracking poll's cost rests here: the whole feed, which pays FMP for every
    holding, is built every 30 s only while this says a price can move, and a LIVE card is asked
    about through its own route otherwise. A `return true` here, or "commodity" back in the set
    (any GLD holder), or a looser coin rule (a bare BTC, the listed ETF), rebuilds the whole feed
    every 30 s all evening and all weekend, every guard on the poll's call still green. Pinned
    whole, and the port below held to the backend's own rule (`asset_class`), which the Swift
    mirrors: crypto alone trades around the clock, by its declared type or its pair form.
    `isMarketActive` is pinned in `test_ios_market_times_eastern.py`."""
    from app.services.asset_class import _ROUND_THE_CLOCK, detect_asset_class, trades_extended_hours

    hours = _code(_HOURS)
    should = _norm(_body(hours, _SHOULD_REFRESH))
    assert should == (
        "{ if isMarketActive() { return true } "
        'let roundTheClock: Set<String> = ["crypto"] '
        "if assetTypes.contains(where: { roundTheClock.contains($0.lowercased()) }) { return true } "
        "return symbols.contains(where: symbolTradesAroundTheClock) }"
    )
    assert _norm(_body(hours, _AROUND_THE_CLOCK)) == (
        "{ let sid = symbol.trimmingCharacters(in: .whitespaces).uppercased() "
        'guard !sid.isEmpty, !sid.hasPrefix("^") else { return false } '
        "if commoditySymbols.contains(sid) { return false } "
        'if sid.count > 3 && (sid.hasSuffix("USD") || sid.hasSuffix("USDT")) { return true } '
        "return false }"
    )
    round_the_clock = _swift_string_set(should, "let roundTheClock: Set<String> =")
    commodities = _swift_string_set(hours, "nonisolated static let commoditySymbols: Set<String> =")
    assert round_the_clock == set(_ROUND_THE_CLOCK)

    def trades_around_the_clock(symbol: str) -> bool:
        sid = symbol.strip(" \t").upper()
        if not sid or sid.startswith("^") or sid in commodities:
            return False
        return len(sid) > 3 and (sid.endswith("USD") or sid.endswith("USDT"))

    def outside_the_session(types: list, symbols: list) -> bool:
        return (any(t.lower() in round_the_clock for t in types)
                or any(trades_around_the_clock(s) for s in symbols))

    for symbol in ("AAPL", "BRK-B", "GLD", "BTCUSD", "ethusdt", " SOLUSD ", "GCUSD", "CLUSD",
                   "BTC", "LTC", "USD", "^GSPC", ""):
        assert trades_around_the_clock(symbol) == (detect_asset_class(symbol) == "crypto"), symbol
    for declared in ("Stock", "crypto", "CRYPTO", "commodity", "etf", "index", ""):
        assert (declared.lower() in round_the_clock) == trades_extended_hours(declared), declared
    assert not outside_the_session(["Stock"] * 3, ["AAPL", "MSFT", "BRK-B"]), "an equity portfolio"
    assert not outside_the_session(["etf", "commodity"], ["GLD", "GCUSD"]), "equity hours, or daily"
    assert not outside_the_session(["Stock"], ["BTC"]), "a bare coin ticker is the listed ETF"
    assert not outside_the_session([], [])
    assert outside_the_session(["Stock"], ["AAPL", "BTCUSD"]), "a coin pair under the column default"
    assert outside_the_session(["crypto"], ["IBIT"]), "a declared crypto holding"


def test_a_result_row_is_hidden_only_for_the_same_report_on_screen():
    """Ticker AND report date: last quarter's result row stays while next quarter's card is
    up, and a row without a report date (sent before the route carried it) is never hidden.
    Only behind a LIVE card, which the poll keeps within 30 s of the server, or one showing its
    final result: reported, EPS in, revenue in unless no consensus says one is coming, none of it
    from the company's release still waiting for FMP. Nothing else refreshes the feed outside
    market hours, so any other card can be the one loaded before the report or before its last
    figure ("EPS not out yet"), and the row is then the only copy on screen of what it says."""
    tab = _code("Views/Organisms/AlertsTabContent.swift")
    body = tab[tab.index("private func duplicatesLiveResultCard("):]
    body = body[: body.index("\n    }")]
    assert "AlertDestination.isEarningsResult(item.kind)" in body
    assert 'let date = item.route["report_date"], !date.isEmpty else { return false }' in body
    assert 'resultCardReports.contains("\\(ticker)|\\(date)")' in body
    section = tab[tab.index("private func activitySection()"):]
    assert "&& !duplicatesLiveResultCard($0, resultCardReports: resultCardReports)" in section
    assert "compactMap(resultCardReport)" in _body(tab, "private func activitySection() -> some View {")
    report = _norm(_body(tab, "private func resultCardReport(_ alert: AppAlert) -> String? {"))
    assert ("guard case .earnings(let data) = alert, data.status == .live || showsFinalResult(data), "
            "let date = data.reportDate, !date.isEmpty, !data.ticker.isEmpty else { return nil }"
            ) in report, "a result row hides behind a card that may not show its result"
    final = _norm(_body(tab, "private func showsFinalResult(_ data: AppAlert.EarningsData) -> Bool {"))
    assert final == (
        "{ guard data.status == .reported, !data.isPreliminary, data.epsActual != nil else "
        "{ return false } if data.revenueActual != nil { return true } "
        "return data.revenueEstimate == nil && !data.revenueChecking }"
    ), "a card stands in for the result before the result is final"
    upcoming = tab[tab.index("private func duplicatesUpcomingCard("):]
    upcoming = upcoming[: upcoming.index("\n    }")]
    assert "isEarningsResult" not in upcoming, "the upcoming rule must stay ticker-scoped"


def test_a_card_stands_in_for_a_result_row_once_the_backend_calls_its_result_final():
    """The rule pinned above, read field by field as `AlertDTO.toAppAlert` maps the wire, over
    the cards the backend builds (`card_fields`, with the estimates `_get_earnings_alerts` prints
    beside them): a reported card stands in exactly when the backend calls the report complete
    on FMP's own figures, and a LIVE card always does. Not here: a revenue consensus only the
    guard reads (`due` with no estimate). The card carries no `due` and names no revenue then,
    so it reads as none coming, as its own sentence does."""
    from app.services.earnings_live_service import (
        LiveMetric, LiveSnapshot, card_fields, is_complete, is_preliminary,
    )

    def stands_in(card: dict) -> bool:
        status = str(card.get("earnings_status") or "").lower()
        if status == "live":
            return True
        if status != "reported" or card.get("results_preliminary") or card.get("eps_actual") is None:
            return False
        if card.get("revenue_actual") is not None:
            return True
        return card.get("revenue_estimate") is None and not card.get("revenue_checking")

    day = date(2026, 11, 17)
    eps_in = LiveMetric(0.41, 0.60, 46.3, "fmp", "adjusted")
    revenue_in = LiveMetric(16.6e9, 17.1e9, 3.0, "fmp")
    filed = LiveMetric(16.6e9, 17.1e9, 3.0, "filing")
    cases = {
        "both in, FMP's": ("reported", eps_in, revenue_in, True),
        "EPS in, no revenue consensus": ("reported", eps_in, LiveMetric(None), True),
        "EPS in, revenue due": ("reported", eps_in, LiveMetric(16.6e9, due=True), False),
        "EPS in, revenue held": ("reported", eps_in, LiveMetric(16.6e9, checking=True), False),
        "EPS in, revenue held, its consensus out of date": (
            "reported", eps_in, LiveMetric(None, checking=True), False),
        "EPS due, revenue the release's": ("reported", LiveMetric(0.41, due=True), filed, False),
        "both in, revenue the release's": ("reported", eps_in, filed, False),
        "GAAP EPS, the release's": ("reported", LiveMetric(None, 0.52, None, "filing", "gaap"),
                                    LiveMetric(None), False),
        "EPS held": ("reported", LiveMetric(0.41, checking=True), revenue_in, False),
        "live, nothing in": ("live", LiveMetric(0.41, due=True), LiveMetric(16.6e9, due=True), True),
        "awaiting, EPS held": ("awaiting", LiveMetric(0.41, checking=True), LiveMetric(None), False),
    }
    for name, (status, eps, revenue, expected) in cases.items():
        snap = LiveSnapshot(symbol="XYZ", day=day, status=status, company_name=None, eps=eps,
                            revenue=revenue, preliminary=is_preliminary(eps, revenue),
                            checked_at=None, filing=None)
        card = {**card_fields(snap, day), "eps_estimate": eps.estimate,
                "revenue_estimate": revenue.estimate}
        assert stands_in(card) is expected, name
        if status == "reported":
            assert expected is (is_complete(eps, revenue) and not is_preliminary(eps, revenue)), name
    assert stands_in(card_fields(None, day)) is False, "a card with no record yet is upcoming"


def test_the_outlook_is_never_cut_on_the_card():
    """The server prints the company's sentence whole, up to the 400 characters a quote may be, and
    refuses any quote it would have to shorten. A line limit here would undo that, and a limit on
    the card's body reaches every text inside it."""
    src = _code("Views/Organisms/EarningsLiveResultsCard.swift")
    block = _bracketed(src, "func guidanceBlock")
    assert "Text(guidance)" in block
    assert ".fixedSize(horizontal: false, vertical: true)" in block
    for cut in (".lineLimit", ".truncationMode", ".prefix("):
        assert cut not in block
        assert cut not in _bracketed(src, "var body: some View")


def test_every_file_using_published_imports_combine():
    """`SWIFT_UPCOMING_FEATURE_MEMBER_IMPORT_VISIBILITY` is on, so a file cannot lean on the
    Combine that `import SwiftUI` drags in: `@Published` / `ObservableObject` need their own
    `import Combine`. EarningsLiveViewModel shipped without it, written without a compiler."""
    project = _IOS.parent / "ios.xcodeproj/project.pbxproj"
    assert "SWIFT_UPCOMING_FEATURE_MEMBER_IMPORT_VISIBILITY = YES" in project.read_text(
        encoding="utf-8"
    ), "the build setting that makes this guard necessary is gone: retire the guard with it"
    missing = []
    scanned = 0
    for path in sorted(_IOS.parent.rglob("*.swift")):
        code = _code(str(path.relative_to(_IOS))) if _IOS in path.parents else None
        if code is None:
            src = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
            code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))
        if not re.search(r"@Published\b|\bObservableObject\b", code):
            continue
        scanned += 1
        if not re.search(r"^\s*(?:@\w+\s+)?import\s+Combine\b", code, re.M):
            missing.append(str(path.relative_to(_IOS.parent)))
    assert scanned >= 40, f"the scan found only {scanned} files: it is not reading the tree"
    assert not missing, f"these files use @Published/ObservableObject with no import Combine: {missing}"


# ── the route ────────────────────────────────────────────────────────────────


_FLOOR = "nonisolated static func liveResultsFloorKey(at instant: Date = Date()) -> String {"


def test_live_results_are_offered_only_while_the_endpoint_serves_the_report():
    """The endpoint answers 400 for a date before the first of its `sheet_days`, two trading days
    back, and a row with no date is never the report it is about, so both keep Open and See the
    numbers only."""
    dest = _code(_DESTINATIONS)
    arm = dest[dest.index("if isEarningsResult(item.kind)"): dest.index('label: "See live results"')]
    assert 'let reportDate = route["report_date"]' in arm
    assert "reportDate >= MarketHoursUtil.liveResultsFloorKey()" in arm
    assert ".flatMap" not in arm, "a row with no date may not reach the screen"
    assert ".earningsLive(ticker: symbol, reportDate: reportDate)" in dest

    helper = _body(_code(_HOURS), _FLOOR)
    assert "let calendar: Calendar = etCalendar" in helper, (
        "the floor is the date in New York, not on the device"
    )
    # Whole statements: the first of the live days, then the trading day before it. One walk
    # fewer hides the day a held result wakes on; one more offers a date the endpoint refuses.
    assert "let today: Date = calendar.startOfDay(for: instant)" in helper
    assert "let firstLiveDay: Date = previousTradingDay(before: today, calendar: calendar)" in helper
    assert "let day: Date = previousTradingDay(before: firstLiveDay, calendar: calendar)" in helper
    assert helper.count("previousTradingDay(") == 2, "a walk was added or dropped"
    assert "calendar.dateComponents([.year, .month, .day], from: day)" in helper
    assert ('return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, '
            'parts.day ?? 0)') in helper


def test_both_result_kinds_open_their_live_results():
    """`isEarningsResult` decides which rows offer "See live results" (the only way into
    `EarningsLiveScreen` from the inbox and a tapped push) and which hide behind their card. Read
    from the backend's own kinds, so a kind renamed on either side fails here: without
    `earnings_live`, a Live Results row loses the screen its push is about."""
    from app.services.notification_kinds import KIND_EARNINGS_LIVE, KIND_EARNINGS_RESULT

    assert _norm(_body(_code(_DESTINATIONS), "static func isEarningsResult(_ kind: String) -> Bool {")) == (
        f'{{ kind == "{KIND_EARNINGS_RESULT}" || kind == "{KIND_EARNINGS_LIVE}" }}'
    )


def test_the_route_floor_is_the_first_day_the_endpoint_serves():
    """iOS may hide a date the server still serves, but it must never offer one it refuses, and
    it must offer the day a held result wakes on: the first of the backend's `sheet_days`. The
    helper the route really calls, its walks traced through its own statements and run over the
    Swift holiday table, against the backend's own functions."""
    from app.services.earnings_live_service import sheet_days

    called = re.search(r"reportDate >= MarketHoursUtil\.(\w+)\(\)", _code(_DESTINATIONS))
    assert called, "the route's floor is no longer a MarketHoursUtil call"
    hours = _code(_HOURS)
    helper = _norm(_body(
        hours, f"nonisolated static func {called.group(1)}(at instant: Date = Date()) -> String {{"
    ))
    table = hours[hours.index("holidays: Set<String> = ["):]
    table = table[: table.index("]")]
    holidays = set(re.findall(r'"(\d{4}-\d{2}-\d{2})"', table))
    assert len(holidays) >= 30, "the holiday table was not read"

    def walks_back(expr: str, traced: dict) -> int:
        """Trading days between New York's today and the day `expr` names."""
        expr = expr.strip()
        if expr == "calendar.startOfDay(for: instant)":
            return 0
        if expr in traced:
            return traced[expr]
        inner = re.fullmatch(r"previousTradingDay\(before: (.+), calendar: calendar\)", expr)
        assert inner, f"the floor helper names a day this guard cannot trace: {expr!r}"
        return walks_back(inner.group(1), traced) + 1

    traced: dict = {}
    for name, expr in re.findall(r"let (\w+): Date = (.+?)(?= let | return )", helper):
        traced[name] = walks_back(expr, traced)
    assert "calendar.dateComponents([.year, .month, .day], from: day)" in helper
    assert "day" in traced, "the day the helper formats was not traced back to today"

    def swift_walk(day: date) -> date:
        probe = day
        for _ in range(10):
            probe -= timedelta(days=1)
            if probe.weekday() < 5 and probe.isoformat() not in holidays:
                break
        return probe

    day = date(2025, 1, 2)
    while day <= date(2028, 12, 31):
        assert swift_walk(day) == previous_trading_day(day), day.isoformat()
        floor = day
        for _ in range(traced["day"]):
            floor = swift_walk(floor)
        served = sheet_days(day)[0]
        assert floor == served, (
            f"on {day.isoformat()} iOS offers live results from {floor.isoformat()}, "
            f"the endpoint serves them from {served.isoformat()}"
        )
        day += timedelta(days=1)


# ── the sheet's retry ────────────────────────────────────────────────────────


def test_a_failed_first_load_keeps_trying_and_the_sheet_says_so():
    vm = _code(_VM)
    start = _body(vm, "func start() {")
    assert "self.live?.pollSeconds" not in start, "a failed first load ends the poll again"
    next_poll = _body(vm, "private var nextPollSeconds: Int? {")
    assert next_poll.index("if let live") < next_poll.index("Self.noAnswerRetrySeconds"), (
        "a real answer must rule before the retry is even considered"
    )
    assert "private static let noAnswerRetrySeconds = 30" in vm
    assert "fallback" not in next_poll.lower(), (
        "a status guessed from the Tracking card would poll a finished report for ever"
    )
    load = _body(vm, "private func load() async {")
    answered = load[: load.index("} catch {")]
    assert "live = response" in answered, "the card reads nothing but `live`"
    assert "loadFailed = false" in answered, (
        "a retry that recovers leaves the failure notice under the real answer"
    )
    card = _code(_CARD)
    body = _body(card, "var body: some View {")
    notice = _body(body, "if viewModel.loadFailed {")
    assert "InlineRetryNotice(" in notice
    assert "onRetry: { viewModel.start() }" in notice, "Try Again must ask again"
    assert "viewModel.loadFailed && fallback == nil" not in card, (
        "the sheet always has a fallback, so this hides the notice from it"
    )


def test_the_retry_is_bounded_and_a_refusal_ends_the_poll_at_once():
    """A report_date ages out under an open sheet, and the 400 it then gets is permanent."""
    vm = _code(_VM)
    next_poll = _body(vm, "private var nextPollSeconds: Int? {")
    assert "guard loadFailed, failedLoads < Self.maxFailedLoads else { return nil }" in next_poll
    bound = re.search(r"private static let maxFailedLoads = (\d+)\b", vm)
    assert bound and 1 < int(bound.group(1)) <= 40, (
        "a bound of minutes, not one retry and not years at 30 s a try"
    )
    assert next_poll.index("if refused { return nil }") < next_poll.index("if let live"), (
        "a refusal must also end a poll running over a shown answer"
    )
    load = _body(vm, "private func load() async {")
    assert "if case .validationFailed = appError { refused = true }" in load
    assert "if live == nil { failedLoads += 1 }" in load
    assert "loadFailed = live == nil || refused" in load
    start = _body(vm, "func start() {")
    for reset in ("loadFailed = false", "failedLoads = 0", "refused = false"):
        assert start.index(reset) < start.index("pollTask = Task"), (
            f"Try Again must start clean: {reset}"
        )


def test_a_cancelled_load_is_dropped_before_the_error_is_mapped():
    load = _body(_code(_VM), "private func load() async {")
    catch = load[load.index("} catch {"):]
    assert catch.index("guard !Task.isCancelled else { return }") < catch.index("AppError.from(error)")
    assert "isCancellation" not in load


def test_a_backend_without_the_route_ends_the_poll_at_once():
    """An older backend (after a rollback) has no live route and answers 404 to every ask, so the
    bounded retry would spend ten minutes on requests that cannot succeed. `APIClient` turns a
    404 whose body is not the error contract (FastAPI's `{"detail": "Not Found"}`) into
    `APIError.notFound`, and `AppError` keeps it `.notFound`: the case the poll ends on, with the
    notice's Try Again as the way back. The whole arm is pinned: anything read between the
    contract and that throw (the `detail` fallback the 400 arm has, say) takes a plain 404 to
    another case, and the sheet asks again."""
    validate = _body(_code("Core/Services/APIClient.swift"),
                     "private func validateResponse(_ response: HTTPURLResponse, data: Data) throws {")
    not_found = _norm(validate[validate.index("case 404:"): validate.index("case 422:")]).strip()
    assert not_found == (
        "case 404: if let errorResponse = try? decoder.decode(APIErrorResponse.self, from: data) "
        "{ throw APIError.businessError( code: errorResponse.errorCode, "
        "message: errorResponse.userMessage ) } throw APIError.notFound"
    ), "a 404 that is not the error contract must reach APIError.notFound, and nothing else first"
    mapping = _norm(_body(_code("Core/Utilities/AppError.swift"),
                          "private static func mapAPIError(_ error: APIError) -> AppError {"))
    assert 'case .notFound: return .notFound(resource: "item")' in mapping
    load = _body(_code(_VM), "private func load() async {")
    assert "if case .notFound = appError { refused = true }" in load
    assert load.index("let appError = AppError.from(error)") < load.index("if case .notFound")


def test_a_failed_load_is_logged_with_its_report_and_the_raw_error():
    """A decode failure maps to a generic `.unknown` ("Something Went Wrong"), so the mapped error
    alone cannot tell contract drift from an outage, and print() never reaches a Release build's
    log. One os.Logger line per failure: the ticker, the report date, the mapped code and the raw
    error, all public (a `<private>` line says nothing), at `.error` for an answer that did not
    decode and `.warning` for the handled rest."""
    vm = _code(_VM)
    assert re.search(r"^import os$", vm, re.M), "Logger needs its own import"
    assert ('nonisolated private static let log = Logger(subsystem: "com.phan.caydex", '
            'category: "earnings-live")') in vm
    assert "print(" not in vm, "print() never reaches a Release build's log"
    load = _body(vm, "private func load() async {")
    catch = load[load.index("} catch {"):]
    assert catch.index("let appError = AppError.from(error)") < catch.index(
        "logFailure(error, as: appError)"
    )
    helper = _body(vm, "private func logFailure(_ error: Error, as appError: AppError) {")
    assert "let symbol: String = ticker" in helper
    assert 'let date: String = reportDate ?? "none"' in helper
    assert "let code: String = appError.analyticsCode" in helper
    assert ('let detail: String = "\\(String(describing: type(of: error))): '
            '\\(String(describing: error))"') in helper
    branch = helper.index("if let apiError = error as? APIError, case .decodingError = apiError {")
    assert (branch < helper.index("Self.log.error(") < helper.index("} else {")
            < helper.index("Self.log.warning(")), "a decode failure is the error; the rest warn"
    for level in ("Self.log.error(", "Self.log.warning("):
        message = _bracketed(helper, level, "(", ")")
        for value in ("symbol", "date", "code", "detail"):
            assert f"\\({value}, privacy: .public)" in message, f"{level} drops {value}"
        assert message.count("\\(") == message.count("privacy: .public)"), (
            f"{level} logs a value as <private>"
        )


# ── the sheet's header ───────────────────────────────────────────────────────


def test_the_sheet_header_reads_the_cards_own_answer():
    detail = _code(_DETAIL)
    body = _body(detail, "var body: some View {")
    assert "Text(alert.title)" not in body and "Text(alert.description)" not in body
    assert body.count("Text(headerAlert.title)") == 2
    assert body.count("Text(headerAlert.description)") == 1
    # The whole getter, down to what it returns: the answer written onto a local copy reaches
    # nothing unless that copy is what the sheet draws.
    assert _norm(_body(detail, "private var headerAlert: AppAlert {")) == (
        "{ guard case .earnings(var data) = alert else { return alert } "
        "if let liveAnswer { data = data.updated(with: liveAnswer) } "
        "data.liveCheckEnded = liveCheckEnded "
        "return .earnings(data) }"
    )
    assert "switch headerAlert {" in _body(detail, "private var detailContent: some View {"), (
        "the Date and Consensus rows must read the same answer as the header above them"
    )
    assert "onAnswer: { liveAnswer = $0 }" in detail

    card = _code(_CARD)
    hook = card[card.index(".onChange(of: viewModel.live)"):]
    hook = hook[: hook.index("\n        }")]
    assert "if let answer { onAnswer?(answer) }" in hook


def test_the_header_is_rebuilt_from_every_field_it_reads():
    tracking = _code(_TRACKING_MODELS)
    read = set(re.findall(r"\bdata\.(\w+)", (
        _body(tracking, "static func earningsDescription(_ data: EarningsData) -> String {")
        + _body(tracking, "static func earningsResultsLine(_ data: EarningsData) -> String {")
    )))
    assert {"status", "epsActual", "revenueActual", "consensus", "reportTime"} <= read, read
    derived = {
        "formattedMonth": ("month",), "formattedDay": ("day",),
        "hasResults": ("epsActual", "revenueActual"),
        # What each verdict reads (`test_each_metric_reads_its_own_figures`).
        "epsOutcome": ("epsEstimate", "epsActual", "epsSurprisePct", "isPreliminary",
                       "epsIsGAAP", "epsChecking"),
        "revenueOutcome": ("revenueEstimate", "revenueActual", "revenueSurprisePct",
                           "isPreliminary", "revenueChecking"),
    }
    # `liveCheckEnded` is no answer's to give: the sheet sets it on this copy (`headerAlert`).
    fields = {field for name in read for field in derived.get(name, (name,))} - {"liveCheckEnded"}
    updated = _norm(_body(_code(_MODELS), _UPDATED))
    missing = sorted(f for f in fields if f"{f}:" not in updated)
    assert not missing, f"updated(with:) leaves the tap's copy of {missing}"
    assert "let answer: EarningsLiveStatus = live.liveStatus" in updated
    assert "let eps: EarningsMetricOutcome = live.epsOutcome" in updated
    assert "let revenue: EarningsMetricOutcome = live.revenueOutcome" in updated
    # The whole call, never label-and-value pairs: `epsEstimate: eps.estimate,` also matches the
    # `consensusText(...)` argument just above it, so a pair cannot see a stale estimate.
    assert updated[updated.index("return AppAlert.EarningsData("):] == (
        "return AppAlert.EarningsData( ticker: ticker, "
        "companyName: live.companyName ?? companyName, "
        "reportTime: sameReport ? reportTime : nil, "
        "consensus: Self.consensusText(epsEstimate: eps.estimate, revenueEstimate: revenue.estimate), "
        "day: shownDay, month: shownMonth, status: answer, reportDate: live.reportDate, "
        "epsEstimate: eps.estimate, revenueEstimate: revenue.estimate, "
        "epsActual: eps.actual, revenueActual: revenue.actual, "
        "epsSurprisePct: eps.surprisePct, revenueSurprisePct: revenue.surprisePct, "
        "epsIsGAAP: eps.isGAAP, isPreliminary: live.preliminary ?? isPreliminary, "
        "checkedAt: live.checkedDate ?? checkedAt, "
        "epsChecking: eps.isChecking, revenueChecking: revenue.isChecking ) }"
    ), "updated(with:) must copy every field from the answer, each to its own label"


def test_each_metric_reads_its_own_figures():
    """The card, the chips and the header all read these. A crossed field prints one metric's
    figure under the other's name, and a lost basis colours a GAAP EPS as a beat or a miss."""
    answer = _code(_MODELS)
    assert _norm(_body(answer, "var epsOutcome: EarningsMetricOutcome {")) == (
        "{ EarningsMetricOutcome( estimate: eps.estimate, actual: eps.actual, "
        'surprisePct: eps.surprisePct, isPreliminary: eps.source == "filing", '
        'isGAAP: eps.basis == "gaap", isChecking: eps.checking ?? false ) }'
    )
    assert _norm(_body(answer, "var revenueOutcome: EarningsMetricOutcome {")) == (
        "{ EarningsMetricOutcome( estimate: revenue.estimate, actual: revenue.actual, "
        'surprisePct: revenue.surprisePct, isPreliminary: revenue.source == "filing", '
        "isChecking: revenue.checking ?? false ) }"
    )
    feed = _code(_TRACKING_MODELS)
    assert _norm(_body(feed, "var epsOutcome: EarningsMetricOutcome {")) == (
        "{ EarningsMetricOutcome(estimate: epsEstimate, actual: epsActual, "
        "surprisePct: epsSurprisePct, isPreliminary: isPreliminary, "
        "isGAAP: epsIsGAAP, isChecking: epsChecking) }"
    )
    assert _norm(_body(feed, "var revenueOutcome: EarningsMetricOutcome {")) == (
        "{ EarningsMetricOutcome(estimate: revenueEstimate, actual: revenueActual, "
        "surprisePct: revenueSurprisePct, isPreliminary: isPreliminary, "
        "isChecking: revenueChecking) }"
    )


def test_each_outcome_stores_each_figure_it_is_handed_under_its_own_name():
    """The call sites above are pinned argument by argument, and every one of them goes through
    this initializer, whose same-typed assignments compile in any order: `self.estimate = actual`
    draws a $0.44 beat on a $0.41 estimate as a red "Miss +7.3%" with the figures swapped, a
    negated surprise reads "Beat -7.3%", a GAAP EPS dropped to adjusted is coloured against the
    adjusted consensus, and a dropped hold shows "—" for a figure being confirmed. The defaults
    are part of it: the revenue outcomes never pass `isGAAP`. Worked out from the struct's own
    stored properties, so a seventh fails here until it is stored too."""
    outcome = _struct_block(_code(_MODELS), "EarningsMetricOutcome")
    stored = re.findall(r"^ {4}let (\w+): ", outcome, re.M)
    assert stored == ["estimate", "actual", "surprisePct", "isPreliminary", "isGAAP", "isChecking"]
    assert _norm(_bracketed(outcome, "init", "(", ")")).strip() == (
        "estimate: Double?, actual: Double?, surprisePct: Double?, "
        "isPreliminary: Bool = false, isGAAP: Bool = false, isChecking: Bool = false"
    )
    assert _norm(_bracketed(outcome, "init(")).strip() == " ".join(
        f"self.{name} = {name}" for name in stored
    ), "each figure and flag is stored under its own name, unchanged"
    assert _norm(_body(outcome, "var hasLanded: Bool {")) == "{ actual != nil }", (
        "a held figure is not a landed one: it reads Confirming…, never In"
    )


def test_an_answer_about_another_report_never_repaints_this_ones_figures():
    updated = _norm(_body(_code(_MODELS), _UPDATED))
    # Every statement before the copy, conditions and order included: a branch can be switched
    # off with all its statements still in place, so naming the statements proves nothing.
    assert updated[: updated.index("return AppAlert.EarningsData(")] == (
        "{ let answer: EarningsLiveStatus = live.liveStatus "
        "let sameReport: Bool = live.reportDate == reportDate "
        # Figures on screen outrank `none` and another date, before anything is copied.
        "if hasResults, answer == .notScheduled || !sameReport { return self } "
        # `none` with no figures takes effect, or the header promises a report the card denies.
        "if answer == .notScheduled { var unscheduled = self "
        "unscheduled.status = .notScheduled return unscheduled } "
        # A moved report takes its new date, whole, or the old date sits by the new status. Any
        # other answer keeps the card's own, or the Date row reads "OCT 0".
        "var shownDay: Int = day var shownMonth: String = month "
        "if !sameReport { let moved = Self.dayAndMonth(of: live.reportDate) "
        'shownDay = moved?.day ?? 0 shownMonth = moved?.month ?? "" } '
        "let eps: EarningsMetricOutcome = live.epsOutcome "
        "let revenue: EarningsMetricOutcome = live.revenueOutcome "
    )
    assert "reportTime: sameReport ? reportTime : nil" in updated, (
        "the time of day belongs to the date the card was drawn for"
    )
    assert "day: shownDay, month: shownMonth," in updated, "the copy prints the date chosen above"
    models = _code(_MODELS)
    day_and_month = _norm(_body(
        models, "private static func dayAndMonth(of reportDate: String) -> (day: Int, month: String)? {"))
    assert "guard parts.count == 3, let month = Int(parts[1]), let day = Int(parts[2])," in day_and_month
    assert "(1...12).contains(month), (1...31).contains(day) else { return nil }" in day_and_month, (
        "month 13 would index past the table and crash the sheet"
    )
    table = models[models.index("private static let monthNames"):]
    months = re.findall(r'"([A-Z]{3})"', table[: table.index("]\n")])
    assert months == [m.upper() for m in calendar.month_abbr[1:]], "the feed's month names"
    assert "return (day: day, month: monthNames[month - 1])" in models


def test_the_card_and_the_header_build_the_consensus_with_one_formatter():
    tracking = _code(_TRACKING_MODELS)
    assert "consensusParts" not in tracking
    dto = _norm(_body(tracking, "func toAppAlert() -> AppAlert {"))
    # The whole call: one argument right says nothing about the other.
    assert ("let consensus = AppAlert.EarningsData.consensusText(epsEstimate: epsEstimate, "
            "revenueEstimate: revenueEstimate)") in dto


def test_no_scheduled_report_has_its_own_sentence():
    """A sheet saying "No earnings report" must not sit under "reports earnings on…"."""
    body = _body(_code(_TRACKING_MODELS),
                 "static func earningsDescription(_ data: EarningsData) -> String {")
    assert "case .upcoming, .notScheduled:" not in body
    arm = body[body.index("case .notScheduled:"): body.index("case .live where")]
    assert "no earnings report" in arm
    assert "reports earnings" not in arm


def test_the_header_says_what_the_card_under_it_shows():
    """Its title and sentence are rebuilt from each answer, so the words have to follow the
    status and the figures, or the header goes on saying "Waiting" above a landed result."""
    tracking = _code(_TRACKING_MODELS)
    sentence = _norm(_body(tracking,
                           "static func earningsDescription(_ data: EarningsData) -> String {"))
    assert ("case .live where !data.hasResults && !data.epsChecking && !data.revenueChecking:"
            in sentence), "`Waiting for results…` is for a report with nothing in and nothing held"
    # The whole arm, the figures' line included: `let results: String = ""` leaves the
    # Tracking card saying "INTC reported OCT 29." with no figures, every other pin still green.
    arm = sentence[sentence.index("case .live, .reported:"): sentence.index("case .awaiting:")]
    assert arm.strip() == (
        'case .live, .reported: let lead = data.status == .live ? "\\(data.ticker) is reporting." '
        ': (dateText.isEmpty ? "\\(data.ticker) reported." '
        ': "\\(data.ticker) reported \\(dateText).") '
        "let results: String = earningsResultsLine(data) "
        'return results.isEmpty ? lead : lead + " " + results'
    )
    title = _norm(_body(tracking, "var title: String {"))
    assert 'case .reported, .awaiting: return "Earnings Results"' in title
    assert 'case .upcoming, .live, .notScheduled: return "Earnings Alert"' in title


def test_either_figure_landing_counts_as_a_result():
    """`hasResults` decides three things: whether the header may still say "Waiting for
    results…" (`test_the_header_says_what_the_card_under_it_shows`), whether the Tracking card
    trades its date badge for the result chips, and whether a `none` or moved-date answer may
    repaint a shown figure
    (`test_an_answer_about_another_report_never_repaints_this_ones_figures`). Revenue often lands
    first, and whatever comes out first is shown right away (owner, 2026-10-29): read from one
    figure alone, the header waits above a shown revenue and a `none` answer wipes it."""
    data = _norm(_bracketed(_code(_TRACKING_MODELS), "struct EarningsData"))
    assert "var hasResults: Bool { epsActual != nil || revenueActual != nil }" in data
    trailing = _norm(_body(_code("Views/Molecules/AlertCardView.swift"),
                           "private var trailingView: some View {"))
    arm = trailing[trailing.index("case .earnings(let data):"): trailing.index("case .market(")]
    assert arm == (
        "case .earnings(let data): if data.hasResults { "
        "EarningsResultChips(eps: data.epsOutcome, revenue: data.revenueOutcome, status: data.status) "
        "} else { EventDateBadge(day: data.formattedDay, month: data.formattedMonth) } "
    ), "the chips once any figure is in, the date badge before that"


# ── the sheet before its first answer ────────────────────────────────────────


def test_the_screen_claims_nothing_before_the_first_answer():
    """Before the first answer the push route has no figures at all: it must not say the opposite
    of the push ("Results will appear here…"), and `isLoading` could not drive this: it is
    false on the first frame, and a cancelled load's `defer` clears it under its replacement."""
    card = _code(_CARD)
    assert "private var hasNothingToShow: Bool { live == nil && fallback == nil }" in card
    body = _body(card, "var body: some View {")
    first = _body(body, "if hasNothingToShow {")
    assert "metricRow" not in first and "footnote" not in first, "a claim before any answer"
    after = body[body.index(first) + len(first):]
    assert after.lstrip().startswith("else if status == .notScheduled {"), (
        "the rows must be the OTHER branch, or they draw under the spinner"
    )
    assert "ProgressView()" in _body(first, "if !viewModel.loadFailed {"), (
        "a spinner must not turn beside the failure notice"
    )
    line = _body(card, "private var statusLine: String {")
    assert line.index("if hasNothingToShow") < line.index("switch status")
    assert ('return viewModel.loadFailed ? "Results aren\'t available right now" '
            ': "Loading results…"') in line
    label = _body(card, "private func outcomeLabel(_ outcome: EarningsMetricOutcome) -> some View {")
    assert "} else if !outcome.hasLanded, status == .live, !viewModel.loadFailed {" in label, (
        "a row spins only while its figure is on its way and the device is asking"
    )
    assert "isLoading" not in card and "isLoading" not in _code(_VM)


def test_the_footnote_follows_the_eps_basis():
    foot = _body(_code(_CARD), "private var footnote: some View {")
    assert re.search(r'let basis: String = eps\.isGAAP\s*\?\s*"This EPS is GAAP.*?"\s*'
                     r':\s*"EPS is adjusted \(non-GAAP\)', foot), (
        "the sentence must match the basis the row shows"
    )
    assert "Text(basis +" in foot


def test_a_gaap_eps_is_labelled_and_never_compared():
    """The consensus is adjusted, so a GAAP figure beside it is neither a beat nor a miss."""
    card = _code(_CARD)
    assert ('metricRow(title: eps.isGAAP ? "EPS (GAAP)" : "EPS (adjusted)", outcome: eps, '
            "format: EarningsFigureFormat.epsRow(actual: eps.actual, estimate: eps.estimate, "
            "beatOrMiss: eps.isBeatOrMiss))") in _norm(_body(card, "var body: some View {"))
    label = _body(card, "private func outcomeLabel(_ outcome: EarningsMetricOutcome) -> some View {")
    assert 'Text("Not comparable")' in _body(label, "if outcome.isGAAP, outcome.hasLanded {")
    models = _code(_MODELS)
    assert "guard !isGAAP, let estimate else { return .noEstimate }" in _body(
        models, "var result: EarningsQuarterResult {")
    assert "guard let pct = surprisePct, pct.isFinite, !isGAAP else { return nil }" in _body(
        models, "var surpriseText: String? {")
    line = _body(_code(_TRACKING_MODELS),
                 "static func earningsResultsLine(_ data: EarningsData) -> String {")
    assert 'let epsLabel = data.epsIsGAAP ? "GAAP EPS" : "EPS"' in line
    chips = _code(_CHIPS)
    assert 'if outcome.isGAAP { return "GAAP" }' in _body(
        chips, "private func value(_ outcome: EarningsMetricOutcome) -> String {")
    assert 'if o.isGAAP { return "\\(name) reported on a GAAP basis" }' in _body(
        chips, "private var spoken: String {")


def test_a_beat_and_a_miss_stay_the_right_way_round_in_every_signal():
    """The owner's rule: "red or green for miss or beat". The verdict, its colour, its word, its
    arrow, the sign of its percentage and VoiceOver are separate switches, and each view that
    draws them is one more place: one flipped arm, or one colour that ignores the verdict, calls
    a miss a beat with everything around it still reading correctly. The colour on the innermost
    view is the one drawn, so each view's colours are pinned whole, in order."""
    models = _code(_MODELS)
    assert _norm(_body(models, "var result: EarningsQuarterResult {")) == (
        "{ guard let actual else { return .pending } "
        "guard !isGAAP, let estimate else { return .noEstimate } "
        "if surprisePct == nil { return .noEstimate } "
        "if actual > estimate { return .beat } if actual < estimate { return .missed } "
        "return .matched }"
    )
    # Whole bodies: a line above the switch (`if isPreliminary { return AppColors.gain }`), or
    # an arm painting `.noEstimate` green, gives a verdict the server never gave with every
    # beat and miss arm still in place.
    outcome = _struct_block(models, "EarningsMetricOutcome")
    for opener, whole in (
        ("var color: Color {",
         "{ switch result { case .beat: return AppColors.gain case .missed: return AppColors.loss "
         "case .matched: return AppColors.accentCyan "
         "case .pending, .noEstimate: return AppColors.textSecondary } }"),
        ("var word: String? {",
         '{ switch result { case .beat: return "Beat" case .missed: return "Miss" '
         'case .matched: return "In line" case .pending, .noEstimate: return nil } }'),
        ("var arrowSymbol: String? {",
         "{ switch result { case .beat: return AppSentiment.symbolName(isPositive: true) "
         "case .missed: return AppSentiment.symbolName(isPositive: false) "
         "case .matched, .pending, .noEstimate: return nil } }"),
    ):
        assert _norm(_body(outcome, opener)) == whole, f"flipped: {opener}"
    assert _norm(_body(_code("Theme/AppSentiment.swift"),
                       "static func symbolName(isPositive: Bool) -> String {")) == (
        '{ isPositive ? "arrowtriangle.up.fill" : "arrowtriangle.down.fill" }'
    ), "turned, every beat draws ▼ and every miss ▲"
    surprise = _bracketed(_bracketed(models, "struct EarningsMetricOutcome"), "var surpriseText: String?")
    assert _norm(surprise).strip() == (
        "guard let pct = surprisePct, pct.isFinite, !isGAAP else { return nil } "
        "return EarningsQuarterData.surpriseText(pct, isMatch: result == .matched)"
    ), 'the percentage keeps the server\'s sign: turned, a beat reads "Beat -7.3%"'
    chips = _code(_CHIPS)
    spoken = _norm(_body(chips, "private var spoken: String {"))
    # The whole sentence, its size included: `test_voiceover_says_how_far_a_figure_beat_or_missed`.
    assert ('switch o.result { case .beat: return "\\(name) beat\\(by)" '
            'case .missed: return "\\(name) missed\\(by)"') in spoken
    assert 'return say("Earnings per share", eps) + ". " + say("Revenue", revenue) + "."' in spoken
    # The sentence above is all VoiceOver reads of the chips: the children are ignored.
    assert (".accessibilityElement(children: .ignore) .accessibilityLabel(spoken)"
            in _norm(_body(chips, "var body: some View {")))

    # The chips: label, arrow, value; the arrow and a landed value in the verdict's colour.
    chip = _bracketed(chips, "private func chip(")
    _in_order(_norm(chip), "Text(label)", "if let arrow = outcome.arrowSymbol { Image(systemName: arrow)",
              "Text(value(outcome))")
    assert _colours(chip) == [
        "AppColors.textMuted", "outcome.color",
        "outcome.hasLanded ? outcome.color : AppColors.textMuted",
    ]

    # The card: the word, its percentage and its arrow in one colour, the verdict's.
    card = _code(_CARD)
    word = _body(_bracketed(card, "private func outcomeLabel("), "} else if let word = outcome.word {")
    _in_order(_norm(word), "if let arrow = outcome.arrowSymbol { Image(systemName: arrow)",
              'Text([word, outcome.surpriseText].compactMap { $0 }.joined(separator: " "))')
    assert _colours(word) == ["outcome.color"]
    # The Actual figure takes the colour its row hands it (`test_each_figure_sits_under_its_own_label`).
    figure = _bracketed(card, "private func figure(")
    _in_order(_norm(figure), "Text(label)", "Text(value)")
    assert _colours(figure) == ["AppColors.textMuted", "color"]

    # The last quarters: each dot and its caption read the quarter's own result.
    quarters = _bracketed(card, "private func lastQuartersBlock(")
    _in_order(_norm(quarters), "EarningsResultDot(result: q.result, size: 12)", "Text(q.quarter)",
              "Text(Self.quarterCaption(q.result, q.surprisePct))")
    assert _colours(quarters) == ["AppColors.textPrimary", "AppColors.textMuted", "q.result.dotColor"]
    assert _norm(_body(card, "private static func quarterCaption(_ result: EarningsQuarterResult, "
                             "_ pct: Double?) -> String {")) == (
        '{ switch result { case .noEstimate, .pending: return "—" case .matched: return "In line" '
        'case .beat, .missed: let word: String = result == .beat ? "Beat" : "Miss" '
        "guard let pct, pct.isFinite else { return word } "
        'return word + " " + EarningsQuarterData.surpriseText(pct, isMatch: false) } }'
    )
    dot = _bracketed(_bracketed(_code("Models/TickerDetailModels.swift"), "enum EarningsQuarterResult"),
                     "var dotColor: Color")
    assert _norm(dot).strip() == (
        "switch self { case .beat, .matched: return AppColors.bullish "
        "case .missed: return AppColors.bearish case .pending: return AppColors.textSecondary "
        "case .noEstimate: return AppColors.primaryBlue }"
    )
    assert _norm(_body(_code("Views/Atoms/EarningsResultDot.swift"), "var body: some View {")) == (
        "{ ZStack { Circle() .fill(result.dotColor) .frame(width: size, height: size) "
        "if result.hasDashedBorder { Circle() .stroke( AppColors.textPrimary, "
        "style: StrokeStyle(lineWidth: 2, dash: [3, 2]) ) "
        ".frame(width: size + 4, height: size + 4) } } }"
    ), "each dot is drawn in its own quarter's colour, and nothing over it"


def _surprise_size(pct: float) -> str:
    """`EarningsQuarterData.surpriseText(pct, isMatch: false)` below 1,000%: the Financials tab's
    one surprise formatter, whose "<0.1%" branch is pinned below."""
    magnitude = abs(pct)
    if magnitude < 0.05:
        return "<0.1%"
    assert magnitude < 1000, "the compact branch is not ported"
    sign = "+" if pct > 0 else "-"
    return sign + (f"{magnitude:.0f}%" if magnitude >= 99.95 else f"{magnitude:.1f}%")


def _quarter_caption(result: str, pct) -> str:
    """`quarterCaption` as pinned in `test_a_beat_and_a_miss_stay_the_right_way_round_in_every_signal`,
    on the word `quarterResults` maps."""
    if result == "matched":
        return "In line"
    if result not in ("beat", "missed"):
        return "—"
    word = "Beat" if result == "beat" else "Miss"
    return word if pct is None else f"{word} {_surprise_size(pct)}"


def _quarter_spoken(quarter: str, result: str, pct) -> str:
    """`quarterSpoken` as pinned below."""
    by = "" if pct is None else " by " + _surprise_size(pct).lstrip("+-")
    return {
        "beat": f"{quarter}: beat{by}",
        "missed": f"{quarter}: missed{by}",
        "matched": f"{quarter}: in line",
    }.get(result, f"{quarter}: no analyst consensus")


def test_a_past_quarters_verdict_is_said_in_words_however_small():
    """A one-cent beat and a one-cent miss on a $35.11 consensus are both under 0.05%, and the
    size alone reads "<0.1%" for both, with no sign: the dot's colour was all that told them
    apart, and VoiceOver read the two columns alike. The caption leads with the verdict ("Beat
    <0.1%", "Miss <0.1%"), and each column tells VoiceOver the verdict and how far, the chips'
    way ("missed by 26.7%", never "by -26.7%"). The quarters are built through the backend's own
    path, the Financials quarters to the wire the sheet decodes."""
    from types import SimpleNamespace

    from app.services.earnings_live_service import history_extras_from, live_payload
    from app.services.earnings_service import _compute_surprise

    card = _code(_CARD)
    # The two Swift functions the ports below stand for, whole.
    assert _norm(_bracketed(card, "func quarterCaption(")).strip() == (
        'switch result { case .noEstimate, .pending: return "—" case .matched: return "In line" '
        'case .beat, .missed: let word: String = result == .beat ? "Beat" : "Miss" '
        "guard let pct, pct.isFinite else { return word } "
        'return word + " " + EarningsQuarterData.surpriseText(pct, isMatch: false) }'
    ), "the caption must lead with its verdict in words"
    assert _norm(_bracketed(card, "func quarterSpoken(")).strip() == (
        'var by: String = "" if let pct, pct.isFinite { '
        "let text: String = EarningsQuarterData.surpriseText(pct, isMatch: false) "
        'by = " by " + String(text.drop(while: { $0 == "+" || $0 == "-" })) } '
        'switch result { case .beat: return "\\(quarter): beat\\(by)" '
        'case .missed: return "\\(quarter): missed\\(by)" '
        'case .matched: return "\\(quarter): in line" '
        'case .noEstimate: return "\\(quarter): no analyst consensus" '
        'case .pending: return "\\(quarter): not reported yet" }'
    )
    block = _bracketed(card, "private func lastQuartersBlock(")
    column = _norm(_bracketed(block, "ForEach(Array(quarters.enumerated())")).strip()
    # The column's last two modifiers, in this order: a label set before `.ignore` is the
    # ignored children's, and anything after it (`.accessibilityHidden(true)`) can take the
    # whole column away from VoiceOver.
    assert column.endswith(
        ".accessibilityElement(children: .ignore) "
        ".accessibilityLabel(Self.quarterSpoken(q.quarter, q.result, q.surprisePct))"
    ), column[-160:]
    assert column.count(".accessibilityLabel(") == 1
    assert "accessibilityHidden" not in block, "VoiceOver must read every quarter"
    assert ".accessibilityElement(children: .combine)" not in column, (
        "combined, the column reads its caption alone: two sizes, no verdict"
    )
    formatter = _norm(_body(_code("Models/TickerDetailModels.swift"),
                            "static func surpriseText(_ surprise: Double, isMatch: Bool) -> String {"))
    assert 'if magnitude < 0.05 { return isMatch ? "0%" : "<0.1%" }' in formatter

    def quarter(label, end, estimate, actual, has_estimate=True):
        surprise = None if actual is None else _compute_surprise(actual, estimate)
        return SimpleNamespace(quarter=label, fiscal_date=end, estimate_value=estimate,
                               actual_value=actual, has_estimate=has_estimate,
                               surprise_percent=surprise)

    day = date(2026, 11, 17)
    response = SimpleNamespace(revenue_quarters=[], eps_quarters=[
        quarter("Q3 '25", "2025-09-30", 35.11, 35.12),
        quarter("Q4 '25", "2025-12-31", 35.11, 35.10),
        quarter("Q1 '26", "2026-03-31", 1.20, 0.88),
        quarter("Q2 '26", "2026-06-30", 2.00, 2.10, has_estimate=False),
        quarter("Q3 '26", "2026-09-30", 2.05, None),
    ])
    payload = live_payload("XYZ", day, None, company_name=None, eps_estimate=2.05,
                           revenue_estimate=None, extras=history_extras_from(response, day),
                           today=day)
    wire = [(q.quarter, q.result, q.surprise_pct)
            for q in EarningsLiveResponse(**payload).last_quarters]
    assert wire == [("Q3 '25", "beat", 0.03), ("Q4 '25", "missed", -0.03),
                    ("Q1 '26", "missed", -26.67), ("Q2 '26", "no_estimate", None)]
    captions = [_quarter_caption(result, pct) for _, result, pct in wire]
    assert captions == ["Beat <0.1%", "Miss <0.1%", "Miss -26.7%", "—"]
    spoken = [_quarter_spoken(*q) for q in wire]
    assert spoken == ["Q3 '25: beat by <0.1%", "Q4 '25: missed by <0.1%",
                      "Q1 '26: missed by 26.7%", "Q2 '26: no analyst consensus"]
    # Not only a $35 EPS: FMP's consensus has four decimals, so an ordinary printed $1.23 sits
    # within 0.05% of an estimate of 1.2304 (a miss) or 1.2296 (a beat).
    for estimate, word in ((1.2304, "Miss"), (1.2296, "Beat")):
        result = _quarter_result(1.23, estimate, True)
        assert _quarter_caption(result, _compute_surprise(1.23, estimate)) == f"{word} <0.1%"


def test_the_last_quarters_read_the_words_the_backend_sends():
    """Anything unmatched reads as no estimate, with no error: a word renamed on either side
    turns every dot grey. The whole body, the surprise each quarter carries included: an `abs`
    on it captions a -26.8% miss "+26.8%" under its red dot."""
    quarters = _norm(_body(_code(_MODELS), (
        "var quarterResults: "
        "[(quarter: String, result: EarningsQuarterResult, surprisePct: Double?)] {")))
    assert quarters == (
        "{ (lastQuarters ?? []).map { q in let result: EarningsQuarterResult switch q.result { "
        f'case "{_quarter_result(2.0, 1.0, True)}": result = .beat '
        f'case "{_quarter_result(1.0, 2.0, True)}": result = .missed '
        f'case "{_quarter_result(1.0, 1.0, True)}": result = .matched '
        "default: result = .noEstimate } "
        "return (quarter: q.quarter, result: result, surprisePct: q.surprisePct) } }"
    )
    assert _quarter_result(1.0, None, None) == "no_estimate", "the arm `default` stands in for"


def test_one_past_quarter_is_called_the_last_quarter():
    """A recent listing has one reported quarter before this report, and the backend sends just
    that one (`history_extras_from` keeps up to four): "Last 1 quarters" is no heading."""
    block = _norm(_bracketed(_code(_CARD), "private func lastQuartersBlock("))
    assert ('Text(quarters.count == 1 ? "Last quarter" : "Last \\(quarters.count) quarters")'
            in block)


def test_each_figure_sits_under_its_own_label():
    card = _code(_CARD)
    row = _norm(_body(card, "private func metricRow(title: String, outcome: EarningsMetricOutcome,\n"
                            "                           format: (Double) -> String) -> some View {"))
    # The row's own title and its own verdict: `outcomeLabel(eps)` compiles here, and puts EPS's
    # green "Beat +7.3%" above a revenue that missed.
    _in_order(row, "Text(title)", "outcomeLabel(outcome) }", 'figure(label: "Estimate"',
              'figure(label: "Actual"')
    assert len(re.findall(r"\bText\(", row)) == 1 and row.count("outcomeLabel(") == 1, row
    assert ('figure(label: "Estimate", value: outcome.estimate.map(format) ?? "—", '
            "color: AppColors.textSecondary)") in row
    assert ('figure(label: "Actual", value: actualText(outcome, format: format), '
            "color: outcome.hasLanded ? outcome.color : AppColors.textMuted, "
            "emphasized: outcome.hasLanded)") in row, "a figure not out yet has no beat colour"
    # VoiceOver reads each row whole, its estimate and actual included. The modifier ends the
    # row's own body: the same one closes four other blocks of this file.
    assert row.endswith(".accessibilityElement(children: .combine) }"), row[-80:]
    assert "accessibilityHidden" not in row
    actual = _body(card, "private func actualText(_ outcome: EarningsMetricOutcome,\n"
                         "                            format: (Double) -> String) -> String {")
    assert "if let actual = outcome.actual { return format(actual) }" in actual
    chips = _code(_CHIPS)
    body = _body(chips, "var body: some View {")
    assert 'chip(label: "EPS", outcome: eps)' in body
    assert 'chip(label: "REV", outcome: revenue)' in body
    # Each chip's value from its own outcome: `eps.surpriseText` here puts EPS's "+7.3%" in
    # revenue's red chip.
    assert _norm(_body(chips, "private func value(_ outcome: EarningsMetricOutcome) -> String {")) == (
        "{ guard outcome.hasLanded else { "
        'return status == .live || outcome.isChecking ? "…" : "—" } '
        'if outcome.isGAAP { return "GAAP" } '
        "if let text = outcome.surpriseText { return text } "
        'return outcome.word ?? "In" }'
    )
    # The year-ago lines: two labelled figures, each with its own growth rate, a fall in red.
    year_ago = _norm(_bracketed(card, "private func yearAgoBlock("))
    assert ('if let rev = yearAgo.revenue { yearAgoLine(label: "Revenue", '
            "then: EarningsFigureFormat.revenue(rev), growth: yearAgo.revenueGrowthPct) } "
            'if let eps = yearAgo.eps { yearAgoLine(label: "EPS", '
            "then: EarningsFigureFormat.eps(eps), growth: yearAgo.epsGrowthPct) }") in year_ago
    line = _bracketed(card, "private func yearAgoLine(")
    _in_order(_norm(line), "Text(label)", 'Text("\\(then) a year ago")',
              "if let growth { Text(EarningsFigureFormat.growth(growth))")
    assert _colours(line) == ["AppColors.textMuted", "AppColors.textSecondary",
                              "growth >= 0 ? AppColors.gain : AppColors.loss"]
    # And the sign it prints beside that colour: turned, a -3.1% fall reads "+3.1%" in red.
    assert _norm(_body(_code(_MODELS), "nonisolated static func growth(_ pct: Double) -> String {")) == (
        '{ guard pct.isFinite else { return "—" } '
        'let sign = pct > 0 ? "+" : (pct < 0 ? "-" : "") '
        'return sign + String(format: "%.1f%%", abs(pct)) }'
    )


def test_the_card_draws_every_block_its_answer_has():
    """The branch that draws the results, whole and in order: each block's own body is pinned
    elsewhere, so a dropped call (the year ago, the last quarters, the outlook, the footnote that
    says which EPS basis the row shows) would leave those pins checking code nobody draws."""
    body = _body(_code(_CARD), "var body: some View {")
    assert _norm(_bracketed(body, "} else {")).strip() == (
        'if showsRevenueRow { metricRow(title: "Revenue", outcome: revenue, '
        "format: EarningsFigureFormat.revenueRow(actual: revenue.actual, "
        "estimate: revenue.estimate, beatOrMiss: revenue.isBeatOrMiss)) } "
        'metricRow(title: eps.isGAAP ? "EPS (GAAP)" : "EPS (adjusted)", outcome: eps, '
        "format: EarningsFigureFormat.epsRow(actual: eps.actual, estimate: eps.estimate, "
        "beatOrMiss: eps.isBeatOrMiss)) "
        "if isPreliminary { preliminaryNote } "
        "if let yearAgo = live?.yearAgo { yearAgoBlock(yearAgo) } "
        "if let quarters = live?.quarterResults, !quarters.isEmpty { lastQuartersBlock(quarters) } "
        "if let guidance = live?.guidance, !guidance.isEmpty { guidanceBlock(guidance) } "
        "footnote"
    )


def test_the_results_sentence_reads_each_actual_against_its_own_estimate():
    """The Tracking card's sentence and the sheet's header: "Revenue $17.1B vs $16.6B est · EPS
    $0.44 vs $0.41 est". Its two return lines are separate places to go wrong: swapped, a $0.44
    beat on a $0.41 estimate reads "EPS $0.41 vs $0.44 est", a miss in words beside chips showing
    the beat; and the line with no estimate is the only one a GAAP EPS ever takes. The whole
    body, because every statement in it feeds one of those lines. Each pair takes its own row's
    format, decided by its own verdict (`test_a_beat_or_a_miss_never_prints_its_two_figures_alike`)."""
    line = _norm(_body(_code(_TRACKING_MODELS),
                       "static func earningsResultsLine(_ data: EarningsData) -> String {"))
    assert line == (
        "{ func part(_ label: String, _ actual: Double?, _ estimate: Double?, checking: Bool, "
        "_ format: (Double) -> String) -> String? { "
        "guard let actual else { "
        'if checking { return "\\(label) being confirmed" } '
        'return data.status == .live || estimate != nil ? "\\(label) not out yet" : nil } '
        'guard let estimate else { return "\\(label) \\(format(actual))" } '
        'return "\\(label) \\(format(actual)) vs \\(format(estimate)) est" } '
        'let epsLabel = data.epsIsGAAP ? "GAAP EPS" : "EPS" '
        "let parts: [String?] = [ "
        'part("Revenue", data.revenueActual, data.revenueEstimate, '
        "checking: data.revenueChecking, "
        "EarningsFigureFormat.revenueRow(actual: data.revenueActual, "
        "estimate: data.revenueEstimate, beatOrMiss: data.revenueOutcome.isBeatOrMiss)), "
        "part(epsLabel, data.epsActual, data.epsIsGAAP ? nil : data.epsEstimate, "
        "checking: data.epsChecking, "
        "EarningsFigureFormat.epsRow(actual: data.epsActual, estimate: data.epsEstimate, "
        "beatOrMiss: data.epsOutcome.isBeatOrMiss)), ] "
        'return parts.compactMap { $0 }.joined(separator: " · ") }'
    ), "each actual first, against its own estimate, in its own metric's format"


def test_a_beat_or_a_miss_never_prints_its_two_figures_alike():
    """FMP's $2.53B revenue against a $2.47B consensus is a +2.4% beat, and at one decimal both
    read "$2.5B": "Estimate $2.5B · Actual $2.5B · ▲ Beat +2.4%" on the card and "Revenue $2.5B
    vs $2.5B est" in the sentence, as many an ordinary beat or miss would. A pair the server
    called a beat or a miss takes the fewest extra decimals, two at most, that print its figures
    apart ("$2.53B" / "$2.47B"); any other pair keeps the usual ones, so a company's "$2.5
    billion" that its own rounding cannot tell from the consensus never grows a digit its
    release did not print. A filing figure that is judged differs from the consensus at its own
    print's decimals, so the widening stops there. Pinned whole here; the card's two rows and
    the sentence are pinned calling it with their own figures and their own verdict."""
    models = _code(_MODELS)
    assert _norm(_body(_struct_block(models, "EarningsMetricOutcome"),
                       "var isBeatOrMiss: Bool {")) == "{ result == .beat || result == .missed }"
    figures = _norm(_bracketed(models, "enum EarningsFigureFormat"))
    assert _body(figures, "nonisolated static func eps(_ value: Double) -> String {") == (
        "{ epsFigure(value, extraDecimals: 0) }")
    assert _body(figures, "nonisolated static func revenue(_ value: Double) -> String {") == (
        "{ revenueFigure(value, extraDecimals: 0) }")
    for metric in ("eps", "revenue"):
        assert _body(figures, f"nonisolated static func {metric}Row(actual: Double?, "
                              "estimate: Double?, beatOrMiss: Bool) -> (Double) -> String {") == (
            f"{{ let extra = decimalsApart(actual, estimate, beatOrMiss: beatOrMiss, {metric}Figure) "
            f"return {{ EarningsFigureFormat.{metric}Figure($0, extraDecimals: extra) }} }}"
        ), f"{metric}Row: both figures of a row in one format, its own metric's"
    assert _body(figures, "private nonisolated static func decimalsApart(_ actual: Double?, "
                          "_ estimate: Double?, beatOrMiss: Bool, "
                          "_ figure: (Double, Int) -> String) -> Int {") == (
        "{ guard beatOrMiss, let actual, let estimate else { return 0 } "
        "var extra = 0 "
        "while extra < 2, figure(actual, extra) == figure(estimate, extra) { extra += 1 } "
        "return extra }"
    ), "the fewest extra decimals, two at most, and none for a pair nobody judged"
    assert _body(figures, _EPS_FIGURE) == (
        '{ guard value.isFinite else { return "—" } '
        'let sign = value < 0 ? "-" : "" '
        'return sign + String(format: "$%.\\(2 + extra)f", abs(value)) }'
    )
    assert _body(figures, _REVENUE_FIGURE) == (
        '{ guard value.isFinite else { return "—" } '
        'let sign = value < 0 ? "-" : "" '
        "let v = abs(value) let body: String "
        "if v >= 1_000_000_000_000 { "
        'body = String(format: "$%.\\(2 + extra)fT", v / 1_000_000_000_000) } '
        "else if v >= 1_000_000_000 { "
        'body = String(format: "$%.\\(1 + extra)fB", v / 1_000_000_000) } '
        "else if v >= 1_000_000 { "
        'body = String(format: "$%.\\(1 + extra)fM", v / 1_000_000) } '
        "else if v >= 1_000 { "
        'body = String(format: "$%.\\(extra)fK", v / 1_000) } '
        'else { body = String(format: "$%.\\(extra)f", v) } '
        "return sign + body }"
    )


def test_voiceover_says_how_far_a_figure_beat_or_missed():
    """"Earnings per share missed by 26.8%", never "missed by -26.8%": the verb says which way,
    and a minus read aloud after it is a double negative (the push says "missed consensus by
    27%"). The size is the chip's own text with its sign dropped, so "<0.1%" and the 1,000% cap
    read as drawn, and the whole sentence reads each metric's own outcome."""
    assert _norm(_body(_code(_CHIPS), "private var spoken: String {")) == (
        "{ func say(_ name: String, _ o: EarningsMetricOutcome) -> String { "
        "guard o.hasLanded else { "
        'if o.isChecking { return "\\(name) being confirmed" } '
        'return status == .live || o.estimate != nil ? "\\(name) not out yet" '
        ': "\\(name) not available" } '
        'if o.isGAAP { return "\\(name) reported on a GAAP basis" } '
        "let size: String? = o.surpriseText.map { text in "
        'String(text.drop(while: { $0 == "+" || $0 == "-" })) } '
        'let by: String = size.map { " by \\($0)" } ?? "" '
        "switch o.result { "
        'case .beat: return "\\(name) beat\\(by)" '
        'case .missed: return "\\(name) missed\\(by)" '
        'case .matched: return "\\(name) in line" '
        'case .pending, .noEstimate: return "\\(name) reported" } } '
        'return say("Earnings per share", eps) + ". " + say("Revenue", revenue) + "." }'
    )


def test_a_preliminary_figure_is_marked_and_links_its_filing():
    """It is the company's own number, not yet FMP's, and the card says so in words, with a link
    that opens: the button only sets `browserLink`, and the body is what presents it."""
    card = _code(_CARD)
    body = _norm(_body(card, "var body: some View {"))
    assert "if isPreliminary { preliminaryNote }" in body
    assert ".inAppBrowser(link: $browserLink)" in body
    assert "@State private var browserLink: BrowserLink?" in card
    note = _body(card, "private var preliminaryNote: some View {")
    assert "Text(preliminaryText)" in _norm(note)
    link = _norm(_body(note, "if let url = live?.filingURL {"))
    assert link.startswith(
        '{ Button { openExternal(url, into: &browserLink) } label: { Text("View the filing")'), link
    assert _norm(_body(card, "private var preliminaryText: String {")) == (
        '{ var text = "Preliminary · read from \\(companyName)\'s own press release (SEC 8-K)" '
        "if let accepted = live?.acceptedDate { "
        'text += ", filed \\(EarningsLiveDates.etTime(accepted))" } '
        'return text + ". It updates when FMP confirms the figures." }'
    )
    # What the note and its link read, where they are made: "filed" is the filing's own time,
    # never the last check's, and the link is the filing's own address.
    answer = _bracketed(_code(_MODELS), "extension EarningsLiveResponse")
    assert _norm(_body(answer, "var acceptedDate: Date? {")) == (
        "{ EarningsLiveDates.parse(filing?.acceptedAt) }")
    assert _norm(_body(answer, "var filingURL: URL? {")) == (
        "{ filing?.url.flatMap(URL.init(string:)) }")


def test_a_filing_time_reads_in_new_york_on_any_phone():
    """"filed 4:02 PM ET" on the note and "Released 4:02 PM ET" on the status line: a filing
    accepted at 20:02Z is 4:02 PM in New York. Formatted in the phone's own zone, the same "ET"
    would read 2:02 PM in Denver and 5:02 AM in Tokyo."""
    dates = _bracketed(_code(_MODELS), "enum EarningsLiveDates")
    assert _norm(_body(dates, "private static let etClock: DateFormatter = {")) == (
        '{ let f = DateFormatter() f.locale = Locale(identifier: "en_US_POSIX") '
        'f.timeZone = TimeZone(identifier: "America/New_York") f.dateFormat = "h:mm a" return f }'
    )
    assert _norm(_body(dates, "static func etTime(_ date: Date) -> String {")) == (
        '{ etClock.string(from: date) + " ET" }')
    assert _norm(_body(dates, "static func parse(_ raw: String?) -> Date? {")) == (
        "{ guard let raw, !raw.isEmpty else { return nil } "
        "return plain.date(from: raw) ?? fractional.date(from: raw) }"
    )
    answer = _bracketed(_code(_MODELS), "extension EarningsLiveResponse")
    assert _norm(_body(answer, "var checkedDate: Date? {")) == (
        "{ EarningsLiveDates.parse(checkedAt) }"), "\"updated N min ago\" is the last check's"
    status = _norm(_body(_code(_CARD), "private var statusLine: String {"))
    assert ('case .reported: if let accepted = live?.acceptedDate { '
            'return "Released \\(EarningsLiveDates.etTime(accepted))" } return "Reported"') in status


def test_a_metric_is_awaited_only_while_the_report_is_live():
    """A report the server finished with no revenue consensus has stopped waiting for revenue,
    so nothing on screen may go on saying it is on its way."""
    card = _code(_CARD)
    actual = _body(card, "private func actualText(_ outcome: EarningsMetricOutcome,\n"
                         "                            format: (Double) -> String) -> String {")
    assert actual.index('"Confirming…"') < actual.index('"Waiting…"')
    assert 'return isLiveNow ? "Waiting…" : "—"' in actual
    body = _body(card, "var body: some View {")
    assert ('if showsRevenueRow { metricRow(title: "Revenue", outcome: revenue, '
            "format: EarningsFigureFormat.revenueRow(actual: revenue.actual, "
            "estimate: revenue.estimate, beatOrMiss: revenue.isBeatOrMiss)) }") in _norm(body)
    rows = _norm(_body(card, "private var showsRevenueRow: Bool {"))
    assert rows == ("{ status == .live || revenue.estimate != nil || revenue.hasLanded "
                    "|| revenue.isChecking }"), "the Revenue row must hide only with nothing to say"

    chips = _code(_CHIPS)
    value = _body(chips, "private func value(_ outcome: EarningsMetricOutcome) -> String {")
    assert 'status == .live || outcome.isChecking ? "…" : "—"' in value
    assert re.search(r"EarningsResultChips\(eps: data\.epsOutcome, revenue: data\.revenueOutcome,"
                     r"\s+status: data\.status\)", _code("Views/Molecules/AlertCardView.swift"))

    line = _body(_code(_TRACKING_MODELS),
                 "static func earningsResultsLine(_ data: EarningsData) -> String {")
    assert 'data.status == .live || estimate != nil ? "\\(label) not out yet" : nil' in line


def test_a_held_figure_reads_the_same_on_the_card_and_the_sheet():
    """The feed carries the hold, so the Tracking card, its chips and the sheet's first frame
    all say what the sheet says once it has answered."""
    tracking = _code(_TRACKING_MODELS)
    dto = _body(tracking, "func toAppAlert() -> AppAlert {")
    assert "epsChecking: epsChecking ?? false" in dto
    assert "revenueChecking: revenueChecking ?? false" in dto
    assert "isGAAP: epsIsGAAP, isChecking: epsChecking)" in tracking
    assert re.search(r"isPreliminary: isPreliminary,\s+isChecking: revenueChecking\)", tracking)
    line = _body(tracking, "static func earningsResultsLine(_ data: EarningsData) -> String {")
    assert 'if checking { return "\\(label) being confirmed" }' in line
    # Each flag on its own metric: crossed, the card names the wrong figure as held.
    flat = _norm(line)
    assert ('part("Revenue", data.revenueActual, data.revenueEstimate, '
            'checking: data.revenueChecking, EarningsFigureFormat.revenueRow(') in flat
    assert ('part(epsLabel, data.epsActual, data.epsIsGAAP ? nil : data.epsEstimate, '
            'checking: data.epsChecking, EarningsFigureFormat.epsRow(') in flat
    # "being confirmed" is what the push says (`earnings_live_sender._phrase`).
    sentence = _body(tracking, "static func earningsDescription(_ data: EarningsData) -> String {")
    assert "!data.epsChecking && !data.revenueChecking" in sentence, (
        "a held figure must not read as `Waiting for results…`"
    )
    spoken = _body(_code(_CHIPS), "private var spoken: String {")
    assert '"\\(name) being confirmed"' in spoken
    answer = _code(_MODELS)
    assert "isChecking: eps.checking ?? false" in _body(answer, "var epsOutcome: EarningsMetricOutcome {")
    assert "isChecking: revenue.checking ?? false" in _body(
        answer, "var revenueOutcome: EarningsMetricOutcome {")


def test_a_held_figure_is_not_called_absent_once_the_window_has_closed():
    """Every figure held and none shown reads `awaiting` from 22:00 ET and on later days: the
    flags must still reach the sentence, or the header says the results aren't in above a row
    saying "Confirming…"."""
    sentence = _body(_code(_TRACKING_MODELS),
                     "static func earningsDescription(_ data: EarningsData) -> String {")
    arm = sentence[sentence.index("case .awaiting:"):]
    assert re.search(r"guard data\.epsChecking \|\| data\.revenueChecking else \{\s*"
                     r"return \"\\\(data\.ticker\)'s\\\(when\) results aren't in yet\.\"\s*\}", arm), (
        "`aren't in yet` is for a report with nothing held"
    )
    assert 'return "\\(data.ticker)\'s\\(when) results: \\(earningsResultsLine(data))"' in arm
    line = _body(_code(_CARD), "private var statusLine: String {")
    assert ('return eps.isChecking || revenue.isChecking ? "Results being confirmed" '
            ': "Results aren\'t in yet"') in _norm(line)


def test_the_spoken_chip_says_what_the_card_says():
    """A report can be finished with one figure missing from the feed: the card says it is not
    out yet, or nothing. "Not reported" beside that is a false claim about the company, and
    the rule has to stay the card's (`earningsResultsLine`)."""
    spoken = _norm(_body(_code(_CHIPS), "private var spoken: String {"))
    assert 'if o.isChecking { return "\\(name) being confirmed" }' in spoken
    assert ('return status == .live || o.estimate != nil ? "\\(name) not out yet" '
            ': "\\(name) not available"') in spoken
    assert "not reported" not in spoken
    line = _body(_code(_TRACKING_MODELS),
                 "static func earningsResultsLine(_ data: EarningsData) -> String {")
    assert 'data.status == .live || estimate != nil ? "\\(label) not out yet"' in line


# ── a poll that has given up ─────────────────────────────────────────────────


def test_a_poll_that_gave_up_says_so_and_a_try_again_clears_it():
    """It ends with no answer once the retries run out, or on a refusal shown answer or not. A
    poll the server itself stopped is not this: the answer then says how the report stands."""
    vm = _code(_VM)
    assert "@Published private(set) var pollEnded = false" in vm
    start = _body(vm, "func start() {")
    assert start.index("pollEnded = false") < start.index("pollTask = Task"), (
        "Try Again and a return to the foreground must draw LIVE again while they ask"
    )
    stopped = _norm(_body(start, "guard let seconds = self.nextPollSeconds else {"))
    assert stopped == "{ self.pollEnded = self.refused || self.live == nil break }"


def test_nothing_on_the_sheet_says_live_once_the_device_has_stopped_asking():
    """`LiveBadge`: LIVE only while it is genuinely being checked. With the retry over, the status
    the card reads is a last word from before that, and the sheet stays open for as long as
    the user leaves it."""
    card = _code(_CARD)
    assert "private var isLiveNow: Bool { status == .live && !viewModel.pollEnded }" in card
    assert card.count("LiveBadge()") == 1
    badge = _body(_body(card, "private var header: some View {"), "if isLiveNow {")
    assert "LiveBadge()" in badge
    line = _body(card, "private var statusLine: String {")
    arm = line[line.index("case .live:"): line.index("case .reported:")]
    assert arm.index("guard isLiveNow else {") < arm.index("Checking for results")
    assert 'guard isLiveNow else { return "Latest results aren\'t available right now" }' in arm
    # The rows: `actualText` is pinned by `test_a_metric_is_awaited_only_while_the_report_is_live`.
    assert "!viewModel.loadFailed" in _body(
        card, "private func outcomeLabel(_ outcome: EarningsMetricOutcome) -> some View {")
    assert re.search(r"loadFailed = live == nil \|\| refused", _code(_VM)), (
        "an ended poll must always be a failed one, or the row keeps spinning"
    )
    # The rules above hold only while the header draws `statusLine`: a literal in its place
    # ("Checking for results as they come out") claims a live check for every status.
    header = _body(card, "private var header: some View {")
    assert re.findall(r"\bText\(([^)]*)\)", header) == ['"Results vs. estimates"', "statusLine"]


def test_the_header_stops_waiting_when_the_card_does():
    """The header sits right above the card: it may not go on saying "Waiting for results…"."""
    card = _code(_CARD)
    hook = _norm(card[card.index(".onChange(of: viewModel.pollEnded)"):])
    assert hook.startswith(".onChange(of: viewModel.pollEnded) { _, ended in onPollEnded?(ended) }"), (
        "Bool, not fire-and-forget: Try Again has to take it back"
    )
    detail = _code(_DETAIL)
    assert "@State private var liveCheckEnded = false" in detail
    header = _body(detail, "private var headerAlert: AppAlert {")
    assert header.index("data.updated(with: liveAnswer)") < header.index(
        "data.liveCheckEnded = liveCheckEnded"), "`updated(with:)` builds a fresh copy"
    tracking = _code(_TRACKING_MODELS)
    assert "var liveCheckEnded: Bool = false" in tracking
    sentence = _body(tracking, "static func earningsDescription(_ data: EarningsData) -> String {")
    assert 'return data.liveCheckEnded ? sentence : sentence + " Waiting for results…"' in sentence
    assert sentence.count("Waiting for results") == 1


# ── the off switch ───────────────────────────────────────────────────────────


def test_the_sheet_shows_the_results_card_only_with_a_report_date():
    """With the feature off, or on an older backend, the feed sends no `report_date`, and the
    card would still ask the endpoint for a report nobody is watching."""
    detail = _body(_code(_DETAIL), "private func earningsDetail(_ data: AppAlert.EarningsData) -> some View {")
    gate = _body(detail, "if data.reportDate != nil {")
    assert "EarningsLiveResultsCard(" in gate
    assert detail.count("EarningsLiveResultsCard(") == 1, "a second card outside the gate"


# ── the previews ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rel,call", [
    (_CARD, "EarningsLiveResultsCard(previewing: .sampleData)"),
    (_SCREEN, "EarningsLiveScreen(previewing: .sampleData)"),
])
def test_each_preview_draws_sample_data_and_makes_no_request(rel, call):
    code = _code(rel)
    assert "#Preview" not in _release(code), "a #Preview outside DEBUG breaks the Release archive"
    assert call in _debug_only(code)
    previews = _debug_only(code)[_debug_only(code).index("#Preview"):]
    assert "ticker:" not in previews, "this preview still goes through the live initialiser"


def test_the_preview_seam_cannot_reach_a_production_path():
    """`ios-swiftui.md`: sample market data is never painted onto a failed load."""
    for rel in (_VM, _CARD, _SCREEN):
        code = _code(rel)
        assert "init(previewing answer: EarningsLiveResponse)" in _debug_only(code), rel
        assert "previewing" not in _release(code), f"{rel}: the seam is compiled into Release"
    models = _code(_MODELS)
    assert "static let sampleData = EarningsLiveResponse(" in _debug_only(models)
    assert "sampleData" not in _release(models)

    vm = _code(_VM)
    start = _body(vm, "func start() {")
    assert start.index("guard !isPreview else { return }") < start.index("pollTask = Task")
    assert "private func load() async {" in vm, "only `start()` may reach the network"
    assert "self.isPreview = false" in _body(
        vm, "init(ticker: String, reportDate: String?, apiClient: APIClient = .shared) {")

    callers = []
    for path in sorted(_IOS.rglob("*.swift")):
        rel = str(path.relative_to(_IOS))
        if re.search(r"Earnings\w+\(previewing:|\bpreviewing: \.sampleData", _release(_code(rel))):
            callers.append(rel)
    assert not callers, f"the preview seam is called from a production path: {callers}"


def test_the_sample_answer_is_labelled_as_sample_data():
    sample = _debug_only(_code(_MODELS))
    assert 'ticker: "SMPL"' in sample
    assert 'companyName: "Sample Company (sample data)"' in sample


# ── isolation ────────────────────────────────────────────────────────────────


def test_the_figure_formatters_are_nonisolated():
    """They are passed as plain `(Double) -> String` values, which a main-actor static cannot
    be without a warning, and they touch nothing but Foundation's string formatting."""
    fmt = _body(_code(_MODELS), "enum EarningsFigureFormat {")
    for name in ("eps", "revenue", "growth", "epsRow", "revenueRow"):
        assert f"\n    nonisolated static func {name}(" in fmt, name
    for name in ("decimalsApart", "epsFigure", "revenueFigure"):
        assert f"\n    private nonisolated static func {name}(" in fmt, name
