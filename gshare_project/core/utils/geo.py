import requests
from urllib.parse import urlencode
from django.conf import settings
import hashlib

def _fake_coords(address: str) -> tuple[float, float]:
    """Return deterministic fake coords in dev/test."""
    h = int(hashlib.sha1(address.encode()).hexdigest(), 16)
    lat = 30 + (h % 4000000) / 100000.0
    lng = -120 + ((h // 4000000) % 4000000) / 100000.0
    return round(lat, 6), round(lng, 6)

def geoLoc(address: str) -> tuple[float, float]:
    if not address or not address.strip():
        return 0.0, 0.0

    if settings.DEBUG and getattr(settings, "FAKE_GEOLOC", True):
        return _fake_coords(address)

    params = {"address": address.strip(), "key": settings.GOOGLE_MAPS_API_KEY}
    url = f"https://maps.googleapis.com/maps/api/geocode/json?{urlencode(params)}"

    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "OK" and data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            return float(loc["lat"]), float(loc["lng"])
    except Exception as e:
        print("Geocode error:", e)

    return 0.0, 0.0
