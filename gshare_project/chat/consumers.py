import json
from channels.generic.websocket import AsyncWebsocketConsumer
from .models import Message, ChatGroup, DirectMessageThread
from django.contrib.auth.models import User
from channels.db import database_sync_to_async

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        
        if 'room_name' in self.scope['url_route']['kwargs']:
            self.room_name = self.scope['url_route']['kwargs']['room_name']
            self.room_group_name = f'chat_{self.room_name}'
        else:
            self.thread_id = self.scope['url_route']['kwargs']['thread_id']
            self.room_group_name = f'dm_{self.thread_id}'
        
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
    
    @database_sync_to_async
    def save_message(self, username, message):
        user = User.objects.get(username=username)
        if hasattr(self, 'room_name'):
            group = ChatGroup.objects.get(slug=self.room_name)
            Message.objects.create(group=group, sender=user, content=message)
        else:
            thread = DirectMessageThread.objects.get(id=self.thread_id)
            Message.objects.create(thread=thread, sender=user, content=message)
    
    async def receive(self, text_data):
        data = json.loads(text_data)
        # Extract the username from the received data
        username = data['username']
        # Extract the message from the received data
        message = data['message']
        # add the message to the database
        await self.save_message(username, message)
        # group = await self.get_group(self.room_name)
        # user = await self.get_user(username)
        # await self.create_message(group, user, message)
        
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

