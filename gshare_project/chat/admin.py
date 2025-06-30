from django.contrib import admin
from .models import ChatGroup, Message


# Register your models here.
@admin.register(ChatGroup)
class ChatGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    search_fields = ('name',)
    
@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('group', 'sender', 'content', 'timestamp')
    search_fields = ('content',)
    list_filter = ('group', 'timestamp')
    
