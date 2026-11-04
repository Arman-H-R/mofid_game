"""The end-of-round play style. Pure Python.

This describes how the round was played; it is not an assessment of the player's
real finances. The style comes only from behaviour: the share of hits per colour
and the hits during Market Rush. The score never decides the style. It only picks
which sentence of that style is shown, so a high score cannot by itself make
someone a risk taker. All thresholds and sentences are in settings ("profiles").

In order, first match wins:
  fewer than min_hits hits                         watchful
  red share >= aggressive_red_share                aggressive
  red share >= risk_red_share, or at least
    rush_min_hits hits in Market Rush making up
    at least rush_share of all hits                risk_tolerant
  green share >= growth_green_share:
    red share <= cautious_red_share                cautious
    otherwise                                      growth
  anything else (no colour dominates)              balanced
"""
from __future__ import annotations

from bisect import bisect_right

from ..config import Section


def playstyle(summary: dict, p: Section) -> str:
    hits = summary.get("hits", {})
    total = sum(hits.values())
    if total < p.min_hits:
        return "watchful"
    red = hits.get("red", 0) / total
    green = hits.get("green", 0) / total
    rush_hits = summary.get("rush", {}).get("hits", 0)
    if red >= p.aggressive_red_share:
        return "aggressive"
    if red >= p.risk_red_share or (rush_hits >= p.rush_min_hits and rush_hits / total >= p.rush_share):
        return "risk_tolerant"
    if green >= p.growth_green_share:
        return "cautious" if red <= p.cautious_red_share else "growth"
    return "balanced"


def classify(summary: dict, p: Section) -> dict:
    key = playstyle(summary, p)
    style = p.styles[key]
    statements = style.statements
    tier = bisect_right(p.score_tiers, summary.get("score", 0))
    return {"key": key, "label": style.label, "statement": statements[min(tier, len(statements) - 1)]}
