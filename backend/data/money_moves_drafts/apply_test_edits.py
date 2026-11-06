"""Move the hard-coded catalog size 23 -> 26 in the three tests that pin it (run AFTER integrate.py).
Exact-string replacements; each must match once or nothing is written."""
from pathlib import Path

TESTS = Path("/Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App/backend/tests")
EDITS = {
    "test_learn_weekly_validators_corpus.py": [
        ('"""The in-app weekly validators, run over the 23 hand-made Money Moves articles.',
         '"""The in-app weekly validators, run over the 26 hand-made Money Moves articles.'),
        ("#: slug → the validator codes its blocks hit. Articles with no hit are absent\n"
         "#: (`nvidias-ai-dominance`, `the-home-depot-vs-lowes`).",
         "#: slug → the validator codes its blocks hit. Articles with no hit are absent\n"
         "#: (`nvidias-ai-dominance`, `the-home-depot-vs-lowes`, and the three hand-written on 2026-11-01:\n"
         "#: `mcdonalds-the-real-estate-empire`, `coca-cola-vs-pepsico`,\n"
         "#: `kodak-inventing-the-future-it-couldnt-sell`)."),
        ("    assert len(articles) == 23, len(articles)", "    assert len(articles) == 26, len(articles)"),
    ],
    "test_learn_weekly_narration.py": [
        ("def test_the_bundles_are_present_and_hold_23_articles_each():",
         "def test_the_bundles_are_present_and_hold_26_articles_each():"),
        ('        assert len(json.loads(path.read_text(encoding="utf-8"))["articles"]) == 23\n'
         "    assert len(_articles()) >= 23",
         '        assert len(json.loads(path.read_text(encoding="utf-8"))["articles"]) == 26\n'
         "    assert len(_articles()) >= 26"),
    ],
    "test_learn_weekly_preview.py": [
        ('len(kw["served_titles"]) == 23', 'len(kw["served_titles"]) == 26'),
    ],
}
staged = {}
for name, pairs in EDITS.items():
    p = TESTS / name
    s = p.read_text()
    for old, new in pairs:
        assert s.count(old) == 1, (name, old[:60])
        s = s.replace(old, new)
    staged[p] = s
for p, s in staged.items():
    p.write_text(s)
print("edited:", [p.name for p in staged])
print("Still to decide after integrate: content-pool _TITLE_COMPANIES / EXCLUDED, if "
      "tests/test_marketing_content_pool.py asks (run it).")
