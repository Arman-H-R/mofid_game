"""The end-of-round play style. Pure Python.

This describes how the round was played; it is not an assessment of the player's
real finances. The style comes only from behaviour: the share of hits per box size
(the tiny boxes are the hardest and pay the most, the medium ones the easiest) and the
hits during Market Rush. The score never decides the style. It only picks which
sentence of that style is shown, so a high score cannot by itself make someone a risk
taker. All thresholds and sentences are in settings ("profiles").

In order, first match wins:
  fewer than min_hits hits                         watchful
  tiny share >= aggressive_tiny_share              aggressive
  tiny share >= risk_tiny_share, or at least
    rush_min_hits hits in Market Rush making up
    at least rush_share of all hits                risk_tolerant
  medium share >= growth_medium_share:
    tiny share <= cautious_tiny_share              cautious
    otherwise                                      growth
  anything else (no size dominates)                balanced
"""
from __future__ import annotations

from bisect import bisect_right

from ..config import Section


def playstyle(summary: dict, p: Section) -> str:
    hits = summary.get("hits", {})
    total = sum(hits.values())
    if total < max(1, p.min_hits):
        return "watchful"
    tiny = hits.get("tiny", 0) / total
    medium = hits.get("medium", 0) / total
    rush_hits = summary.get("rush", {}).get("hits", 0)
    if tiny >= p.aggressive_tiny_share:
        return "aggressive"
    if tiny >= p.risk_tiny_share or (rush_hits >= p.rush_min_hits and rush_hits / total >= p.rush_share):
        return "risk_tolerant"
    if medium >= p.growth_medium_share:
        return "cautious" if tiny <= p.cautious_tiny_share else "growth"
    return "balanced"


def classify(summary: dict, p: Section) -> dict:
    key = playstyle(summary, p)
    style = p.styles[key]
    statements = style.statements
    tier = bisect_right(p.score_tiers, summary.get("score", 0))
    return {"key": key, "label": style.label, "statement": statements[min(tier, len(statements) - 1)]}
