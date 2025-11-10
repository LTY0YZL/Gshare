from django.db import models
from django.contrib.auth.models import User
import uuid

# Create your models here.

def generate_group_code():
    return uuid.uuid4().hex[:8]  # Generates a random 8-character code

class ChatGroup(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    group_code = models.CharField(max_length=8, unique=True, default=generate_group_code)
    members = models.ManyToManyField(User, related_name='chat_groups')
    
    def __str__(self):
        return self.name
    

class DirectMessageThread(models.Model):
    participants = models.ManyToManyField(User, related_name='direct_message')
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        usernames = ", ".join(self.participants.values_list("username", flat=True))
        return f"Direct Message Between {usernames}"
    
    @classmethod
    def get_or_create_thread(cls, user1, user2):
        users = sorted([user1, user2], key=lambda u: u.id)
        thread = cls.objects.filter(participants=users[0]).filter(participants=users[1]).first()
        if thread:
            return thread, False
        if not thread:
            thread = DirectMessageThread.objects.create()
            thread.participants.add(users[0], users[1])
        return thread, True
    

class Message(models.Model):
    group = models.ForeignKey(ChatGroup, on_delete=models.CASCADE, null=True, blank=True, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE)
    thread = models.ForeignKey(DirectMessageThread, on_delete=models.CASCADE, null=True, blank=True, related_name='messages')
    content = models.TextField()
    # image = models.ImageField(upload_to='chat_images/', null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['timestamp']
    
    def __str__(self):
        return f'{self.sender.username}: {self.content[:20]}'
    
    def clean(self):
        from django.core.exceptions import ValidationError
        if bool(self.group) == bool(self.thread):
            raise ValidationError("Message must be associated with either a group or a direct message thread.")

