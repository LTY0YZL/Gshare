import json
from channels.generic.websocket import AsyncWebsocketConsumer
from .models import Message, ChatGroup, DirectMessageThread, Notification
from django.contrib.auth.models import User
from channels.db import database_sync_to_async
from core.utils.aws_s3 import upload_image_to_aws
import base64
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile
import asyncio
from concurrent.futures import ThreadPoolExecutor

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        if 'room_name' in self.scope['url_route']['kwargs']:
            self.room_name = self.scope['url_route']['kwargs']['room_name']
            self.room_group_name = f'chat_{self.room_name}'
        else:
            self.thread_id = self.scope['url_route']['kwargs']['thread_id']
            self.room_group_name = f'dm_{self.thread_id}'
        
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()
        
    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    @database_sync_to_async
    def handle_new_message(self, sender, chat_group, message):
        try:
            group = ChatGroup.objects.get(slug=chat_group)
            
            for member in group.members.all():
                if member.username != sender:
                    Notification.objects.create(
                        user=member,
                        message=f"New message from {sender}: {message[:50]}"
                    )
                    print(f"Notification created for {member.username}: New message from {sender}")
        except ChatGroup.DoesNotExist:
            print(f"ChatGroup with slug {chat_group} does not exist.")
    
    @database_sync_to_async
    def save_message(self, username, message, image_url=None):
        try:
            user = User.objects.get(username=username)
            if hasattr(self, 'room_name'):
                group = ChatGroup.objects.get(slug=self.room_name)
                Message.objects.create(group=group, sender=user, content=message, image=image_url)
            else:
                thread = DirectMessageThread.objects.get(id=self.thread_id)
                Message.objects.create(thread=thread, sender=user, content=message, image=image_url)
            print(f"Message saved: {message}, Image URL: {image_url}")
        except Exception as e:
            print(f"Error saving message: {e}")
    
    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            message_type = data.get('type', 'message')
            
            if message_type == 'message':
                username = data['username']
                message = data['message']
                image_data = data.get('image')
                
                image_url = None
                
                # If image data is provided, upload to AWS
                if image_data:
                    try:
                        print("Processing image upload...")
                        # Convert base64 to file
                        image_file = self._base64_to_file(image_data)
                        # Upload to AWS (run in thread pool)
                        image_url = await self._upload_to_aws_async(username, image_file)
                        print(f"Image uploaded successfully: {image_url}")
                    except Exception as e:
                        print(f"Image upload error: {e}")
                
                # Save message to database
                await self.save_message(username, message, image_url)
                
                # Send to group
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'chat_message',
                        'username': username,
                        'message': message,
                        'image_url': image_url
                    }
                )
                # Handle notifications
                await self.handle_new_message(username, self.room_name if hasattr(self, 'room_name') else None, message)
                
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'chat_notification',
                        'username': username,
                        'message': message
                    }
                )
            
            elif message_type == 'typing_start':
                username = data['username']
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'user_typing_start',
                        'username': username
                    }
                )
            elif message_type == 'typing_stop':
                username = data['username']
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'user_typing_stop',
                        'username': username
                    }
                )
        except json.JSONDecodeError:
            print("Invalid JSON received")
    
    def _base64_to_file(self, base64_data):
        try:
            if "," in base64_data:
                base64_data = base64_data.split(",")[1]
            
            decoded_file = base64.b64decode(base64_data)
            file_buffer = BytesIO(decoded_file)
            
            file = InMemoryUploadedFile(
                file_buffer,
                'ImageField',
                'image.png',
                'image/png',
                len(decoded_file),
                None
            )
            return file
        except Exception as e:
            raise Exception(f"Error converting base64 to file: {e}")
    
    async def _upload_to_aws_async(self, username, file):
        """Run AWS upload in thread pool to avoid blocking"""
        loop = asyncio.get_event_loop()
        executor = ThreadPoolExecutor()
        return await loop.run_in_executor(
            executor,
            upload_image_to_aws,
            file,
            f'chat/{username}/'
        )
                
    async def chat_message(self, event):
        username = event['username']
        message = event['message']
        image_url = event.get('image_url')
        
        await self.send(text_data=json.dumps({
            'type': 'message',
            'username': username,
            'message': message,
            'image_url': image_url
        }))
    
    async def user_typing_start(self, event):
        username = event['username']
        await self.send(text_data=json.dumps({
            'type': 'typing_start',
            'username': username
        }))
    
    async def user_typing_stop(self, event):
        username = event['username']
        await self.send(text_data=json.dumps({
            'type': 'typing_stop',
            'username': username
        }))
    
    async def chat_notification(self, event):
        username = event['username']
        message = event['message']
        
        if self.scope['user'].username != username:
            await self.send(text_data=json.dumps({
                'type': 'notification',
                'title': f"New message from {username}",
                'body': message[:50] + ("..." if len(message) > 50 else "")
            }))