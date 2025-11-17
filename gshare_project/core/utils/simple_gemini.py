# core/utils/simple_gemini.py

import base64
import json

from django.conf import settings
from django.utils import timezone

from google import genai

from core.models import Receipt, ReceiptLine
from core.utils.aws_s3 import get_s3_client

# Single shared Gemini client
client = genai.Client(api_key=settings.GEMINI_API_KEY)


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
        model="gemini-2.0-flash",
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


def chat_about_receipt(receipt: Receipt, history, user_message: str):
    """
    Use Gemini to BOTH:
      - generate a natural-language reply
      - return JSON operations to update ReceiptLine rows.

    Returns a dict:
      {
        "reply_text": str,
        "operations": [
          {
            "op": "update" | "add" | "delete",
            "target_name": "string or null",
            "fields": {
              "name": "optional string",
              "quantity": optional number,
              "unit_price": optional number,
              "total_price": optional number
            }
          },
          ...
        ]
      }
    """
    # current items
    lines = (
        ReceiptLine.objects.using("gsharedb")
        .filter(receipt=receipt)
        .order_by("id")
    )

    items_text = "\n".join(
        f"- {l.name} x{l.quantity} (total {l.total_price})" for l in lines
    ) or "No items were parsed yet."

    system_prompt = f"""
You are helping correct a grocery order based on this receipt.

Here are the current parsed items (do NOT rename them unless the user explicitly says so):

{items_text}

When the user asks to change something in the list (e.g., change quantity, fix a name, delete an item, or add a missing item),
you MUST respond ONLY in valid JSON with this exact schema:

{{
  "reply_text": "a short, friendly explanation to show to the user",
  "operations": [
    {{
      "op": "update" | "add" | "delete",
      "target_name": "name of the existing item you are changing or deleting, or null for add",
      "fields": {{
        "name": "optional new name for the item",
        "quantity": optional number,
        "unit_price": optional number,
        "total_price": optional number
      }}
    }}
  ]
}}

Rules:
- If the user is only asking a question (no change), return an empty list for "operations".
- For update/delete, set "target_name" to exactly one of the item names from the list above.
- For add, set "target_name" to null and fill in "fields.name" (and quantity/price if given).
- Never include extra keys. Always return valid JSON, no backticks, no markdown.
"""

    history_text = ""
    for role, content in history:
        prefix = "User" if role == "user" else "Assistant"
        history_text += f"{prefix}: {content}\n"

    full_prompt = (
        system_prompt
        + "\n\nConversation so far:\n"
        + history_text
        + f"\nUser: {user_message}\n"
        + 'Now return ONLY the JSON object as described above.'
    )

    resp = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[full_prompt],
    )

    raw = resp.text or "{}"

    # Try to parse JSON – same style as scan_receipt
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            data = json.loads(raw[start : end + 1])
        else:
            # fallback: no ops, plain text error
            return {
                "reply_text": "Sorry, I had trouble understanding that change.",
                "operations": [],
            }

    # Make sure structure is at least present
    if "reply_text" not in data:
        data["reply_text"] = "Okay, I updated the items."
    if "operations" not in data or not isinstance(data["operations"], list):
        data["operations"] = []

    return data