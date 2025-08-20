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
    

class Message(models.Model):
    group = models.ForeignKey(ChatGroup, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f'{self.sender.username}: {self.content[:20]}'
