from django.contrib import admin
from django.urls import path, include
from . import views
# from core import views

urlpatterns = [
    path('', views.homepage)
]