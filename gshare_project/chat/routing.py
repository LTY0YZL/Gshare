from django.urls import re_path
from django.urls import path
from . import consumers
from .locationhub import LocationHub

websocket_urlpatterns = [
    re_path(r"ws/chat/(?P<room_name>[-\w]+)/$", consumers.ChatConsumer.as_asgi()),
    re_path(r"ws/chat/dm/(?P<thread_id>\d+)/$", consumers.ChatConsumer.as_asgi()),

    # re_path(r'ws/chat/(?P<room_name>\w+)/$', consumers.ChatConsumer.as_asgi()),
    re_path(r"ws/location/$", LocationHub.as_asgi()),
]