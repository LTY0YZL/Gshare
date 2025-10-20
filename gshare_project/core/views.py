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
import stripe
from datetime import timedelta


from core.models import (
    Users,
    Stores, Items,
    Orders, OrderItems,
    Deliveries, Feedback, GroupOrders, GroupMembers,
    RecurringCart, RecurringCartItem
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

def get_most_recent_order(user: Users, delivery_person: Users, status: str):
    try:
        order = Deliveries.objects.using('gsharedb').filter(delivery_person=delivery_person, status=status).latest('order_date')
        return order
    except Orders.DoesNotExist:
        return None

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

def get_most_recent_order(user: Users, delivery_person: Users, status: str):
    try:
        Delivery = Deliveries.objects.using('gsharedb').filter(delivery_person=delivery_person, status=status)

        for d in Delivery:
            order = Orders.objects.using('gsharedb').get(id=d.order.id, user=user)
        return order

    except Orders.DoesNotExist:
        return None

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
        print("Fetching items for order:", order.id)
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

def get_order_items_by_order_id(order_id: int):
     # Upsert into order_items (composite PK table) and recompute total
    with transaction.atomic(using='gsharedb'):
        print("Fetching items for order:", order_id)
        with connections['gsharedb'].cursor() as cur:
            # Fetch items with their details
            cur.execute(
                """
                SELECT oi.*, i.name, i.price, i.store_id
                FROM order_items oi
                JOIN items i ON oi.item_id = i.id
                WHERE oi.order_id = %s
                """,
                [order_id]
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

""" functions for feedback """

def add_feedback(reviewee: Users, reviewer: Users, feedback_text: str, rating: int):
    try:
        feedback = Feedback.objects.using('gsharedb').create(
            reviewee=reviewee,
            reviewer=reviewer,
            feedback=feedback_text,
            rating=rating,
            description_subject=feedback_text[:50] if feedback_text else None
        )
        return feedback
    except IntegrityError as e:
        print(f"Error adding feedback: {e}")
        return None

def add_feedback(reviewee: Users, reviewer: Users, feedback_text: str, subject: str, rating: int):
    try:
        feedback = Feedback.objects.using('gsharedb').create(
            reviewee=reviewee,
            reviewer=reviewer,
            feedback=feedback_text,
            rating=rating,
            description_subject= subject
        )
        return feedback
    except IntegrityError as e:
        print(f"Error adding feedback: {e}")
        return None
def get_feedback_for_user(user: Users):
    feedbacks = Feedback.objects.using('gsharedb').filter(reviewee=user)
    if not feedbacks.exists():
        return []
    return feedbacks

def get_feedback_by_order(reviewee: Users, reviewer: Users):
    try:
        feedback = Feedback.objects.using('gsharedb').get(reviewee=reviewee, reviewer=reviewer)
        return feedback
    except Feedback.DoesNotExist:
        return None

    

""" functions from here are for group orders """

@login_required
def create_group_order_json(request, order_id: int):
    if request.method == 'POST':
        data = json.loads(request.body)
        raw_password = data.get('password')
        
        if not raw_password:
            return JsonResponse({'error': 'Password is required'}, status=400)
        
        profile = get_user("email", request.user.email)
        if not profile:
            return JsonResponse({'error': 'Profile not found'}, status=404)
        
        try:
            order = Orders.objects.using('gsharedb').get(id=order_id, user=profile)
        except Orders.DoesNotExist:
            return JsonResponse({'error': 'Order not found'}, status=404)
        
        print(f"Creating group order for user {profile.email} with order {order_id}")
        try:
            group = create_group_order(profile, [order_id], raw_password)
            print("created group")
            return JsonResponse({'success': True, 'group_id': group.group_id})
        except Exception as e:
            print(f"Error creating group order: {e}")
            return JsonResponse({'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request'}, status=400)

"""
Create a new group order with a list of order IDs and a password.
Args:
    order_ids (list[int]): A list of order IDs to be included in the group order.
    raw_password (str): The raw password to be hashed and stored for the group order.
    Returns:
    GroupOrders: The created GroupOrders object.
"""
def create_group_order(user: Users, order_ids: list[int], raw_password: str):
    print(f"Creating group order for user {user.email} with orders {order_ids}")
    if not order_ids:
        raise ValueError("order_ids list cannot be empty")

    with transaction.atomic(using='gsharedb'):
        group = GroupOrders.objects.using('gsharedb').create(description="Group Order", password_hash="")
        print(f"Created group order with ID: {group.group_id}")
        set_group_password(group, raw_password)
        for oid in order_ids:
            print(f"Adding order {oid} to group {group.group_id}")
            try:
                print(f"Adding order {oid} to group")
                order = Orders.objects.using('gsharedb').get(id=oid, user=user)
                print(f"Found order {oid} for user {user.email}")
                GroupMembers.objects.using('gsharedb').create(group=group, user=user, order=order)
                print("I'm lost")
            except Orders.DoesNotExist:
                continue
        print(f"Created group order {group.group_id} with password hash {group.password_hash}")
        return group
    
def add_user_to_group_json(request, group: int):
    print("add_user_to_group_json called with group:", group)
    if request.method == 'POST':
        data = json.loads(request.body)
        password = data.get('password')
        
        if not password:
            return JsonResponse({'error': 'Password is required'}, status=400)
        
        profile = get_user("email", request.user.email)
        if not profile:
            return JsonResponse({'error': 'Profile not found'}, status=404)
        
        group_info = get_group_by_id(group)
        print("group_info:", group_info)
        if not group_info:
            return JsonResponse({'error': 'Group not found'}, status=404)
        
        if not verify_group_password(group_info, password):
            return JsonResponse({'error': 'Invalid password'}, status=403)
        
        print(f"Adding user {profile.email} to group {group_info.id}")
        try:
            success = add_user_to_group(group_info, profile)
            if success:
                return JsonResponse({'success': True})
            else:
                return JsonResponse({'error': 'User already in group'}, status=400)
        except Exception as e:
            print(f"Error adding user to group: {e}")
            return JsonResponse({'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request'}, status=400)

def add_user_to_group(group: GroupOrders, user: Users, order: Orders = None):
    try:
        GroupMembers.objects.using('gsharedb').create(group=group, user=user, order=order)
        return True
    except IntegrityError:
        return False
    
def remove_user_from_group_json(request, group: GroupOrders):
    if request.method == 'POST':
        data = json.loads(request.body)
        password = data.get('password')
        
        if not password:
            return JsonResponse({'error': 'Password is required'}, status=400)
        
        profile = get_user("email", request.user.email)
        if not profile:
            return JsonResponse({'error': 'Profile not found'}, status=404)
        
        group = get_group_by_id(data.get('group_id'))
        if not group:
            return JsonResponse({'error': 'Group not found'}, status=404)
        
        if not verify_group_password(group, password):
            return JsonResponse({'error': 'Invalid password'}, status=403)
        
        print(f"Removing user {profile.email} from group {group.id}")
        try:
            success = remove_user_from_group(group, profile)
            if success:
                return JsonResponse({'success': True})
            else:
                return JsonResponse({'error': 'User not in group'}, status=404)
        except Exception as e:
            print(f"Error removing user from group: {e}")
            return JsonResponse({'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request'}, status=400)

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
    return GroupMembers.objects.using('gsharedb').filter(users=user).distinct()

def get_cart_in_group(user: Users, group: GroupOrders):
    try:
        membership = GroupMembers.objects.using('gsharedb').filter(user=user, group=group)
        if membership.order and membership.order.status == 'cart':
            return membership.order
        return None
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
        membership = GroupMembers.objects.using('gsharedb').filter(user=user, order=order).first()
        print(membership)
        if membership is not None:
            if membership is not None:
                return membership.group
        
        return None

        return None
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

"""
    Retrieve orders associated with users within a specified geographical viewport.

    Args:
        min_lat (float): Minimum latitude of the viewport.
        min_lng (float): Minimum longitude of the viewport.
        max_lat (float): Maximum latitude of the viewport.
        max_lng (float): Maximum longitude of the viewport.
        limit (int): Maximum number of orders to retrieve (default: 500).

    Returns:
        list: A list of dictionaries containing order details and user information.
"""
def orders_in_viewport(min_lat, min_lng, max_lat, max_lng, limit=500):
    print(f"orders_in_viewport: {min_lat}, {min_lng}, {max_lat}, {max_lng}, limit={limit}")
    # Get users within the viewport
    users_in_viewport = _users_in_viewport(min_lat, min_lng, max_lat, max_lng, limit)
    print(f"Users in viewport: {len(users_in_viewport)}")

    if not users_in_viewport:
        return []  # No users found in the viewport

    # Extract user IDs from the users in the viewport
    user_ids = [user['id'] for user in users_in_viewport]
    print(f"Found {len(user_ids)} users in viewport")

    # Fetch orders for the users in the viewport
    orders = Orders.objects.using('gsharedb').filter(user_id__in=user_ids, status="placed").select_related('user')

    # Prepare the output
    orders_with_users = []
    for order in orders:
        user = next((u for u in users_in_viewport if u['id'] == order.user_id), None)
        if user:
            orders_with_users.append({
                'order_id': order.id,
                'user': user,
                'status': order.status,
                'total_amount': float(order.total_amount or 0),
                'order_date': order.order_date,
                'delivery_address': order.delivery_address,
            })

    return orders_with_users

"""
Return users whose (latitude, longitude) fall inside the map viewport.
Works with your current schema: latitude, longitude DECIMAL(9,6).
Handles antimeridian (min_lng > max_lng).
"""

def _users_in_viewport(min_lat, min_lng, max_lat, max_lng, limit=500, exclude_id=None):
    qs = (Users.objects.using('gsharedb')        # ← use MySQL
          .filter(latitude__isnull=False, longitude__isnull=False))

    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)

    lat_q = Q(latitude__gte=min_lat, latitude__lte=max_lat)
    if min_lng <= max_lng:
        lng_q = Q(longitude__gte=min_lng, longitude__lte=max_lng)
        qs = qs.filter(lat_q & lng_q)
    else:
        qs = qs.filter(lat_q & (Q(longitude__gte=min_lng) | Q(longitude__lte=max_lng)))

    rows = list(qs.values("id", "name", "address", "longitude", "latitude")[:limit])
    for r in rows:
        r["longitude"] = float(r["longitude"])
        r["latitude"]  = float(r["latitude"])
    return rows


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
            return redirect('signup')


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
        address = data['address']

        lat, lng = geoLoc(address)
        print(f"Geocoded {address} to lat={lat}, lng={lng}")

        profile.address = address
        print(f"Updated profile address to {address}")

        profile.latitude = lat
        profile.longitude = lng

        print(f"Geocoded {address} to lat={lat}, lng={lng}")
        print(f"Updated profile address to {address}, lat={profile.latitude}, lng={profile.longitude}")


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
                    address = request.POST['address']

                    profile.address = address
                    print(f"Updated profile address to {address}")

                    lat, lng = geoLoc(address)
                    print(f"Geocoded {address} to lat={lat}, lng={lng}")

                    profile.address = address
                    print(f"Updated profile address to {address}")

                    profile.latitude = lat
                    profile.longitude = lng

                    print(f"Updated profile address to {address}, lat={profile.latitude}, lng={profile.longitude}")
                                


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
def remove_from_cart(request, item_id, quantity=1):
    profile = get_user("email", request.user.email)
    if not profile:
        messages.error(request, "Profile not found.")
        return redirect('cart')

    order = Orders.objects.using('gsharedb').filter(user=profile, status='cart').first()
    if not order:
        messages.error(request, "No active cart.")
        return redirect('cart')

    with transaction.atomic(using='gsharedb'):

        # RECALC TOTAL from line items
        with connections['gsharedb'].cursor() as cur:
            cur.execute("""
                SELECT quantity FROM order_items
                WHERE order_id=%s AND item_id=%s
                LIMIT 1
            """, [order.id, item_id])
            line = cur.fetchone()
            if not line:
                messages.warning(request, "Item not found in cart.")
                return redirect("cart_view")

            if line[0] > quantity:
                cur.execute("UPDATE order_items SET quantity=quantity-%s WHERE order_id=%s AND item_id=%s ", [quantity, order.id, item_id])
            else:
                cur.execute("DELETE FROM order_items WHERE order_id=%s AND item_id=%s ", [order.id, item_id])

            # 3) Recompute order total
            cur.execute("""
                UPDATE orders o
                JOIN (
                    SELECT order_id, COALESCE(SUM(price * quantity),0) AS total
                    FROM order_items
                    WHERE order_id=%s
                ) t ON t.order_id = o.id
                SET o.total_amount = t.total
                WHERE o.id=%s
            """, [order.id, order.id])
            total = cur.fetchone() or 0

        order.total_amount = total
        order.save(using='gsharedb')

        item = Items.objects.using('gsharedb').filter(id=item_id).first()

    # Always return JSON for AJAX
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == "application/json":
        return JsonResponse({"success": True, "message": f"removed {item.name} frpm your cart."})

    messages.success(request, f"Removed {item.name} from your cart.")
    return redirect('shoppingcart')


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

import random
def estimate_order_time(user_address, store_address, num_items, api_key):
    
    drive_info = drive_time(user_address, store_address, api_key)
    if not drive_info:
        return None
    
    time_to_store = drive_info["duration_value"] / 60
    round_trip = time_to_store * 2
    
    shopping_time = num_items * 1.5
    
    total_time = round_trip + shopping_time
    
    variation = random.uniform(0.9, 1.1)
    total_time *= variation
    
    
    return {
        'distance': drive_info["distance_text"],
        'drive_time': round(round_trip, 1),
        'shopping_time': round(shopping_time, 1),
        'total_estimate': round(total_time, 1),
    }
    
def drive_time(user_address, store_address, api_key):
    url = f"https://maps.googleapis.com/maps/api/distancematrix/json?origins={user_address}&destination={store_address}&key={api_key}"
    
    response = response.get(url)
    data = response.json()
    
    if data["status"] == "OK":
        element = data['rows'][0]["elements"][0]
        if element["status"] == "OK":
            distance_text = element["distance"]["text"]
            distance_value = element["distance"]["value"]
            duration_text = element["duration"]["text"]
            duration_value = element["duration"]["value"]
            return {
                "distance_text": distance_text,
                "distance_value": distance_value,
                "duration_text": duration_text,
                "duration_value": duration_value,
            }
    return None
    
def maps_data(request, min_lat, min_lng, max_lat, max_lng):
    
    min_lat = float(min_lat)
    min_lng = float(min_lng)
    max_lat = float(max_lat)
    max_lng = float(max_lng)
    
    stores = Stores.objects.all()
    user = get_user("email", request.user.email)
    user_address = user.address
    orders = get_orders_by_status('placed')
    print("maps data")
    info = {}
    oiv = orders_in_viewport(min_lat, min_lng, max_lat, max_lng)
    print("orders in viewport:", len(oiv))
    for order in oiv:
        if order['delivery_address']:
            user = get_user("id", order['user']['id'])

            
        print(order['order_id'])
        items = get_order_items_by_order_id(order['order_id'])
                
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
            'address': order['delivery_address'],
            'items': items_with_totals,
            'subtotal': subtotal,
            'order_id': order['order_id'],
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
    
    return JsonResponse(grouped_info, safe=False)

@login_required
def maps(request):
    stores = Stores.objects.all()
    user = get_user("email", request.user.email)
    user_address = user.address
    orders = get_orders_by_status('placed')
    
    return render(request, "maps.html", {
        'google_maps_api_key': settings.GOOGLE_MAPS_API_KEY,
        'location': {'lat': 40.7607, 'lng': -111.8939},
        'stores_for_map': stores,
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

    orders = get_orders(profile, 'placed')
    print(orders)
    if not orders:
        return JsonResponse({'items': [], 'order': {'subtotal': 0, 'tax': 0, 'total': 0}, 'id': None})

    order_list = []
    
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
        order_list.append({
            'id': order.id,
            'summary': order_summary,
            'items': items_with_totals,
        })
    print(order_list)
    return JsonResponse({
        'orders': order_list
    })
    
def inprogress_data(request):
    print("inprogress data")
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    profile = get_user("email", request.user.email)
    if not profile:
        print("no user")
        return JsonResponse({'error': 'Profile not found'}, status=404)

    orders = get_orders(profile, 'inprogress')
    print(orders)
    if not orders:
        return JsonResponse({'items': [], 'order': {'subtotal': 0, 'tax': 0, 'total': 0}, 'id': None})

    order_list = []
    
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
        order_list.append({
            'id': order.id,
            'summary': order_summary,
            'items': items_with_totals,
        })
    print(order_list)
    return JsonResponse({
        'orders': order_list
    })
    
    
def group_data(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    profile = get_user("email", request.user.email)
    if not profile:
        print("no user")
        return JsonResponse({'error': 'Profile not found'}, status=404)
    groups = get_groups_for_user(profile)
    if not groups:
        return JsonResponse({'items': [], 'order': {'subtotal': 0, 'tax': 0, 'total': 0}, 'id': None})
    
    orders = []
    for group in groups:
        order = get_cart_in_group(profile, group.group_id)
        print("order in group:", order)
        if order:
            orders.append(order)
    # order = get_orders_in_group(profile, 'cart')
    print("orders:", orders)
    if not orders:
        return JsonResponse({'items': [], 'order': {'subtotal': 0, 'tax': 0, 'total': 0}, 'id': None})
    order_info = []
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
        order_info.append({
            'id': order.id,
            'summary': order_summary,
            'items': items_with_totals,
        })

    return JsonResponse({
        'orders': order_info
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
            'id': item[1],  # item id
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
        print(item)
        items_with_totals.append({
            'id': item[1],  # item id
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
def getUserProfile(request, userID):
    authUser = get_user("email", request.user.email)
    reviewee = get_user("id", userID)
    userReviews = get_user_ratings(userID)
    #latestOrder = get_most_recent_order(authUser, reviewee, "done")
    
    context = {
        'user': reviewee,
        'userReviews': userReviews[0],
        #'latestOrder': latestOrder,
    }

    if request.method == 'POST':
        reviewText = (request.POST.get('review') or '').strip()
        try:
            reviewRating = int(request.POST.get('rating') or 1)
        except (TypeError, ValueError):
            reviewRating = 1
        
        subject = "Bad Delivery"
        print(authUser)
        print(reviewee)

        #if latestOrder is None:
        #    messages.error(request, "You can only leave a review if you have a completed order with this user.")
        #    return render(request, 'aboutUserPage.html', context=context)

        add_feedback(reviewee, authUser, reviewText, subject, reviewRating)
        messages.success(request, "Review posted.")
        context['userReviews'] = get_user_ratings(userID)[0]
        return render(request, 'aboutUserPage.html', context=context)

    return render(request, 'aboutUserPage.html', context=context)

@login_required
def payments(request):
    user = get_user("email", request.user.email)
    order = get_orders(user, "inprogress").first()
    orders = []
    members = []

    group = get_group_by_user_and_order(user, order)
    print(group)

    carts_in_group = []
    members_payments = []

    if group is not None:
        orders = get_orders_in_group(group.group_id)
        members = get_group_members(group)
        # carts data
        for ord in orders:
            items = get_order_items(ord)
            subtotal = sum(item[2] * item[5] for item in items)  # quantity * price
            tax = round(subtotal * Decimal(0.07), 2)
            total = round(subtotal + tax, 2)
            user_name = ord.user.name
            carts_in_group.append({
                'user_name': user_name,
                'total': total,
            })
        
        for member in members:
            delivery_pref = f"Delivered to {member.user.address}" 
            payment_status = "⏳" 
            if member.order:
                if member.order.status == 'cart':
                    payment_status = "🛒"
                elif member.order.status == 'placed':
                    payment_status = "✅"
                elif member.order.status == 'pending':
                    payment_status = "⏳"
                else:
                    payment_status = "🔄"  # For other statuses like 'inprogress'
            members_payments.append({
                'user_name': member.user.name,
                'delivery_pref': delivery_pref,
                'payment_status': payment_status,
            })
    else:
        orders.append(order)
        members.append(user)

        for ord in orders:
            items = get_order_items(ord)
            subtotal = sum(item[2] * item[5] for item in items)  # quantity * price
            tax = round(subtotal * Decimal(0.07), 2)
            total = round(subtotal + tax, 2)
            user_name = ord.user.name
            carts_in_group.append({
                'user_name': user_name,
                'total': total,
            })
        delivery_pref = f"Delivered to {user.address}" 
        payment_status = "⏳" 
        if order:
            if order.status == 'cart':
                payment_status = "🛒"
            elif order.status == 'placed':
                payment_status = "✅"
            elif order.status == 'pending':
                payment_status = "⏳"
            else:
                payment_status = "🔄"  # For other statuses like 'inprogress'
        members_payments.append({
            'user_name': user.name,
            'delivery_pref': delivery_pref,
            'payment_status': payment_status,
        })
        
    print(order)

    context = {
        'carts_in_group': carts_in_group,
        'members_payments': members_payments,
    }
    return render(request, "paymentsPage.html", context)

@login_required
def paymentsCheckout(request):

    # change order status to placed upon successful payment
    try:
        stripe.api_key = settings.STRIPE_SECRET_KEY
        profile = get_user("email", request.user.email)
        try:
            orders = get_orders(profile, "inprogress")
            if not orders:
                messages.error(request, "No items in the cart to checkout.")
                return redirect('cart')
            order = orders[0]
            orderItems = get_order_items(order)
        except Exception:
            messages.error(request, "No items in the cart to checkout.")
            return redirect('cart')
        
        if not orderItems:
            messages.error(request, "Cart is empty.")
            return redirect('cart')
        
        itemObjects = []
        for oi in orderItems:
            # oi is a tuple: (order_id, item_id, quantity, price, name, price, store_id)
            itemObjects.append({
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': oi[4],
                    },
                    'unit_amount': int(oi[3] * 100),  # price in cents
                },
                'quantity': oi[2],
            })

        change_order_status(order.id, "delivered")

        checkoutSession = stripe.checkout.Session.create(
            line_items=itemObjects,
            mode='payment',
            success_url="http://127.0.0.1:8000/",
            cancel_url="http://127.0.0.1:8000/cart/payments",
        )
        return redirect(checkoutSession.url)
    except stripe.error.StripeError as e:
        messages.error(request, f"Payment error: {e.user_message}")
        return redirect('payments')
    except Exception as e:
        print(f"A serious error occurred: {e}. We have been notified.")
        messages.error(request, "An unexpected error occurred. Please try again.")
        return redirect('payments')  # Added return


def createGroupForShoppingCart(request, order_id):
    user = get_user("email", request.user.email)


    orderGroup = create_group_order(user, [order_id], "testPassword")
    print(orderGroup)

    return redirect('shoppingcart')

import math
def pickup_price(user_location, drop_off_location, num_items, store_address, api_key, base_rate=2.0, scale=0.3, item_rate=0.3):
    
    distance_from_user_to_store = drive_time(user_location, store_address, api_key)
    distance_from_dropoff_to_store = drive_time(drop_off_location, store_address, api_key)
    
    time_taken = estimate_order_time(user_location, store_address, num_items, api_key)
    time_cost = time_taken['total_estimate'] * 0.05
    
    diff_distance = abs(distance_from_user_to_store['distance_value'] - distance_from_dropoff_to_store['distance_value'])
    distance_cost = base_rate * (1-math.exp(scale * diff_distance**base_rate))
    
    item_cost = num_items * item_rate
    
    total_cost = round(base_rate + distance_cost + item_cost + time_cost)
    
    return {
        'distance_difference': diff_distance,
        'distance_cost': distance_cost,
        'time_cost': time_cost,
        'total_cost': total_cost,
    }

@login_required
def getUserProfile(request, userID):
    authUser = get_user("email", request.user.email)
    reviewee = get_user("id", userID)
    userReviews = get_user_ratings(userID)
    #latestOrder = get_most_recent_order(authUser, reviewee, "done")
    
    context = {
        'user': reviewee,
        'userReviews': userReviews[0],
    }

    if request.method == 'POST':
        reviewText = (request.POST.get('review') or '').strip()
        try:
            reviewRating = int(request.POST.get('rating') or 0)
        except (TypeError, ValueError):
            reviewRating = 0
        
        subject = ""

        # if latestOrder is None:
        #     messages.error(request, "You can only leave a review if you have a completed order with this user.")
        #     return render(request, 'aboutUserPage.html', context=context)

        add_feedback(reviewee, authUser, reviewText, subject, reviewRating)
        messages.success(request, "Review posted.")
        context['userReviews'] = get_user_ratings(userID)[0]
        return render(request, 'aboutUserPage.html', context=context)

    return render(request, 'aboutUserPage.html', context=context)


@login_required
def create_recurring_cart(request):
    messages.info(request, " ")
    return redirect('recurring_carts')

@login_required
def manage_recurring_carts(request):
    profile = get_object_or_404(Users.objects.using('gsharedb'), email=request.user.email)
    carts = RecurringCart.objects.using('gsharedb').filter(user=profile).prefetch_related('items__item')
    context = {'carts': carts}
    return render(request, 'scheduled_orders.html', context)

@login_required
def create_recurring_from_order(request, order_id):
    profile = get_object_or_404(Users.objects.using('gsharedb'), email=request.user.email)
    original_order = get_object_or_404(Orders.objects.using('gsharedb'), pk=order_id, user=profile)

    new_recurring_cart = RecurringCart.objects.using('gsharedb').create(
        user=profile,
        name=f"Recurring from Order #{original_order.id}",
        frequency='weekly',
        status='enabled',
        next_order_date=timezone.now().date() + timedelta(days=7)
    )

    items_to_copy = []
    with connections['gsharedb'].cursor() as cursor:
        cursor.execute("SELECT item_id, quantity FROM order_items WHERE order_id = %s", [original_order.id])
        items_to_copy = cursor.fetchall()

    for item_id, quantity in items_to_copy:
        RecurringCartItem.objects.using('gsharedb').create(
            recurring_cart=new_recurring_cart,
            item_id=item_id,  
            quantity=quantity
        )
    
    messages.success(request, f"Successfully created a new recurring list from Order #{original_order.id}.")
    return redirect('manage_recurring_carts')

@login_required
def toggle_recurring_cart_status(request, cart_id):
    cart = get_object_or_404(RecurringCart.objects.using('gsharedb'), pk=cart_id, user__email=request.user.email)
    if cart.status == 'enabled':
        cart.status = 'paused'
    else:
        cart.status = 'enabled'
    cart.save(using='gsharedb')
    return redirect('manage_recurring_carts')

@login_required
def delete_recurring_cart(request, cart_id):
    cart = get_object_or_404(RecurringCart.objects.using('gsharedb'), pk=cart_id, user__email=request.user.email)
    cart_name = cart.name
    cart.delete()
    messages.success(request, f"Successfully deleted the recurring list: '{cart_name}'.")
    return redirect('manage_recurring_carts')

@login_required
def scheduled_orders(request):
    profile = get_object_or_404(Users.objects.using('gsharedb'), email=request.user.email)
    carts = RecurringCart.objects.using('gsharedb').filter(user=profile).prefetch_related('items__item')
    context = {'carts': carts}
    return render(request, "scheduled_orders.html", context)

@login_required
def updateScheduledOrders(request, cart_id):
    user = get_user("email", request.user.email)
    if request.method == 'POST':
        try:
            cart = RecurringCart.objects.using('gsharedb').get(id=cart_id, user=user)
        except RecurringCart.DoesNotExist:
            messages.error(request, "Recurring cart not found.")
            return redirect('scheduled_orders')

        # Update next date of order
        nextDate = request.POST.get('next_order_date')
        if nextDate:
            try:
                cart.next_order_date = timezone.datetime.fromisoformat(nextDate).date()
            except ValueError:
                messages.error(request, "Invalid date format.")
                return redirect('scheduled_orders')

        # Update item quantities
        for item in cart.items.all():
            quantity_key = f'quantity_{item.id}'
            quantity_str = request.POST.get(quantity_key)
            if quantity_str:
                try:
                    quantity = int(quantity_str)
                    if quantity > 0:
                        item.quantity = quantity
                        item.save(using='gsharedb')
                    else:
                        messages.warning(request, f"Quantity for {item.item.name} must be greater than 0.")
                except ValueError:
                    messages.error(request, f"Invalid quantity for {item.item.name}.")
                    return redirect('scheduled_orders')

        cart.save(using='gsharedb')
        messages.success(request, "Recurring cart updated successfully.")
        return redirect('scheduled_orders')  

    return redirect('scheduled_orders')

@login_required
def create_recurring_cart(request):
    return redirect('scheduled_orders')

@login_required
def toggle_cart_status(request, cart_id):
    user = get_user("email", request.user.email)
    if request.method == "POST":
        recurringCart = RecurringCart.objects.using('gsharedb').get(id=cart_id, user=user)
        cartStatus = request.POST.get("cartStatus")
        if cartStatus == "enabled":
            recurringCart.status = "disabled"
        else:
            recurringCart.status = "enabled"
        recurringCart.save(using='gsharedb')
        return redirect('scheduled_orders')
    return redirect('scheduled_orders')

@login_required
def delete_cart(request, cart_id):
    user = get_user("email", request.user.email)
    if request.method == "POST":
        recurringCart = RecurringCart.objects.using('gsharedb').get(id=cart_id, user=user)
        recurringCart.delete(using='gsharedb')
    return redirect('scheduled_orders')