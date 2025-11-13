import re
from rapidfuzz import fuzz, process

def _n(s): s=s.lower(); s=re.sub(r'[^a-z0-9\s]', ' ', s); return re.sub(r'\s+',' ',s).strip()

def assign_lines_to_orders(lines, orders, score_threshold=70, ambiguity_gap=8):
    choices, meta = [], []
    for o in orders:
        for it in o["items"]:
            nm=_n(it["item_name"])
            choices.append(nm)
            meta.append({"order_id": o["id"], "item_id": it["item_id"], "display": it["item_name"]})
    out=[]
    for ln in lines:
        q=_n(ln["name"])
        ms=process.extract(q, choices, scorer=fuzz.WRatio, limit=3)
        best=sec=None
        for _,score,idx in ms:
            if score<score_threshold: continue
            cand={**meta[idx], "score":score}
            if not best: best=cand
            elif not sec: sec=cand
        ambiguous = bool(best and sec and (best["score"]-sec["score"]<ambiguity_gap))
        out.append({"line": ln, "best": best, "runner_up_score": sec["score"] if sec else None, "ambiguous": ambiguous})
    return out

def pick_best_order_for_receipt(assignments, min_coverage=0.66, min_avg=75, gap=8):
    from collections import defaultdict
    s, c = defaultdict(float), defaultdict(int)
    for a in assignments:
        if a["best"]:
            oid=a["best"]["order_id"]; s[oid]+=a["best"]["score"]; c[oid]+=1
    if not c: return {"order_id": None, "coverage":0.0, "avg_score":0.0, "second_best_gap":0.0}
    ranked=sorted(((oid, s[oid]/c[oid], c[oid]) for oid in s), key=lambda x:x[1], reverse=True)
    best_oid, best_avg, best_n = ranked[0]
    second = ranked[1][1] if len(ranked)>1 else 0.0
    coverage = best_n/max(1,len(assignments))
    if coverage>=min_coverage and best_avg>=min_avg and (best_avg-second)>=gap:
        return {"order_id":best_oid,"coverage":coverage,"avg_score":best_avg,"second_best_gap":best_avg-second}
    return {"order_id":None,"coverage":coverage,"avg_score":best_avg,"second_best_gap":best_avg-second}