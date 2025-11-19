# core/utils/order_resolver.py
import re
from collections import defaultdict

from rapidfuzz import fuzz, process


def _normalize_name(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def assign_lines_to_orders(lines, orders, score_threshold=70, ambiguity_gap=8):
    """
    lines: [{"name": str, "quantity": number}, ...]
    orders: output from get_active_orders_for_driver()

    Returns:
    [
      {
        "line": {...},
        "best": { "order_id", "item_id", "display", "score" } or None,
        "runner_up_score": number or None,
        "ambiguous": bool
      },
      ...
    ]
    """
    choices = []
    meta = []
    for o in orders:
        for it in o["items"]:
            nm = _normalize_name(it["item_name"])
            choices.append(nm)
            meta.append(
                {
                    "order_id": o["id"],
                    "item_id": it["item_id"],
                    "display": it["item_name"],
                }
            )

    out = []
    for ln in lines:
        q = _normalize_name(ln.get("name", ""))
        if not q or not choices:
            out.append(
                {
                    "line": ln,
                    "best": None,
                    "runner_up_score": None,
                    "ambiguous": False,
                }
            )
            continue

        results = process.extract(q, choices, scorer=fuzz.WRatio, limit=3)
        best = None
        second = None

        for _, score, idx in results:
            if score < score_threshold:
                continue
            cand = {**meta[idx], "score": score}
            if best is None:
                best = cand
            elif second is None:
                second = cand

        ambiguous = False
        runner_score = None
        if best and second:
            runner_score = second["score"]
            if best["score"] - second["score"] < ambiguity_gap:
                ambiguous = True

        out.append(
            {
                "line": ln,
                "best": best,
                "runner_up_score": runner_score,
                "ambiguous": ambiguous,
            }
        )

    return out


def pick_best_order_for_receipt(
    assignments, min_coverage=0.66, min_avg=75, gap=8
):
    """
    Given assignments from assign_lines_to_orders, pick the most likely order_id.

    Returns:
    {
      "order_id": int or None,
      "coverage": float,
      "avg_score": float,
      "second_best_gap": float
    }
    """
    scores_sum = defaultdict(float)
    counts = defaultdict(int)

    for a in assignments:
        best = a.get("best")
        if not best:
            continue
        oid = best["order_id"]
        scores_sum[oid] += best["score"]
        counts[oid] += 1

    if not counts:
        return {"order_id": None, "coverage": 0.0, "avg_score": 0.0, "second_best_gap": 0.0}

    # (order_id, avg_score, count)
    ranked = sorted(
        ((oid, scores_sum[oid] / counts[oid], counts[oid]) for oid in counts),
        key=lambda x: x[1],
        reverse=True,
    )
    best_oid, best_avg, best_n = ranked[0]
    second_avg = ranked[1][1] if len(ranked) > 1 else 0.0

    coverage = best_n / max(1, len(assignments))

    if (
        coverage >= min_coverage
        and best_avg >= min_avg
        and (best_avg - second_avg) >= gap
    ):
        return {
            "order_id": best_oid,
            "coverage": coverage,
            "avg_score": best_avg,
            "second_best_gap": best_avg - second_avg,
        }

    return {
        "order_id": None,
        "coverage": coverage,
        "avg_score": best_avg,
        "second_best_gap": best_avg - second_avg,
    }