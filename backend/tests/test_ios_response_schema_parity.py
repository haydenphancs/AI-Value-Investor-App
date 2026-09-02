"""Backend↔iOS response-shape parity for the AUTH and MONEY paths.

WHY THIS EXISTS. Eight Pydantic response models that iOS decodes on the sign-in and payment
paths had ZERO test coverage of any kind. `.claude/rules/testing.md` calls this class of gap
out by name: "A failure here means iOS will crash on decode in production — the test is a
guard rail, not a nice-to-have." The decoder is a plain `JSONDecoder` with hand-written
`CodingKeys` (`APIClient.swift:61-67`), so ADDING a backend field is safe, while renaming one,
dropping one, or letting one go null that Swift declares non-optional is an instant crash on
that screen — sign-in, credits, or a completed purchase.

⚠️ ALL NINE PAIRS AGREE TODAY. This module is REGRESSION INSURANCE, not a bug report. If a
test here is red the first time you run it, the test is wrong — do not "fix" a correct backend
to satisfy it.

TECHNIQUE. Not the shape of `test_ticker_report_schema_parity.py`, which builds a worst-case
dict and runs it through an assembler — there is no assembler here, and the risk is DRIFT, not
construction. This imports the Pydantic model and parses the Swift DTO, the technique this repo
has proven three times (`test_ios_auth_policy_parity.py`, `test_ios_theme_parity.py`,
`test_ios_a11y_parity.py`). It generalises the hand-written prototype in
`test_research_report_detail_parity.py`, which is hardcoded to one Swift file and two structs.

⚠️ THE PREDICATE THAT MATTERS — read before changing any comparison.

The intuitive rule, "Pydantic `is_required()` must match Swift non-Optional", is WRONG and
fails on five fields today:

    TokenResponse.token_type    SignUpResponse.token_type    VerifyPurchaseResponse.was_replay
    UserResponse.tier           ResearchStatusResponse.progress

All five are `x: T = <default>`: not *required*, but always ON THE WIRE (FastAPI serialises
defaults, and no route sets `response_model_exclude_*` — pinned below) and never `null` (a
non-`Optional` annotation rejects `None` at construction). `is_required()` describes how the
model is CONSTRUCTED; only a `null` can crash a Swift non-optional.

So the predicate is NULLABILITY ALONE. The asymmetry then falls out for free:

    backend                     swift      verdict
    str            (required)   String     safe
    str = "bearer" (defaulted)  String     safe   ← the trap; `is_required()` would misfire
    Optional[str]               String     FATAL  ← a single null crashes the screen
    str                         String?    safe   (over-tolerant client, deliberate)
    field absent from model     String     FATAL
    field absent from Swift     —          harmless: the decoder ignores unknown keys

`test_a_defaulted_non_nullable_field_is_not_a_violation` pins this so nobody simplifies it back.

Source-level on both sides: no DB, no network, no simulator, no app build.
"""

import importlib
import re
from pathlib import Path
from typing import get_args, get_origin

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_BACKEND = _REPO / "backend/app"


class Pair:
    __slots__ = ("model", "module", "swift_file", "swift_struct")

    def __init__(self, model, module, swift_file, swift_struct):
        self.model, self.module = model, module
        self.swift_file, self.swift_struct = swift_file, swift_struct

    def __repr__(self):
        return f"{self.model}->{self.swift_struct}"


# NINE pairs for eight models: `UserCreditsResponse` has TWO independent decoders for one
# endpoint. `CreditInfo` and `BackendCreditsResponse` are byte-identical shapes maintained in
# two files — if either drifts, one of the two call paths breaks and the other does not. That
# duplication is a latent bug in itself, and listing both is what makes it visible.
_PAIRS = (
    Pair("TokenResponse", "app.schemas.auth",
         "Core/Services/AuthService.swift", "AuthResponse"),
    Pair("SignUpResponse", "app.schemas.auth",
         "Core/Services/AuthService.swift", "SignUpResponseDTO"),
    Pair("PasswordChangedResponse", "app.schemas.auth",
         "Core/Services/APIEndpoint.swift", "PasswordChangedResponse"),
    Pair("VerifyPurchaseResponse", "app.schemas.subscription",
         "Models/SubscriptionModels.swift", "VerifyPurchaseResponse"),
    Pair("UserCreditsResponse", "app.schemas.user",
         "Core/Services/TaskPollingManager.swift", "BackendCreditsResponse"),
    Pair("UserCreditsResponse", "app.schemas.user",
         "Core/State/AppState.swift", "CreditInfo"),
    Pair("UserResponse", "app.schemas.user",
         "Core/State/AppState.swift", "UserProfile"),
    Pair("ResearchGenerationResponse", "app.schemas.research",
         "Core/Services/TaskPollingManager.swift", "ResearchGenerationResponse"),
    Pair("ResearchStatusResponse", "app.schemas.research",
         "Core/Services/TaskPollingManager.swift", "ResearchStatusResponse"),
)

_IDS = [f"{p.model}->{p.swift_struct}" for p in _PAIRS]


# ── Swift parsing ────────────────────────────────────────────────────────────

def _code(path: Path) -> str:
    """Source with `//` comment lines dropped and trailing comments cut. Not optional:
    `APIEndpoint.swift:1074-1080` and `AuthService.swift:41-46` are prose blocks that QUOTE
    field names and DTO shapes, and a naive scan reads documentation as declarations."""
    out = []
    for raw in path.read_text().splitlines():
        if raw.strip().startswith("//"):
            continue
        out.append(re.sub(r"//.*$", "", raw))
    return "\n".join(out)


def _struct_body(src: str, name: str) -> str | None:
    """Brace-matched body of `struct <name>`.

    ⚠️ The conformance list is NOT anchored on. Three of these nine declare
    `struct X: Sendable` and put `Decodable` in a SEPARATE `extension` — a regex requiring
    `Codable`/`Decodable` on the struct line silently finds nothing and the pair goes green
    having compared zero fields. `nonisolated` also prefixes one of them.
    """
    m = re.search(rf"^[ \t]*(?:nonisolated[ \t]+)?(?:public[ \t]+|internal[ \t]+)?"
                  rf"struct[ \t]+{re.escape(name)}\b[^{{\n]*\{{", src, re.M)
    if not m:
        return None
    depth, i, start = 0, m.end() - 1, m.end()
    while i < len(src):
        c = src[i]
        if c == '"':
            i += 1
            while i < len(src) and src[i] != '"':
                i += 2 if src[i] == "\\" else 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start:i]
        i += 1
    return None


# Exactly four spaces: a property of the struct itself. Deeper indents belong to a nested type
# or a function body and must not leak in.
_PROP = re.compile(r"^ {4}let[ \t]+(\w+)[ \t]*:[ \t]*([^\n=/]+?)[ \t]*$", re.M)
_CK_ENUM = re.compile(r"enum[ \t]+CodingKeys[ \t]*:[ \t]*String[ \t]*,[ \t]*CodingKey[ \t]*\{(.*?)\n[ \t]*\}", re.S)
_CK_CASE = re.compile(r"^[ \t]*case[ \t]+(.+)$", re.M)


def _properties(body: str) -> dict[str, bool]:
    """{swiftPropertyName: isOptional}. Computed `var`s are excluded by construction —
    `VerifyPurchaseResponse.userTier` and `ResearchStatusResponse.isCompleted` are derived,
    not decoded."""
    return {n: (t.endswith("?") or t.startswith("Optional<"))
            for n, t in _PROP.findall(body)}


def _coding_keys(body: str, props: dict) -> dict[str, str]:
    """{swiftPropertyName: wireKey}. Handles both forms on one enum — `case tier, status`
    (implicit raw value) and `case wasReplay = "was_replay"` — which co-occur in
    `VerifyPurchaseResponse` and `CreditInfo`. No enum at all ⇒ identity mapping."""
    m = _CK_ENUM.search(body)
    if not m:
        return {p: p for p in props}
    keys = {}
    for line in _CK_CASE.findall(m.group(1)):
        for part in line.split(","):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                name, raw = part.split("=", 1)
                keys[name.strip()] = raw.strip().strip('"')
            else:
                keys[part] = part
    return keys


def _swift_type_base(body: str, prop: str) -> str:
    m = re.search(rf"^ {{4}}let[ \t]+{re.escape(prop)}[ \t]*:[ \t]*([^\n=/]+?)[ \t]*$", body, re.M)
    return m.group(1).rstrip("?").strip() if m else ""


# JSON int → Swift Double is a legal widening, so Double accepts int.
_SWIFT_TO_PY = {"String": (str,), "Int": (int,), "Bool": (bool,),
                "Double": (float, int), "Float": (float, int),
                # Arrays of scalars. Mapped to `list` so the arm below can assert the backend
                # really is a sequence — an unmapped entry here does not merely skip the type
                # check, it fails `test_every_swift_type_is_one_we_can_compare` outright, which
                # is the anti-vacuity control that forces this decision.
                "[String]": (list,)}


# ── Backend introspection ────────────────────────────────────────────────────

def _model(pair: Pair):
    return getattr(importlib.import_module(pair.module), pair.model)


def _may_be_null(field) -> bool:
    """The ONLY predicate that matters. See the module docstring — `is_required()` is about
    construction and misfires on five defaulted-but-non-nullable fields today."""
    ann = field.annotation
    return ann is type(None) or type(None) in get_args(ann)


def _wire_fields(model) -> dict:
    """{wireName: FieldInfo}. Alias-aware from day one even though there are zero aliases
    today — `test_no_model_declares_a_field_alias` keeps that true, and if one ever appears
    this resolves the WIRE name rather than silently comparing the Python name."""
    return {(f.serialization_alias or f.alias or n): f
            for n, f in model.model_fields.items()}


# ── The comparator, as a pure function so synthetics can drive it ────────────

def _findings(wire: dict, props: dict[str, bool], keys: dict[str, str],
              types: dict[str, str] | None = None) -> list[str]:
    types = types or {}
    out = []
    for prop, is_opt in props.items():
        key = keys.get(prop, prop)
        if key not in wire:
            out.append(
                f"Swift decodes `{key}` but the model has no such field — "
                + ("UNCONDITIONAL decode crash" if not is_opt
                   else "permanently nil (dead UI), which is silent and worse to find"))
            continue
        if not is_opt and _may_be_null(wire[key]):
            out.append(f"`{key}` is nullable on the backend but non-Optional in Swift — "
                       f"a single null crashes this screen")
        want = _SWIFT_TO_PY.get(types.get(prop, ""), None)
        if want is not None:
            ann = wire[key].annotation
            base = next((a for a in get_args(ann) if a is not type(None)), ann)
            origin = get_origin(base)
            if base in (str, int, bool, float) and base not in want:
                out.append(f"`{key}` is {base.__name__} on the backend, "
                           f"{types[prop]} in Swift")
            elif want == (list,) and origin is not list:
                # Swift decodes an array; a scalar (or object) on the wire throws.
                name = getattr(base, "__name__", str(base))
                out.append(f"`{key}` is {name} on the backend, {types[prop]} in Swift")
            elif origin is list and want != (list,):
                out.append(f"`{key}` is a list on the backend, {types[prop]} in Swift")
    # Backend-only fields are deliberately NOT reported. The decoder ignores unknown keys
    # (`APIClient.swift:61-67`), and three exist today — SignUpResponse.token_type,
    # UserResponse.updated_at, ResearchStatusResponse.error_code — all correct. Asserting on
    # them would block every future backend field addition.
    return out


def _parsed(pair: Pair):
    path = _IOS / pair.swift_file
    assert path.exists(), f"{pair.swift_file} moved — update _PAIRS, do not delete the pair"
    body = _struct_body(_code(path), pair.swift_struct)
    assert body, f"struct {pair.swift_struct} not found in {pair.swift_file}"
    props = _properties(body)
    return body, props, _coding_keys(body, props)


# ── The contract ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pair", _PAIRS, ids=_IDS)
def test_swift_dto_matches_the_response_model(pair):
    body, props, keys = _parsed(pair)
    types = {p: _swift_type_base(body, p) for p in props}
    findings = _findings(_wire_fields(_model(pair)), props, keys, types)
    assert not findings, f"{pair.model} -> {pair.swift_file}:{pair.swift_struct}\n  " + \
                         "\n  ".join(findings)


@pytest.mark.parametrize("pair", _PAIRS, ids=_IDS)
def test_coding_keys_cover_exactly_the_properties(pair):
    """IDENTITY, not superset — this is the strongest anti-vacuity control in the module. A
    dead `_CK_ENUM` regex yields `{}`, which is not equal to a non-empty property set, so the
    whole parser cannot go quietly blind. It also catches a property added without a case."""
    body, props, keys = _parsed(pair)
    assert set(keys) == set(props), (
        f"{pair.swift_struct}: CodingKeys {sorted(set(keys) ^ set(props))} disagree with "
        f"properties")


@pytest.mark.parametrize("pair", _PAIRS, ids=_IDS)
def test_every_swift_type_is_one_we_can_compare(pair):
    """An unmapped base type must force a decision rather than be silently skipped — that is
    how a nested DTO would slip in and be compared as nothing."""
    body, props, _ = _parsed(pair)
    unmapped = {_swift_type_base(body, p) for p in props} - set(_SWIFT_TO_PY)
    assert not unmapped, f"{pair.swift_struct}: unmapped Swift types {unmapped}"


def test_manual_decoders_agree_with_their_optionality():
    """The three `extension X: Decodable` DTOs hand-roll `init(from:)`. The compiler cannot
    catch the one variant that still compiles: `let x: String?` assigned from
    `container.decode(...)` rather than `decodeIfPresent`, which THROWS on an absent key
    despite the `?`."""
    violations = []
    for pair in _PAIRS:
        src = _code(_IOS / pair.swift_file)
        m = re.search(rf"extension[ \t]+{re.escape(pair.swift_struct)}[ \t]*:[ \t]*Decodable[ \t]*\{{",
                      src)
        if not m:
            continue
        _, props, _ = _parsed(pair)
        for prop, how in re.findall(r"self\.(\w+)\s*=\s*try\s+container\.(decode|decodeIfPresent)\(",
                                    src[m.end():m.end() + 2000]):
            if prop not in props:
                continue
            if props[prop] and how == "decode":
                violations.append(f"{pair.swift_struct}.{prop} is Optional but uses "
                                  f"decode() — throws on an absent key")
            if not props[prop] and how == "decodeIfPresent":
                violations.append(f"{pair.swift_struct}.{prop} is non-Optional but uses "
                                  f"decodeIfPresent() — will not compile / lies about intent")
    assert not violations, "\n".join(violations)


def test_the_over_tolerant_trio_stays_over_tolerant():
    """`PasswordChangedResponse` declares three backend-REQUIRED fields as `String?` in Swift,
    on purpose: the app and backend deploy independently, so a build carrying this type can hit
    a Railway instance predating token rotation and get a bare `{"message": …}`. That is the
    only intentional over-tolerance in the nine — pinning it converts the explanatory comment
    at `APIEndpoint.swift:1074-1080` into an assertion."""
    _, props, _ = _parsed(next(p for p in _PAIRS if p.swift_struct == "PasswordChangedResponse"))
    assert {p for p, opt in props.items() if opt} == {"accessToken", "refreshToken", "userId"}
    model = _model(next(p for p in _PAIRS if p.model == "PasswordChangedResponse"))
    for f in ("access_token", "refresh_token", "user_id"):
        assert not _may_be_null(model.model_fields[f]), \
            f"{f} became nullable — the Swift side declares it Optional, so this is now SAFE, " \
            f"but the comment claiming the backend always sends it is wrong"


# ── Assumptions this module rests on ─────────────────────────────────────────

def test_the_ios_decoder_is_plain():
    """Every comparison here matches against the CodingKeys RAW VALUE. If the decoder ever
    gained `.convertFromSnakeCase`, all nine DTOs would break at runtime while this module
    stayed green — it would be measuring a contract the app no longer uses."""
    src = _code(_IOS / "Core/Services/APIClient.swift")
    dec = src[src.index("self.decoder = JSONDecoder()"):src.index("self.encoder = JSONEncoder()")]
    assert "convertFromSnakeCase" not in dec, \
        "the decoder now converts keys — every explicit CodingKey in the app double-converts"
    assert "dateDecodingStrategy = .iso8601" in dec


def test_no_route_excludes_unset_or_defaults():
    """The nullability-only predicate is sound ONLY because FastAPI serialises defaults. One
    `response_model_exclude_unset=True` and the five defaulted fields stop being emitted,
    turning a safe pairing into an absent-key crash that this module would not see."""
    hits = [f"{p.relative_to(_REPO)}:{i}"
            for p in (_BACKEND).rglob("*.py")
            for i, l in enumerate(p.read_text().splitlines(), 1)
            if re.search(r"response_model_exclude_(unset|defaults|none)\s*=\s*True", l)]
    assert not hits, "\n".join(hits)


def test_no_model_declares_a_field_alias():
    """Zero aliases today. The day one appears, the wire name stops equalling the field name
    and `_wire_fields` must be the thing that resolves it — this fails loudly rather than
    letting a naive comparison check the wrong string."""
    offenders = []
    for pair in _PAIRS:
        model = _model(pair)
        cfg = model.model_config
        if cfg.get("populate_by_name") or cfg.get("alias_generator"):
            offenders.append(f"{pair.model}: model_config sets an alias policy")
        for n, f in model.model_fields.items():
            if f.alias or f.serialization_alias:
                offenders.append(f"{pair.model}.{n} declares an alias")
    assert not offenders, "\n".join(offenders)


def test_endpoints_still_declare_these_response_models():
    """Without this, the pair table can describe a route that no longer returns that model."""
    expected = {
        "app/api/v1/endpoints/auth.py": {"TokenResponse", "SignUpResponse", "PasswordChangedResponse"},
        "app/api/v1/endpoints/users.py": {"UserResponse", "UserCreditsResponse"},
        "app/api/v1/endpoints/research.py": {"ResearchGenerationResponse", "ResearchStatusResponse"},
        "app/api/v1/endpoints/billing.py": {"VerifyPurchaseResponse"},
    }
    for rel, names in expected.items():
        src = (_REPO / "backend" / rel).read_text()
        declared = set(re.findall(r"response_model=(\w+)", src))
        missing = names - declared
        assert not missing, f"{rel} no longer declares {missing}"


def test_pair_table_covers_all_eight_models():
    assert {p.model for p in _PAIRS} == {
        "TokenResponse", "SignUpResponse", "PasswordChangedResponse", "VerifyPurchaseResponse",
        "UserCreditsResponse", "UserResponse", "ResearchGenerationResponse",
        "ResearchStatusResponse"}
    assert len(_PAIRS) == 9, "UserCreditsResponse has two decoders; both must stay listed"


# ── Anti-vacuity ─────────────────────────────────────────────────────────────

def test_swift_parser_finds_the_real_population():
    total = 0
    for pair in _PAIRS:
        _, props, _ = _parsed(pair)
        assert len(props) >= 3, f"{pair.swift_struct}: only {len(props)} properties parsed"
        total += len(props)
    assert total >= 38, f"only {total} properties across all nine pairs — parser went blind"


def test_the_struct_resolver_does_not_invent_structs():
    assert _struct_body(_code(_IOS / "Core/Services/AuthService.swift"), "NoSuchDTO") is None


def _synthetic(backend_nullable: bool, swift_optional: bool, extra_backend: bool = False):
    from pydantic import create_model
    from typing import Optional
    fields = {"always_there": (Optional[str], None) if backend_nullable else (str, ...)}
    if extra_backend:
        fields["extra_new"] = (str, ...)
    M = create_model("Synthetic", **fields)
    props = {"alwaysThere": swift_optional}
    keys = {"alwaysThere": "always_there"}
    return _findings(_wire_fields(M), props, keys)


def test_a_nullable_backend_field_under_a_non_optional_swift_field_is_caught():
    f = _synthetic(backend_nullable=True, swift_optional=False)
    assert len(f) == 1 and "always_there" in f[0]


def test_a_backend_only_field_is_not_a_violation():
    """Guards the drift class that must NEVER fail, or this module blocks every future
    backend addition."""
    assert _synthetic(backend_nullable=False, swift_optional=False, extra_backend=True) == []


def test_the_safe_asymmetry_is_not_flagged():
    """Backend-required under Swift-Optional is an over-tolerant client. Safe."""
    assert _synthetic(backend_nullable=False, swift_optional=True) == []


def test_a_defaulted_non_nullable_field_is_not_a_violation():
    """⚠️ THE control for the module's central decision. `token_type: str = "bearer"` is not
    `is_required()`, but it is always on the wire and never null, so a Swift `String` is
    correct. Five real fields depend on this. If someone "simplifies" the predicate back to
    `is_required()`, this test is what stops it."""
    from pydantic import create_model
    M = create_model("Defaulted", token_type=(str, "bearer"))
    assert not M.model_fields["token_type"].is_required(), "premise changed"
    assert not _may_be_null(M.model_fields["token_type"])
    assert _findings(_wire_fields(M), {"tokenType": False}, {"tokenType": "token_type"}) == []


def test_a_missing_backend_field_is_caught():
    from pydantic import create_model
    M = create_model("Empty", other=(str, ...))
    f = _findings(_wire_fields(M), {"gone": False}, {"gone": "gone"})
    assert len(f) == 1 and "UNCONDITIONAL" in f[0]
