from decimal import Decimal, InvalidOperation
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.core.paginator import Paginator

from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User as AuthUser
from django.contrib import messages
from django.utils import timezone
from django.conf import settings
from core.models import Items, Stores
from django.db.models import Q

from core.models import (
    Users as ProfileUser,
    Stores, Items,
    Orders, OrderItems,
    Deliveries, Feedback
)

def get_custom_user(request):
    if not request.user.is_authenticated:
        return None
    try:
        return ProfileUser.objects.using('gsharedb').get(email=request.user.email)
    except ProfileUser.DoesNotExist:
        return None

def home(request):
    stores = Stores.objects.all().order_by('name')
    return render(request, 'home.html', {
        'google_maps_api_key': settings.GOOGLE_MAPS_API_KEY,
        'location': {'lat': 40.7607, 'lng': -111.8939},
        'all_stores': stores,
    })

def aboutus(request):
    return render(request, "aboutus.html")

def login_view(request):
    if request.method == 'POST':
        u = request.POST.get('username','').strip()
        p = request.POST.get('password','')
        user = authenticate(request, username=u, password=p)
        if user:
            auth_login(request, user)
            return redirect(request.GET.get('next','home'))
        messages.error(request, "Invalid credentials")
    return render(request, 'login.html')

def signup_view(request):
    if request.method == 'POST':
        u = request.POST.get('username', '').strip()
        e = request.POST.get('email', '').strip()
        p = request.POST.get('password', '')

        if User.objects.filter(username=u).exists():
            messages.error(request, "Username taken")
            return redirect('home')

        user = User.objects.create_user(username=u, email=e, password=p)
        auth_login(request, user)
        return redirect('home')

    return render(request, 'signup.html')

def logout_view(request):
    auth_logout(request)
    return redirect('login')

def validatePasswordChange(request, currentPassword, newPassword1, newPassword2):

    if not request.user.check_password(currentPassword):
        return False, 'Your current password was entered incorrectly.'
    
    if newPassword1 != newPassword2:
        return False, 'The two password fields didn\'t match.'
    
    if len(newPassword1) < 5:
        return False, 'This password is too short. It must contain at least 5 characters.'
    
    return True, None

def handlePasswordChange(request, newPassword):
    request.user.set_password(newPassword)
    request.user.save()

@login_required
def userprofile(request):
    profile = get_custom_user(request)
    orders = profile.orders_placed.select_related('store')\
                .prefetch_related('order_items__item') if profile else []
    
    errors = []
    if request.method == 'POST' and 'change_password' in request.POST:
        currentPassword = request.POST.get('current_password', '')
        newPassword1 = request.POST.get('new_password1', '')
        newPassword2 = request.POST.get('new_password2', '')
        
        isValid, errorMessage = validatePasswordChange(
            request, currentPassword, newPassword1, newPassword2
        )
        
        if isValid:
            handlePasswordChange(request, newPassword1)
            errors.append({
                'message': 'Your password was successfully updated!',
                'is_success': True
            })
        else:
            errors.append({
                'message': errorMessage,
                'is_success': False
            })
    
    return render(request, "profile.html", {
        'custom_user': profile,
        'user_orders': orders,
        'errors': errors
    })

@login_required
def menu(request):
    return render(request, "menu.html", {
        'custom_user': get_custom_user(request),
    })

@login_required
def groups(request):
    # delivery_people = ProfileUser.objects.filter(user_type__in=['delivery','both'])
    return render(request, "groups.html")

@login_required
def browse_items(request):
    items = Items.objects.select_related('store').all()
    q = request.GET.get('search','').strip()
    if q:
        items = items.filter(name__icontains=q)
    try:
        lo = request.GET.get('min_price'); hi = request.GET.get('max_price')
        if lo: items = items.filter(price__gte=Decimal(lo))
        if hi: items = items.filter(price__lte=Decimal(hi))
    except (InvalidOperation, ValueError):
        messages.error(request, "Bad price filter")
    store_id = request.GET.get('store')
    if store_id and store_id.isdigit():
        items = items.filter(store_id=int(store_id))
    stores = Stores.objects.all()
    return render(request, "cart.html", {
        'items': items.order_by('store_name','name'),
        'all_stores': stores,
        'custom_user': get_custom_user(request),
    })

@login_required
def add_to_cart(request, item_id):
    # profile = get_custom_user(request)
    # item = get_object_or_404(Items, pk=item_id)
    # order, _ = Orders.objects.get_or_create(
    #     user=profile,
    #     status='cart',
    #     defaults={
    #         'order_date': timezone.now(),
    #         'store': item.store
    #     }
    # )
    # oi, created = OrderItems.objects.get_or_create(
    #     order=order,
    #     item=item,
    #     defaults={'quantity':1, 'price':item.price}
    # )
    # if not created:
    #     oi.quantity += 1
    #     oi.save()
    # messages.success(request, f"Added {item.name} to cart")
     return redirect('cart')

@login_required
def cart(request):
    store_filter = request.GET.get('Stores', 'All')
    price_filter = request.GET.get('Price-Range', 'Any')
    search_query = request.GET.get('Item_Search_Bar', '')

    items = Items.objects.using('gsharedb').all()

    if store_filter and store_filter != 'All':
        items = items.filter(store__name=store_filter)

    if price_filter and price_filter != 'Any':
        if price_filter == '100+':
            items = items.filter(price__gte=100)
        else:
            low, high = map(float, price_filter.split('-'))
            items = items.filter(price__gte=low, price__lte=high)

    if search_query:
        items = items.filter(name__icontains=search_query)
        
    paginator = Paginator(items, 10)  # Show 10 items per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'store_filter': store_filter,
        'price_filter': price_filter,
        'search_query': search_query,
    }
    
    return render(request, "cart.html", context)

    # profile = get_custom_user(request)
    # try:
    #     order = Order.objects.get(user=profile, status='cart')
    #     items = order.order_items.select_related('item__store')
    # except Order.DoesNotExist:
    #     order = None
    #     items = []
    # return render(request, "cart.html", {
    #     'active_cart': order,
    #     'cart_items': items,
    #     'custom_user': profile,
    # })

@login_required
def checkout(request):
    profile = get_custom_user(request)
    order = get_object_or_404(Orders, user=profile, status='cart')
    if request.method == 'POST':
        addr = request.POST.get('delivery_address','').strip()
        order.delivery_address = addr
        order.status           = 'pending'
        total = sum(oi.quantity * oi.price for oi in order.order_items.all())
        order.total_amount = total
        order.save()
        Deliveries.objects.create(order=order, status='pending')
        messages.success(request, "Order placed successfully!")
        return redirect('userprofile')
    return render(request, "checkout.html", {
        'order': order,
        'custom_user': profile,
    })


@login_required
def maps(request):
    stores = Stores.objects.all()
    #delivery_people = ProfileUser.objects.filter(user_type__in=['delivery','both'])
    return render(request, "maps.html", {
        'google_maps_api_key': settings.GOOGLE_MAPS_API_KEY,
        'location': {'lat': 40.7607, 'lng': -111.8939},
        'stores_for_map': stores,
        # 'delivery_persons': delivery_people,
        'custom_user': get_custom_user(request),
    })
    
@login_required
def shoppingcart(request):
    return render(request, "shoppingcart.html")