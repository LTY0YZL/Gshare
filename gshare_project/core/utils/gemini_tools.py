# core/utils/gemini_tools.py
import json

import google.generativeai as genai
from django.conf import settings
from django.db import connections

from .orders_for_driver import get_active_orders_for_driver
from .order_resolver import assign_lines_to_orders


def _remove_items(order_id: int, items):
    """
    items: [{"item_id": int, "quantity": number}, ...]
    """
    with connections["gsharedb"].cursor() as cur:
        for it in items:
            cur.execute(
                """
                UPDATE order_items
                SET quantity = GREATEST(quantity - %s, 0)
                WHERE order_id = %s AND item_id = %s
                """,
                [it["quantity"], order_id, it["item_id"]],
            )
        cur.execute(
            "DELETE FROM order_items WHERE order_id = %s AND quantity <= 0",
            [order_id],
        )


def _resolve_and_remove_by_name(driver_user_id: int, lines):
    """
    lines: [{"name": str, "quantity": number}, ...]

    Returns:
      {"ok": True, "orders_changed": [...], "decisions": [...]}
    """
    orders = get_active_orders_for_driver(driver_user_id)
    if not orders:
        return {"ok": False, "reason": "no_active_orders"}

    assignments = assign_lines_to_orders(
        [{"name": ln["name"], "quantity": ln.get("quantity", 1)} for ln in lines],
        orders,
    )

    grouped = {}
    decisions = []
    for a in assignments:
        ln = a["line"]
        best = a["best"]
        if not best:
            decisions.append(
                {
                    "name": ln["name"],
                    "resolved": False,
                    "reason": "no_match",
                }
            )
            continue

        oid = best["order_id"]
        grouped.setdefault(oid, []).append(
            {
                "item_id": best["item_id"],
                "quantity": ln.get("quantity", 1),
            }
        )
        decisions.append(
            {
                "name": ln["name"],
                "resolved": True,
                "order_id": oid,
                "item_id": best["item_id"],
                "score": best["score"],
                "ambiguous": a["ambiguous"],
            }
        )

    for oid, items in grouped.items():
        _remove_items(oid, items)

    return {"ok": True, "orders_changed": list(grouped.keys()), "decisions": decisions}


def start_chat_session_with_resolver():
    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model=settings.GEMINI_MODEL,
        tools=[
            {
                "functionDeclarations": [
                    {
                        "name": "remove_items_by_name_no_order",
                        "description": (
                            "Remove items by name and quantity. The backend will "
                            "infer which active order(s) they belong to for this driver."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "lines": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "quantity": {
                                                "type": "number",
                                                "minimum": 0,
                                            },
                                        },
                                        "required": ["name", "quantity"],
                                    },
                                }
                            },
                            "required": ["lines"],
                        },
                    }
                ]
            }
        ],
    )

    system = (
        "You help a delivery driver reconcile a receipt with several active carts. "
        "When the user asks to remove items like 'remove 2 x apples and 1 x milk', "
        "call remove_items_by_name_no_order with those item names and quantities. "
        "Do NOT ask for order id; the backend will infer it. "
        "Ask for clarification if the request is unclear."
    )

    return model.start_chat(history=[{"role": "system", "parts": [system]}])


def run_chat_turn_with_resolver(chat, driver_user_id: int, user_text: str):
    """
    Send one user message, handle any function call, and return the final response.
    """
    resp = chat.send_message(user_text)

    # handle any function calls
    for cand in resp.candidates or []:
        for part in cand.content.parts:
            fc = getattr(part, "function_call", None)
            if not fc:
                continue
            if fc.name == "remove_items_by_name_no_order":
                args = json.loads(fc.args or "{}")
                lines = args.get("lines", [])
                result = _resolve_and_remove_by_name(driver_user_id, lines)
                # send tool result back to model for a natural-language summary
                resp = chat.send_message(
                    [
                        {
                            "role": "tool",
                            "parts": [json.dumps(result)],
                        }
                    ]
                )
                return resp

    return resp
