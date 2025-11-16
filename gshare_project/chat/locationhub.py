import time
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.cache import cache

TTL = 120  
INDEX_KEY = "loc:index" 

def _index_add(user_id: int) -> None:
    ids = cache.get(INDEX_KEY) or []
    if user_id not in ids:
        ids.append(user_id)
    cache.set(INDEX_KEY, ids, TTL)


class LocationHub(AsyncJsonWebsocketConsumer):
    group = "location_stream"

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close(code=4401)
            return

        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.send_json({"type": "hello"})

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if content.get("type") != "ping":
            return

        try:
            lat = float(content["lat"])
            lng = float(content["lng"])
        except Exception:
            return

        role = (content.get("role") or "").strip()
        user = self.scope["user"]

        payload = {
            "type": "loc",
            "uid": user.id,
            "username": user.username,
            "lat": lat,
            "lng": lng,
            "role": role,
            "ts": int(time.time()),
        }

        cache.set(f"loc:{user.id}", payload, TTL)  
        _index_add(user.id)

        await self.channel_layer.group_send(
            self.group, {"type": "broadcast", "payload": payload}
        )

    async def broadcast(self, event):
        await self.send_json(event["payload"])