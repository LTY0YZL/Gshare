from django.db import transaction
from decimal import Decimal, InvalidOperation
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.core.paginator import Paginator
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password, check_password
from django.contrib import messages
from django.utils import timezone
from django.db import connections
from django.db import transaction
from django.conf import settings
from django.db import IntegrityError 
from django.db.models import Q
from django.db.models import Avg, Count
from django.http import JsonResponse
from django.db import connection
import json
import re
import requests
from core.utils.geo import geoLoc
from urllib.parse import urlencode
from . import kroger_api

from core.models import (
    Users,
    Stores, Items,
    Orders, OrderItems,
    Deliveries, Feedback, GroupOrders, GroupMembers
)

"""helper functions"""

"""
Retrieve all feedback for a specific user and calculate their average rating.

Args:
    user_id (int): The ID of the user whose feedback and average rating are being retrieved.

Returns:
    tuple:
        - QuerySet or None: A QuerySet of feedback objects if feedback exists, otherwise None.
        - float: The average rating of the user if feedback exists, otherwise 0.0.
"""
def get_user_ratings(user_id: int):
    feedbacks = Feedback.objects.using('gsharedb').filter(reviewee_id=user_id)
    if not feedbacks.exists():
        return None, 0.0
    avg_rating = feedbacks.aggregate(Avg('rating'))['rating__avg']
    return feedbacks, avg_rating


"""
Retrieve a user from the 'gsharedb' database based on a specific field and value.

Args:
    field (str): The name of the field to filter by (e.g., 'username', 'email').
    value (str): The value of the field to match.

Returns:
    Users: The user object if found, otherwise None.
"""
def get_user(field: str, value):
    
    try:
       # dynamic lookup: **{field: value}
        return Users.objects.using('gsharedb').get(**{field: value})
    except Users.DoesNotExist:
        return None

# edits phone, email, address with value based on the field selected.
def edit_user(user_email: str, field: str, value: str):
    try: 
        user = Users.objects.using("gsharedb").get(email=user_email)
        setattr(user, field, value)
        user.save(using='gsharedb')
        return user
    except Users.DoesNotExist:
        return None
    except Exception as e:
        print(f"Error updating user: {e}")
        return None

"""
Edit a specific field of a user in the 'gsharedb' database.

Args:
    user_id (int): The ID of the user to be updated.
    field (str): The name of the field to be updated (e.g., 'email', 'phone').
    value (str): The new value to set for the specified field.

Returns:
    Users: The updated user object if the user exists, otherwise None.
"""
def create_user_signin(name: str, email: str, address: str = "Not provided", phone: str | None = None, request=None):
    lat, lng = 0, 0

    if address != "Not provided":
        lat, lng = geoLoc(address)

    print(f"Geocoded {address} to lat={lat}, lng={lng}")

    if lat != 0 and lng != 0:
        try:
            with transaction.atomic(using='gsharedb'):
                return Users.objects.using('gsharedb').create(
                    name=name,
                    email=email,     # email is unique but nullable
                    phone=phone,     # optional
                    address=address,  # REQUIRED by your schema
                    latitude= lat,
                    longitude= lng
                )
        except IntegrityError as e:
            # e.g., duplicate email or other constraint violations
            raise

"""
Edit the quantity of a specific item in an order.

Args:
    order_id (int): The ID of the order containing the item to be updated.
    item_id (int): The ID of the item whose quantity needs to be updated.
    new_quantity (int): The new quantity to set for the specified item.

Returns:
    bool: True if the item quantity was successfully updated, False otherwise.
"""
def Edit_order_items(order_id: int, item_id: int, new_quantity: int) -> bool:
    try:
        order_item = OrderItems.objects.using('gsharedb').get(order_id=order_id, item_id=item_id)
        order_item.quantity = new_quantity
        order_item.save(using='gsharedb')
        return True
    except OrderItems.DoesNotExist:
        return False
    except Exception as e:
        print(f"Error updating order item: {e}")
        return False

"""
Retrieve all orders for a specific user from the 'gsharedb' database.

Args:
    user (Users): The user object for whom the orders are being retrieved.
    Status (str): The status of the orders to filter by ('cart', 'placed', 'inprogress', 'delivered').

Returns:
    QuerySet or list: A QuerySet of orders if orders exist, otherwise an empty list.
"""
def get_orders(user: Users, order_status: str):

    orders = Orders.objects.using('gsharedb').filter(user_id = user, status = order_status) # Getting all the orders related to this user and status.
    if not orders.exists():  # Checking if the queryset is empty.
        return []
    return orders

"""
Retrieve all orders from the 'gsharedb' database based on their status.

Args:
    order_status (str): The status of the orders to filter by 
                        (e.g., 'cart', 'placed', 'inprogress', 'delivered').

Returns:
    QuerySet or list: A QuerySet of orders if orders with the specified status exist, 
                      otherwise an empty list.
"""
def get_orders_by_status(order_status: str):
    orders = Orders.objects.using('gsharedb').filter(status=order_status)
    if not orders.exists():
        return []
    return orders

"""
Retrieve all items in a specific order from the 'gsharedb' database.

Args:
    order (Orders): The order object for which the items are being retrieved.

Returns:
    QuerySet or list: A QuerySet of order items if they exist, otherwise an empty list.
"""
def get_order_items(order: Orders):

     # Upsert into order_items (composite PK table) and recompute total
    with transaction.atomic(using='gsharedb'):
        with connections['gsharedb'].cursor() as cur:
            # Fetch items with their details
            cur.execute(
                """
                SELECT oi.*, i.name, i.price, i.store_id
                FROM order_items oi
                JOIN items i ON oi.item_id = i.id
                WHERE oi.order_id = %s
                """,
                [order.id]
            )

        items = cur.fetchall()
   # items = OrderItems.objects.using('gsharedb').filter(order=order).select_related('item')
   # if not items.exists():
   #     return []
    return items

"""
Change the status of an order in the 'gsharedb' database.
Args:
    order_id (int): The ID of the order to be updated.
    new_status (str): The new status to set for the order ('cart', 'placed', 'inprogress','delivered').
    Returns:
    bool: True if the order status was successfully updated, False otherwise.
"""
def change_order_status(order_id: int, new_status: str) -> bool:
    try:
        order = Orders.objects.using('gsharedb').get(id=order_id)
        order.status = new_status
        order.save(using='gsharedb')
        return True
    except Orders.DoesNotExist:
        return False
    except Exception as e:
        print(f"Error updating order status: {e}")
        return False
    
def change_order_status_json(request, order_id, new_status):
    if request.method == 'POST':
        success = change_order_status(order_id, new_status)
        return JsonResponse({'success': success})
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)
    
"""
Retrieve all deliveries for a specific user based on delivery status.
Args:
    user (Users): The user object for whom the deliveries are being retrieved.
    delivery_status (str): The status of the deliveries to filter by ('accepted', 'inprogress', 'delivered').
Returns:
    QuerySet or list: A QuerySet of deliveries if deliveries exist, otherwise an empty list.
"""
def get_my_deliveries(user: Users, delivery_status: str):
    deliveries = Deliveries.objects.using('gsharedb').filter(order__user=user, status=delivery_status)
    if not deliveries.exists():
        return []
    return deliveries

""" functions from here are for group orders """

"""
Create a new group order with a list of order IDs and a password.
Args:
    order_ids (list[int]): A list of order IDs to be included in the group order.
    raw_password (str): The raw password to be hashed and stored for the group order.
    Returns:
    GroupOrders: The created GroupOrders object.
"""
def create_group_order(user: Users, order_ids: list[int], raw_password: str):

    if not order_ids:
        raise ValueError("order_ids list cannot be empty")

    with transaction.atomic(using='gsharedb'):
        group = GroupOrders.objects.using('gsharedb').create(description="Group Order", password_hash="")
        set_group_password(group, raw_password)
        for oid in order_ids:
            try:
                order = Orders.objects.using('gsharedb').get(id=oid, user=user)
                GroupMembers.objects.using('gsharedb').create(group=group, user=user, order=order)
            except Orders.DoesNotExist:
                continue
        return group
    
def add_user_to_group(group: GroupOrders, user: Users, order: Orders = None):
    try:
        GroupMembers.objects.using('gsharedb').create(group=group, user=user, order=order)
        return True
    except IntegrityError:
        return False

def remove_user_from_group(group: GroupOrders, user: Users):
    try:
        membership = GroupMembers.objects.using('gsharedb').get(group=group, user=user)
        membership.delete()
        return True
    except GroupMembers.DoesNotExist:
        return False
    
def get_group_by_id(group_id: int):
    try:
        return GroupOrders.objects.using('gsharedb').get(group_id=group_id)
    except GroupOrders.DoesNotExist:
        return None

def get_group_members(group: GroupOrders):
    return GroupMembers.objects.using('gsharedb').filter(group=group).select_related('user', 'order')

def get_groups_for_user(user: Users):
    return GroupOrders.objects.using('gsharedb').filter(members=user).distinct()

def get_cart_in_group(user: Users):
    try:
        membership = GroupMembers.objects.using('gsharedb').get(user=user, order__status='cart')
        print(membership)
        return membership
    except GroupMembers.DoesNotExist:
        return None

def get_orders_in_group(group_id: int):
    order_ids = (
        GroupMembers.objects.using('gsharedb')
        .filter(group_id=group_id, order_id__isnull=False)
        .values_list('order_id', flat=True)
        .distinct()
    )
    return list(
        Orders.objects.using('gsharedb')
        .filter(id__in=order_ids)
    )

def remove_group(group: GroupOrders):
    try:
        group.delete()
        return True
    except Exception as e:
        print(f"Error deleting group: {e}")
        return False
    
def get_group_by_user_and_order(user: Users, order: Orders):
    try:
        membership = GroupMembers.objects.using('gsharedb').get(user=user, order=order)
        return membership.group
    except GroupMembers.DoesNotExist:
        return None
    
def add_order_to_group(group: GroupOrders, user: Users, order: Orders):
    try:
        GroupMembers.objects.using('gsharedb').create(group=group, user=user, order=order)
        return True
    except IntegrityError:
        return False
    
def remove_order_from_group(group: GroupOrders, order: Orders):
    try:
        membership = GroupMembers.objects.using('gsharedb').get(group=group, order=order)
        membership.delete()
        return True
    except GroupMembers.DoesNotExist:
        return False

def set_group_password(group, raw_password: str):
    # explicitly tell Django to use Argon2 for this hash
    group.password_hash = make_password(raw_password, hasher='argon2')
    group.save(using='gsharedb')

def verify_group_password(group, raw_password: str) -> bool:
    # check_password auto-detects the hasher from the stored hash
    return check_password(raw_password, group.password_hash)


""""Functions for spatial queries"""

def _users_in_viewport_spatial(min_lat, min_lng, max_lat, max_lng, limit=500, exclude_id=None):
    if min_lng <= max_lng:
        rect_wkt = f"POLYGON(({min_lng} {min_lat},{min_lng} {max_lat},{max_lng} {max_lat},{max_lng} {min_lat},{min_lng} {min_lat}))"
        sql = """
          SELECT id, name, address, ST_X(location) AS lng, ST_Y(location) AS lat
          FROM users
          WHERE MBRIntersects(location, ST_SRID(ST_PolygonFromText(%s), 4326))
          {exclude}
          LIMIT %s
        """
        exclude_clause = "AND id <> %s" if exclude_id is not None else ""
        params = [rect_wkt] + ([exclude_id] if exclude_id is not None else []) + [limit]
        with connection.cursor() as cur:
            cur.execute(sql.format(exclude=exclude_clause), params)
            rows = cur.fetchall()
        return [{"id": r[0], "name": r[1], "address": r[2],
                 "longitude": float(r[3]), "latitude": float(r[4])} for r in rows]
    else:
        # Antimeridian split
        left = _users_in_viewport_spatial(min_lat, min_lng, max_lat, 180.0, limit, exclude_id)
        right = _users_in_viewport_spatial(min_lat, -180.0, max_lat, max_lng, limit, exclude_id)
        seen, out = set(), []
        for row in left + right:
            if row["id"] in seen: 
                continue
            seen.add(row["id"]); out.append(row)
            if len(out) >= limit: 
                break
        return out

"""Main functions"""

def home(request):
    stores = Stores.objects.all().order_by('name')
    context = {
        'google_maps_api_key': settings.GOOGLE_MAPS_API_KEY,
        'location': {'lat': 40.7607, 'lng': -111.8939},
        'all_stores': stores,
    }
    
    if request.user.is_authenticated:
        profile = get_user("email", request.user.email)
        if profile:
            context['user'] = profile
    
    return render(request, 'home.html', context)

def aboutus(request):
    return render(request, "aboutus.html")

def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')
        
    if request.method == 'POST':
        u = request.POST.get('username','').strip()
        p = request.POST.get('password','')
        user = authenticate(request, username=u, password=p)
        if user is not None:
            login(request, user)
            request.session.save()
            return redirect(request.GET.get('next', 'home'))
        messages.error(request, "Invalid username or password")
    return render(request, 'login.html')

def signup_view(request):
    if request.method == 'POST':
        u = request.POST.get('username', '').strip()
        f = request.POST.get('first_name', '').strip()
        l = request.POST.get('last_name', '').strip()
        e = request.POST.get('email', '').strip()
        p = request.POST.get('password', '')
        addr = (request.POST.get('address', '') or '').strip() or "Not provided"
        phone = (request.POST.get('phone', '') or '').strip() or None


        if User.objects.filter(username=u).exists():
            messages.error(request, "Username taken")
            return redirect('login')
        

        # Create auth user in default DB
        auth_user = User.objects.create_user(username=u, email=e, password=p)
        # save first/last to auth user (optional but you collected them)
        if f: auth_user.first_name = f
        if l: auth_user.last_name = l
        if f or l: auth_user.save()


        # Create business profile in gsharedb
        full_name = " ".join(part for part in [f, l] if part) or u  # fallback to username
        try:
            create_user_signin(full_name, e, address=addr, phone=phone,request=request)
        except IntegrityError as ex:
            # roll back auth user if business insert fails
            auth_user.delete()
            messages.error(request, f"Could not create profile: {ex}")
            return redirect('home')


        # Login and redirect
        auth_login(request, auth_user)
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

def updateProfile(profile, data, files):
    if 'name' in data:
        profile.name = data['name']
    
    if 'email' in data:
        profile.email = data['email']
    
    if 'phone' in data:
        profile.phone = data['phone']
    
    if 'address' in data:
        profile.address = data['address']
    
    if 'about_me' in data:
        profile.about_me = data['about_me']
    
    if 'profile_picture' in files:
        profile_picture = files.get('profile_picture')
        if profile_picture:
            profile.profile_picture = profile_picture
    
    # Save the profile
    profile.save(using='gsharedb')
    return True

@login_required
def userprofile(request):

    user_email = request.user.email
    if not user_email:
        messages.error(request, 'No email associated with your account')
        return redirect('home')
        
    profile = get_user("email", user_email)
    if not profile:
        messages.error(request, 'User profile not found')
        return redirect('login')
    
    errors = []
    
    if request.method == 'POST':

        if 'save_description' in request.POST:
            if 'description' in request.POST:
                    profile.description = request.POST.get('description', '').strip()
                    print(profile.description)

            profile.save(using='gsharedb')
            messages.success(request, 'About Me updated!')
            return redirect('profile')

        if 'save_profile' in request.POST:
            try:
                if 'name' in request.POST:
                    profile.name = request.POST['name']
                
                if 'email' in request.POST and request.POST['email'] != profile.email:
                    if Users.objects.using('gsharedb').filter(email=request.POST['email']).exists():
                        errors.append({
                            'message': 'This email is already in use',
                            'is_success': False
                        })
                    else:
                        profile.email = request.POST['email']
                
                if 'phone' in request.POST:
                    profile.phone = request.POST['phone']
                
                if 'address' in request.POST:
                    profile.address = request.POST['address']

                if 'profile_picture' in request.FILES:
                    profile_picture = request.FILES.get('profile_picture')
                    if profile_picture:
                        profile.profile_picture = profile_picture
                
                # Save the profile
                profile.save(using='gsharedb')
                
                # Update the auth user's email if it was changed
                if 'email' in request.POST and request.user.email != request.POST['email']:
                    request.user.email = request.POST['email']
                    request.user.save()
                
                messages.success(request, 'Profile updated successfully!')
                return redirect('profile')
                
            except Exception as e:
                errors.append({
                    'message': f'Error updating profile: {str(e)}',
                    'is_success': False
                })
        
        elif 'change_password' in request.POST:
            currentPassword = request.POST.get('current_password', '')
            newPassword1 = request.POST.get('new_password1', '')
            newPassword2 = request.POST.get('new_password2', '')
            
            isValid, errorMessage = validatePasswordChange(
                request, currentPassword, newPassword1, newPassword2
            )
            
            if isValid:
                handlePasswordChange(request, newPassword1)
                messages.success(request, 'Your password was successfully updated!')
            else:
                messages.error(request, errorMessage)
            
            return redirect('profile')
    
    Feedback, avg_rating = get_user_ratings(profile.id)
    review_count = Feedback.count() if Feedback else 0

    avg = float(avg_rating or 0)
    stars_full = max(0, min(5, int(round(avg))))  
    stars_text = '★' * stars_full + '☆' * (5 - stars_full)
        
    return render(request, "profile.html", {
        'user': profile,
        'errors': errors,
        'request': request,
        'auth_user': request.user,
        'avg_rating': avg,
        'review_count': review_count,
        'stars_text': stars_text,
    })

@login_required
def browse_items(request):
    # items = Items.objects.select_related('store').all()
    # q = request.GET.get('search','').strip()
    # if q:
    #     items = items.filter(name__icontains=q)
    # try:
    #     lo = request.GET.get('min_price'); hi = request.GET.get('max_price')
    #     if lo: items = items.filter(price__gte=Decimal(lo))
    #     if hi: items = items.filter(price__lte=Decimal(hi))
    # except (InvalidOperation, ValueError):
    #     messages.error(request, "Bad price filter")
    # store_id = request.GET.get('store')
    # if store_id and store_id.isdigit():
    #     items = items.filter(store_id=int(store_id))
    # stores = Stores.objects.all()
    return # render(request, "cart.html", {
    #     'items': items.order_by('store_name','name'),
    #     'all_stores': stores,
    #     'custom_user': get_custom_user(request),
    # })


@login_required
def menu(request):
    return render(request, "menu.html", {
        'user': get_user("name", "anand"),
    })


"""
Add an item to the user's cart. If the item already exists in the cart, increase its quantity.

Args:
    request: The HTTP request object.
    item_id (int): The ID of the item to add to the cart.

Returns:
    Redirects to the cart page.
"""
@login_required
def add_to_cart(request, item_id, quantity=1):
    
    print("Adding item to cart:", item_id)
    print("Quantity:", quantity)

    profile = get_user("email", request.user.email)
    if not profile:
        messages.error(request, "Profile not found.")
        return redirect('cart')


    # Grab item
    try:
        item = Items.objects.using('gsharedb').get(id=item_id)
    except Items.DoesNotExist:
        messages.error(request, "Item not found.")
        return redirect('cart') 

    # Get or create cart
    order = Orders.objects.using('gsharedb').filter(user=profile, status='cart').first()
    if not order:
        order = Orders.objects.using('gsharedb').create(
            user=profile,
            status='cart',
            order_date=timezone.now(),
            store=item.store,
            total_amount=0,
            delivery_address = profile.address
        )

    # Upsert into order_items (composite PK table) and recompute total
    with transaction.atomic(using='gsharedb'):
        with connections['gsharedb'].cursor() as cur:
            # Insert or bump quantity
            cur.execute(
                """
                INSERT INTO order_items (order_id, item_id, quantity, price)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    quantity = quantity + VALUES(quantity)
                """,
                [order.id, item.id, quantity, str(item.price or 0)]
            )

            # RECALC TOTAL from line items
            cur.execute(
                """
                SELECT COALESCE(SUM(quantity * price), 0)
                FROM order_items
                WHERE order_id = %s
                """,
                [order.id]
            )
            total = cur.fetchone()[0] or 0

    # Update order fields using ORM
    order.total_amount = total
    order.order_date = timezone.now()
    order.save(using='gsharedb')
    
    # Always return JSON for AJAX
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == "application/json":
        return JsonResponse({"success": True, "message": f"Added {item.name} to your cart."})


    messages.success(request, f"Added {item.name} to your cart.")
    return redirect('cart')

@login_required
def remove_from_cart(request, item_id):
    profile = get_user("email", request.user.email)
    if not profile:
        messages.error(request, "Profile not found.")
        return redirect('cart')

    order = Orders.objects.using('gsharedb').filter(user=profile, status='cart').first()
    if not order:
        messages.error(request, "No active cart.")
        return redirect('cart')

    try:
        order_item = OrderItems.objects.using('gsharedb').get(order=order, item_id=item_id)
    except OrderItems.DoesNotExist:
        messages.error(request, "Item not in cart.")
        return redirect('cart')

    with transaction.atomic(using='gsharedb'):
        order_item.delete()

        # RECALC TOTAL from line items
        with connections['gsharedb'].cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(quantity * price), 0)
                FROM order_items
                WHERE order_id = %s
                """,
                [order.id]
            )
            total = cur.fetchone()[0] or 0

        order.total_amount = total
        order.save(using='gsharedb')

    messages.success(request, f"Removed {order_item.item.name} from your cart.")
    return redirect('cart')

@login_required
def cart(request):
    store_filter = request.GET.get('Stores', 'All')
    price_filter = request.GET.get('Price-Range', 'Any')
    search_query = request.GET.get('Item_Search_Bar', '')
    
    sf_override = request.GET.get('store_filter')
    if sf_override:
        store_filter = sf_override

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
        
    if store_filter == 'Kroger':
        context = {
            'store_filter': store_filter,
            'price_filter': price_filter,
            'search_query': search_query,
        }
        context['saved_kroger_items'] = Items.objects.using('gsharedb') \
            .filter(store__name='Kroger').order_by('name')
        zip_code = (request.GET.get('zip_code') or '').strip()
        term = (request.GET.get('search_term') or '').strip()
        context['zip_code'] = zip_code
        context['search_term'] = term

        if zip_code and term:
            try:
                locations = kroger_api.find_kroger_locations_by_zip(zip_code)
                if locations:
                    loc_id = locations[0]['locationId']
                    context['kroger_products'] = kroger_api.search_kroger_products(loc_id, term)
                else:
                    messages.error(request, f"No stores found for {zip_code}.")
            except Exception:
                messages.error(request, "Kroger search failed.")
        return render(request, "cart.html", context)
        
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
def add_kroger_item_to_cart(request):
    if request.method != 'POST':
        messages.error(request, "Invalid request.")
        return redirect('cart')
    product_name = (request.POST.get('product_name') or '').strip()
    product_price = request.POST.get('product_price')
    if not product_name and request.content_type == 'application/json':
        try:
            body = json.loads(request.body or '{}')
            product_name = (body.get('product_name') or '').strip()
            product_price = body.get('product_price')
        except Exception:
            product_name, product_price = '', None

    if not product_name or product_price is None:
        messages.error(request, "Missing Kroger product data.")
        return redirect('cart')

    try:
        price_dec = Decimal(str(product_price))
    except Exception:
        messages.error(request, "Invalid Kroger product price.")
        return redirect('cart')
    kroger_store, _ = Stores.objects.using('gsharedb').get_or_create(name='Kroger')
    item, _ = Items.objects.using('gsharedb').update_or_create(
        name=product_name,
        store=kroger_store,
        defaults={'price': price_dec, 'stock': 0}
    )

    return add_to_cart(request, item.id)

@login_required
def save_kroger_results(request):
    if request.method != 'POST':
        messages.error(request, "Invalid request.")
        return redirect('cart')
    raw = request.POST.get('products_json') or '[]'
    try:
        products = json.loads(raw)
    except Exception:
        products = []

    kroger_store, _ = Stores.objects.using('gsharedb').get_or_create(name='Kroger')
    created = 0
    updated = 0
    for p in products:
        try:
            name = (p.get('description') or '').strip()
            regular = (
                p.get('items', [{}])[0]
                 .get('price', {})
                 .get('regular')
            )
            if not name or regular is None:
                continue
            price_dec = Decimal(str(regular))
        except Exception:
            continue

        _, was_created = Items.objects.using('gsharedb').update_or_create(
            name=name,
            store=kroger_store,
            defaults={'price': price_dec, 'stock': 0}
        )
        if was_created:
            created += 1
        else:
            updated += 1
    messages.success(request, f"Saved {created} new and {updated} existing Kroger item(s).")
    return redirect('cart')

@login_required
def clear_kroger_items(request):
    if request.method != 'POST':
        messages.error(request, "Invalid request.")
        return redirect('cart')
    qs = Items.objects.using('gsharedb').filter(store__name='Kroger')
    count = qs.count()
    qs.delete()
    messages.success(request, f"Cleared {count} saved Kroger item(s).")
    return redirect(request.META.get('HTTP_REFERER', 'cart'))

@login_required
def checkout(request):
    profile = get_user("email", request.user.email)
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
    user = get_user("email", request.user.email)
    user_address = user.address
    #delivery_people = ProfileUser.objects.filter(user_type__in=['delivery','both'])
    orders = get_orders_by_status('placed')
    # print(orders.user)
    # print(orders)  # Debug print in your view
    # for order in orders:
    #     print("Order ID:", order.id)  # Debug print in your view
    #     print("User ID:", order.user.id)  # Debug print in your view
    #     print("Delivery Address:", order.delivery_address)  # Debug print in your view
    # addresses = [order.delivery_address for order in orders if order.delivery_address]
    info = {}
    for order in orders:
        if order.delivery_address:
            user = get_user("id", order.user.id)
                 
                
            
            # print(user.name)
            items = get_order_items(order)
                
            subtotal = 0
            items_with_totals = []
            for item in items:
                total = float(item[2]) * float(item[5])  # quantity * price
                subtotal += total
                items_with_totals.append({
                    'name': item[4],  # item name
                    'quantity': int(item[2]),
                    'price': float(item[5]),  # item price
                    'total': total,
                })
        order_data = {
            'address': order.delivery_address,
            'items': items_with_totals,
            'subtotal': subtotal,
            'order_id': order.id,
        }

        # Group by user name (or user.id if you prefer)
        if user.name not in info:
            info[user.name] = []
        info[user.name].append(order_data)

    # Convert to grouped list format for easy JSON use
    grouped_info = [
        {'user': user_name, 'orders': orders}
        for user_name, orders in info.items()
    ]

    # print("Addresses JSON:", json.dumps(grouped_info, indent=2))
    # print("Addresses:", addresses)  # Debug print in your view
    # print("Addresses JSON:", json.dumps(info))  # Debug print in your view
    return render(request, "maps.html", {
        'google_maps_api_key': settings.GOOGLE_MAPS_API_KEY,
        'location': {'lat': 40.7607, 'lng': -111.8939},
        'stores_for_map': stores,
        # 'delivery_persons': delivery_people,
        # 'custom_user': get_user("email", request.user.email),
        'delivery_addresses_with_info_json': json.dumps(grouped_info),
        'user_address': user_address,
    })
    
def placed_data(request):
    print("placed data")
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    profile = get_user("email", request.user.email)
    if not profile:
        print("no user")
        return JsonResponse({'error': 'Profile not found'}, status=404)

    order = get_orders(profile, 'placed')
    print(order)
    if not order:
        return JsonResponse({'items': [], 'order': {'subtotal': 0, 'tax': 0, 'total': 0}, 'id': None})

    items = get_order_items(order[0]) if order[0] else []
    subtotal = 0
    items_with_totals = []
    for item in items:
        total = item[2] * item[5]  # quantity * price
        subtotal += total
        items_with_totals.append({
            'name': item[4],  # item name
            'quantity': item[2],
            'price': item[5],  # item price
            'total': total,
        })
        
    tax = round(subtotal * Decimal(0.07), 2)  # Example: 7% tax
    grand_total = round(subtotal + tax, 2)

    order_summary = {
        'subtotal': subtotal,
        'tax': tax,
        'total': grand_total,
    }
    print(order_summary)

    return JsonResponse({
        'items': items_with_totals,
        'order': order_summary,
        'id': order[0].id if order else None,
    })
    
    
def group_data(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    profile = get_user("email", request.user.email)
    if not profile:
        print("no user")
        return JsonResponse({'error': 'Profile not found'}, status=404)

    order = get_orders_in_group(profile, 'cart')
    if not order:
        return JsonResponse({'items': [], 'order': {'subtotal': 0, 'tax': 0, 'total': 0}, 'id': None})

    items = get_order_items(order[0]) if order[0] else []
    subtotal = 0
    items_with_totals = []
    for item in items:
        total = item[2] * item[5]  # quantity * price
        subtotal += total
        items_with_totals.append({
            'name': item[4],  # item name
            'quantity': item[2],
            'price': item[5],  # item price
            'total': total,
        })
        
    tax = round(subtotal * Decimal(0.07), 2)  # Example: 7% tax
    grand_total = round(subtotal + tax, 2)

    order_summary = {
        'subtotal': subtotal,
        'tax': tax,
        'total': grand_total,
    }

    return JsonResponse({
        'items': items_with_totals,
        'order': order_summary,
        'id': order[0].id if order else None,
    })
    
    
def cart_data(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    profile = get_user("email", request.user.email)
    if not profile:
        print("no user")
        return JsonResponse({'error': 'Profile not found'}, status=404)

    order = get_orders(profile, 'cart')
    if not order:
        return JsonResponse({'items': [], 'order': {'subtotal': 0, 'tax': 0, 'total': 0}, 'id': None})

    items = get_order_items(order[0]) if order[0] else []
    subtotal = 0
    items_with_totals = []
    for item in items:
        total = item[2] * item[5]  # quantity * price
        subtotal += total
        items_with_totals.append({
            'name': item[4],  # item name
            'quantity': item[2],
            'price': item[5],  # item price
            'total': total,
        })
        
    tax = round(subtotal * Decimal(0.07), 2)  # Example: 7% tax
    grand_total = round(subtotal + tax, 2)

    order_summary = {
        'subtotal': subtotal,
        'tax': tax,
        'total': grand_total,
    }

    return JsonResponse({
        'items': items_with_totals,
        'order': order_summary,
        'id': order[0].id if order else None,
    })
    
def group_carts(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    
    profile = get_user("email", request.user.email)
    if not profile:
        return JsonResponse({'error': 'Profile not found'}, status=404)

    orders = get_orders(profile, 'cart', 'group')
    if not orders:
        return JsonResponse({'carts': []})

    carts = []
    for order in orders:
        items = get_order_items(order) if order else []
        subtotal = 0
        items_with_totals = []
        for item in items:
            total = item[2] * item[5]  # quantity * price
            subtotal += total
            items_with_totals.append({
                'name': item[4],  # item name
                'quantity': item[2],
                'price': item[5],  # item price
                'total': total,
            })
            
        tax = round(subtotal * Decimal(0.07), 2)  # Example: 7% tax
        grand_total = round(subtotal + tax, 2)

        order_summary = {
            'subtotal': subtotal,
            'tax': tax,
            'total': grand_total,
        }

        carts.append({
            'order_id': order.id,
            'items': items_with_totals,
            'order_summary': order_summary,
        })

    return JsonResponse({'carts': carts})

@login_required
def shoppingcart(request):
    # return render(request, "shoppingcart.html")

    user = request.user
    profile = get_user("email", user.email)
    print("User profile:", profile.name)
    print(profile.id)
    order = get_orders(profile, 'cart')
    if not order:
        order = []
        print("No active cart found.")
        return render(request, "shoppingcart.html", {
            'items': [],
            'order': {
                'subtotal': 0,
                'tax': 0,
                'total': 0,
            },
            'id': None,
        })
    # print(len(order))
    # print(order[0].total_amount)
    # print(order[0].id if order else "No order")

    items = get_order_items(order[0]) if order[0] else []
    subtotal = 0
    items_with_totals = []
    for item in items:
        total = item[2] * item[5]  # quantity * price
        subtotal += total
        items_with_totals.append({
            'name': item[4],  # item name
            'quantity': item[2],
            'price': item[5],  # item price
            'total': total,
        })
        
    tax = round(subtotal * Decimal(0.07), 2)  # Example: 7% tax
    grand_total = round(subtotal + tax, 2)

    order_summary = {
        'subtotal': subtotal,
        'tax': tax,
        'total': grand_total,
    }
    # print(order[0].id if order else "No order")
    # for item in items:
    #     totals.append(item[2] * item[5])  # quantity * price
    # print(items)

    # print(items)
    return render(request, "shoppingcart.html", {
        'items': items_with_totals,
        'order': order_summary,
        'id': order[0].id if order else None,
        # 'order': order[0],
        # 'items': items,
        # 'items': items,
        # 'totals': totals,
    })

@login_required
def myorders(request):
    user = get_user("email", request.user.email)
    all_orders = []
    orders_cart = get_orders(user, "cart")
    orders_placed = get_orders(user, "placed")
    orders_inprogress = get_orders(user, "inprogress")
    orders_completed = get_orders(user, "completed")
    
    all_orders.extend(orders_cart)
    all_orders.extend(orders_placed)
    all_orders.extend(orders_inprogress)
    all_orders.extend(orders_completed)

    
    # each tuple is (order, items)
    orders_with_items = []
    for order in all_orders:
        items = get_order_items(order)
        items_with_totals = []
        for item in items:
            total = item[2] * item[5]  # quantity * price
            items_with_totals.append({
                'name': item[4],  # item name
                'quantity': item[2],
                'price': item[5],  # item price
                'total': total,
            })
        orders_with_items.append((order, items_with_totals))


    # print(items_with_totals)
    
    return render(request, 'ordershistory.html', {'orders_with_items': orders_with_items})

@login_required
def payments(request):
    return render(request, "paymentsPage.html")