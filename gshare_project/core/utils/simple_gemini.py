# core/utils/simple_gemini.py

import base64
import json

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


def suggest_matching_order(*, receipt, lines, candidate_orders):
    """
    Ask Gemini which delivery order(s) best match this receipt.

    It will ONLY ask the driver to confirm when the receipt contains
    ALL the items of an order (receipt is a superset of that order).
    """

    # --- Build receipt items summary ---
    receipt_items = [
        {
            "name": l.name,
            "quantity": l.quantity,
            "unit_price": l.unit_price,
            "total_price": l.total_price,
        }
        for l in lines
    ]

    # --- Load line items for each candidate order via raw SQL (since order_items has composite PK) ---
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
                {"name": r[0], "quantity": float(r[1] or 0), "price": float(r[2] or 0)}
                for r in cur.fetchall()
            ]
            o = dict(order)  # copy
            o["items"] = row_items
            orders_info.append(o)

    # --- Prompt: define FULL match vs PARTIAL and when to ask user ---
    prompt = f"""
You are helping a grocery delivery driver match a scanned receipt to the
delivery orders they are currently responsible for.

RECEIPT ITEMS (parsed from the image):
{json.dumps(receipt_items, indent=2)}

CANDIDATE DELIVERY ORDERS (what the driver might be delivering):
{json.dumps(orders_info, indent=2)}

DEFINITIONS (VERY IMPORTANT):

- Normalize item names sensibly (ignore case, small spelling differences).
- A receipt **FULLY MATCHES** an order if:
    - For every item in the order, there is a corresponding item on the receipt
      with quantity >= that of the order.
    - Extra items on the receipt are allowed.
- A **PARTIAL MATCH** is when some items overlap but **at least one** order item
  is missing or has too low a quantity.

YOUR JOB:

1. Determine which orders (if any) are FULL MATCHES.
2. Optionally, list orders that are only PARTIAL MATCHES.
3. Decide a confidence score between 0.0 and 1.0 that reflects how sure you are
   about the best full match (if any).

USER-FACING RULES:

- If there is at least one FULL MATCH:
    - In your natural-language reply, clearly say which order IDs are full matches.
    - Politely ASK the driver to confirm before anything is marked delivered.
      Example: "The receipt fully covers order #215. Do you want to confirm
      this match?"
- If there are NO full matches (only partial or none):
    - DO NOT ask to confirm or mark anything delivered.
    - Just explain briefly that no order is fully covered by the receipt.
      You may mention partial matches by order id.

OUTPUT FORMAT:

Respond with **NO code fences** and include exactly one JSON block between the
markers BEGIN and END:

BEGIN
{{
  "full_matches": [215, 233],      // order IDs that are fully covered by the receipt
  "partial_matches": [210],        // optional list of partial matches
  "confidence": 0.87,              // confidence that the best full match is correct
  "natural_reply": "What you say to the driver in plain English."
}}
END

- "full_matches" and "partial_matches" must always exist (use [] if none).
- "natural_reply" is what will be shown directly in chat.
- Do not include any other text outside BEGIN/END.
"""

    resp = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[prompt],
    )

    text = (resp.text or "").strip()

    # --- Extract JSON between BEGIN/END ---
    start = text.find("BEGIN")
    end = text.find("END")

    # Fallback: if markers missing, treat whole text as explanation
    if start == -1 or end == -1 or end <= start:
        return None, text

    json_block = text[start + len("BEGIN"):end].strip()

    # safety: strip stray backticks if Gemini ever adds them
    json_block = json_block.strip().strip("`")
    if json_block.lower().startswith("json"):
        json_block = json_block[4:].strip()

    try:
        data = json.loads(json_block)
    except Exception:
        # If parsing fails, just return raw text
        return None, text

    full_matches = data.get("full_matches") or []
    partial_matches = data.get("partial_matches") or []
    confidence = float(data.get("confidence") or 0.0)
    natural_reply = (data.get("natural_reply") or "").strip()

    inferred_order_id = None

    # ONLY when there is a full match do we propose an order id
    # (status is still changed later by your confirm view).
    if full_matches:
        # choose the first full match as suggested id
        inferred_order_id = int(full_matches[0])

    # If Gemini forgot to provide a user-facing reply, synthesize one
    if not natural_reply:
        if inferred_order_id:
            natural_reply = (
                f"The receipt fully covers order #{inferred_order_id}. "
                f"Do you want to confirm this match?"
            )
        elif partial_matches:
            natural_reply = (
                "I couldn't find any order that is fully covered by this receipt, "
                "but there are partial overlaps with these orders: "
                + ", ".join(str(o) for o in partial_matches)
                + "."
            )
        else:
            natural_reply = (
                "I couldn't find any delivery order that clearly matches this receipt."
            )

    return inferred_order_id, natural_reply