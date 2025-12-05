# core/utils/simple_gemini.py

import base64
import json
import re
import unicodedata


from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.db import connections

from collections import defaultdict
from typing import List, Dict, Tuple, Optional

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

        base = receipt.gemini_json or {}
        if not isinstance(base, dict):
            base = {}

        # NEVER modify original_items
        if "original_items" not in base:
            base["original_items"] = new_items  # fallback if somehow missing

        # Always update current_items
        base["items"] = new_items

        receipt.gemini_json = base
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
        # If first scan: store original_items permanently
    base = receipt.gemini_json or {}
    if "original_items" not in base:
        base["original_items"] = data.get("items", [])

    # Always update current items
    base["items"] = data.get("items", [])

    receipt.gemini_json = base
    receipt.status = "done"
    receipt.error = ""
    receipt.save(using="gsharedb")

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
      - EXPLAIN why a particular order is only a partial match using match_debug.
    """

    # Items on the receipt
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

    gem = receipt.gemini_json or {}
    orig = gem.get("original_items", [])
    current = gem.get("items", [])

    items_json = json.dumps(
        {"original_items": orig, "current_items": current},
        ensure_ascii=False, indent=2
    )

    # NEW: load matching debug info saved by suggest_matching_order
    base_json = receipt.gemini_json or {}
    if not isinstance(base_json, dict):
        base_json = {}
    match_debug = base_json.get("match_debug") or {}
    match_debug_json = json.dumps(match_debug, ensure_ascii=False, indent=2)

    system_prompt = f"""
You are an assistant for grocery receipts.

You have BOTH the original scanned receipt items and the edited items.

- "original_items" = the receipt exactly as scanned from the image
- "current_items" = after user edits (removals, renames, quantity changes)

When the user asks:
- "what changed?"
- "what was originally on the receipt?"
- "compare original to edited"
- "why was order 310 originally not a full match?"

You MUST use both original_items and current_items.

You can:
- Answer questions about the receipt (totals, most expensive, cheapest, counts, etc.).
- Edit the list (remove items, change quantities, rename, add items).
- Explain why some delivery orders are only PARTIAL matches or NOT full matches.

Current items (JSON):

{items_json}

Matching debug info for delivery orders (if any):

{match_debug_json}

Each key in "match_debug" is an order ID as a string. For each order:
- "missing_items" lists items that are required by the order but not present on the receipt.
- "insufficient_quantity_items" lists items where the receipt has LESS quantity than the order needs.

When the user asks things like:
- "why is order not a match"
- "why is only a partial match"
- "what is missing for order "

You MUST:
- Look up that order ID in match_debug.
- Explain which items are missing or have insufficient quantity, in clear natural language.
- Do NOT say that you don't have access to databases; you DO have all relevant info in match_debug.

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


def suggest_matching_order(*, receipt, lines, candidate_orders):
    """
    Determine which delivery order(s) are fully contained in this receipt.

    FULL MATCH:
      - For every item in the ORDER, the RECEIPT has that item (ignoring case/small
        name differences) with quantity >= the order quantity.
      - Extra items on the receipt are allowed.

    PARTIAL MATCH:
      - Receipt shares at least one item with the order,
      - BUT at least one order item is missing on the receipt OR has lower quantity.

    Also records, per order, which items are missing or have insufficient quantity
    into receipt.gemini_json["match_debug"] so chat can later explain “why it is not a match”.
    """

    # --- Build receipt items summary from parsed receipt lines ---
    receipt_items = [
        {
            "name": l.name,
            "quantity": float(l.quantity or 0),
            "unit_price": float(l.unit_price or 0),
            "total_price": float(l.total_price or 0),
        }
        for l in lines
    ]

    print(f"order candidates: {candidate_orders}")

    # --- Load line items for each candidate order via raw SQL ---
    orders_info = []
    with connections["gsharedb"].cursor() as cur:
        for order in candidate_orders:
            oid = order["id"]
            cur.execute(
                """
                SELECT i.name, oi.quantity, oi.price
                FROM order_items oi
                JOIN items i ON oi.item_id = i.id
                WHERE oi.order_id = %s
                """,
                [oid],
            )
            row_items = [
                {
                    "name": r[0],
                    "quantity": float(r[1] or 0),
                    "price": float(r[2] or 0),
                }
                for r in cur.fetchall()
            ]
            o = dict(order)
            o["items"] = row_items
            orders_info.append(o)

    print(f"receipt items: {receipt_items}")

    print(f"orders info: {orders_info}")

    # --- Helper: normalize item names so small differences don't break matches ---
    def norm_name(name: str) -> str:
        if not name:
            return ""
        # Lowercase
        s = name.lower()
        # Normalize unicode (e.g., fancy symbols)
        s = unicodedata.normalize("NFKD", s)
        # Keep only letters, digits, and spaces
        s = "".join(ch for ch in s if ch.isalnum() or ch.isspace())
        # Collapse multiple spaces
        s = re.sub(r"\s+", " ", s).strip()
        return s

    # --- Build RECEIPT quantity index (by normalized name) ---
    receipt_qty = {}
    for item in receipt_items:
        n = norm_name(item["name"])
        q = float(item.get("quantity") or 0)
        receipt_qty[n] = receipt_qty.get(n, 0.0) + q

    full_matches = []
    partial_matches = []

    # 🔍 NEW: per-order debug info
    match_debug = {}

    # --- Core logic: check if receipt fully has everything from the order ---
    for order in orders_info:
        oid = order["id"]
        order_items = order.get("items", [])

        if not order_items:
            continue

        all_order_items_covered = True  # assume full match until proven otherwise
        any_overlap = False             # track if at least one item overlaps

        missing_items = []
        insufficient_items = []

        for oi in order_items:
            oname_raw = oi["name"]
            oname = norm_name(oname_raw)
            oqty = float(oi.get("quantity") or 0)
            rqty = receipt_qty.get(oname, 0.0)

            if rqty > 0:
                any_overlap = True

            if rqty <= 0:
                # completely missing from receipt
                all_order_items_covered = False
                missing_items.append({
                    "name": oname_raw,
                    "required_quantity": oqty,
                    "receipt_quantity": 0.0,
                })
            elif rqty + 1e-6 < oqty:
                # present but not enough quantity
                all_order_items_covered = False
                insufficient_items.append({
                    "name": oname_raw,
                    "required_quantity": oqty,
                    "receipt_quantity": rqty,
                })

        # store debug info for this order regardless
        match_debug[str(oid)] = {
            "missing_items": missing_items,
            "insufficient_quantity_items": insufficient_items,
        }

        if all_order_items_covered:
            full_matches.append(oid)
        elif any_overlap:
            partial_matches.append(oid)

    # --- Persist debug info on the receipt so chat can use it later ---
    try:
        base_json = receipt.gemini_json or {}
    except AttributeError:
        base_json = {}

    if not isinstance(base_json, dict):
        base_json = {}

    base_json["match_debug"] = match_debug
    receipt.gemini_json = base_json
    receipt.save(using="gsharedb")

    # --- Confidence heuristic (optional; useful for logging / debugging) ---
    if len(full_matches) == 1:
        confidence = 0.99
    elif len(full_matches) > 1:
        confidence = 0.9
    elif partial_matches:
        confidence = 0.5
    else:
        confidence = 0.0

    inferred_order_id = int(full_matches[0]) if full_matches else None

    # --- Natural language reply for your UI ---
    if full_matches:
        if len(full_matches) == 1:
            natural_reply = (
                f"The receipt fully covers order #{full_matches[0]}. "
                f"Do you want to confirm this match?"
            )
        else:
            ids_str = ", ".join(f"#{oid}" for oid in full_matches)
            natural_reply = (
                f"The receipt fully covers these orders: {ids_str}. "
                f"Do you want to confirm these matches?"
            )
    elif partial_matches:
        ids_str = ", ".join(f"#{oid}" for oid in partial_matches)
        natural_reply = (
            "I couldn't find any order that is fully covered by this receipt, "
            f"but there are partial overlaps with these orders: {ids_str}."
        )
    else:
        natural_reply = (
            "I couldn't find any delivery order that clearly matches this receipt."
        )

    print(
        f"matching summary -> full_matches={full_matches}, "
        f"partial_matches={partial_matches}, confidence={confidence}"
    )

    return inferred_order_id, natural_reply