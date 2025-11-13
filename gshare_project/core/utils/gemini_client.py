import base64, json, google.generativeai as genai
from django.conf import settings

def _inline(mime,b): return {"inlineData":{"mimeType":mime,"data":base64.b64encode(b).decode()}}

def parse_receipt_with_gemini(image_bytes, mime, text_lines=None):
    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel(settings.GEMINI_MODEL)
    prompt = ("Extract this receipt to STRICT JSON ONLY with keys: "
              "merchant (string), datetime (string|null), subtotal (number|null), tax (number|null), total (number|null), "
              "lines (array of {name (string), quantity (number), unit_price (number|null), total_price (number|null)}). "
              "Assume quantity=1 if missing.")
    parts=[prompt]
    if text_lines: parts.append("Detected lines:\n" + "\n".join(text_lines[:100]))
    parts.append(_inline(mime, image_bytes))
    resp=model.generate_content(parts); text=(resp.text or "").strip()
    if text.startswith("```"): text=text.strip("`"); text=text[4:].strip() if text.lower().startswith("json") else text
    try: return json.loads(text)
    except: return {"merchant":"","datetime":None,"subtotal":None,"tax":None,"total":None,"lines":[],"raw":text}
