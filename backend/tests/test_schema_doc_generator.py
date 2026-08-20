"""
Tests for the Database Atlas generator (backend/scripts/).

The generator is a transform over a 14k-line pg_dump, so the interesting bugs
are all in parsing: a construct whose shape the regex did not anticipate gets
silently dropped, or — worse — a non-greedy match runs past its statement and
swallows the next one. Both produce a page that looks fine and is wrong.

So these are built from small synthetic dump fragments containing exactly the
constructs that bite, NOT by reading the real snapshot. The real snapshot is
covered by `generate_schema_doc.py --check`, which asserts its structural
counts.

Historical note: `test_partitioned_table_is_not_skipped` is a regression test.
`realtime.messages` ends `)\nPARTITION BY RANGE (inserted_at);` rather than
`);`, and the first version of the table regex required `\n);` — so that table
vanished from the atlas and the non-greedy body match ran on into the following
statement. The count was 129 instead of 130 and nothing else complained.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
SCRIPTS = BACKEND / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import schema_doc_data as data  # noqa: E402
import schema_doc_svg as svgmod  # noqa: E402
from schema_parser import parse_dump  # noqa: E402

HEADER = """--
-- PostgreSQL database dump
--

-- Dumped from database version 17.6
-- Dumped by pg_dump version 18.4

"""


def dump(*chunks: str) -> str:
    return HEADER + "\n\n".join(chunks) + "\n"


# ---------------------------------------------------------------------------
# Columns and types
# ---------------------------------------------------------------------------


def test_column_types_that_have_bitten_before():
    """Array types, a schema-qualified parameterised type, and a multi-word
    type must survive with their modifiers intact."""
    s = parse_dump(dump("""CREATE TABLE public.odd (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tags text[],
    factors jsonb[] DEFAULT '{}'::jsonb[] NOT NULL,
    embedding public.vector(1536),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp(0) without time zone,
    ratio numeric(10,2) DEFAULT 0.0
);"""))
    t = s.tables["public.odd"]
    got = {c.name: c for c in t.columns}
    assert len(t.columns) == 7
    assert got["tags"].type == "text[]" and got["tags"].is_array
    assert got["factors"].type == "jsonb[]"
    assert got["factors"].default == "'{}'::jsonb[]"
    assert got["factors"].nullable is False
    assert got["embedding"].type == "public.vector(1536)"
    assert got["created_at"].type == "timestamp with time zone"
    assert got["updated_at"].type == "timestamp(0) without time zone"
    # A comma inside numeric(10,2) must not split the column list.
    assert got["ratio"].type == "numeric(10,2)"
    assert got["ratio"].default == "0.0"
    assert got["ratio"].nullable is True


def test_inline_check_constraints_do_not_become_columns():
    """A CHECK body is full of parens and commas — the top-level split must not
    break inside it, and the constraint must not be mistaken for a column."""
    s = parse_dump(dump("""CREATE TABLE public.scored (
    id uuid NOT NULL,
    score numeric,
    state text,
    CONSTRAINT scored_range_check CHECK (((score >= (0)::numeric) AND (score <= (100)::numeric))),
    CONSTRAINT scored_state_check CHECK ((state = ANY (ARRAY['a'::text, 'b'::text, 'c'::text])))
);"""))
    t = s.tables["public.scored"]
    assert [c.name for c in t.columns] == ["id", "score", "state"]
    assert [n for n, _ in t.checks] == ["scored_range_check", "scored_state_check"]
    assert "ARRAY['a'::text, 'b'::text, 'c'::text]" in t.checks[1][1]


def test_default_containing_a_comma_and_parens():
    s = parse_dump(dump("""CREATE TABLE public.d (
    a text DEFAULT concat('x', 'y') NOT NULL,
    b text DEFAULT 'has, comma'::text
);"""))
    t = s.tables["public.d"]
    assert len(t.columns) == 2
    assert t.column("a").default == "concat('x', 'y')"
    assert t.column("b").default == "'has, comma'::text"


def test_partitioned_table_is_not_skipped():
    """REGRESSION: `PARTITION BY` sits between the closing paren and the
    semicolon. Requiring `\\n);` dropped the table AND let the non-greedy body
    match run into the next statement."""
    s = parse_dump(dump(
        """CREATE TABLE realtime.messages (
    topic text NOT NULL,
    payload jsonb
)
PARTITION BY RANGE (inserted_at);""",
        """CREATE TABLE public.after (
    id uuid NOT NULL
);""",
    ))
    assert set(s.tables) == {"realtime.messages", "public.after"}
    assert s.tables["realtime.messages"].partition_by == "PARTITION BY RANGE (inserted_at)"
    assert [c.name for c in s.tables["realtime.messages"].columns] == ["topic", "payload"]
    # The statement that followed must be intact, not swallowed.
    assert [c.name for c in s.tables["public.after"].columns] == ["id"]


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


def test_composite_pk_unique_and_cross_schema_fk():
    s = parse_dump(dump(
        "CREATE TABLE public.users (\n    id uuid NOT NULL\n);",
        "CREATE TABLE auth.users (\n    id uuid NOT NULL\n);",
        "CREATE TABLE public.child (\n    user_id uuid NOT NULL,\n    day date NOT NULL,\n"
        "    grp uuid\n);",
        """ALTER TABLE ONLY public.child
    ADD CONSTRAINT child_pkey PRIMARY KEY (user_id, day);""",
        """ALTER TABLE ONLY public.child
    ADD CONSTRAINT child_uniq UNIQUE (user_id, grp);""",
        """ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_id_fkey FOREIGN KEY (id) REFERENCES auth.users(id) ON DELETE CASCADE;""",
        """ALTER TABLE ONLY public.child
    ADD CONSTRAINT child_grp_fkey FOREIGN KEY (grp) REFERENCES public.users(id) ON DELETE SET NULL;""",
    ))
    child = s.tables["public.child"]
    assert child.primary_key == ["user_id", "day"]
    assert child.uniques == [["user_id", "grp"]]

    by_name = {f.name: f for f in s.foreign_keys}
    assert by_name["users_id_fkey"].ref_schema == "auth"
    assert by_name["users_id_fkey"].on_delete == "CASCADE"
    assert by_name["child_grp_fkey"].on_delete == "SET NULL"
    assert by_name["child_grp_fkey"].parent == "public.users"


def test_partial_unique_index_keeps_its_predicate():
    s = parse_dump(dump(
        "CREATE TABLE public.portfolios (\n    user_id uuid,\n    is_active boolean\n);",
        "CREATE UNIQUE INDEX idx_one_active ON public.portfolios USING btree (user_id) "
        "WHERE is_active;",
        "CREATE INDEX idx_hnsw ON public.portfolios USING hnsw (user_id public.vector_cosine_ops);",
    ))
    idx = {i.name: i for i in s.tables["public.portfolios"].indexes}
    assert idx["idx_one_active"].unique is True
    assert idx["idx_one_active"].predicate == "is_active"
    assert idx["idx_hnsw"].method == "hnsw"
    assert idx["idx_hnsw"].unique is False
    assert idx["idx_hnsw"].predicate is None


def test_index_expression_with_nested_parens():
    s = parse_dump(dump(
        "CREATE TABLE public.chunks (\n    ticker text,\n    fiscal_quarter integer,\n"
        "    chunk_index integer\n);",
        "CREATE UNIQUE INDEX idx_c ON public.chunks USING btree "
        "(ticker, COALESCE(fiscal_quarter, 0), chunk_index);",
    ))
    i = s.tables["public.chunks"].indexes[0]
    assert i.expression == "ticker, COALESCE(fiscal_quarter, 0), chunk_index"


# ---------------------------------------------------------------------------
# Policies, RLS, enums, triggers, functions, views
# ---------------------------------------------------------------------------


def test_policy_name_with_comma_and_role_list():
    s = parse_dump(dump(
        "CREATE TABLE public.t (\n    id uuid\n);",
        "ALTER TABLE public.t ENABLE ROW LEVEL SECURITY;",
        """CREATE POLICY "Read, but only your own" ON public.t FOR SELECT TO anon, authenticated USING ((auth.uid() = id));""",
        """CREATE POLICY t_service_all ON public.t TO service_role USING (true) WITH CHECK (true);""",
    ))
    t = s.tables["public.t"]
    assert t.rls is True
    pol = {p.name: p for p in t.policies}
    assert "Read, but only your own" in pol
    assert pol["Read, but only your own"].command == "SELECT"
    assert pol["Read, but only your own"].roles == ["anon", "authenticated"]
    assert pol["Read, but only your own"].using == "(auth.uid() = id)"
    assert pol["t_service_all"].command == "ALL"
    assert pol["t_service_all"].with_check == "true"


def test_table_with_no_rls_no_policies_no_indexes_still_parses():
    s = parse_dump(dump("CREATE TABLE public.bare (\n    id uuid\n);"))
    t = s.tables["public.bare"]
    assert (t.rls, t.policies, t.indexes, t.primary_key, t.uniques) == (False, [], [], [], [])


def test_enum_and_column_linkage():
    s = parse_dump(dump(
        """CREATE TYPE public.report_status AS ENUM (
    'pending',
    'completed',
    'failed'
);""",
        "CREATE TABLE public.r (\n    status public.report_status "
        "DEFAULT 'pending'::public.report_status NOT NULL\n);",
    ))
    assert s.enums["public.report_status"].values == ["pending", "completed", "failed"]
    assert s.tables["public.r"].column("status").type == "public.report_status"


def test_function_security_definer_body_and_table_references():
    s = parse_dump(dump(
        "CREATE TABLE public.user_credits (\n    id uuid\n);",
        """CREATE FUNCTION public.spend_credits(p_user_id uuid, p_amount integer) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    UPDATE public.user_credits SET used = used + p_amount;
    RETURN 0;
END;
$$;""",
        """CREATE FUNCTION public.search_book_chunks(q public.vector) RETURNS SETOF record
    LANGUAGE sql STABLE
    AS $_$ SELECT 1 $_$;""",
    ))
    fns = {f.name: f for f in s.functions}
    assert fns["spend_credits"].security_definer is True
    assert fns["spend_credits"].language == "plpgsql"
    assert fns["spend_credits"].returns == "integer"
    assert fns["spend_credits"].tables == ["user_credits"]
    # $_$ is a different dollar-quote tag and must be handled too.
    assert fns["search_book_chunks"].security_definer is False
    assert fns["search_book_chunks"].volatility == "STABLE"


def test_trigger_and_view():
    s = parse_dump(dump(
        "CREATE TABLE public.users (\n    id uuid\n);",
        "CREATE TRIGGER trg_x AFTER INSERT ON public.users FOR EACH ROW "
        "EXECUTE FUNCTION public.create_user_credits();",
        "CREATE VIEW public.v AS\n SELECT 1 AS one;",
    ))
    tr = s.tables["public.users"].triggers[0]
    assert (tr.name, tr.timing, tr.function) == ("trg_x", "AFTER INSERT", "public.create_user_credits")
    assert [v.qname for v in s.views] == ["public.v"]


def test_comment_on_attaches_to_table_column_and_function():
    s = parse_dump(dump(
        "CREATE TABLE public.t (\n    id uuid,\n    user_id uuid\n);",
        """CREATE FUNCTION public.f() RETURNS void
    LANGUAGE sql
    AS $$ SELECT 1 $$;""",
        "COMMENT ON TABLE public.t IS 'The table. It''s quoted.';",
        "COMMENT ON COLUMN public.t.user_id IS 'Deliberately NOT a foreign key.';",
        "COMMENT ON FUNCTION public.f() IS 'Does nothing.';",
    ))
    t = s.tables["public.t"]
    assert t.comment == "The table. It's quoted."
    assert t.column("user_id").comment == "Deliberately NOT a foreign key."
    assert s.functions[0].comment == "Does nothing."


# ---------------------------------------------------------------------------
# Edge inference — the half that must never invent a relationship
# ---------------------------------------------------------------------------


def _edges(sql: str):
    return data.build_edges(parse_dump(sql))


def test_unconstrained_user_id_becomes_a_soft_edge():
    hard, soft = _edges(dump(
        "CREATE TABLE public.users (\n    id uuid NOT NULL\n);",
        "CREATE TABLE public.watchlist_items (\n    id uuid,\n    user_id uuid NOT NULL\n);",
    ))
    assert hard == []
    assert len(soft) == 1
    assert (soft[0]["from"], soft[0]["col"], soft[0]["to"]) == (
        "public.watchlist_items", "user_id", "public.users")
    assert soft[0]["kind"] == "soft"


def test_a_constrained_column_is_never_also_a_soft_edge():
    hard, soft = _edges(dump(
        "CREATE TABLE public.users (\n    id uuid NOT NULL\n);",
        "CREATE TABLE public.device_tokens (\n    id uuid,\n    user_id uuid NOT NULL\n);",
        """ALTER TABLE ONLY public.device_tokens
    ADD CONSTRAINT dt_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;""",
    ))
    assert len(hard) == 1 and soft == []


def test_no_edge_is_invented_when_no_such_table_exists():
    """`<x>_id` pointing at nothing must produce NO edge. A fabricated
    relationship in a schema diagram is worse than a missing one."""
    hard, soft = _edges(dump(
        "CREATE TABLE public.chat_sessions (\n    id uuid,\n    widget_id uuid,\n"
        "    gizmo_id uuid\n);",
    ))
    assert hard == []
    assert soft == []


def test_not_a_ref_suppresses_an_edge_that_would_otherwise_be_inferred():
    """NOT_A_REF filters nothing against today's schema — every entry names a
    column whose would-be target table does not exist. This test creates that
    target, so the guard is actually exercised rather than passing by accident.

    `chat_sessions.stock_id` is in NOT_A_REF; `chat_sessions.gizmo_id` is not.
    With both target tables present, exactly one edge must be drawn."""
    frag = dump(
        "CREATE TABLE public.stocks (\n    id uuid\n);",
        "CREATE TABLE public.gizmos (\n    id uuid\n);",
        "CREATE TABLE public.chat_sessions (\n    id uuid,\n    stock_id uuid,\n"
        "    gizmo_id uuid\n);",
    )
    _, soft = _edges(frag)
    assert [(e["col"], e["to"]) for e in soft] == [("gizmo_id", "public.gizmos")]

    # Same input with the guard emptied: the suppressed edge reappears. If this
    # half ever stops holding, the assertion above has gone vacuous.
    import schema_curation as cur
    saved = cur.NOT_A_REF
    try:
        cur.NOT_A_REF = frozenset()
        _, unguarded = _edges(frag)
    finally:
        cur.NOT_A_REF = saved
    assert sorted(e["col"] for e in unguarded) == ["gizmo_id", "stock_id"]


def test_curated_implicit_ref_is_emitted_with_its_reason():
    hard, soft = _edges(dump(
        "CREATE TABLE public.agent_personas (\n    id uuid,\n    key text\n);",
        "CREATE TABLE public.research_reports (\n    id uuid,\n    investor_persona text\n);",
    ))
    assert hard == []
    e = next(x for x in soft if x["col"] == "investor_persona")
    assert e["to"] == "public.agent_personas"
    assert e["toCol"] == "key"
    assert "persona key" in e["why"]


def test_self_reference_is_dropped():
    _, soft = _edges(dump(
        "CREATE TABLE public.users (\n    id uuid,\n    user_id uuid\n);",
    ))
    assert soft == []


# ---------------------------------------------------------------------------
# Code scan
# ---------------------------------------------------------------------------


def test_code_scan_direct_named_and_rpc(tmp_path: Path):
    app = tmp_path / "app" / "services"
    app.mkdir(parents=True)
    (app / "a_service.py").write_text(
        'x = supabase.table("watchlist_items").select("*")\n'
        'y = supabase.rpc("spend_credits", {})\n'
    )
    (app / "b_service.py").write_text(
        '_TABLE = "ticker_data_cache"\n'
        'z = supabase.table(_TABLE).select("*")\n'
    )
    usage = data.scan_code([tmp_path / "app"], tmp_path,
                           {"watchlist_items", "ticker_data_cache", "never_used"})
    assert usage.direct["watchlist_items"] == ["app/services/a_service.py:1"]
    assert usage.rpc["spend_credits"] == ["app/services/a_service.py:2"]
    # Reached through a constant: no direct hit, but it IS named.
    assert "ticker_data_cache" not in usage.direct
    assert usage.named["ticker_data_cache"] == ["app/services/b_service.py:1"]
    # A table nothing mentions must appear in neither map.
    assert "never_used" not in usage.direct and "never_used" not in usage.named


def test_code_scan_ignores_the_generators_own_sources(tmp_path: Path):
    """The atlas sources name tables by definition — `schema_doc_data.py` holds
    bare quoted table names in its _JOB_STATE / _REGISTRY sets, and the fallback
    scan matches exactly that shape. Counting them would make a dead table look
    live in its own documentation."""
    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True)
    # The real shape that would otherwise match: a bare quoted table name.
    (scripts / "schema_doc_data.py").write_text('_REGISTRY = {"dead_table"}\n')
    (scripts / "schema_curation.py").write_text('X = {"dead_table": 1}\n')
    (scripts / "real.py").write_text('supabase.table("live_table")\n')
    usage = data.scan_code([scripts], tmp_path, {"dead_table", "live_table"})
    assert usage.direct == {"live_table": ["scripts/real.py:1"]}
    assert "dead_table" not in usage.named, usage.named

    # Control: the identical string in a file that is NOT excluded IS picked up,
    # which is what proves the exclusion above did the work.
    (scripts / "other_service.py").write_text('_REGISTRY = {"dead_table"}\n')
    usage2 = data.scan_code([scripts], tmp_path, {"dead_table", "live_table"})
    assert usage2.named["dead_table"] == ["scripts/other_service.py:1"]


# ---------------------------------------------------------------------------
# Payload + rendering
# ---------------------------------------------------------------------------


SMALL = dump(
    "CREATE TABLE public.users (\n    id uuid NOT NULL\n);",
    "CREATE TABLE public.research_reports (\n    id uuid NOT NULL,\n    user_id uuid NOT NULL\n);",
    "ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;",
)


def test_payload_reports_uncurated_tables_rather_than_guessing():
    s = parse_dump(dump("CREATE TABLE public.brand_new_table (\n    id uuid\n);"))
    usage = data.CodeUsage({}, {}, {})
    _, uncurated = data.build_payload(
        s, usage, generated="2026-01-01", source="x.sql", allow_uncurated=False)
    assert uncurated == ["public.brand_new_table"]

    _, uncurated2 = data.build_payload(
        s, usage, generated="2026-01-01", source="x.sql", allow_uncurated=True)
    assert uncurated2 == []


def test_db_comment_beats_curated_purpose_and_curated_is_kept_as_extra():
    s = parse_dump(SMALL + "\nCOMMENT ON TABLE public.research_reports IS 'From the schema.';\n")
    payload, _ = data.build_payload(
        parse_dump(SMALL + "\nCOMMENT ON TABLE public.research_reports IS 'From the schema.';\n"),
        data.CodeUsage({}, {}, {}), generated="2026-01-01", source="x.sql",
        allow_uncurated=True)
    rr = payload["tables"]["public.research_reports"]
    assert rr["purpose"] == "From the schema."
    assert rr["purposeSrc"] == "db"
    # research_reports has no curated `purpose` (its DB comment covers it), so
    # extraPurpose is empty — but the curated `note` must still come through.
    assert "migration 110" in rr["note"]
    assert s.tables["public.research_reports"].comment == "From the schema."


def test_key_columns_fall_back_to_the_primary_key_when_curation_lists_none():
    s = parse_dump(dump(
        "CREATE TABLE public.brand_new_table (\n    id uuid,\n    a text,\n    b text\n);",
        "ALTER TABLE ONLY public.brand_new_table\n"
        "    ADD CONSTRAINT bnt_pkey PRIMARY KEY (id);",
    ))
    payload, _ = data.build_payload(s, data.CodeUsage({}, {}, {}), generated="d",
                                    source="x", allow_uncurated=True)
    assert payload["tables"]["public.brand_new_table"]["keyCols"] == ["id"]


def test_key_columns_never_name_a_column_that_does_not_exist():
    """A curation entry that outlives a dropped column must not print a ghost
    row in the inspector."""
    s = parse_dump(dump(
        "CREATE TABLE public.user_settings (\n    user_id uuid\n);"))  # `preferences` dropped
    payload, _ = data.build_payload(s, data.CodeUsage({}, {}, {}), generated="d",
                                    source="x", allow_uncurated=True)
    assert payload["tables"]["public.user_settings"]["keyCols"] == ["user_id"]


def test_table_kind_classification():
    s = parse_dump(dump(
        "CREATE TABLE public.growth_cache (\n    ticker text\n);",
        "CREATE TABLE public.moat_intel_audit (\n    run_id uuid\n);",
        "CREATE TABLE public.book_chunks (\n    embedding public.vector(1536)\n);",
        "CREATE TABLE public.watchlist_items (\n    user_id uuid\n);",
        "CREATE TABLE auth.sessions (\n    id uuid\n);",
        # a *_cache that also holds a vector must classify as RAG, not cache
        "CREATE TABLE public.odd_cache (\n    embedding public.vector(1536)\n);",
    ))
    k = {q: data.table_kind(t) for q, t in s.tables.items()}
    assert k["public.growth_cache"] == "cache"
    assert k["public.moat_intel_audit"] == "audit"
    assert k["public.book_chunks"] == "rag"
    assert k["public.watchlist_items"] == "user-scoped"
    assert k["auth.sessions"] == "managed"
    assert k["public.odd_cache"] == "rag"


def test_svg_marks_soft_edges_dashed_and_never_draws_a_missing_table():
    s = parse_dump(dump(
        "CREATE TABLE public.users (\n    id uuid NOT NULL\n);",
        "CREATE TABLE public.device_tokens (\n    id uuid,\n    user_id uuid\n);",
        "CREATE TABLE public.watchlist_items (\n    id uuid,\n    user_id uuid\n);",
        """ALTER TABLE ONLY public.device_tokens
    ADD CONSTRAINT dt_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;""",
    ))
    payload, _ = data.build_payload(s, data.CodeUsage({}, {}, {}), generated="d",
                                    source="x", allow_uncurated=True)
    hard, soft = data.build_edges(s)
    svg = svgmod.build_svg(payload["tables"], hard, soft)
    assert svg.count("stroke-dasharray") == 1        # exactly the one soft edge
    assert "device_tokens" in svg and "watchlist_items" in svg
    # An edge whose endpoint is not in `tables` must be skipped, not crash.
    ghost = dict(hard[0], to="public.does_not_exist")
    assert "does_not_exist" not in svgmod.build_svg(payload["tables"], [ghost], [])


def test_svg_output_is_stable_across_runs():
    s = parse_dump(SMALL)
    payload, _ = data.build_payload(s, data.CodeUsage({}, {}, {}), generated="d",
                                    source="x", allow_uncurated=True)
    hard, soft = data.build_edges(s)
    a = svgmod.build_svg(payload["tables"], hard, soft)
    b = svgmod.build_svg(payload["tables"], hard, soft)
    assert a == b


def test_special_characters_are_escaped_into_the_svg():
    """Covers BOTH svg code paths: a hub with >=2 children renders boxes via
    `_box`, a hub with exactly one renders the compact pair strip. An earlier
    version of this test only ever reached the pair strip, so removing the
    escaping from `_box` did not fail it."""
    s = parse_dump(dump(
        "CREATE TABLE public.users (\n    id uuid\n);",
        'CREATE TABLE public."we<ird&" (\n    user_id uuid\n);',
        'CREATE TABLE public."ma&lice" (\n    user_id uuid\n);',
        "CREATE TABLE public.solos (\n    id uuid\n);",
        'CREATE TABLE public."child<>" (\n    solo_id uuid\n);',
    ))
    payload, _ = data.build_payload(s, data.CodeUsage({}, {}, {}), generated="d",
                                    source="x", allow_uncurated=True)
    hard, soft = data.build_edges(s)
    svg = svgmod.build_svg(payload["tables"], hard, soft)
    # users has 2 children -> cluster (_box); solos has 1 -> compact pair strip.
    assert "<g" in svg
    for raw, safe in (("we<ird&", "we&lt;ird&amp;"),
                      ("ma&lice", "ma&amp;lice"),
                      ("child<>", "child&lt;&gt;")):
        assert raw not in svg, raw
        assert safe in svg, safe


# ---------------------------------------------------------------------------
# End-to-end against the real snapshot
# ---------------------------------------------------------------------------

SNAPSHOT = BACKEND / "database" / "schema_snapshot.sql"


@pytest.mark.skipif(not SNAPSHOT.exists(), reason="no schema snapshot checked out")
def test_check_mode_passes_on_the_current_snapshot():
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "generate_schema_doc.py"), "--check"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(not SNAPSHOT.exists(), reason="no schema snapshot checked out")
def test_every_public_table_is_curated():
    """The atlas must not contain a blank card. This is the same gate the
    generator enforces at run time, asserted here so it fails in CI too."""
    import schema_curation as cur

    s = parse_dump(SNAPSHOT.read_text())
    missing = sorted(
        t.qname for t in s.tables.values()
        if t.schema == "public" and t.qname not in cur.CURATION
    )
    assert missing == [], f"add these to backend/scripts/schema_curation.py: {missing}"


@pytest.mark.skipif(not SNAPSHOT.exists(), reason="no schema snapshot checked out")
def test_curation_never_names_a_column_that_no_longer_exists():
    """A stale key-column name is invisible in the page (it is filtered out),
    so it needs a test to surface it."""
    import schema_curation as cur

    s = parse_dump(SNAPSHOT.read_text())
    stale: list[str] = []
    for q, doc in cur.CURATION.items():
        t = s.tables.get(q)
        if t is None:
            stale.append(f"{q} (table gone)")
            continue
        names = {c.name for c in t.columns}
        stale += [f"{q}.{k}" for k in doc.key if k not in names]
    assert stale == []


@pytest.mark.skipif(not SNAPSHOT.exists(), reason="no schema snapshot checked out")
def test_generated_page_is_byte_identical_on_a_second_run(tmp_path: Path):
    out = tmp_path / "atlas.html"
    cmd = [sys.executable, str(SCRIPTS / "generate_schema_doc.py"), "--out", str(out)]
    assert subprocess.run(cmd, capture_output=True, text=True).returncode == 0
    first = out.read_bytes()
    assert subprocess.run(cmd, capture_output=True, text=True).returncode == 0
    assert out.read_bytes() == first


@pytest.mark.skipif(not SNAPSHOT.exists(), reason="no schema snapshot checked out")
def test_generated_page_is_offline_and_script_safe(tmp_path: Path):
    out = tmp_path / "atlas.html"
    subprocess.run(
        [sys.executable, str(SCRIPTS / "generate_schema_doc.py"), "--out", str(out)],
        capture_output=True, text=True, check=True,
    )
    html = out.read_text()
    # No asset the page would have to fetch. The SVG namespace URL is a
    # namespace identifier, not a request, so it is allowed.
    for token in ("<script src=", "<link ", "@import", "url(http"):
        assert token not in html, token
    # The embedded JSON must not be able to terminate its own <script>.
    body = html.split("const SCHEMA = ", 1)[1]
    assert "</script>" not in body.split("\n", 1)[0]
