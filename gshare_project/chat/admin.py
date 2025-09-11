from django.contrib import admin
from .models import ChatGroup, Message, DirectMessageThread


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
    
@admin.register(DirectMessageThread)
class DirectMessageThreadAdmin(admin.ModelAdmin):
    list_display = ('id', 'created_at')
    search_fields = ('participants__username',)
    filter_horizontal = ('participants',)
    
