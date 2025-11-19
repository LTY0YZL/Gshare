import json
from typing import Dict, Any, List

import google.generativeai as genai
from django.conf import settings

from core.models import Receipt, ReceiptLine

# Configure Gemini once
if settings.GEMINI_API_KEY:
    genai.configure(api_key=settings.GEMINI_API_KEY)


def _get_model(name: str):
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not configured")
    return genai.GenerativeModel(name)


def parse_receipt_image(receipt: Receipt) -> Dict[str, Any]:
    """
    Download the receipt image from S3 and ask Gemini to parse it into JSON.
    Returns a dict like: {"store": {...}, "items": [...]}
    """
    from core.utils.aws_s3 import get_s3_client  # avoid circular import

    s3 = get_s3_client()
    obj = s3.get_object(Bucket=receipt.s3_bucket, Key=receipt.s3_key)
    image_bytes = obj["Body"].read()

    prompt = """
You are an assistant that extracts structured data from grocery receipts.

Return ONLY valid JSON (no explanation, no markdown) in this exact schema:

{
  "store": {
    "name": "string or null",
    "address": "string or null",
    "datetime": "ISO8601 datetime string or null"
  },
  "items": [
    {
      "name": "string",
      "quantity": float,
      "unit_price": float or null,
      "total_price": float or null,
      "meta": {
        "category": "string or null",
        "brand": "string or null",
        "code": "UPC/SKU if present, else null"
      }
    },
    ...
  ]
}

Rules:
- If unit price is not clearly printed, set unit_price to null.
- If total line price is not clear, set total_price to null.
- Prefer human-friendly item names, but keep them concise (e.g., "2% milk", "bananas").
- Do NOT add comments or trailing commas. The response MUST be valid JSON.
"""

    model_name = getattr(settings, "GEMINI_RECEIPT_MODEL", "gemini-1.5-flash")
    model = _get_model(model_name)

    # vision call: prompt + image
    response = model.generate_content(
        [
            prompt,
            {
                "mime_type": "image/jpeg",  # okay for png/jpg; Gemini is tolerant
                "data": image_bytes,
            },
        ]
    )

    raw = response.text.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to salvage by trimming junk
        # (very defensive, but helpful in practice)
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            data = json.loads(raw[start : end + 1])
        else:
            raise

    return data


def apply_parsed_receipt(receipt: Receipt, data: Dict[str, Any]) -> None:
    """
    Save parsed JSON into Receipt.gemini_json and populate ReceiptLine rows.
    """
    # Store full JSON for debugging later
    receipt.gemini_json = data

    items: List[Dict[str, Any]] = data.get("items", [])
    # Clear old lines
    ReceiptLine.objects.using("gsharedb").filter(receipt=receipt).delete()

    for item in items:
        name = item.get("name", "").strip()
        if not name:
            continue

        quantity = item.get("quantity") or 1
        unit_price = item.get("unit_price")
        total_price = item.get("total_price")
        meta = item.get("meta") or {}

        ReceiptLine.objects.using("gsharedb").create(
            receipt=receipt,
            name=name,
            quantity=float(quantity),
            unit_price=float(unit_price) if unit_price is not None else None,
            total_price=float(total_price) if total_price is not None else None,
            meta=meta,
        )

    receipt.status = "done"
    receipt.error = ""
    receipt.save(using="gsharedb")


def build_receipt_context(receipt: Receipt) -> str:
    """
    Turn receipt + lines into a text summary that we send to Gemini for chat.
    """
    lines = list(receipt.lines.using("gsharedb").all().order_by("id"))

    summary_lines = []
    for line in lines:
        summary_lines.append(
            f"- {line.name} x {line.quantity}"
            + (f" @ {line.unit_price:.2f}" if line.unit_price is not None else "")
            + (f" = {line.total_price:.2f}" if line.total_price is not None else "")
        )

    summary_text = "\n".join(summary_lines) or "(no items yet)"

    return f"""
Current receipt summary:

Receipt ID: {receipt.id}
Status: {receipt.status}

Items:
{summary_text}
"""


def chat_about_receipt(receipt: Receipt, history, user_message: str) -> str:
    """
    Use Gemini to chat about this receipt. 
    history = list of (role, content) tuples.
    Returns assistant_message (string).
    """
    model_name = getattr(settings, "GEMINI_CHAT_MODEL", "gemini-1.5-flash")
    model = _get_model(model_name)

    system_prompt = """
You are a helpful assistant helping a user clean up and correct a parsed grocery receipt.

Goals:
- Help the user make sure quantities, item names and prices are correct.
- Suggest corrections or clarifications.
- If the user asks "what looks wrong?", point out suspicious lines (e.g., quantity 0, huge prices).
- Be concise. 2-4 sentences unless more detail is requested.
- DO NOT output JSON here – just plain text explanation.
"""

    receipt_context = build_receipt_context(receipt)

    # Build history in Gemini format
    gem_history = [
        {
            "role": "user",
            "parts": [system_prompt + "\n\n" + receipt_context],
        }
    ]

    for msg in history:
        r, c = msg
        if r == "user":
            gem_history.append({"role": "user", "parts": [c]})
        elif r == "assistant":
            gem_history.append({"role": "model", "parts": [c]})

    chat = model.start_chat(history=gem_history)
    response = chat.send_message(user_message)
    return response.text.strip()