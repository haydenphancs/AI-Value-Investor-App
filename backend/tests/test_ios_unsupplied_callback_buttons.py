"""No shipped Button whose whole action calls an optional callback that no caller supplies.

`test_ios_no_dead_buttons.py` catches an action that is literally empty. This is the dead
button it cannot see:

    var onLikeTapped: (() -> Void)?
    Button(action: { onLikeTapped?() }) { … }

It compiles, it reads as wired, and it does nothing — because no call site ever passes
`onLikeTapped:`, so the optional chain short-circuits on nil every time. The Money Moves
comments UI shipped exactly that way: "Add a comment", "View all", "Load more", Like, Reply
and the author avatar, six controls behind one call site that passed none of them.

How it decides, over the whole iOS tree (comments stripped, string literals blanked,
`#Preview` / `PreviewProvider` dropped — a preview supplying a callback proves nothing):

1. Every type's optional Void-closure stored property (`var onX: ((Item) -> Void)?`).
2. Every call site of that type: labeled arguments and trailing closures.
3. A callback is SUPPLIED when some call site hands it a live value. Not live: `nil`, `{ }`,
   or a FORWARDER — `{ onY?(…) }` or a bare `onY` — whose `onY` is itself an unsupplied
   callback of the calling type. Solved to a fixpoint, so a forwarding chain dies with its
   source (ArticleCommentCard.onLikeTapped ← MoneyMoveArticleCommentsSection.onCommentLiked
   ← nothing).
4. A Button whose entire action is `onX?(…)` (or `onX ?? {}`) with `onX` unsupplied is dead.

Conservative on purpose — a guard that cries wolf gets its list padded until it means nothing:

- A type with no call site found is skipped: unreachable code is a different rot.
- A live trailing closure is taken to supply every callback of that type.
- A callback assigned by name (`copy.onX = …`, a modifier-style setter, a method writing
  `onX = …`) or reached by key path counts as supplied.
- An explicit `init` is followed from argument label to `self.onX = param`; an init that
  delegates (`self.init(`), or any init declared in an extension, makes the type unanalysed.
- `.onTapGesture { onX?() }` is NOT checked. An unfired tap gesture is the tap-SWALLOW idiom
  (ScannerCard / SignalDisclosureRow → HomeDashboardView): its effect is the gesture itself.
- The rule is "no call site supplies it", not "every call site does". A Button behind
  `if onX != nil` is fine, and source scanning cannot see conditional rendering.
- An action with a second statement (`{ Haptics.tap(); onX?() }`) is not "only a callback".

`_KNOWN_UNSUPPLIED` is SHRINK-ONLY and counted per site, like the sibling's `_KNOWN_DEAD`.
"""
import re
from functools import lru_cache

import pytest

from test_ios_no_dead_buttons import IOS, _drop_previews, _strip_comments

# (file relative to IOS, "Type.callback") → (dead Button sites tolerated, why).
_KNOWN_UNSUPPLIED = {
    ("Views/Atoms/FollowButton.swift", "FollowButton.onTap"): (
        1,
        "its only caller, SearchResultRow, forwards `onFollowTap`, which no caller supplies. "
        "Unreachable today: SearchViewModel builds every live result with isFollowable: false "
        "(following is account-scoped; WhaleService.toggleFollow is the real one).",
    ),
}

_BRACE = re.compile(r"[{}]")
_PAREN = re.compile(r"[()]")
_STR_STOP = re.compile(r'\\\(|\\.|"""|"|\n', re.S)
_INTERP_STOP = re.compile(r'[()"]')


def _blank(s: str) -> str:
    return re.sub(r"[^\n]", " ", s)


def _mask_strings(src: str) -> str:
    """Blank string-literal contents, interpolations included, keeping every offset.

    Labels and braces inside strings (`"onTap: {"`) must not read as code. Regex-driven
    rather than per character: the tree is ~8 MB.
    """
    n = len(src)

    def scan_string(i: int, triple: bool) -> tuple[int, int]:
        """From just after an opening delimiter → (content end, index after the close)."""
        while True:
            m = _STR_STOP.search(src, i)
            if not m:
                return n, n
            t = m.group()
            if t == "\\(":
                i = scan_interp(m.end())
            elif t.startswith("\\"):
                i = m.end()
            elif t == '"""' and triple:
                return m.start(), m.end()
            elif t.startswith('"') and not triple:
                return m.start(), m.start() + 1
            elif t == "\n" and not triple:
                return m.start(), m.start()   # unterminated: stop at the line end
            else:
                i = m.end()

    def scan_interp(i: int) -> int:
        depth = 1
        while True:
            m = _INTERP_STOP.search(src, i)
            if not m:
                return n
            if m.group() == '"':
                triple = src.startswith('"""', m.start())
                i = scan_string(m.start() + (3 if triple else 1), triple)[1]
                continue
            depth += 1 if m.group() == "(" else -1
            i = m.end()
            if depth == 0:
                return i

    out, last, i = [], 0, 0
    while (q := src.find('"', i)) >= 0:
        triple = src.startswith('"""', q)
        start = q + (3 if triple else 1)
        end, after = scan_string(start, triple)
        out += [src[last:start], _blank(src[start:end])]
        last = i = max(after, end, start)
    out.append(src[last:])
    return "".join(out)


def _clean(src: str) -> str:
    return _drop_previews(_mask_strings(_strip_comments(src)))


def _close(s: str, i: int) -> int:
    """Index of the bracket closing the `{` or `(` at s[i], or -1."""
    pat, opener = (_BRACE, "{") if s[i] == "{" else (_PAREN, "(")
    depth = 0
    for m in pat.finditer(s, i):
        depth += 1 if m.group() == opener else -1
        if depth == 0:
            return m.start()
    return -1


def _top_level(body: str) -> str:
    """`body` with every nested {…} blanked, so only the type's own members remain."""
    out, depth, last = [], 0, 0
    for m in _BRACE.finditer(body):
        if m.group() == "{":
            if depth == 0:
                out.append(body[last:m.start()])
                last = m.start()
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                out.append(_blank(body[last:m.end()]))
                last = m.end()
    out.append(_blank(body[last:]) if depth else body[last:])
    return "".join(out)


def _split_top(s: str) -> list[str]:
    """`s` split on its top-level commas."""
    parts, depth, cur = [], 0, 0
    for i, c in enumerate(s):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(s[cur:i])
            cur = i + 1
    if s[cur:].strip():
        parts.append(s[cur:])
    return parts


def _split_args(s: str) -> list[tuple[str | None, str]]:
    """Top-level `label: expr` pairs of an argument list (label None when positional)."""
    out = []
    for p in _split_top(s):
        m = re.match(r"\s*(\w+)\s*:(?!:)\s*(.*)$", p, re.S)
        out.append((m.group(1), m.group(2).strip()) if m else (None, p.strip()))
    return out


_FIRST_TRAILING = re.compile(r"\s*\{")
_LABELED_TRAILING = re.compile(r"\s*\w+\s*:\s*\{")


def _trailing_closures(s: str, j: int) -> list[str]:
    """Closure texts trailing a call at s[j:] — the unlabeled one, then any `label: { … }`."""
    out = []
    m = _FIRST_TRAILING.match(s, j)
    while m:
        ob = m.end() - 1
        cb = _close(s, ob)
        if cb < 0:
            break
        out.append(s[ob:cb + 1])
        m = _LABELED_TRAILING.match(s, cb + 1)
    return out


_TYPE = re.compile(
    r"^[ \t]*(?P<mods>(?:@[\w.]+(?:\([^)\n]*\))?\s+)*"
    r"(?:(?:private|fileprivate|internal|public|package|open|final|indirect)\s+)*)"
    r"(?P<kind>struct|class|actor|enum|extension)\s+(?P<name>[A-Za-z_][\w.]*)", re.M)
_CALLBACK = re.compile(
    r"^[ \t]*(?:@\w+\s+)*(?:(?:private|fileprivate|internal|public|package)(?:\(set\))?\s+)*"
    r"(?:var|let)\s+(?P<name>\w+)\s*:\s*\(\s*(?:@\w+\s+)?\((?:[^()\n]|\([^()\n]*\))*\)\s*"
    r"(?:async\s+)?(?:throws\s+)?->\s*(?:Void|\(\))\s*\)\?", re.M)
_INIT = re.compile(r"(?<![\w.])init\s*[?!]?\s*(?:<[^>\n]*>)?\s*\(")
_ONLY_CALL = re.compile(r"\s*(?:self\s*\.\s*)?(?P<name>\w+)\s*\?\s*(?P<open>\()", re.S)
_COALESCE_EMPTY = re.compile(r"\s*(?:self\s*\.\s*)?(?P<name>\w+)\s*\?\?\s*\{\s*\}\s*")
_CLOSURE_HEAD = re.compile(r"\s*(?:\[[^\]]*\]\s*)?(?:\(?\s*\w+(?:\s*,\s*\w+)*\s*\)?)\s+in\b")


def _only_optional_call(code: str) -> str | None:
    """`onX` when `code` is exactly one `onX?(…)` call (optionally `self.`-qualified)."""
    m = _ONLY_CALL.match(code)
    if not m:
        return None
    close = _close(code, m.start("open"))
    if close < 0 or code[close + 1:].strip():
        return None
    return m.group("name")


def _closure_body(expr: str) -> str | None:
    """The statements of a closure literal `{ [cap] args in … }`, or None if not a closure."""
    e = expr.strip()
    if not (e.startswith("{") and e.endswith("}")):
        return None
    inner = e[1:-1]
    head = _CLOSURE_HEAD.match(inner)
    return inner[head.end():] if head else inner


class _Tree:
    """Everything the fixpoint needs, parsed once from `{relative path: cleaned source}`."""

    def __init__(self, files: dict[str, str]):
        self.files = files
        self.spans: dict[str, list[tuple[int, int, tuple, str]]] = {}
        self.callbacks: dict[tuple, set[str]] = {}
        self.blanket: set[tuple] = set()           # types left unanalysed
        self.always: set[tuple[tuple, str]] = set()  # callbacks proven set some other way
        self.labels: dict[tuple, dict[str | None, set[str]]] = {}
        self.sites: dict[tuple, list[dict]] = {}
        self._index_types()
        self._index_callbacks()
        self._index_assignments()
        self._index_call_sites()

    # -- types -------------------------------------------------------------------------
    def _index_types(self):
        decls = []
        for f, src in self.files.items():
            for m in _TYPE.finditer(src):
                name = re.sub(r"<.*", "", m.group("name")).split(".")[-1]
                if name in {"func", "var", "let", "subscript", "init", "case"}:
                    continue   # `class func`, `class var`
                ob = src.find("{", m.end())
                cb = _close(src, ob) if ob >= 0 else -1
                if cb < 0:
                    continue
                private = bool(re.search(r"\b(?:private|fileprivate)\b", m.group("mods")))
                decls.append((f, ob + 1, cb, m.group("kind"), name, private))
        private_keys = {(n, f) for f, _, _, k, n, p in decls if p and k != "extension"}
        for f, start, end, kind, name, private in decls:
            if kind == "extension":
                key = (name, f) if (name, f) in private_keys else (name, "")
            else:
                key = (name, f) if private else (name, "")
            self.spans.setdefault(f, []).append((start, end, key, kind))

    def enclosing(self, f: str, pos: int) -> tuple | None:
        best = None
        for start, end, key, _ in self.spans.get(f, ()):
            if start <= pos < end and (best is None or start > best[0]):
                best = (start, key)
        return best and best[1]

    def bodies(self, key: tuple, kinds=("struct", "class", "actor", "extension")):
        for f, spans in self.spans.items():
            for start, end, k, kind in spans:
                if k == key and kind in kinds:
                    yield f, start, end, kind

    # -- callbacks and how an init sets them ---------------------------------------------
    def _index_callbacks(self):
        for f, spans in self.spans.items():
            for start, end, key, kind in spans:
                if kind not in ("struct", "class", "actor"):
                    continue
                names = {m.group("name") for m in _CALLBACK.finditer(_top_level(self.files[f][start:end]))}
                if names:
                    self.callbacks.setdefault(key, set()).update(names)
        for key, names in self.callbacks.items():
            labels: dict[str | None, set[str]] = {}
            explicit = False
            for f, start, end, kind in self.bodies(key):
                body = self.files[f][start:end]
                for m in _INIT.finditer(_top_level(body)):
                    if kind == "extension":
                        self.blanket.add(key)
                        continue
                    explicit = True
                    self._follow_init(key, names, body, m.end() - 1, labels)
            self.labels[key] = labels if explicit else {n: {n} for n in names}

    def _follow_init(self, key, names, body, open_paren, labels):
        close = _close(body, open_paren)
        ob = body.find("{", close)
        cb = _close(body, ob) if close >= 0 and ob >= 0 else -1
        if cb < 0:
            self.blanket.add(key)
            return
        params = {}   # internal name → argument label (None when `_`)
        for raw in _split_top(body[open_paren + 1:close]):
            pm = re.match(r"\s*(?:(\w+)\s+)?(\w+)\s*:", raw)
            if pm:
                ext = pm.group(1) or pm.group(2)
                params[pm.group(2)] = None if ext == "_" else ext
        init_body = body[ob + 1:cb]
        if re.search(r"(?<![\w.])self\s*\.\s*init\s*\(", init_body):
            self.blanket.add(key)
            return
        for am in re.finditer(r"(?:(?<![\w.])self\s*\.\s*|(?<![\w.]))(\w+)\s*=(?!=)\s*([^\n;]*)", init_body):
            cb_name, rhs = am.group(1), am.group(2).strip()
            if cb_name not in names or rhs == "nil":
                continue
            src_param = re.match(r"(\w+)", rhs)
            if src_param and src_param.group(1) in params:
                labels.setdefault(params[src_param.group(1)], set()).add(cb_name)
            else:
                self.always.add((key, cb_name))

    # -- assignments that make a callback settable without the init -----------------------
    def _index_assignments(self):
        # `copy.onX = a` (a modifier-style setter), `vm?.onX = …`, or a key path `\.onX`. A
        # `self.onX =` is not counted here: it is an init's business, or a member's (below).
        settable = set()
        for src in self.files.values():
            for m in re.finditer(r"\.\s*(\w+)\s*=(?!=)", src):
                if not re.search(r"(?<![\w.])self\s*$", src[max(0, m.start() - 12):m.start()]):
                    settable.add(m.group(1))
            settable.update(re.findall(r"\\(?:[A-Z][\w.]*)?\.(\w+)", src))
        for key, names in self.callbacks.items():
            for cb_name in names & settable:
                self.always.add((key, cb_name))
            # A member writing its own callback (`onX = …` / `self.onX = …`) outside an init.
            for f, start, end, _ in self.bodies(key):
                body = self.files[f][start:end]
                for im in reversed(list(_INIT.finditer(_top_level(body)))):
                    close = _close(body, im.end() - 1)
                    ob = body.find("{", close) if close >= 0 else -1
                    cb = _close(body, ob) if ob >= 0 else -1
                    if cb > 0:
                        body = body[:ob] + _blank(body[ob:cb + 1]) + body[cb + 1:]
                for cb_name in names:
                    for am in re.finditer(rf"(?<![\w.]){cb_name}\s*=(?!=)", body):
                        before = body[max(0, am.start() - 16):am.start()]
                        if not re.search(r"\b(?:var|let)\s+$", before):   # a local shadow
                            self.always.add((key, cb_name))

    # -- call sites --------------------------------------------------------------------
    def _index_call_sites(self):
        names = {k[0] for k in self.callbacks}
        # Any capitalised name then `(` / `{`, filtered by set membership afterwards: a
        # 250-way alternation here cost ~1.7 s over the tree.
        call = re.compile(r"(?<![\w.])(?:[A-Z]\w*\.)*(?P<name>[A-Z]\w*)"
                          r"(?:\s*<[^<>(){}\n]*>)?(?:\.init)?\s*(?P<open>[({])")
        decl = re.compile(r"\b(?:struct|class|actor|enum|extension|protocol)\s+(?:[A-Z]\w*\.)*$")
        type_position = re.compile(r"(?:->|:)\s*(?:[A-Z]\w*\.)*$")   # `-> Row {`, `var r: Row {`
        for f, src in self.files.items():
            for m in call.finditer(src):
                if m.group("name") not in names:
                    continue
                window = max(0, m.start() - 40)
                if decl.search(src, window, m.start()) or (
                        m.group("open") == "{" and type_position.search(src, window, m.start())):
                    continue
                key = (m.group("name"), f)
                if key not in self.callbacks:
                    key = (m.group("name"), "")
                if key not in self.callbacks:
                    continue
                op = m.start("open")
                if src[op] == "(":
                    cp = _close(src, op)
                    if cp < 0:
                        continue
                    args, trailing = _split_args(src[op + 1:cp]), _trailing_closures(src, cp + 1)
                else:
                    args, trailing = [], _trailing_closures(src, op)
                self.sites.setdefault(key, []).append(dict(
                    file=f, caller=self.enclosing(f, m.start()), args=args, trailing=trailing))

    # -- the fixpoint --------------------------------------------------------------------
    def _live(self, expr: str, caller, supplied) -> bool:
        body = _closure_body(expr)
        if body is None:
            e = expr.strip()
            if e == "nil":
                return False
            m = re.fullmatch(r"(?:self\s*\.\s*)?(\w+)", e)
            name = m and m.group(1)
        else:
            if not body.strip():
                return False
            name = _only_optional_call(body)
        return supplied.get((caller, name), True) if name else True

    def unsupplied(self) -> set[tuple[tuple, str]]:
        analysable = {k for k in self.callbacks if k not in self.blanket and self.sites.get(k)}
        supplied = {(k, n): True for k in analysable for n in self.callbacks[k]}
        changed = True
        while changed:
            changed = False
            for (key, cb_name), was in supplied.items():
                if not was or (key, cb_name) in self.always:
                    continue
                labels = self.labels[key]
                live = False
                for site in self.sites[key]:
                    for label, expr in site["args"]:
                        if cb_name in labels.get(label, ()) and self._live(expr, site["caller"], supplied):
                            live = True
                    if any(self._live(c, site["caller"], supplied) for c in site["trailing"]):
                        live = True   # conservative: a live trailing closure may bind to any callback
                if not live:
                    supplied[(key, cb_name)] = False
                    changed = True
        return {k for k, v in supplied.items() if not v}

    def button_actions(self):
        """(file, line, enclosing type key, callback name) for every Button whose whole
        action is one optional-callback call (or `onX ?? {}`)."""
        for f, src in self.files.items():
            for m in re.finditer(r"(?<![\w.])Button\b\s*", src):
                i, action = m.end(), None
                if src.startswith("(", i):
                    cp = _close(src, i)
                    if cp < 0:
                        continue
                    action = next((e for l, e in _split_args(src[i + 1:cp]) if l == "action"), None)
                    i = cp + 1
                if action is None:
                    trailing = _trailing_closures(src, i)
                    action = trailing[0] if trailing else None
                if action is None:
                    continue
                body = _closure_body(action)
                if body is not None:
                    name = _only_optional_call(body)
                else:
                    cm = _COALESCE_EMPTY.fullmatch(action)
                    name = cm and cm.group("name")
                if name:
                    yield f, src[:m.start()].count("\n") + 1, self.enclosing(f, m.start()), name


def _dead_buttons(files: dict[str, str]) -> list[tuple[str, int, str]]:
    tree = _Tree(files)
    dead = tree.unsupplied()
    return sorted((f, line, f"{key[0]}.{name}")
                  for f, line, key, name in tree.button_actions() if (key, name) in dead)


@lru_cache(maxsize=1)
def _scan() -> tuple[_Tree, tuple]:
    files = {str(p.relative_to(IOS)): _clean(p.read_text()) for p in sorted(IOS.rglob("*.swift"))}
    assert len(files) > 400, f"only {len(files)} Swift files — the sweep is scanning the wrong tree"
    return _Tree(files), tuple(_dead_buttons(files))


def _by_key(found) -> dict[tuple[str, str], list[int]]:
    out: dict[tuple[str, str], list[int]] = {}
    for f, line, label in found:
        out.setdefault((f, label), []).append(line)
    return out


def test_no_button_only_calls_a_callback_nobody_supplies():
    found = _by_key(_scan()[1])
    new = {k: ls for k, ls in found.items() if len(ls) > _KNOWN_UNSUPPLIED.get(k, (0, ""))[0]}
    assert not new, (
        "Buttons whose whole action is an optional callback no call site supplies — they "
        "render, take the tap, and do nothing. Pass the callback, or remove the control:\n  "
        + "\n  ".join(f"{f}:{ls}  {label}" for (f, label), ls in new.items()))


def test_the_known_unsupplied_list_only_shrinks():
    found = _by_key(_scan()[1])
    fixed = {k: (n, len(found.get(k, []))) for k, (n, _) in _KNOWN_UNSUPPLIED.items()
             if len(found.get(k, [])) < n}
    assert not fixed, f"fixed — lower the count or remove from _KNOWN_UNSUPPLIED: {fixed}"


def test_the_sweep_is_not_vacuous():
    """A regex that silently matches nothing would make every other test here pass."""
    tree = _scan()[0]
    n_callbacks = sum(len(v) for v in tree.callbacks.values())
    n_sites = sum(len(v) for v in tree.sites.values())
    n_buttons = sum(1 for _ in tree.button_actions())
    assert n_callbacks > 250, n_callbacks
    assert n_sites > 250, n_sites
    assert n_buttons > 100, n_buttons


def _type_body(src: str, kind_and_name: str) -> str:
    m = re.search(rf"^[ \t]*(?:\w+\s+)*{kind_and_name}\b[^{{]*\{{", src, re.M)
    assert m, f"{kind_and_name} not found — this pin is looking at the wrong declaration"
    return src[m.end():_close(src, m.end() - 1)]


def test_the_money_moves_comments_ui_stays_removed():
    """There is no comment backend: every comment the section could show is authored JSON —
    fiction presented as user content — and every control on it was dead."""
    tree = _scan()[0]
    for name in ("MoneyMoveArticleCommentsSection", "ArticleCommentCard"):
        declared = [f for f, spans in tree.spans.items() for *_, key, kind in spans
                    if key[0] == name and kind != "extension"]
        assert not declared, f"{name} is back in {declared}"
    content = _type_body(tree.files["Views/Organisms/MoneyMoveArticleContent.swift"],
                         "struct MoneyMoveArticleContent")
    assert not re.search(r"\bcomment", content, re.I), "MoneyMoveArticleContent renders comments again"


def test_article_comments_still_decode_tolerantly():
    """Removing the UI must not tighten the content contract (.claude/rules/learn-content.md):
    served rows and old app builds may still carry `comments` / `commentCount`, and a required
    or strictly-decoded field would drop the whole article."""
    src = _scan()[0].files["Models/MoneyMovesContentModels.swift"]
    top = _top_level(_type_body(src, "struct MoneyMoveArticleDTO"))
    assert re.search(r"\blet\s+comments\s*:\s*\[ArticleCommentDTO\]\?", top), "comments must stay Optional"
    assert re.search(r"\blet\s+commentCount\s*:\s*Int\?", top), "commentCount must stay Optional"
    decoder = _type_body(src, "extension MoneyMoveArticleDTO")   # init(from:) lives here
    assert re.search(r"\bcomments\s*=\s*\w+\.isEmpty\s*\?\s*nil", decoder)
    assert re.search(r"lenientArray\(\s*ArticleCommentDTO\.self\s*,\s*forKey:\s*\.comments\s*\)", decoder)
    assert re.search(r"\bcommentCount\s*=\s*c\.flexibleInt\(\s*forKey:\s*\.commentCount\s*\)", decoder)
    comment_dto = _type_body(src, "struct ArticleCommentDTO")
    assert not re.search(r"\.decode\(", comment_dto), "a comment field became strictly required"


# -- the detector itself ---------------------------------------------------------------

_ROW = """
struct Row: View {
    let title: String
    var onTap: (() -> Void)?
    var body: some View { Button(action: { onTap?() }) { Text("x") } }
}
"""


def _tree(**files: str) -> dict[str, str]:
    return {f"{name}.swift": _clean(src) for name, src in files.items()}


@pytest.mark.parametrize("files, expected", [
    # The bug itself: the only caller passes nothing.
    (dict(Row=_ROW, Screen='struct S: View { var body: some View { Row(title: "a") } }'),
     [("Row.swift", 5, "Row.onTap")]),
    # Supplied → live.
    (dict(Row=_ROW, Screen='struct S: View { var body: some View { Row(title: "a", onTap: { go() }) } }'), []),
    # One live caller is enough (conditional rendering is out of reach, see the docstring).
    (dict(Row=_ROW, A='struct A: View { var body: some View { Row(title: "a") } }',
          B='struct B: View { var body: some View { Row(title: "b", onTap: go) } }'), []),
    # nil and { } are not supplies.
    (dict(Row=_ROW, Screen='struct S: View { var body: some View { Row(title: "a", onTap: nil) } }'),
     [("Row.swift", 5, "Row.onTap")]),
    (dict(Row=_ROW, Screen='struct S: View { var body: some View { Row(title: "a", onTap: { }) } }'),
     [("Row.swift", 5, "Row.onTap")]),
    # A forwarder of an unsupplied callback is dead, through the chain — the comments shape.
    (dict(Row=_ROW, Section="""
struct Section: View {
    var onPick: ((Int) -> Void)?
    var body: some View { Row(title: "a", onTap: { onPick?(1) }) }
}""", Screen="struct S: View { var body: some View { Section() } }"),
     [("Row.swift", 5, "Row.onTap")]),
    # ...and the same chain comes alive when its source is supplied.
    (dict(Row=_ROW, Section="""
struct Section: View {
    var onPick: ((Int) -> Void)?
    var body: some View { Row(title: "a", onTap: { onPick?(1) }) }
}""", Screen="struct S: View { var body: some View { Section(onPick: { i in open(i) }) } }"), []),
    # Bare forwarding of an optional.
    (dict(Row=_ROW, Section="""
struct Section: View {
    var onPick: (() -> Void)?
    var body: some View { Row(title: "a", onTap: onPick) }
}""", Screen="struct S: View { var body: some View { Section() } }"),
     [("Row.swift", 5, "Row.onTap")]),
    # Trailing closure: live supplies, dead forwarder does not (the FollowButton shape).
    (dict(Row=_ROW, Screen='struct S: View { var body: some View { Row(title: "a") { go() } } }'), []),
    (dict(Row=_ROW, Section="""
struct Section: View {
    var onFollow: (() -> Void)?
    var body: some View { Row(title: "a") { onFollow?() } }
}""", Screen="struct S: View { var body: some View { Section() } }"),
     [("Row.swift", 5, "Row.onTap")]),
    # Every Button spelling, plus `self.` and `?? {}`.
    (dict(Row="""
struct Row: View {
    var onA: (() -> Void)?
    var onB: ((String) -> Void)?
    var onC: (() -> Void)?
    var onD: (() -> Void)?
    var onE: (() -> Void)?
    var body: some View {
        Button { onA?() } label: { Text("a") }
        Button("B") { onB?("x") }
        Button("C", action: { self.onC?() })
        Button(role: .destructive) { onD?() } label: { Text("d") }
        Button(action: onE ?? {}) { Text("e") }
    }
}""", Screen="struct S: View { var body: some View { Row() } }"),
     [("Row.swift", 9, "Row.onA"), ("Row.swift", 10, "Row.onB"), ("Row.swift", 11, "Row.onC"),
      ("Row.swift", 12, "Row.onD"), ("Row.swift", 13, "Row.onE")]),
    # Out of scope by design: tap-swallow gestures, and multi-statement actions.
    (dict(Row="""
struct Row: View {
    var onBodyTap: (() -> Void)?
    var onGo: (() -> Void)?
    var body: some View {
        Color.clear.onTapGesture { onBodyTap?() }
        Button { Haptics.tap(); onGo?() } label: { Text("x") }
    }
}""", Screen="struct S: View { var body: some View { Row() } }"), []),
    # A comment or a string that LOOKS like a supply is not one.
    (dict(Row=_ROW, Screen="""
struct S: View {
    var body: some View {
        // Row(title: "a", onTap: { go() })
        Row(title: "onTap: { go() }")
    }
}"""), [("Row.swift", 5, "Row.onTap")]),
    # Only a preview supplies it → still dead in the shipped app.
    (dict(Row=_ROW, Screen='struct S: View { var body: some View { Row(title: "a") } }',
          P='#Preview {\n    Row(title: "p", onTap: { print(1) })\n}'),
     [("Row.swift", 5, "Row.onTap")]),
    # No call site at all → unreachable, not this guard's problem.
    (dict(Row=_ROW), []),
    # Settable some other way → supplied.
    (dict(Row=_ROW + "extension Row { func onTapping(_ a: @escaping () -> Void) -> Row "
                     "{ var c = self; c.onTap = a; return c } }",
          Screen='struct S: View { var body: some View { Row(title: "a").onTapping { go() } } }'), []),
    # Explicit init: followed from label to property...
    (dict(Row="""
struct Row: View {
    var onTap: (() -> Void)?
    init(title: String, action: (() -> Void)? = nil) { self.onTap = action }
    var body: some View { Button(action: { onTap?() }) { Text("x") } }
}""", Screen='struct S: View { var body: some View { Row(title: "a", action: { go() }) } }'), []),
    # ...and an init that never takes it leaves it dead.
    (dict(Row="""
struct Row: View {
    var onTap: (() -> Void)?
    init(title: String) { }
    var body: some View { Button(action: { onTap?() }) { Text("x") } }
}""", Screen='struct S: View { var body: some View { Row(title: "a") } }'),
     [("Row.swift", 5, "Row.onTap")]),
    # Private types are per file: a same-named type elsewhere does not lend its callers.
    (dict(A='private struct Row: View {\n    var onTap: (() -> Void)?\n'
            '    var body: some View { Button(action: { onTap?() }) { Text("x") } }\n}\n'
            'struct A: View { var body: some View { Row() } }',
          B='private struct Row: View {\n    var onTap: (() -> Void)?\n'
            '    var body: some View { Button(action: { onTap?() }) { Text("x") } }\n}\n'
            'struct B: View { var body: some View { Row(onTap: { go() }) } }'),
     [("A.swift", 3, "Row.onTap")]),
])
def test_the_detector_itself(files, expected):
    """Anti-vacuity: each rule catches what it claims and exempts only what it says."""
    assert _dead_buttons(_tree(**files)) == expected
