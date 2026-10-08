"""How a snapshot metric's peer group is named to a MODEL (Cay AI chat, the paid report).

Snapshot metric names keep the words "sector avg" / "vs sector" / "sector average" on the
wire for every median, because shipped iOS builds strip the comparison suffix by the word
"sector" (`TickerReportModels.displayLabel`). `SnapshotMetricResponse.peer_level` says whose
median it is (2026-10-07). The 1.1 app renames an INDUSTRY median on screen; every place
that hands the name to a model must rename it the same way, or the model calls an
industry median a "sector average" beside an app that says "industry".
"""

from __future__ import annotations

from typing import Any


def peer_worded_metric_name(metric: Any) -> str:
    """``metric.name`` with an industry median named as the industry's."""
    name = str(getattr(metric, "name", "") or "")
    if getattr(metric, "peer_level", None) != "industry":
        return name
    return (
        name.replace("sector avg", "industry avg")
        .replace("sector average", "industry average")
        .replace("vs sector", "vs industry")
    )
