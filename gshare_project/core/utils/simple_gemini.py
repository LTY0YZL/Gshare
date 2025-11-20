# core/utils/simple_gemini.py

import base64
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.db import connections

from collections import defaultdict

from google import genai

from core.models import Receipt, ReceiptLine
from core.utils.aws_s3 import get_s3_client

# Single shared Gemini client
client = genai.Client(api_key=settings.GEMINI_API_KEY)

def _dump_items_for_receipt(receipt):
    """Return a pure-Python list of items for prompts/JSON."""
    lines = (
        ReceiptLine.objects.using("gsharedb")
        .filter(receipt=receipt)
        .order_by("id")
    )
    items = []
    for l in lines:
        items.append({
            "name": l.name,
            "quantity": float(l.quantity or 1),
            "unit_price": float(l.unit_price) if l.unit_price is not None else None,
            "total_price": float(l.total_price) if l.total_price is not None else None,
            "meta": l.meta or {},
        })
    return items


def _apply_operations_to_receipt(receipt: Receipt, operations):
    """
    Apply a list of edit operations to ReceiptLine rows *safely*.

    `operations` can be:
      - a dict with key "operations"
      - a JSON string
      - a list of dicts
      - anything else (which we ignore)
    """

    # -------- Normalize `operations` into a Python list of dicts ----------
    if isinstance(operations, str):
        # maybe the model returned a JSON string
        try:
            operations = json.loads(operations)
        except Exception:
            operations = []

    if isinstance(operations, dict) and "operations" in operations:
        operations = operations["operations"]

    if not isinstance(operations, list):
        operations = []

    # ---------------------------------------------------------------------
    # From here on, `operations` is a list. Each element might still be
    # garbage (like a string), so we guard every access with isinstance().
    # ---------------------------------------------------------------------

    with transaction.atomic(using="gsharedb"):
        for op in operations:
            if not isinstance(op, dict):
                # this is what stops `'str' object has no attribute "get"'`
                continue

            action = op.get("op")
            if not action:
                continue

            action = action.lower().strip()

            name = (op.get("name") or "").strip()
            old_name = (op.get("old_name") or "").strip()
            new_name = (op.get("new_name") or "").strip()

            qs = ReceiptLine.objects.using("gsharedb").filter(receipt=receipt)

            if action == "remove" and name:
                qs.filter(name__iexact=name).delete()

            elif action == "update_quantity" and name:
                try:
                    qty = float(op.get("quantity"))
                except Exception:
                    continue
                for line in qs.filter(name__iexact=name):
                    line.quantity = qty
                    line.save(using="gsharedb")

            elif action == "rename" and old_name and new_name:
                qs.filter(name__iexact=old_name).update(name=new_name)

            elif action == "add" and name:
                def _safe_float(x):
                    try:
                        return float(x)
                    except Exception:
                        return None

                qty = _safe_float(op.get("quantity")) or 1
                unit_price = _safe_float(op.get("unit_price"))
                total_price = _safe_float(op.get("total_price"))

                ReceiptLine.objects.using("gsharedb").create(
                    receipt=receipt,
                    name=name[:256],
                    quantity=qty,
                    unit_price=unit_price,
                    total_price=total_price,
                    meta=op,
                )

        # After edits, refresh the JSON snapshot on the receipt
        new_items = [
            {
                "name": l.name,
                "quantity": l.quantity,
                "unit_price": l.unit_price,
                "total_price": l.total_price,
            }
            for l in ReceiptLine.objects.using("gsharedb")
            .filter(receipt=receipt)
            .order_by("id")
        ]
        receipt.gemini_json = {"items": new_items}
        receipt.uploaded_at = timezone.now()
        receipt.save(using="gsharedb")


def _load_image_bytes_from_s3(receipt: Receipt) -> bytes:
    s3 = get_s3_client()
    obj = s3.get_object(Bucket=receipt.s3_bucket, Key=receipt.s3_key)
    return obj["Body"].read()


def scan_receipt(receipt_id: int) -> None:
    """
    Synchronous scan:
      - download image from S3
      - ask Gemini Vision for JSON items
      - store items as ReceiptLine rows
      - update Receipt.status and gemini_json
    """
    # receipt from gsharedb
    receipt = Receipt.objects.using("gsharedb").get(pk=receipt_id)

    receipt.status = "processing"
    receipt.uploaded_at = timezone.now()
    receipt.save(using="gsharedb")

    img_bytes = _load_image_bytes_from_s3(receipt)

    prompt = """
    You are a grocery receipt parser.
    Read the receipt and return ONLY valid JSON, no extra text.

    Format:
    {
      "items": [
        {
          "name": "string",
          "quantity": number,
          "unit_price": number or null,
          "total_price": number or null
        }
      ]
    }
    """

    result = client.models.generate_content(
        model="models/gemini-2.0-flash",
        contents=[
            prompt,
            {
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(img_bytes).decode("utf-8"),
                }
            },
        ],
    )

    raw = result.text or ""

    # Try to parse JSON safely
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            data = json.loads(raw[start : end + 1])
        else:
            # mark error on the receipt for debugging
            receipt.status = "error"
            receipt.error = "JSON parse failed from Gemini"
            receipt.save(using="gsharedb")
            return

    # Save raw JSON
    receipt.gemini_json = data

    # Replace existing lines for this receipt
    ReceiptLine.objects.using("gsharedb").filter(receipt=receipt).delete()

    for item in data.get("items", []):
        ReceiptLine.objects.using("gsharedb").create(
            receipt=receipt,
            name=(item.get("name") or "")[:256],
            quantity=item.get("quantity") or 1,
            unit_price=item.get("unit_price"),
            total_price=item.get("total_price"),
            meta=item,
        )

    receipt.status = "done"
    receipt.error = ""
    receipt.save(using="gsharedb")


def chat_about_receipt(receipt: Receipt, history, user_message: str) -> str:
    """
    Mixed mode:
      - answer questions about the receipt (totals, cheapest, etc.)
      - optionally edit the list when user asks, via JSON operations.
    """

    items = [
        {
            "name": l.name,
            "quantity": l.quantity,
            "unit_price": l.unit_price,
            "total_price": l.total_price,
        }
        for l in ReceiptLine.objects.using("gsharedb")
        .filter(receipt=receipt)
        .order_by("id")
    ]
    items_json = json.dumps({"items": items}, ensure_ascii=False, indent=2)

    system_prompt = f"""
You are an assistant for grocery receipts.

You can:
- Answer questions about the receipt (totals, most expensive, cheapest, counts, etc.).
- Edit the list (remove items, change quantities, rename, add items).

Current items (JSON):

{items_json}

When you respond, you MUST:

1) First, write a natural-language reply for the user.

2) At the END, output a JSON block between:

BEGIN_OPERATIONS
...JSON here...
END_OPERATIONS

Format:

{{
  "operations": [
    {{"op": "remove", "name": "KRO COCONUT"}},
    {{"op": "update_quantity", "name": "BANANAS", "quantity": 3}},
    {{"op": "rename", "old_name": "BANANAS", "new_name": "Organic Bananas"}},
    {{
      "op": "add",
      "name": "NEW ITEM",
      "quantity": 1,
      "unit_price": 1.23,
      "total_price": 1.23
    }}
  ]
}}

If the user did NOT request changes, still output:

BEGIN_OPERATIONS
{{"operations": []}}
END_OPERATIONS
"""

    history_text = ""
    for role, content in history:
        prefix = "User" if role == "user" else "Assistant"
        history_text += f"{prefix}: {content}\n"

    full_prompt = (
        system_prompt
        + "\n\nConversation so far:\n"
        + history_text
        + f"\nUser: {user_message}\nAssistant:"
    )

    resp = client.models.generate_content(
        model="models/gemini-2.0-flash",       # 🔴 2.0 model here
        contents=[full_prompt],
    )

    text = (resp.text or "").strip()

    # ---- Extract operations block ------------------------------------------
    ops_start = text.find("BEGIN_OPERATIONS")
    ops_end = text.find("END_OPERATIONS")

    natural_reply = text
    ops_raw = []

    if ops_start != -1 and ops_end != -1 and ops_end > ops_start:
        natural_reply = text[:ops_start].strip()
        ops_block = text[ops_start + len("BEGIN_OPERATIONS"):ops_end].strip()

        # strip code fences like ```json ... ``` if they appear
        ops_block = ops_block.strip().strip("`")
        if ops_block.lower().startswith("json"):
            ops_block = ops_block[4:].strip()

        try:
            parsed = json.loads(ops_block)
        except Exception:
            # maybe it's a quoted string of JSON
            try:
                parsed = json.loads(ops_block.strip('"'))
            except Exception:
                parsed = {"operations": []}

        # 🚨 Normalize to a dict with "operations" key
        if isinstance(parsed, list):
            parsed = {"operations": parsed}
        elif not isinstance(parsed, dict):
            parsed = {"operations": []}

        ops_raw = parsed
    else:
        ops_raw = {"operations": []}

    # Apply changes + refresh gemini_json
    _apply_operations_to_receipt(receipt, ops_raw)

    clean_reply = (
        natural_reply
        .replace("```json", "")
        .replace("```", "")
        .strip()
    )

    return clean_reply or "Okay, I’ve updated the receipt."

client = genai.Client(api_key=settings.GEMINI_API_KEY)


def suggest_matching_order(receipt, lines, candidate_orders):
    """
    Given a receipt + parsed lines + a list of candidate_orders,
    ask Gemini which order (if any) best matches the receipt.

    candidate_orders = [
        {"id": ..., "status": "...", "store_id": ..., "created_at": "..."},
        ...
    ]

    Returns: (best_order_id_or_None, explanation_str)
    """

    # ---- 1) Build JSON for receipt items -------------------------------
    receipt_items = [
        {
            "name": l.name,
            "quantity": l.quantity,
            "unit_price": l.unit_price,
            "total_price": l.total_price,
        }
        for l in lines
    ]

    # ---- 2) Load order item names/quantities via raw SQL ---------------
    order_ids = [o["id"] for o in candidate_orders]
    orders_items = defaultdict(list)  # order_id -> list of items

    if order_ids:
        with connections["gsharedb"].cursor() as cur:
            # NOTE: this only *reads* from order_items and items
            cur.execute(
                """
                SELECT oi.order_id, i.name, oi.quantity
                FROM order_items AS oi
                JOIN items AS i ON oi.item_id = i.id
                WHERE oi.order_id IN %s
                """,
                [tuple(order_ids)],
            )
            for order_id, item_name, qty in cur.fetchall():
                orders_items[order_id].append(
                    {
                        "name": item_name,
                        "quantity": float(qty) if qty is not None else None,
                    }
                )

    # Assemble candidate orders payload for Gemini
    orders_payload = []
    for o in candidate_orders:
        oid = o["id"]
        orders_payload.append(
            {
                "id": oid,
                "status": o["status"],
                "store_id": o["store_id"],
                "created_at": o["created_at"],
                "items": orders_items.get(oid, []),
            }
        )

    payload = {
        "receipt_id": receipt.id,
        "receipt_items": receipt_items,
        "candidate_orders": orders_payload,
    }

    # ---- 3) Ask Gemini for the best match ------------------------------
    system_prompt = """
You are helping match a grocery receipt to one of several delivery orders.

You are given JSON with:
- receipt_items: items parsed from the receipt
- candidate_orders: each order has id, status, store_id, created_at, and items

Your job:
1. Decide which order (if any) best matches the receipt items.
2. If no order is a good match, choose null.
3. Return ONLY JSON, nothing else, in this format:

{
  "best_order_id": <number or null>,
  "explanation": "short human explanation"
}
"""

    prompt = system_prompt + "\n\nJSON data:\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )

    resp = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[prompt],
    )

    text = (resp.text or "").strip()

    # ---- 4) Parse JSON from Gemini safely ------------------------------
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # try to strip any extra text
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                result = json.loads(text[start : end + 1])
            except Exception:
                result = {}
        else:
            result = {}

    best_order_id = result.get("best_order_id")
    explanation = result.get("explanation") or "No explanation provided."

    # Normalize: only accept numeric best_order_id
    if not isinstance(best_order_id, (int, float)):
        best_order_id = None

    return best_order_id, explanation