import json
from channels.generic.websocket import AsyncJsonWebsocketConsumer

class Tracking(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close(code=4401)
            return

        # Use chat room slug instead of group id
        self.slug = self.scope["url_route"]["kwargs"]["slug"]
        self.room = f"livetrack_room_{self.slug}"

        await self.channel_layer.group_add(self.room, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if hasattr(self, "room"):
            await self.channel_layer.group_discard(self.room, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if content.get("type") != "ping":
            return

        lat, lng = content.get("lat"), content.get("lng")
        role = content.get("role", "")
        if lat is None or lng is None:
            return

        user = self.scope["user"]
        payload = {
            "type": "update",
            "username": user.username,
            "lat": float(lat),
            "lng": float(lng),
            "role": role,
        }
        await self.channel_layer.group_send(self.room, {"type": "broadcast", "payload": payload})

    async def broadcast(self, event):
        await self.send_json(event["payload"])