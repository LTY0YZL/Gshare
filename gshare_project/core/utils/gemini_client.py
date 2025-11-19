import base64, json, google.generativeai as genai
from django.conf import settings

def _inline_part(mime_type: str, data: bytes):
    return {
        "inlineData": {
            "mimeType": mime_type,
            "data": base64.b64encode(data).decode("utf-8"),
        }
    }


def parse_receipt_with_gemini(image_bytes: bytes, mime_type: str, text_lines=None):
    """
    Call Gemini to parse a receipt image into a strict JSON structure.

    Returns something like:
    {
        "merchant": str,
        "datetime": str or null,
        "subtotal": number or null,
        "tax": number or null,
        "total": number or null,
        "lines": [
            {
                "name": str,
                "quantity": number,
                "unit_price": number or null,
                "total_price": number or null
            }, ...
        ]
    }
    """
    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel(settings.GEMINI_MODEL)

    prompt = (
        "You are extracting data from a shopping receipt. "
        "Return STRICT JSON only, no prose, no markdown. "
        "Use this schema exactly:\n"
        "{\n"
        '  "merchant": string,\n'
        '  "datetime": string | null,\n'
        '  "subtotal": number | null,\n'
        '  "tax": number | null,\n'
        '  "total": number | null,\n'
        '  "lines": [\n'
        "    {\n"
        '      "name": string,\n'
        '      "quantity": number,\n'
        '      "unit_price": number | null,\n'
        '      "total_price": number | null\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Assume quantity = 1 when the receipt does not show it. "
        "Do NOT wrap this JSON in backticks."
    )

    parts = [prompt]
    if text_lines:
        parts.append(
            "Detected text lines (may help you, but image is the main source):\n"
            + "\n".join(text_lines[:100])
        )
    parts.append(_inline_part(mime_type, image_bytes))

    resp = model.generate_content(parts)
    text = (resp.text or "").strip()

    # Strip accidental ```json ``` fences
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()

    try:
        return json.loads(text)
    except Exception:
        # fallback, keep raw text for debugging
        return {
            "merchant": "",
            "datetime": None,
            "subtotal": None,
            "tax": None,
            "total": None,
            "lines": [],
            "raw": text,
        }