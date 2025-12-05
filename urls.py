from django.urls import path
from . import views

urlpatterns = [
    path('users/', views.users, name='users'),
    path('stores/', views.stores, name='stores'),
    path('items/', views.items, name='items'),
    path('orders/', views.orders, name='orders'),
    path('order-items/', views.order_items, name='order_items'),
    path('deliveries/', views.deliveries, name='deliveries'),
    path('feedback/', views.feedback, name='feedback'),
]