"""
URL configuration for GShare project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from core import views 

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),
    path('about/', views.aboutus, name='aboutus'),
    path('profile/', views.userprofile, name='profile'),
    path('profile/<int:userID>/', views.getUserProfile, name="getUserProfile"),
    path('menu/', views.menu, name='menu'),
    path('groups/', include('chat.urls')),
    path('maps/', views.maps, name="maps"),
    path('login/', views.login_view, name='login'),
    path('signup/', views.signup_view, name='signup'), 
    path('logout/', views.logout_view, name='logout'),
    path('browse/', views.browse_items, name='browse_items'),
    path('cart/', views.cart, name='cart'),
    # path('cart/', views.cart, name='shoppingcart'),
    path('cart/add/<int:item_id>/<int:quantity>/', views.add_to_cart, name='add_to_cart'),
    path('change_order_status/<int:order_id>/<str:new_status>/', views.change_order_status_json, name='change_order_status_json'),
    path('change_status_pending/<int:order_id>/', views.change_status_pending_json, name='change_status_pending_json'),

    path('checkout/', views.checkout, name='checkout'),
    path('shoppingcart/', views.shoppingcart, name='shoppingcart'),
    path('shoppingcart/cartItems/', views.cart_data, name='cart_items'),
    path('shoppingcart/groupItems/', views.group_data, name='group_items'),
    path('shoppingcart/placedItems/', views.placed_data, name='placed_items'),
    path('shoppingcart/inprogress/', views.inprogress_data, name='inprogress'),

    # voice orders
    path('shoppingcart/voice_order/chat/', views.voice_order_chat, name="voice_order_chat"),

    path('shoppingcart/pending/', views.pending_orders, name='pending_orders'),
    
    path('update_delivery_person/<int:order_id>/', views.delivery_accepted_json, name='update_delivery_person'),
    path('remove_delivery_person/<int:order_id>/', views.remove_delivery_json, name='remove_delivery_person'),
    path('create_delivery/<int:order_id>/', views.create_delivery_json, name='create_delivery'),

    path('shoppingcart/<int:order_id>/', views.create_group_order_json, name='create_group_order_json'),
    path('shoppingcart/add_user_to_group/<int:group>/', views.add_user_to_group_json, name='add_user_to_group_json'),
    path('shoppingcart/remove_user_from_group/<int:group>/', views.remove_user_from_group_json, name='remove_user_from_group_json'),
    path('shoppingcart/updateItem/<int:item_id>/<int:quantity>/', views.edit_order_items_json, name='update_item'),
    path('shoppingcart/removeItem/<int:item_id>/<int:quantity>/', views.remove_from_cart, name="remove_item"),
    
    path("maps/maps-data/<str:min_lat>/<str:min_lng>/<str:max_lat>/<str:max_lng>/", views.maps_data, name="maps_data"),
    path('maps/people-data/<str:min_lat>/<str:min_lng>/<str:max_lat>/<str:max_lng>/', views.people_data, name='people_data'),


    path('myorders/', views.myorders, name='order_history'),
    path('payments/<int:order_id>/', views.payments, name='payments'),
    path('payments/checkout/<int:order_id>/', views.paymentsCheckout, name='paymentscheckout'),
    # path('chat/', include('chat.urls')),
    path("__reload__/", include("django_browser_reload.urls")),
    
    # kroger api
    path('cart/kroger/add/',   views.add_kroger_item_to_cart, name='add_kroger_item_to_cart'),
    path('cart/kroger/save/',  views.save_kroger_results,     name='save_kroger_results'),
    path('cart/kroger/clear/', views.clear_kroger_items,      name='clear_kroger_items'),   
    
    # recurring Carts
    path('recurring/', views.manage_recurring_carts, name='manage_recurring_carts'),
    path('recurring/create/', views.create_recurring_cart, name='create_recurring_cart'),
    path('recurring/toggle/<int:cart_id>/', views.toggle_recurring_cart_status, name='toggle_recurring_cart_status'),
    path('recurring/create-from-order/<int:order_id>/', views.create_recurring_from_order, name='create_recurring_from_order'),
    path('recurring/delete/<int:cart_id>/', views.delete_recurring_cart, name='delete_recurring_cart'),
    path('recurring/update/<int:cart_id>/', views.updateScheduledOrders, name='updateScheduledOrders'),

    path('cart/kroger/clear/', views.clear_kroger_items,      name='clear_kroger_items'), 
    path('myorders/recurring', views.scheduled_orders, name='scheduled_orders'),
    path('myorders/create_recurring_cart/', views.create_recurring_cart, name='create_recurring_cart'),
    path('myorders/toggle_cart_status/<int:cart_id>/', views.toggle_cart_status, name='toggle_cart_status'),
    path('myorders/delete_cart/<int:cart_id>/', views.delete_cart, name='delete_cart'),
    path('payment_success/<int:order_id>/', views.payment_success, name='payment_success'),
    
    path('groups/<slug:slug>/map/', views.group_map, name='group_map'),
    path('groups/<slug:slug>/join/', views.join_group, name='join_group'),
    
    # delivery confirmation
    path('orders/<int:order_id>/confirm_delivery/', views.confirm_delivery_json, name='confirm_delivery'),

    # receipt parsing and chat
    path("deliveries/receipt-upload/", views.receipt_upload_view, name="receipt_upload"),
    path("deliveries/receipt/<int:rid>/", views.receipt_detail_view, name="receipt_detail"),
    path("deliveries/receipt/<int:rid>/chat/", views.receipt_chat_view, name="receipt_chat"),
    path("deliveries/receipt/<int:rid>/match-orders/", views.receipt_match_orders_view, name="receipt_match_orders"),
    path("deliveries/receipt/<int:rid>/confirm/", views.receipt_confirm_delivery, name="receipt_confirm_delivery"),
    
    # images
    path("api/upload-image/", views.upload_image, name="upload_image"),
    path("api/image-url/<int:image_id>/", views.get_image_url, name="get_image_url"),
    path("api/users/<int:user_id>/avatar/", views.upload_user_avatar, name="upload_user_avatar"),
    path("api/users/<int:user_id>/avatar/url/", views.get_user_avatar_url, name="get_user_avatar_url"),


]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])