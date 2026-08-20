"""
schema_doc_data.py — turn a parsed dump + the curation file into the JSON blob
the Database Atlas page renders from.

Split out from generate_schema_doc.py so the data model can be tested without
touching HTML.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import schema_curation as cur
from schema_parser import Schema, Table

# ---------------------------------------------------------------------------
# Code-usage scan
# ---------------------------------------------------------------------------

_RE_TABLE_CALL = re.compile(r"""\.table\(\s*["'](\w+)["']""")
_RE_RPC_CALL = re.compile(r"""\.rpc\(\s*["'](\w+)["']""")

_SKIP_DIRS = {"venv", ".venv", "__pycache__", "node_modules", ".git", "site-packages"}


@dataclass
class CodeUsage:
    """Where the backend touches each table / RPC.

    `direct` are `.table("x")` call sites — unambiguous.
    `named`  are bare-string occurrences, collected ONLY for tables with zero
             direct hits. Many tables are reached through a module-level table
             name constant, so a `.table()`-only scan reports them as unused,
             which is wrong. These are labelled differently in the UI because
             they are weaker evidence, not equivalent evidence.
    """

    direct: dict[str, list[str]]
    named: dict[str, list[str]]
    rpc: dict[str, list[str]]


def scan_code(roots: list[Path], repo_root: Path, table_names: set[str]) -> CodeUsage:
    direct: dict[str, list[str]] = defaultdict(list)
    named: dict[str, list[str]] = defaultdict(list)
    rpc: dict[str, list[str]] = defaultdict(list)

    files: list[tuple[str, list[str]]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if _SKIP_DIRS & set(path.parts):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            rel = path.relative_to(repo_root).as_posix()
            files.append((rel, lines))

    for rel, lines in files:
        for n, line in enumerate(lines, 1):
            for m in _RE_TABLE_CALL.finditer(line):
                if m.group(1) in table_names:
                    direct[m.group(1)].append(f"{rel}:{n}")
            for m in _RE_RPC_CALL.finditer(line):
                rpc[m.group(1)].append(f"{rel}:{n}")

    # Second pass, only for tables no `.table()` call names.
    undiscovered = sorted(table_names - set(direct))
    if undiscovered:
        pat = re.compile(
            r"""["'](""" + "|".join(re.escape(t) for t in undiscovered) + r""")["']"""
        )
        for rel, lines in files:
            for n, line in enumerate(lines, 1):
                for m in pat.finditer(line):
                    named[m.group(1)].append(f"{rel}:{n}")

    return CodeUsage(direct=dict(direct), named=dict(named), rpc=dict(rpc))


# ---------------------------------------------------------------------------
# Relationship edges
# ---------------------------------------------------------------------------


def _singular_candidates(col: str) -> list[str]:
    """Table names a `<base>_id` column could plausibly point at."""
    if not col.endswith("_id"):
        return []
    base = col[:-3]
    if not base:
        return []
    return [base, base + "s", base + "es", base[:-1] + "ies" if base.endswith("y") else base + "s"]


def build_edges(schema: Schema) -> tuple[list[dict], list[dict]]:
    """Return (hard_edges, soft_edges).

    hard = a real FOREIGN KEY constraint.
    soft = a column that joins to another table with NO constraint behind it.

    The soft set is the load-bearing half of this schema: only 28 of the joins
    are enforced, because the guest-partitioned tables had their user_id FK
    deliberately dropped (migrations 108/110/111/131) and cache tables key on a
    natural ticker string. Drawing only the hard edges would show `users`
    connected to almost nothing, which is false.
    """
    hard: list[dict] = []
    constrained: set[tuple[str, str]] = set()
    for fk in schema.foreign_keys:
        for col in fk.columns:
            constrained.add((fk.child, col))
        hard.append(
            {
                "from": fk.child,
                "col": ", ".join(fk.columns),
                "to": fk.parent,
                "toCol": ", ".join(fk.ref_columns),
                "kind": "fk",
                "onDelete": fk.on_delete or "NO ACTION",
                "name": fk.name,
                "why": "",
            }
        )

    by_name: dict[str, str] = {}
    for t in schema.tables.values():
        if t.schema == "public":
            by_name[t.name] = t.qname

    soft: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def add_soft(frm: str, col: str, to: str, to_col: str, why: str) -> None:
        key = (frm, col, to)
        if key in seen or (frm, col) in constrained or frm == to:
            return
        seen.add(key)
        soft.append({"from": frm, "col": col, "to": to, "toCol": to_col,
                     "kind": "soft", "onDelete": "", "name": "", "why": why})

    # Curated joins first — they win, and they cover the ones no rule can infer.
    for (frm, col), (to, to_col, why) in cur.IMPLICIT_REFS.items():
        if frm in schema.tables and to in schema.tables:
            add_soft(frm, col, to, to_col, why)

    # Then inference over `<base>_id` columns.
    for t in schema.tables.values():
        if t.schema != "public":
            continue
        for c in t.columns:
            if (t.qname, c.name) in constrained or (t.qname, c.name) in cur.NOT_A_REF:
                continue
            for cand in _singular_candidates(c.name):
                target = by_name.get(cand)
                if target and target != t.qname:
                    add_soft(t.qname, c.name, target, "id",
                             "column name matches a table; no FK constraint exists")
                    break

    hard.sort(key=lambda e: (e["from"], e["col"]))
    soft.sort(key=lambda e: (e["from"], e["col"]))
    return hard, soft


# ---------------------------------------------------------------------------
# Table kind (the secondary grouping)
# ---------------------------------------------------------------------------

_JOB_STATE = {
    "notification_job_state", "updates_insight_state", "ai_insight_budget",
    "chat_usage_budget", "guest_report_budget", "push_send_log",
}
_REGISTRY = {
    "agent_personas", "credit_packs", "plan_credits", "whales", "books", "lessons",
    "money_move_articles", "daily_briefings", "trending_themes", "sector_benchmarks",
    "sector_aggregates", "industry_dossier", "industry_moat_benchmarks",
}


def table_kind(t: Table) -> str:
    if t.schema != "public":
        return "managed"
    if any("vector" in c.type for c in t.columns):
        return "rag"
    if t.name.endswith("_cache"):
        return "cache"
    if t.name.endswith("_audit"):
        return "audit"
    if t.name in _JOB_STATE:
        return "job-state"
    if any(c.name in ("user_id", "identity_key") for c in t.columns):
        return "user-scoped"
    if t.name in _REGISTRY:
        return "registry"
    return "reference"


KIND_LABELS: dict[str, str] = {
    "user-scoped": "User-scoped",
    "cache": "Cache (tier 2)",
    "audit": "LLM audit trail",
    "rag": "RAG / pgvector",
    "registry": "Registry / content",
    "job-state": "Budget / job state",
    "reference": "Reference data",
    "managed": "Supabase-managed",
}


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_payload(
    schema: Schema,
    usage: CodeUsage,
    *,
    generated: str,
    source: str,
    allow_uncurated: bool,
) -> tuple[dict, list[str]]:
    """Return (payload, uncurated_public_table_names)."""
    hard, soft = build_edges(schema)
    edges = hard + soft

    out_by: dict[str, list[dict]] = defaultdict(list)
    in_by: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        out_by[e["from"]].append(e)
        in_by[e["to"]].append(e)

    fns_by_table: dict[str, list[str]] = defaultdict(list)
    for f in schema.functions:
        if f.schema != "public":
            continue
        for tname in f.tables:
            fns_by_table[f"public.{tname}"].append(f.signature)

    uncurated: list[str] = []
    tables: dict[str, dict] = {}

    for t in sorted(schema.tables.values(), key=lambda x: (x.schema, x.name)):
        doc = cur.CURATION.get(t.qname)
        if doc is None and t.schema == "public":
            uncurated.append(t.qname)

        if doc is not None:
            domain = doc.domain
            curated_purpose = doc.purpose
            note = doc.note
        elif t.schema != "public":
            domain = "supabase"
            curated_purpose = cur.MANAGED_TABLE_NOTES.get(t.qname, "")
            note = ""
        else:
            domain = "ops"
            curated_purpose = ""
            note = ""

        # COMMENT ON TABLE wins — it is versioned with the schema.
        if t.comment:
            purpose, purpose_src = t.comment, "db"
        elif curated_purpose:
            purpose, purpose_src = curated_purpose, "curated"
        else:
            purpose, purpose_src = "", "none"
        extra = curated_purpose if (purpose_src == "db" and curated_purpose) else ""

        pk = set(t.primary_key)
        fk_cols = {c for e in out_by[t.qname] for c in e["col"].split(", ")}
        soft_cols = {c for e in out_by[t.qname] if e["kind"] == "soft"
                     for c in e["col"].split(", ")}

        cols = []
        for c in t.columns:
            enum = c.type if c.type in schema.enums else None
            cols.append({
                "n": c.name,
                "t": c.type,
                "null": c.nullable,
                "def": c.default,
                "pk": c.name in pk,
                "fk": c.name in fk_cols,
                "soft": c.name in soft_cols,
                "enum": enum,
                "c": c.comment,
            })

        key_cols = list(doc.key) if doc and doc.key else []
        known = {c.name for c in t.columns}
        key_cols = [k for k in key_cols if k in known]
        if not key_cols:
            # Fall back to the identifying columns, which is always meaningful.
            key_cols = (t.primary_key or [c.name for c in t.columns[:4]])[:6]

        enums_used = sorted({c["enum"] for c in cols if c["enum"]})

        tables[t.qname] = {
            "schema": t.schema,
            "name": t.name,
            "domain": domain,
            "kind": table_kind(t),
            "purpose": purpose,
            "purposeSrc": purpose_src,
            "extraPurpose": extra,
            "note": note,
            "cols": cols,
            "keyCols": key_cols,
            "pk": t.primary_key,
            "uniques": t.uniques,
            "checks": [{"n": n, "e": e} for n, e in t.checks],
            "indexes": [
                {"n": i.name, "m": i.method, "e": i.expression, "u": i.unique,
                 "w": i.predicate, "c": i.comment}
                for i in sorted(t.indexes, key=lambda i: i.name)
            ],
            "rls": t.rls,
            "policies": [
                {"n": p.name, "c": p.command, "r": p.roles}
                for p in sorted(t.policies, key=lambda p: p.name)
            ],
            "triggers": [
                {"n": tr.name, "t": tr.timing, "f": tr.function}
                for tr in sorted(t.triggers, key=lambda x: x.name)
            ],
            "partitionBy": t.partition_by,
            "out": out_by[t.qname],
            "in": in_by[t.qname],
            "enums": enums_used,
            "fns": sorted(set(fns_by_table.get(t.qname, []))),
            "direct": sorted(usage.direct.get(t.name, [])),
            "named": sorted(usage.named.get(t.name, [])),
            "ddl": t.raw_ddl,
        }

    domains = []
    for d in cur.DOMAINS:
        members = sorted(
            (q for q, v in tables.items() if v["domain"] == d.key),
            key=lambda q: (tables[q]["schema"] != "public", tables[q]["name"]),
        )
        domains.append({"key": d.key, "label": d.label, "color": d.color,
                        "blurb": d.blurb, "tables": members})

    functions = [
        {
            "name": f.name, "args": f.args, "returns": f.returns,
            "secdef": f.security_definer, "vol": f.volatility, "lang": f.language,
            "comment": f.comment, "tables": f.tables,
            "callsites": sorted(usage.rpc.get(f.name, [])),
        }
        for f in sorted(schema.functions, key=lambda f: f.name)
        if f.schema == "public"
    ]

    pub = [t for t in schema.tables.values() if t.schema == "public"]
    counts = {
        "tables": len(schema.tables),
        "public": len(pub),
        "fk": len([e for e in hard if e["from"].startswith("public.")]),
        "soft": len(soft),
        "policies": sum(len(t.policies) for t in pub),
        "rls": sum(1 for t in pub if t.rls),
        "indexes": sum(len(t.indexes) for t in pub),
        "functions": len(functions),
        "secdef": sum(1 for f in functions if f["secdef"]),
        "enums": len([e for e in schema.enums.values() if e.schema == "public"]),
        "views": len(schema.views),
        "columns": sum(len(t.columns) for t in pub),
        "triggers": sum(len(t.triggers) for t in pub),
    }

    payload = {
        "meta": {
            "generated": generated,
            "source": source,
            "pgVersion": schema.pg_version,
            "dumpVersion": schema.dump_version,
            "extensions": schema.extensions,
            "counts": counts,
            "managedSchemas": cur.MANAGED_SCHEMAS,
        },
        "domains": domains,
        "kinds": KIND_LABELS,
        "tables": tables,
        "enums": {
            e.qname: {"values": e.values}
            for e in sorted(schema.enums.values(), key=lambda e: e.qname)
        },
        "functions": functions,
        "views": [
            {"name": v.name, "schema": v.schema, "comment": v.comment,
             "definition": v.definition}
            for v in sorted(schema.views, key=lambda v: v.qname)
        ],
    }
    if uncurated and not allow_uncurated:
        return payload, uncurated
    return payload, []
