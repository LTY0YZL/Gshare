from django.urls import re_path
from django.urls import path
from . import consumers

websocket_urlpatterns = [
    path('ws/chat/<str:room_name>/', consumers.ChatConsumer.as_asgi()),
    path("ws/chat/dm/<str:thread_id>/", consumers.ChatConsumer.as_asgi()),

    # re_path(r'ws/chat/(?P<room_name>\w+)/$', consumers.ChatConsumer.as_asgi()),
]