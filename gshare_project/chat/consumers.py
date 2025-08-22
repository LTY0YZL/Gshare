import json
from channels.generic.websocket import AsyncWebsocketConsumer
from .models import Message, ChatGroup
from django.contrib.auth.models import User
from channels.db import database_sync_to_async

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.room_group_name = f'chat_{self.room_name}'
        
        # Join the group chat_<room_name>
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()
        
    async def disconnect(self, close_code):
        # Leave the group chat_<room_name>
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    @database_sync_to_async
    def get_group(self, slug):
        return ChatGroup.objects.get(slug=slug)
    
    @database_sync_to_async
    def get_user(self, username):
        return User.objects.get(username=username)
    
    @database_sync_to_async
    def create_message(self, group, user, content):
        return Message.objects.create(group=group, sender=user, content=content)
    
    async def receive(self, text_data):
        data = json.loads(text_data)
        # Extract the username from the received data
        username = data['username']
        # Extract the message from the received data
        message = data['message']
        # add the message to the database
        group = await self.get_group(self.room_name)
        user = await self.get_user(username)
        await self.create_message(group, user, message)
        
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat_message',
                'username': username,
                'message': message
            }
        )
                
    async def chat_message(self, event):
        username = event['username']
        message = event['message']
        
        # Send the message to the Websocket
        await self.send(text_data=json.dumps({
            'username': username,
            'message': message
        }))
