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
    path('menu/', views.menu, name='menu'),
    path('groups/', include('chat.urls')),
    path('maps/', views.maps, name="maps"),
    path('login/', views.login_view, name='login'),
    path('signup/', views.signup_view, name='signup'), 
    path('logout/', views.logout_view, name='logout'),
    path('browse/', views.browse_items, name='browse_items'),
    path('cart/', views.cart, name='cart'),
    # path('cart/', views.cart, name='shoppingcart'),
    path('cart/add/<int:item_id>/', views.add_to_cart, name='add_to_cart'),
    path('change_order_status/<int:order_id>/<str:new_status>/', views.change_order_status_json, name='change_order_status_json'),
    path('checkout/', views.checkout, name='checkout'),
    path('shoppingcart/', views.shoppingcart, name='shoppingcart'),
    path('myorders/', views.myorders, name='order_history'),
    path('cart/payments/', views.payments, name='payments'),
    # path('chat/', include('chat.urls')),
    path("__reload__/", include("django_browser_reload.urls")),
    
    path('cart/kroger/add/',   views.add_kroger_item_to_cart, name='add_kroger_item_to_cart'),
    path('cart/kroger/save/',  views.save_kroger_results,     name='save_kroger_results'),
    path('cart/kroger/clear/', views.clear_kroger_items,      name='clear_kroger_items'),   
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])