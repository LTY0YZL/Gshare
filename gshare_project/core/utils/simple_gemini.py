import base64
import json

from django.conf import settings
from django.utils import timezone

from google import genai

from core.models import Receipt, ReceiptLine, ReceiptChatMessage
from core.utils.aws_s3 import get_s3_client

client = genai.Client(api_key=settings.GEMINI_API_KEY)


def _load_image_bytes_from_s3(receipt: Receipt) -> bytes:
    s3 = get_s3_client()
    obj = s3.get_object(Bucket=receipt.s3_bucket, Key=receipt.s3_key)
    return obj["Body"].read()


def scan_receipt(receipt_id: int):
    """
    Synchronous scan:
      - download image from S3
      - ask Gemini Vision for JSON items
      - store items as ReceiptLine rows
      - update Receipt.status and gemini_json
    """
    receipt = Receipt.objects.using("gsharedb").get(pk=receipt_id)

    # mark as processing (optional, mostly for debugging)
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

    # naive: assume jpeg if key ends in jpg/jpeg, otherwise png
    key_lower = receipt.s3_key.lower()
    if key_lower.endswith((".jpg", ".jpeg")):
        mime = "image/jpeg"
    else:
        mime = "image/png"

    result = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[
            prompt,
            {
                "inline_data": {
                    "mime_type": mime,
                    "data": base64.b64encode(img_bytes).decode("utf-8"),
                }
            },
        ],
    )

    raw = result.text or ""

    # try to parse JSON safely
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            data = json.loads(raw[start : end + 1])
        else:
            raise

    receipt.gemini_json = data

    # replace existing lines for this receipt
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
    Very simple chat: we summarize lines + history and call a text model.
    history is a list[(role, content)] with role in {"user","assistant"}.
    """
    # build a short summary of items
    lines = ReceiptLine.objects.using("gsharedb").filter(receipt=receipt).order_by("id")
    items_text = "\n".join(
        f"- {l.name} x{l.quantity} (total {l.total_price})"
        for l in lines
    ) or "No items were parsed yet."

    system_prompt = f"""
You are helping match and correct a grocery order based on this receipt.
Here are the parsed items:

{items_text}

If the user asks to correct items, update quantities, or clarify, answer in concise plain English.
Do NOT invent items that clearly aren't on the receipt.
"""

    # turn history into a single text block (cheapest possible)
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
        model="gemini-1.5-flash-002",
        contents=[full_prompt],
    )

    return resp.text or "Sorry, I could not generate a response."
