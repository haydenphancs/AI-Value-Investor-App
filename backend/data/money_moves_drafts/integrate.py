"""Append the three verified articles to BOTH Money Moves bundles (identical bytes) and add back-links.

Run once, after the three clips exist:  python3 integrate.py
Idempotent guard: refuses to run if any new slug is already in the bundle.
"""
import json
import subprocess
from pathlib import Path

SCR = Path(__file__).parent
REPO = Path("/Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App")
FRONT = REPO / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"
VENDORED = REPO / "backend/data/money_moves.json"
AUDIO = REPO / "backend/data/money_moves_audio"

NEW = ["mcdonalds-the-real-estate-empire", "coca-cola-vs-pepsico", "kodak-inventing-the-future-it-couldnt-sell"]
# new slug -> titles of the existing/new articles it lists as related
RELATED = {
    "mcdonalds-the-real-estate-empire": ["Costco's Membership Magic", "Coca-Cola vs. PepsiCo", "How Amazon Built Its Moat"],
    "coca-cola-vs-pepsico": ["The Home Depot vs. Lowe's", "Visa vs. Mastercard", "McDonald's: The Real Estate Empire"],
    "kodak-inventing-the-future-it-couldnt-sell": ["The Fall of Sears", "Microsoft's Cloud Metamorphosis", "Meta's Metaverse Pivot"],
}
# existing slug -> new slug it gains as a related card (appended)
BACKLINKS = {
    "costcos-membership-magic": "mcdonalds-the-real-estate-empire",
    "the-home-depot-vs-lowes": "coca-cola-vs-pepsico",
    "the-fall-of-sears": "kodak-inventing-the-future-it-couldnt-sell",
    "microsofts-cloud-metamorphosis": "kodak-inventing-the-future-it-couldnt-sell",
}
KEY_ORDER = ["slug", "title", "subtitle", "cardSubtitle", "category", "author", "tagLabel", "heroGradientColors",
             "keyHighlights", "sections", "statistics", "readTimeMinutes", "sortOrder", "publishedDaysAgo",
             "isFeatured", "hasAudioVersion", "audioUrl", "audioDurationSeconds", "viewCount", "relatedArticles"]
SECTION_ORDER = ["title", "icon", "hasGlowEffect", "content"]


def duration(slug: str) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(AUDIO / f"{slug}.m4a")], capture_output=True, text=True, check=True).stdout
    return int(round(float(out.strip())))


def related_card(target: dict) -> dict:
    return {"title": target["title"], "subtitle": target["cardSubtitle"], "category": target["category"],
            "readTimeMinutes": target["readTimeMinutes"], "viewCount": "",
            "gradientColors": target["heroGradientColors"][:2]}


raw = FRONT.read_text()
assert raw == VENDORED.read_text(), "bundles differ before the edit"
data = json.loads(raw)
arts = data["articles"]
assert not {a["slug"] for a in arts} & set(NEW), "already integrated"

new_arts = []
for i, slug in enumerate(NEW):
    d = json.loads((SCR / f"{slug}.json").read_text())
    # learn_catalog_fetch.py merges the read-along with the duration its timings were cut against;
    # keep that value. ffprobe can read a slightly different length (encoder priming), so it is only
    # the fallback for a clip that came without read-along.
    if not isinstance(d.get("audioDurationSeconds"), (int, float)) or isinstance(d.get("audioDurationSeconds"), bool):
        d["audioDurationSeconds"] = duration(slug)
    if not any(b.get("readAlong") or b.get("itemsReadAlong") for s in d["sections"] for b in s["content"]):
        print(f"WARNING {slug}: no read-along in the draft — run scripts/learn_catalog_fetch.py first")
    d.update(author={"name": "Caydex Research", "title": "Editorial", "isVerified": False},
             sortOrder=len(arts) + i, publishedDaysAgo=0, isFeatured=False, hasAudioVersion=True,
             audioUrl=None, viewCount="")
    d["sections"] = [{k: s[k] for k in SECTION_ORDER if k in s} for s in d["sections"]]
    new_arts.append(d)

by_title = {a["title"]: a for a in arts + new_arts}
by_slug = {a["slug"]: a for a in arts + new_arts}
for d in new_arts:
    d["relatedArticles"] = [related_card(by_title[t]) for t in RELATED[d["slug"]]]
for i, d in enumerate(new_arts):
    unknown = set(d) - set(KEY_ORDER)
    assert not unknown, unknown
    new_arts[i] = {k: d[k] for k in KEY_ORDER if k in d}
for slug, target in BACKLINKS.items():
    a = by_slug[slug]
    card = related_card(by_slug[target] if target not in {n["slug"] for n in new_arts}
                        else next(n for n in new_arts if n["slug"] == target))
    assert card["title"] not in [r["title"] for r in a.get("relatedArticles", [])]
    a.setdefault("relatedArticles", []).append(card)

arts.extend(new_arts)
for i, a in enumerate(arts):
    assert a["sortOrder"] == i, (a["slug"], a["sortOrder"], i)
out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
FRONT.write_text(out)
VENDORED.write_text(out)
print(f"bundle now {len(arts)} articles; durations:", {d['slug']: d['audioDurationSeconds'] for d in new_arts})
