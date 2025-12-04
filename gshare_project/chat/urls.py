from django.urls import path
from . import views

urlpatterns = [
    path('', views.groups_page, name='groups_page'),  # Main chat groups page
    # path('api/messages/send/', views.send_message, name='send_message'),
    # path('api/messages/stream/', views.sse_stream_messages, name='sse_stream_messages'),
    # path('api/notifications/stream/', views.sse_stream_notifications, name='sse_stream_notifications'),
    # path('api/typing/set/', views.set_typing, name='set_typing'),
    # path('api/typing/stream/', views.sse_typing, name='sse_typing'),
    # path('history/<slug:room_slug>/', views.load_chat_history, name='chat_history'),
    # path('history/dm/<str:thread_id>/', views.load_chat_history, name='dm_chat_history'),
    path('send_message/', views.send_message, name='send_message'),
    path('list_messages/', views.list_messages, name='list_messages'),
    path("json/notifications/", views.json_notifications, name="json_notifications"),
    path("edit_message/<int:message_id>/", views.edit_message, name="edit_message"),
    path("delete_message/<int:message_id>/", views.delete_message, name="delete_message"),
    # path('upload_image/', views.upload_image, name='send_image'),
    path("autocomplete_usernames/", views.autocomplete_usernames, name="autocomplete_usernames"),
    path('create/', views.create_group, name='create_group'),  # Create a new chat group
    path('join/', views.join_group, name='join_group'),  # Join an existing chat group
    path('dm/<str:thread_id>/', views.direct_message, name='direct_message'),  # Direct message thread
    path('<str:room_name>/', views.chat_room, name='chat_room'),  # Individual chat room   
]
