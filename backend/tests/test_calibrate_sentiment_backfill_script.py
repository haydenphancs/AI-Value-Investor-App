"""`scripts/calibrate_sentiment_backfill.py` — hermetic checks of its pure parts and its
read-only guarantees. Nothing here reaches Supabase or a model."""

import pytest

from scripts import calibrate_sentiment_backfill as cal


def test_agreement_counts_an_unlabelled_article_as_a_miss():
    report = cal.agreement([("bullish", "bullish"), ("bearish", "neutral"), ("neutral", None)])
    assert report["total"] == 3 and report["matches"] == 1
    assert report["rate"] == pytest.approx(1 / 3)
    assert report["unlabelled"] == 1
    assert report["confusion"]["neutral"] == {"none": 1}


def test_agreement_on_nothing_is_zero_not_a_crash():
    assert cal.agreement([])["rate"] == 0.0


def test_the_tripwire_refuses_every_supabase_use(monkeypatch):
    import app.database as database

    monkeypatch.setattr(database, "_supabase_client", None)
    cal.install_supabase_tripwire()
    for name in ("table", "rpc", "storage"):
        with pytest.raises(cal.SupabaseRefused):
            getattr(database._supabase_client, name)


def test_there_is_no_write_flag():
    args = cal.parse_args(["--limit", "10"])
    assert not any("write" in k or "apply" in k for k in vars(args))


def test_scrub_hides_keys():
    assert "SECRETKEY123" not in cal.scrub("boom apikey=SECRETKEY123", ["SECRETKEY123"])


@pytest.mark.parametrize("backfill,baseline,expected", [
    (0.86, None, "PASS"),          # the absolute bar
    (0.847, 0.817, "PASS"),        # measured 2026-09-27: better than the live self-agreement
    (0.80, 0.817, "PASS"),         # within the 2-point tolerance
    (0.79, 0.817, "BELOW TARGET"),
    (0.80, None, "BELOW TARGET"),  # no baseline: only the absolute bar applies
])
def test_verdict_is_relative_to_the_live_labellers_own_consistency(backfill, baseline, expected):
    assert cal.verdict(backfill, baseline) == expected
