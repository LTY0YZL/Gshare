import json
from channels.generic.websocket import AsyncWebsocketConsumer
from .models import Message, ChatGroup, DirectMessageThread, Notification
from django.contrib.auth.models import User
from channels.db import database_sync_to_async
from core.utils.aws_s3 import upload_image_to_aws, presigned_url
import base64
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile
import asyncio
from concurrent.futures import ThreadPoolExecutor

class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        user = self.scope['user']

        # Determine room or DM
        if 'room_name' in self.scope['url_route']['kwargs']:
            self.room_name = self.scope['url_route']['kwargs']['room_name']
            self.room_group_name = f'chat_{self.room_name}'
        else:
            self.thread_id = self.scope['url_route']['kwargs']['thread_id']
            self.room_group_name = f'dm_{self.thread_id}'

        # Join the chat room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        # Join a user-specific group for notifications
        await self.channel_layer.group_add(
            f"user_{user.username}",
            self.channel_name
        )

        await self.accept()

    async def disconnect(self, close_code):
        user = self.scope['user']

        # Leave the chat room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

        # Leave the user-specific notification group
        await self.channel_layer.group_discard(
            f"user_{user.username}",
            self.channel_name
        )

    @database_sync_to_async
    def handle_new_message(self, sender_username, chat_group_slug, message_content):
        """
        Creates notifications for all members of the chat except the sender
        and returns a list of usernames who should receive real-time notifications.
        """
        notified_users = []

        try:
            # Get the chat group
            group = ChatGroup.objects.get(slug=chat_group_slug)

            for member in group.members.all():
                if member.username != sender_username:
                    # Create a notification in the database
                    Notification.objects.create(
                        user=member,
                        message=f"New message from {sender_username}: {message_content[:50]}"
                    )
                    notified_users.append(member.username)

        except ChatGroup.DoesNotExist:
            print(f"ChatGroup with slug {chat_group_slug} does not exist.")

        return notified_users

    @database_sync_to_async
    def get_presigned_url(self, image_key):
        return presigned_url(image_key)
    
    
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
                
                image_key = None
                
                # If image data is provided, upload to AWS
                if image_data:
                    try:
                        print("Processing image upload...")
                        # Convert base64 to file
                        image_file = self._base64_to_file(image_data)
                        # Upload to AWS (run in thread pool)
                        image_key = await self._upload_to_aws_async(username, image_file)
                        print(f"Image uploaded successfully: {image_key}")
                    except Exception as e:
                        print(f"Image upload error: {e}")
                
                # Save message to database
                await self.save_message(username, message, image_key)
                
                # Send to group
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'chat_message',
                        'username': username,
                        'message': message,
                        'image_url': image_key
                    }
                )
                # Handle notifications
                # Get the list of users to notify
                notified_users = await self.handle_new_message(
                    sender_username=username,
                    chat_group_slug=self.room_name if hasattr(self, 'room_name') else None,
                    message_content=message
                )
                
                if hasattr(self, 'thread_id'):
                    # Notify the other participant
                    thread = DirectMessageThread.objects.get(id=self.thread_id)
                    for member in thread.members.all():
                        if member.username != username:
                            Notification.objects.create(
                                user=member,
                                message=f"{username} sent a message"
                            )
                            notified_users.append(member.username)
                    return notified_users

                # Send notifications to each user's personal group
                for u in notified_users:
                    await self.channel_layer.group_send(
                        f"user_{u}",
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
        folder = f'chat/{username}'.strip('/')  # remove trailing slash
        return await loop.run_in_executor(
            executor,
            upload_image_to_aws,
            file,
            folder
        )
                
    async def chat_message(self, event):
        username = event['username']
        message = event['message']
        image_key = event.get('image_url')
        
        presigned = None
        if image_key:
            presigned = await self.get_presigned_url(image_key)
            print(f"Image key: {image_key}, presigned URL: {presigned}")
        
        await self.send(text_data=json.dumps({
            'type': 'message',
            'username': username,
            'message': message,
            'image_url': presigned
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
            
