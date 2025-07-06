from django.urls import path
from . import views

urlpatterns = [
    path('', views.groups_page, name='groups_page'),  # Main chat groups page
    path('create/', views.create_group, name='create_group'),  # Create a new chat group
    path('<str:room_name>/', views.chat_room, name='chat_room'),  # Individual chat room
]
