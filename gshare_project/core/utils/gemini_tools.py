import json, google.generativeai as genai
from django.conf import settings
from django.db import connections
from .orders_for_driver import get_active_orders_for_driver
from .order_resolver import assign_lines_to_orders

def _remove_items(order_id, items):
    with connections['gsharedb'].cursor() as cur:
        for it in items:
            cur.execute("""
                UPDATE order_items
                SET quantity = GREATEST(quantity - %s, 0)
                WHERE order_id=%s AND item_id=%s
            """, [it["quantity"], order_id, it["item_id"]])
        cur.execute("DELETE FROM order_items WHERE order_id=%s AND quantity<=0", [order_id])

def _resolve_and_remove_by_name(driver_user_id, lines):
    orders = get_active_orders_for_driver(driver_user_id)
    assigns = assign_lines_to_orders([{"name":ln["name"],"quantity":ln.get("quantity",1)} for ln in lines], orders)
    grouped, decisions = {}, []
    for a in assigns:
        if not a["best"]:
            decisions.append({"name":a["line"]["name"],"resolved":False,"reason":"no_match"}); continue
        oid=a["best"]["order_id"]
        grouped.setdefault(oid, []).append({"item_id":a["best"]["item_id"],"quantity":a["line"]["quantity"]})
        decisions.append({"name":a["line"]["name"],"resolved":True,"order_id":oid,"item_id":a["best"]["item_id"],"score":a["best"]["score"],"ambiguous":a["ambiguous"]})
    for oid, items in grouped.items(): _remove_items(oid, items)
    return {"ok":True,"orders_changed":list(grouped.keys()),"decisions":decisions}

def start_chat_session_with_resolver():
    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model=settings.GEMINI_MODEL,
        tools=[{
            "functionDeclarations": [{
                "name": "remove_items_by_name_no_order",
                "description": (
                    "Remove items by names/quantities; backend infers correct "
                    "order among driver's active carts."
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
                                        "minimum": 0
                                    }
                                },
                                "required": ["name", "quantity"]
                            },
                        }
                    },
                    "required": ["lines"],
                },
            }]
        }]
    )

    system = (
        "You help a delivery driver reconcile a receipt across several active carts. "
        "If the user doesn’t specify an order id, call remove_items_by_name_no_order. "
        "Ask for confirmation if intent is unclear."
    )

    return model.start_chat(
        history=[{"role": "system", "parts": [system]}]
    )

def run_chat_turn_with_resolver(chat, driver_user_id, user_text):
    resp = chat.send_message(user_text)
    for c in (resp.candidates or []):
        for p in c.content.parts:
            if getattr(p, "function_call", None) and p.function_call.name == "remove_items_by_name_no_order":
                args=json.loads(p.function_call.args)
                result=_resolve_and_remove_by_name(driver_user_id, args["lines"])
                resp=chat.send_message([{"role":"tool","parts":[json.dumps(result)]}])
    return resp