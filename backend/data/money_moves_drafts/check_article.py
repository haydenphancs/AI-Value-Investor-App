"""Check ONE draft Money Moves article (a JSON object file) against the bundle's rules.

Run from backend/:  ./venv/bin/python <this file> <draft.json>
Exit 0 = clean. Prints every problem otherwise.
"""
import json
import re
import sys
from pathlib import Path

BACKEND = Path("/Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App/backend")
sys.path.insert(0, str(BACKEND))

from app.services import learn_content_rules as R  # noqa: E402
from app.services.learn_weekly import validators as V  # noqa: E402

MISCONDUCT = re.compile(r"\b(?:fraud\w*|convicted|sentenced|indicted|charged|prison|embezzl\w*|ponzi|"
                        r"scandal|misconduct|deceiv\w*|lied|lies)\b", re.I)
ROLE_PRONOUN = re.compile(r"\b(?:ceo|chief executive|founder|co-?founder|chairman|chairwoman|boss|"
                          r"president|heir|he|him|his|himself|she|her|hers|herself)\b(?![-])(?!s\b)", re.I)
REQUIRED = ["slug", "title", "subtitle", "cardSubtitle", "category", "tagLabel",
            "heroGradientColors", "keyHighlights", "sections", "statistics", "readTimeMinutes"]

art = json.loads(Path(sys.argv[1]).read_text())
problems, notes = [], []

for k in REQUIRED:
    if k not in art:
        problems.append(f"missing key {k}")
if art.get("category") not in ("blueprints", "battles", "valueTraps"):
    problems.append(f"bad category {art.get('category')!r}")

texts = []  # (where, text)
for k in ("title", "subtitle", "cardSubtitle", "tagLabel"):
    texts.append((k, art.get(k, "")))
for i, h in enumerate(art.get("keyHighlights", [])):
    if h.get("icon") not in R.ICON_ALLOWLIST:
        problems.append(f"keyHighlights[{i}] icon {h.get('icon')!r} not in ICON_ALLOWLIST")
    texts += [(f"keyHighlights[{i}].title", h.get("title", "")), (f"keyHighlights[{i}].description", h.get("description", ""))]
if len(art.get("keyHighlights", [])) != 3:
    problems.append("keyHighlights should hold 3 entries")
for i, s in enumerate(art.get("statistics", [])):
    if s.get("trend") not in ("up", "down", "neutral"):
        problems.append(f"statistics[{i}] trend {s.get('trend')!r}")
    texts += [(f"statistics[{i}].value", s.get("value", "")), (f"statistics[{i}].label", s.get("label", ""))]

body_words = 0
blocks_total = 0
for i, sec in enumerate(art.get("sections", [])):
    if sec.get("icon") not in R.ICON_ALLOWLIST:
        problems.append(f"sections[{i}] icon {sec.get('icon')!r} not in ICON_ALLOWLIST")
    texts.append((f"sections[{i}].title", sec.get("title", "")))
    for j, b in enumerate(sec.get("content", [])):
        blocks_total += 1
        where = f"sections[{i}].content[{j}]"
        t = b.get("type")
        if t not in ("paragraph", "subheading", "callout", "bulletList"):
            problems.append(f"{where}: block type {t!r} (use paragraph/subheading/callout/bulletList; no quote blocks)")
        if t == "callout":
            if b.get("icon") not in R.ICON_ALLOWLIST:
                problems.append(f"{where}: callout icon {b.get('icon')!r} not in ICON_ALLOWLIST")
            if b.get("style") not in ("highlight", "warning", "info"):
                problems.append(f"{where}: callout style {b.get('style')!r}")
        if t == "bulletList":
            for n, it in enumerate(b.get("items") or []):
                texts.append((f"{where}.items[{n}]", it))
                body_words += len(it.split())
        elif b.get("text"):
            texts.append((where, b["text"]))
            body_words += len(b["text"].split())
            if len(b["text"].split()) > 75:
                problems.append(f"{where}: {len(b['text'].split())} words (cap 75 per block)")
        for issue in V.validate_block(where, b, section_kind="history"):
            problems.append(f"{where}: validator {issue.code}: {issue.detail}")

# Header strings through the same validator, as a paragraph.
for where, t in texts:
    if not where.startswith("sections[") or ".content[" not in where:
        for issue in V.validate_block(where, {"type": "paragraph", "text": t}, section_kind="history"):
            problems.append(f"{where}: validator {issue.code}: {issue.detail}")

all_text = " ".join(t for _w, t in texts)
normed = R.norm(all_text)
for p in R.BANNED_PHRASES:
    if p in normed:
        problems.append(f"banned phrase {p!r}")
for p, who in R.ATTRIBUTION_REQUIRED.items():
    if p in normed:
        problems.append(f"attribution-required phrase {p!r} (avoid quotes entirely)")
mis = [w for _w, t in texts for w in MISCONDUCT.findall(t)]
if mis:
    problems.append(f"misconduct words (keep out of a no-wrongdoing case study): {mis}")
roles = [(w, m) for w, t in texts for m in ROLE_PRONOUN.findall(t)]
if roles:
    notes.append(f"role/pronoun mentions (allowed, but each such sentence is dropped from the marketing fact sheet; keep few): {len(roles)} -> {roles[:12]}")
if '"' in all_text or "“" in all_text:
    notes.append("contains quotation marks: no direct quotes of people (scare quotes for a term are OK)")

print(f"body words: {body_words}  blocks: {blocks_total}  sections: {len(art.get('sections', []))}")
print(f"est. narration seconds (~2.42 words/s incl. headers): {round((body_words + 60) / 2.42)}")
for n in notes:
    print("NOTE:", n)
if problems:
    print(f"\n{len(problems)} PROBLEM(S):")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("CLEAN")
