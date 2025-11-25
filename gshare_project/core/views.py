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
from django.core.files.storage import default_storage
from core.utils.simple_gemini import scan_receipt, chat_about_receipt, suggest_matching_order
import json
import re
import requests
from core.utils.geo import geoLoc
from urllib.parse import urlencode
from . import kroger_api
import stripe
from datetime import timedelta
import io, os, mimetypes
from uuid import uuid4
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils.text import get_valid_filename
from .models import UploadedImage
from core.utils.aws_s3 import get_s3_client, get_bucket_and_region
from .tasks import parse_receipt_task
from .utils.aws_s3 import upload_file_like, presigned_url
from django.views.decorators.http import require_POST
from chat.models import ChatGroup
from groqai.groq_proxy import call_groq
from groqai.instructions import VOICE_ORDER_CHAT_INSTRUCTIONS, VOICE_ORDER_FINALIZE_INSTRUCTIONS
from core.models import (
    Users,
    Stores, Items,
    Orders, OrderItems,
    Deliveries, Feedback, GroupOrders, GroupMembers,
    RecurringCart, RecurringCartItem, ProductImage,
    Receipt, ReceiptLine, ReceiptChatMessage
)

"""helper functions"""

def calculate_tax(subtotal):
    """Calculate tax as 7% of subtotal, in cents (int), rounded to nearest cent."""
    return round(subtotal * Decimal('0.07') * 100)

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
    
def Create_delivery(order: Orders, delivery_person: Users):
    try:
        delivery = Deliveries.objects.using('gsharedb').create(
            order=order,
            delivery_person=delivery_person,
            status='pending',
            pickup_time=timezone.now() + timedelta(minutes=15),
        )
        return delivery
    except IntegrityError as e:
        print(f"Error creating delivery: {e}")
        return None
    
def reject_delivery(delivery: Deliveries):
    try:
        delivery.delete()
        return True
    except Exception as e:
        print(f"Error deleting delivery: {e}")
        return False
    
def get_order_for_delivery(delivery: Deliveries):
    try:
        order = Orders.objects.using('gsharedb').get(id=delivery.order.id)
        return order
    except Orders.DoesNotExist:
        return None

def get_delivery_for_order(order):
    """
    Return the most recent Delivery for the given order (or order_id).
    If none exist, return None.
    """
    # Allow order to be either an Orders instance or an integer id
    order_id = order.id if hasattr(order, "id") else order

    return (
        Deliveries.objects.using("gsharedb")
        .filter(order=order_id)
        .order_by("-id")      # latest delivery first; adjust if you have a better field
        .first()
    )
    
def delivery_done(delivery: Deliveries):
    try:
        delivery.status = 'delivered'
        delivery.delivery_time = timezone.now()
        delivery.save(using='gsharedb')
        return True
    except Exception as e:
        print(f"Error updating delivery status: {e}")
        return False

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
    
def get_store_for_order(order: Orders):
    try:
        store = Stores.objects.using('gsharedb').get(id=order.store.id)
        return store
    except Stores.DoesNotExist:
        return None
    
def get_store_from_item(item: Items):
    try:
        store = Stores.objects.using('gsharedb').get(id=item.store.id)
        return store
    except Stores.DoesNotExist:
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
def create_user_signin(name: str, email: str, address: str = "Not provided", username: str = "Not provided", phone: str | None = None, request=None):
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
                    username = username,
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
        with transaction.atomic(using="gsharedb"):
            with connections["gsharedb"].cursor() as cur:
                cur.execute(
                    """
                    UPDATE order_items
                    SET quantity = %s
                    WHERE order_id = %s AND item_id = %s
                    """,
                    [new_quantity, order_id, item_id],
                )

                if cur.rowcount == 0:
                    return False

        return True

    except Exception as e:
        print(f"Error updating order item: {e}")
        return False
    
def edit_order_items_json(request, item_id, quantity):
    if request.method == 'POST':
        user = get_user("email", request.user.email)
        order = get_orders(user, "cart")
        
        if not order:
            return JsonResponse({'success': False, 'error': 'No cart order found'})
        
        order_id = order[0].id
        success = Edit_order_items(order_id, item_id, quantity)
        return JsonResponse({'success': success})
    
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)

"""
Retrieve all orders for a specific user from the 'gsharedb' database.

Args:
    user (Users): The user object for whom the orders are being retrieved.
    Status (str): The status of the orders to filter by ('cart', 'placed', 'inprogress', 'delivered').

Returns:
    QuerySet or list: A QuerySet of orders if orders exist, otherwise an empty list.
"""
def get_orders(user: Users, order_status: str):

    # Filter by the actual user object (or its id), not the Users instance itself as user_id
    orders = Orders.objects.using('gsharedb').filter(user=user, status=order_status)
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
Return a queryset of Orders assigned to this delivery person
with the given status.
"""
def get_orders_by_delivery_person(delivery_person: Users, status: str):

    return Deliveries.objects.using('gsharedb').filter(
        delivery_person=delivery_person,
        status=status,
    ).distinct()

def get_most_recent_order(user: Users, delivery_person: Users, status: str):
    try:
        Delivery = Deliveries.objects.using('gsharedb').filter(delivery_person=delivery_person, status=status)

        for d in Delivery:
            order = Orders.objects.using('gsharedb').get(id=d.order.id, user=user)
        return order

    except Orders.DoesNotExist:
        return None


def update_status_order_accepting(order: Orders):
    try:
        order.status = "accepting"
        order.save(using='gsharedb')
        user = (Deliveries.objects.using('gsharedb').get(order=order)).delivery_person
        return user
    except Exception as e:
        print(f"Error updating order status or get order: {e}")
        return False
    


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
    new_status (str): The new status to set for the order ('cart', 'placed', 'pending', 'inprogress','delivered').
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

def change_order_status_with_driver(request, order_id, new_status):
    try:
        order = Orders.objects.using('gsharedb').get(id=order_id)
        order.status = new_status
        order.save(using='gsharedb')

        if new_status == 'inprogress':
            driver = get_user("email", request.user.email)
            delivery, _ = Deliveries.objects.using('gsharedb').get_or_create(order=order)
            delivery.delivery_person = driver
            delivery.status = 'inprogress'
            delivery.save(using='gsharedb')
        elif new_status == 'delivered':
            delivery, _ = Deliveries.objects.using('gsharedb').get_or_create(order=order)
            delivery.status = 'delivered'
            delivery.save(using='gsharedb')
        return True
    except Orders.DoesNotExist:
        return False

@login_required
def confirm_delivery_json(request, order_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid method'}, status=405)

    user = get_user("email", request.user.email)
    try:
        order = Orders.objects.using('gsharedb').get(id=order_id)
    except Orders.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Order not found'}, status=404)

    delivery, _ = Deliveries.objects.using('gsharedb').get_or_create(order=order)

    if user.id == order.user_id:
        delivery.buyer_confirmed = True
    if delivery.delivery_person and user.id == delivery.delivery_person.id:
        delivery.driver_confirmed = True

    delivery.save(using='gsharedb')

    fully = delivery.buyer_confirmed and delivery.driver_confirmed
    if fully:
        order.status = 'delivered'
        order.save(using='gsharedb')
        delivery.status = 'delivered'
        delivery.save(using='gsharedb')

    return JsonResponse({'success': True, 'fully_delivered': fully})

def change_order_status_json(request, order_id, new_status):
    if request.method == 'POST':
        try:
            success = change_order_status_with_driver(request, order_id, new_status)
            return JsonResponse({'success': success})
        except Exception as e:
            print("Error in change_order_status_json:", e)
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)

def update_status_order_pending(order: Orders):
    try:
        order.status = "pending"
        order.save(using='gsharedb')
        return True
    except Exception as e:
        print(f"Error updating order status or get order: {e}")
        return False
    
def change_status_pending_json(request, order_id):
    if request.method == 'POST':
        success = update_status_order_pending(order_id)
        return JsonResponse({'success': success})
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)

def Create_delivery(order: Orders, delivery_person: Users):
    try:
        delivery = Deliveries.objects.using('gsharedb').create(
            order=order,
            delivery_person=delivery_person,
            status='pending',
            pickup_time=timezone.now() + timedelta(minutes=15),
        )
        return delivery
    except IntegrityError as e:
        print(f"Error creating delivery: {e}")
        return None

def get_delivery_for_order(order: Orders):
    try:
        delivery = Deliveries.objects.using('gsharedb').get(order=order)
        return delivery
    except Deliveries.DoesNotExist:
        return None

def delivery_done(delivery: Deliveries):
    try:
        delivery.status = 'accepted'
        delivery.delivery_time = timezone.now()
        delivery.save(using='gsharedb')
        return True
    except Exception as e:
        print(f"Error updating delivery status: {e}")
        return False
    
def reject_delivery(delivery: Deliveries):
    try:
        delivery.delete()
        return True
    except Exception as e:
        print(f"Error deleting delivery: {e}")
        return False
    
def get_order_for_delivery(delivery: Deliveries):
    try:
        order = Orders.objects.using('gsharedb').get(id=delivery.order.id)
        return order
    except Orders.DoesNotExist:
        return None

def remove_delivery_json(request, order_id):
    if request.method == 'POST':
        try:
            delivery = Deliveries.objects.using('gsharedb').get(order__id=order_id)
            success = reject_delivery(delivery)
            return JsonResponse({'success': success})
        except Deliveries.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Delivery not found'}, status=404)
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)

def create_delivery_json(request, order_id):
    if request.method == 'POST':
        try:
            order = Orders.objects.using('gsharedb').get(id=order_id)
            delivery_person = get_user("email", request.user.email)
            print("delivery_person:", delivery_person)
            delivery = Create_delivery(order, delivery_person)
            if delivery:
                return JsonResponse({'success': True, 'delivery_id': delivery.id})
            else:
                return JsonResponse({'success': False, 'error': 'Failed to create delivery'}, status=500)
        except Orders.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Order not found'}, status=404)

def delivery_accepted_json(request, order_id):
    if request.method == 'POST':
        try:
            delivery = Deliveries.objects.using('gsharedb').get(order__id=order_id)
            success = delivery_done(delivery)
            return JsonResponse({'success': success})
        except Deliveries.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Delivery not found'}, status=404)
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)

def update_status_order_pending(order: Orders):
    try:
        order.status = "pending"
        order.save(using='gsharedb')
        return True
    except Exception as e:
        print(f"Error updating order status or get order: {e}")
        return False
    
def change_status_pending_json(request, order_id):
    if request.method == 'POST':
        success = update_status_order_pending(order_id)
        return JsonResponse({'success': success})
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)

def Create_delivery(order: Orders, delivery_person: Users):
    try:
        delivery = Deliveries.objects.using('gsharedb').create(
            order=order,
            delivery_person=delivery_person,
            status='pending',
            pickup_time=timezone.now() + timedelta(minutes=15),
        )
        return delivery
    except IntegrityError as e:
        print(f"Error creating delivery: {e}")
        return None

def get_delivery_for_order(order: Orders):
    try:
        delivery = Deliveries.objects.using('gsharedb').get(order=order)
        return delivery
    except Deliveries.DoesNotExist:
        return None

def delivery_done(delivery: Deliveries):
    try:
        delivery.status = 'accepted'
        delivery.delivery_time = timezone.now()
        delivery.save(using='gsharedb')
        return True
    except Exception as e:
        print(f"Error updating delivery status: {e}")
        return False
    
def reject_delivery(delivery: Deliveries):
    try:
        delivery.delete()
        return True
    except Exception as e:
        print(f"Error deleting delivery: {e}")
        return False
    
def get_order_for_delivery(delivery: Deliveries):
    try:
        order = Orders.objects.using('gsharedb').get(id=delivery.order.id)
        return order
    except Orders.DoesNotExist:
        return None

def remove_delivery_json(request, order_id):
    if request.method == 'POST':
        try:
            delivery = Deliveries.objects.using('gsharedb').get(order__id=order_id)
            success = reject_delivery(delivery)
            return JsonResponse({'success': success})
        except Deliveries.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Delivery not found'}, status=404)
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)

def create_delivery_json(request, order_id):
    if request.method == 'POST':
        try:
            order = Orders.objects.using('gsharedb').get(id=order_id)
            delivery_person = get_user("email", request.user.email)
            print("delivery_person:", delivery_person)
            delivery = Create_delivery(order, delivery_person)
            if delivery:
                return JsonResponse({'success': True, 'delivery_id': delivery.id})
            else:
                return JsonResponse({'success': False, 'error': 'Failed to create delivery'}, status=500)
        except Orders.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Order not found'}, status=404)

def delivery_accepted_json(request, order_id):
    if request.method == 'POST':
        try:
            delivery = Deliveries.objects.using('gsharedb').get(order__id=order_id)
            success = delivery_done(delivery)
            return JsonResponse({'success': success})
        except Deliveries.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Delivery not found'}, status=404)
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
        # Ensure rating is within valid range (1-5)
        rating = max(1, min(5, rating))
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
    return GroupMembers.objects.using('gsharedb').filter(user=user).distinct()

def get_cart_in_group(user: Users, group: GroupOrders):
    membership = GroupMembers.objects.using('gsharedb').filter(user=user, group=group).first()
    if membership and membership.order and membership.order.status == 'cart':
        return membership.order
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
def orders_in_viewport(min_lat, min_lng, max_lat, max_lng, limit=500, viewer=None):
    print(f"orders_in_viewport: {min_lat}, {min_lng}, {max_lat}, {max_lng}, limit={limit}")
    # Get users within the viewport
    users_in_viewport = _users_in_viewport(min_lat, min_lng, max_lat, max_lng, limit)
    print(f"Users in viewport: {len(users_in_viewport)}")

    if not users_in_viewport:
        return []  # No users found in the viewport

    # Extract user IDs from the users in the viewport
    user_ids = [user['id'] for user in users_in_viewport]
    print(f"Found {len(user_ids)} users in viewport")

    base_qs = Orders.objects.using('gsharedb').filter(user_id__in=user_ids)

    if viewer is not None:
        orders = base_qs.filter(
            Q(status="placed") |
            Q(status="inprogress", user=viewer) |
            Q(status="inprogress", deliveries__delivery_person=viewer)
        ).select_related('user', 'store').distinct()
    else:
        orders = base_qs.filter(status="placed").select_related('user', 'store')

    # Prepare the output
    orders_with_users = []
    for order in orders:
        user = next((u for u in users_in_viewport if u['id'] == order.user_id), None)
        if not user:
            continue

        store = order.store 

        orders_with_users.append({
            'order_id': order.id,
            'user': user,
            'status': order.status,
            'total_amount': float(order.total_amount or 0),
            'order_date': order.order_date,
            'delivery_address': order.delivery_address,
            'store_id': store.id if store else None,
            'store_name': store.name if store else "",
            'store_address': store.location if (store and store.location) else "",
            'store_lat': float(store.latitude) if (store and store.latitude is not None) else None,
            'store_lng': float(store.longitude) if (store and store.longitude is not None) else None,
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

"""image functions"""
@csrf_exempt  # if you're posting from a non-CSRF context; otherwise keep CSRF
def upload_image(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    if "file" not in request.FILES:
        return HttpResponseBadRequest("Missing 'file' in form-data.")

    file = request.FILES["file"]
    original_name = get_valid_filename(file.name)
    content_type = (
        file.content_type
        or mimetypes.guess_type(original_name)[0]
        or "application/octet-stream"
    )

    # Build a unique S3 key
    key = f"uploads/{uuid4().hex}_{original_name}"

    s3 = get_s3_client()
    bucket, region = get_bucket_and_region()

    try:
        # Upload the file to S3
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=file.read(),
            ContentType=content_type,
        )
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"S3 upload failed: {e}"}, status=500)

    # Save record directly into gsharedb
    img = UploadedImage.objects.using("gsharedb").create(
        key=key,
        content_type=content_type,
        original_name=original_name,
    )

    # Canonical object URL (may require public ACL if used directly)
    object_url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}"

    try:
        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=3600,  # 1 hour
        )
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Presign failed: {e}"}, status=500)

    return JsonResponse(
        {
            "ok": True,
            "id": img.id,
            "key": key,
            "content_type": content_type,
            "object_url": object_url,
            "presigned_url": presigned_url,
        },
        status=201,
    )


def get_image_url(request, image_id):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    try:
        # Retrieve record directly from gsharedb
        img = UploadedImage.objects.using("gsharedb").get(pk=image_id)
    except UploadedImage.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Not found"}, status=404)

    s3 = get_s3_client()
    bucket, region = get_bucket_and_region()
    object_url = f"https://{bucket}.s3.{region}.amazonaws.com/{img.key}"

    try:
        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": img.key},
            ExpiresIn=3600,
        )
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Presign failed: {e}"}, status=500)

    return JsonResponse(
        {
            "ok": True,
            "id": img.id,
            "key": img.key,
            "object_url": object_url,
            "presigned_url": presigned_url,
        }
    )


def upload_user_avatar(request, user_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    if "file" not in request.FILES:
        return HttpResponseBadRequest("Missing 'file' in form-data.")

    file = request.FILES["file"]
    original_name = get_valid_filename(file.name)
    content_type = file.content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    key = f"avatars/{user_id}/{uuid4().hex}_{original_name}"

    s3 = get_s3_client()
    bucket, region = get_bucket_and_region()

    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=file.read(),
            ContentType=content_type,
        )
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"S3 upload failed: {e}"}, status=500)

    # Save the key on the user in gsharedb
    try:
        Users.objects.using("gsharedb").filter(pk=user_id).update(image_key=key)
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"DB update failed: {e}"}, status=500)

    # Return a presigned URL to preview immediately
    try:
        presigned = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=3600,
        )
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Presign failed: {e}"}, status=500)

    return JsonResponse({
        "ok": True,
        "user_id": user_id,
        "image_key": key,
        "preview_url": presigned,
    }, status=201)


def get_user_avatar_url(request, user_id: int):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    try:
        user = Users.objects.using("gsharedb").get(pk=user_id)
    except Users.DoesNotExist:
        return JsonResponse({"ok": False, "error": "User not found"}, status=404)

    if not user.image_key:
        # Optional: return a default image (S3 key) or None
        return JsonResponse({"ok": True, "user_id": user_id, "image_key": None, "url": None})

    s3 = get_s3_client()
    bucket, region = get_bucket_and_region()

    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": user.image_key},
            ExpiresIn=3600,
        )
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Presign failed: {e}"}, status=500)

    return JsonResponse({"ok": True, "user_id": user_id, "image_key": user.image_key, "url": url})

def upload_user_avatar_helper(user_id: int, uploaded_file) -> dict:
    """
    Upload a new avatar to S3 for the given user, store the S3 key on users.image_key (gsharedb),
    and return {ok, key, url} where url is a presigned GET URL.
    """
    s3 = get_s3_client()
    bucket, region = get_bucket_and_region()

    original_name = get_valid_filename(uploaded_file.name)
    content_type = uploaded_file.content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"

    # Build new key: avatars/<user_id>/<uuid>_<filename>
    new_key = f"avatars/{user_id}/{uuid4().hex}_{original_name}"

    # Read old key (if any) before updating
    user = Users.objects.using("gsharedb").only("image_key").get(pk=user_id)
    old_key = user.image_key

    # Upload to S3
    s3.put_object(
        Bucket=bucket,
        Key=new_key,
        Body=uploaded_file.read(),
        ContentType=content_type,
    )

    # Persist the new key
    Users.objects.using("gsharedb").filter(pk=user_id).update(image_key=new_key)

    # Optional: cleanup old object to avoid orphans
    if old_key and old_key != new_key:
        try:
            s3.delete_object(Bucket=bucket, Key=old_key)
        except Exception:
            pass

    # Build presigned URL
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": new_key},
        ExpiresIn=3600,
    )

    return {"ok": True, "key": new_key, "url": url}


def get_user_avatar_url_helper(user_id: int) -> str | None:
    """
    Return a presigned GET URL for the user's current avatar, or None if not set.
    """
    try:
        user = Users.objects.using("gsharedb").only("image_key").get(pk=user_id)
    except Users.DoesNotExist:
        return None

    if not user.image_key:
        return None

    s3 = get_s3_client()
    bucket, region = get_bucket_and_region()
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": user.image_key},
        ExpiresIn=3600,
    )


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

def video_url(video_key):
    s3 = get_s3_client()
    bucket, region = get_bucket_and_region()
    object_url = f"https://gshare-media-prod.s3.us-east-2.amazonaws.com/uploads/Tutorial_Video.mp4"

    try:
        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": video_key},
            ExpiresIn=3600,
        )
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Presign failed: {e}"}, status=500)
    return JsonResponse(
        {
            "ok": True,
            "key": video_key,
            "object_url": object_url,
            "presigned_url": presigned_url,
        }
    )

def aboutus(request):
    jsonResponse = video_url("uploads/Tutorial_Video.mp4")
    print(jsonResponse.items())
    context = {
        'presigned_url': "gshare_project/static/media/Tutorial_Video.mp4",
    }
    return render(request, "aboutus.html", context=context)

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
            create_user_signin(full_name, e, address=addr, username=u, phone=phone,request=request)
        except IntegrityError as ex:
            # roll back auth user if business insert fails
            auth_user.delete()
            messages.error(request, f"Could not create profile: {ex}")
            return redirect('signup')


        # Login and redirect
        auth_login(request, auth_user)
        return redirect('home')
    
    return render(request, 'login.html')


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
def receipt_upload_view(request):
    if request.method == "POST":
        uploaded = request.FILES.get("receipt_image")
        if not uploaded:
            messages.error(request, "Please choose an image.")
            return redirect("receipt_upload")

        # gsharedb user row
        g_user = Users.objects.using("gsharedb").get(email=request.user.email)

        s3 = get_s3_client()
        bucket, region = get_bucket_and_region()

        original_name = get_valid_filename(uploaded.name)
        key = f"receipts/{g_user.id}/{uuid4().hex}_{original_name}"

        # Upload to S3
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=uploaded.read(),
            ContentType=uploaded.content_type or "image/jpeg",
        )

        # Create Receipt in gsharedb
        receipt = Receipt.objects.using("gsharedb").create(
            uploader=g_user,
            s3_bucket=bucket,
            s3_key=key,
            status="pending",
        )

        # FASTEST: do scan synchronously right here (may take a few seconds)
        try:
            scan_receipt(receipt.id)
        except Exception as e:
            receipt.status = "error"
            receipt.error = str(e)
            receipt.save(using="gsharedb")

        return redirect("receipt_detail", rid=receipt.id)

    return render(request, "deliveries/receipt_upload.html")



@login_required
def receipt_detail_view(request, rid: int):
    # Receipt from gsharedb
    receipt = get_object_or_404(Receipt.objects.using("gsharedb"), pk=rid)

    # lines from gsharedb
    lines = (
        ReceiptLine.objects.using("gsharedb")
        .filter(receipt=receipt)
        .order_by("id")
    )

    chat_messages = (
        ReceiptChatMessage.objects
        .filter(receipt_id=receipt.id)
        .order_by("created_at", "id")
    )

    image_url = None
    if getattr(receipt, "s3_bucket", None) and getattr(receipt, "s3_key", None):
        try:
            image_url = presigned_url(receipt.s3_bucket, receipt.s3_key)
        except Exception:
            image_url = None

    return render(
        request,
        "deliveries/receipt_detail.html",
        {
            "receipt": receipt,
            "lines": lines,
            "chat_messages": chat_messages,
            "image_url": image_url,
        },
    )

def _apply_receipt_operations(receipt, operations):
    """
    Apply Gemini's operations to ReceiptLine objects in gsharedb.
    operations is a list of dicts with keys:
      - op: "update" | "add" | "delete"
      - target_name: str or None
      - fields: dict with optional keys name, quantity, unit_price, total_price
    """
    qs = ReceiptLine.objects.using("gsharedb").filter(receipt=receipt)

    def find_line_by_name(name: str):
        # exact (case-insensitive) first
        q = qs.filter(name__iexact=name)
        if q.exists():
            return q.first()
        # fallback: contains
        q = qs.filter(name__icontains=name)
        return q.first() if q.exists() else None

    for op in operations:
        kind = (op.get("op") or "").lower()
        target_name = op.get("target_name")
        fields = op.get("fields") or {}

        if kind == "update":
            if not target_name:
                continue
            line = find_line_by_name(target_name)
            if not line:
                continue

            new_name = fields.get("name")
            if new_name:
                line.name = new_name

            if "quantity" in fields:
                try:
                    line.quantity = float(fields["quantity"])
                except (TypeError, ValueError):
                    pass

            if "unit_price" in fields:
                try:
                    line.unit_price = float(fields["unit_price"])
                except (TypeError, ValueError):
                    pass

            if "total_price" in fields:
                try:
                    line.total_price = float(fields["total_price"])
                except (TypeError, ValueError):
                    pass

            # optionally keep a copy of everything in meta
            line.meta = fields
            line.save(using="gsharedb")

        elif kind == "delete":
            if not target_name:
                continue
            line = find_line_by_name(target_name)
            if line:
                line.delete(using="gsharedb")

        elif kind == "add":
            f = fields
            name = f.get("name")
            if not name:
                continue

            qty = f.get("quantity", 1)
            unit_price = f.get("unit_price")
            total_price = f.get("total_price")

            try:
                qty = float(qty)
            except (TypeError, ValueError):
                qty = 1

            if unit_price is not None:
                try:
                    unit_price = float(unit_price)
                except (TypeError, ValueError):
                    unit_price = None

            if total_price is not None:
                try:
                    total_price = float(total_price)
                except (TypeError, ValueError):
                    total_price = None

            ReceiptLine.objects.using("gsharedb").create(
                receipt=receipt,
                name=name[:256],
                quantity=qty,
                unit_price=unit_price,
                total_price=total_price,
                meta=f,
            )

@login_required
@require_POST
def receipt_match_orders_view(request, rid: int):
    receipt = get_object_or_404(Receipt.objects.using("gsharedb"), pk=rid)

    # 1) Load receipt items
    lines = list(
        ReceiptLine.objects.using("gsharedb")
        .filter(receipt=receipt)
        .order_by("id")
    )

    if not lines:
        ReceiptChatMessage.objects.create(
            receipt_id=receipt.id,
            role="assistant",
            content=(
                "I can't match this to an order yet, because there are no "
                "parsed items on this receipt."
            ),
        )
        return redirect("receipt_detail", rid=receipt.id)

    driver_user_id = receipt.uploader_id or 0  # or however you identify the driver

    candidate_orders = []

    # 2) Get active delivery orders for this driver (orders + deliveries)
    with connections["gsharedb"].cursor() as cur:
        cur.execute(
            """
            SELECT o.id, o.status, o.store_id, o.order_date
            FROM deliveries d
            JOIN orders o ON d.order_id = o.id
            WHERE d.delivery_person_id = %s
              AND d.status IN ('accepted', 'inprogress', 'delivering')   -- adjust to your statuses
            ORDER BY o.order_date DESC
            LIMIT 10
            """,
            [driver_user_id],
        )
        order_rows = cur.fetchall()

    if not order_rows:
        ReceiptChatMessage.objects.create(
            receipt_id=receipt.id,
            role="assistant",
            content="You don’t seem to have any active delivery orders I can match this receipt to right now.",
        )
        return redirect("receipt_detail", rid=receipt.id)

    # 3) For each order, pull items via raw SQL (avoids composite-PK ORM issues)
    for oid, status, store_id, order_date in order_rows:
        with connections["gsharedb"].cursor() as cur:
            cur.execute(
                """
                SELECT i.name, oi.quantity
                FROM order_items oi
                JOIN items i ON i.id = oi.item_id
                WHERE oi.order_id = %s
                """,
                [oid],
            )
            item_rows = cur.fetchall()

        line_items = [
            {"name": name, "quantity": float(qty or 0)}
            for (name, qty) in item_rows
        ]

        candidate_orders.append(
            {
                "id": oid,
                "status": status,
                "store_id": store_id,
                "created_at": str(order_date),
                "items": line_items,   # <-- this is what Gemini sees
            }
        )

    # 4) Ask Gemini which orders match this receipt
    try:
        inferred_order_id, ai_reply = suggest_matching_order(
            receipt=receipt,
            lines=lines,
            candidate_orders=candidate_orders,
        )

        if inferred_order_id:
            receipt.inferred_order_id = inferred_order_id
            receipt.save(using="gsharedb")

        ReceiptChatMessage.objects.create(
            receipt_id=receipt.id,
            role="assistant",
            content=ai_reply,
        )
    except Exception as e:
        ReceiptChatMessage.objects.create(
            receipt_id=receipt.id,
            role="assistant",
            content=f"Sorry, I couldn't match this receipt to an order: {e}",
        )

    return redirect("receipt_detail", rid=receipt.id)


@login_required
@require_POST
def receipt_chat_view(request, rid: int):
    receipt = get_object_or_404(Receipt.objects.using("gsharedb"), pk=rid)
    user_message = (request.POST.get("message") or "").strip()

    if not user_message:
        # For normal POST, just redirect back
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": "Empty message"}, status=400)
        return redirect("receipt_detail", rid=receipt.id)

    # Load prior chat (user + assistant) from default DB
    history_qs = ReceiptChatMessage.objects.filter(
        receipt_id=receipt.id
    ).order_by("created_at", "id")

    history = [
        (m.role, m.content)
        for m in history_qs
        if m.role in ("user", "assistant")
    ]

    # Save user message
    ReceiptChatMessage.objects.create(
        receipt_id=receipt.id,
        role="user",
        content=user_message,
    )

    # Call Gemini (which also updates lines)
    try:
        reply_text = chat_about_receipt(
            receipt,
            history + [("user", user_message)],
            user_message,
        )
    except Exception as e:
        reply_text = f"Sorry, I had an error talking to the AI: {e}"

    # Save assistant reply
    ReceiptChatMessage.objects.create(
        receipt_id=receipt.id,
        role="assistant",
        content=reply_text,
    )

    # If this is an AJAX request, return JSON so JS can update the chat
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse(
            {
                "ok": True,
                "reply": reply_text,
            }
        )

    # Fallback: normal non-AJAX behaviour
    return redirect("receipt_detail", rid=receipt.id)

@require_POST
@login_required
def receipt_confirm_delivery_view(request, rid: int):
    receipt = get_object_or_404(
        Receipt.objects.using("gsharedb"),
        pk=rid
    )

    if not receipt.inferred_order_id:
        messages.error(request, "No matched order to confirm.")
        return redirect("receipt_detail", rid=rid)

    order_id = receipt.inferred_order_id

    # Update Deliveries table
    try:
        delivery = Deliveries.objects.using("gsharedb").get(order_id=order_id)
        delivery.status = "delivered"
        delivery.delivery_time = timezone.now()
        delivery.save(using="gsharedb")

        # Mark the receipt as done
        receipt.status = "done"
        receipt.save(using="gsharedb")

        # Add chat confirmation
        ReceiptChatMessage.objects.create(
            receipt_id=receipt.id,
            role="assistant",
            content=f"Great! I've marked order #{order_id} as delivered."
        )

        messages.success(request, "Delivery confirmed!")
    except Deliveries.DoesNotExist:
        messages.error(request, "Could not find a delivery entry for this order.")

    return redirect("receipt_detail", rid=rid)

@login_required
def userprofile(request):
    user_email = request.user.email
    if not user_email:
        messages.error(request, 'No email associated with your account')
        return redirect('home')

    profile = get_user("email", user_email)  # your existing helper
    getItemNamesForUser(profile, ['cart'])
    if not profile:
        messages.error(request, 'User profile not found')
        return redirect('login')

    errors = []

    if request.method == 'POST':

        if 'save_description' in request.POST:
            if 'description' in request.POST:
                profile.description = request.POST.get('description', '').strip()
            profile.save(using='gsharedb')
            messages.success(request, 'About Me updated!')
            return redirect('profile')

        if 'save_profile' in request.POST:
            try:
                with transaction.atomic(using='gsharedb'):
                    # ---- Profile fields ----
                    if 'name' in request.POST:
                        profile.name = request.POST['name']

                    if 'email' in request.POST and request.POST['email'] != profile.email:
                        if Users.objects.using('gsharedb').filter(email=request.POST['email']).exists():
                            errors.append({'message': 'This email is already in use', 'is_success': False})
                        else:
                            profile.email = request.POST['email']

                    if 'phone' in request.POST:
                        profile.phone = request.POST['phone']

                    if 'address' in request.POST:
                        address = request.POST['address']
                        profile.address = address
                        lat, lng = geoLoc(address)  # your existing geocoder
                        profile.latitude = lat
                        profile.longitude = lng

                    # Save the base profile first
                    profile.save(using='gsharedb')

                    # ---- Avatar upload via helper ----
                    uploaded = request.FILES.get('profile_picture')
                    if uploaded:
                        res = upload_user_avatar_helper(profile.id, uploaded)
                        if not res.get("ok"):
                            raise RuntimeError("Avatar upload failed")

                # Sync Django auth email if changed
                if 'email' in request.POST and request.user.email != request.POST['email']:
                    request.user.email = request.POST['email']
                    request.user.save()

                messages.success(request, 'Profile updated successfully!')
                return redirect('profile')

            except Exception as e:
                errors.append({'message': f'Error updating profile: {str(e)}', 'is_success': False})

        elif 'change_password' in request.POST:
            currentPassword = request.POST.get('current_password', '')
            newPassword1 = request.POST.get('new_password1', '')
            newPassword2 = request.POST.get('new_password2', '')
            isValid, errorMessage = validatePasswordChange(request, currentPassword, newPassword1, newPassword2)
            if isValid:
                handlePasswordChange(request, newPassword1)
                messages.success(request, 'Your password was successfully updated!')
            else:
                messages.error(request, errorMessage)
            return redirect('profile')

    # ----- Ratings (unchanged) -----
    Feedback, avg_rating = get_user_ratings(profile.id)
    review_count = Feedback.count() if Feedback else 0
    avg = float(avg_rating or 0)
    stars_full = max(0, min(5, int(round(avg))))
    stars_text = '★' * stars_full + '☆' * (5 - stars_full)

    # ----- Presigned URL for avatar via helper -----
    avatar_url = get_user_avatar_url_helper(profile.id)

    return render(request, "profile.html", {
        'user': profile,
        'errors': errors,
        'request': request,
        'auth_user': request.user,
        'avg_rating': avg,
        'review_count': review_count,
        'stars_text': stars_text,
        'avatar_url': avatar_url,
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
    
    active_store_id = (
        request.session.get('kroger_store_id')  
        or request.session.get('active_store_id') 
    )

    active_store = None
    if active_store_id:
        active_store = Stores.objects.using('gsharedb').filter(id=active_store_id).first()

    # Get or create cart
    order = Orders.objects.using('gsharedb').filter(user=profile, status='cart').first()
    if not order:
        order = Orders.objects.using('gsharedb').create(
            user=profile,
            status='cart',
            order_date=timezone.now(),
            store=active_store,                  
            total_amount=0,
            delivery_address=profile.address,
        )
    else:
        if active_store and (order.store_id != active_store.id):
            order.store = active_store
            order.save(using='gsharedb', update_fields=['store'])


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
    search_query = request.GET.get('Item_Search_Bar', '').strip()
    zip_code = (request.GET.get('zip_code') or '').strip()

    items = Items.objects.using('gsharedb').all()
    if store_filter and store_filter != 'All' and store_filter != 'Kroger':
        items = items.filter(store__name=store_filter)
    if price_filter and price_filter != 'Any':
        if price_filter == '100+':
            items = items.filter(price__gte=100)
        else:
            lo, hi = map(float, price_filter.split('-'))
            items = items.filter(price__gte=lo, price__lte=hi)
    if search_query:
        items = items.filter(name__icontains=search_query)

    context = {
        'store_filter': store_filter,
        'price_filter': price_filter,
        'search_query': search_query,
        'zip_code': zip_code,
    }

    paginator = Paginator(items, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    context['page_obj'] = page_obj

    if store_filter == 'Kroger':
        context['using_kroger'] = True
        if zip_code and search_query:
            try:
                locations = kroger_api.find_kroger_locations_by_zip(zip_code)
                if locations:
                    loc = locations[0]
                    loc_id = loc['locationId']
                    store = upsert_kroger_store_from_location(loc)
                    context['kroger_store'] = store
                    request.session['kroger_store_id'] = store.id
                    context['kroger_products'] = kroger_api.search_kroger_products(
                        loc_id, search_query
                    )
                else:
                    messages.error(request, f"No Kroger-owned stores found near {zip_code}.")
            except Exception as e:
                print("Kroger search error:", e)
                messages.error(request, "Kroger search failed.")
        else:
            messages.info(request, "Enter a zip code and a search term for Kroger search.")

    return render(request, "cart.html", context)
    # store_filter = request.GET.get('Stores', 'All')
    # print("Store filter:", store_filter)
    # price_filter = request.GET.get('Price-Range', 'Any')
    # search_query = request.GET.get('Item_Search_Bar', '')
    
    # sf_override = request.GET.get('store_filter')
    # if sf_override:
    #     store_filter = sf_override

    # items = Items.objects.using('gsharedb').all()
    
    # if store_filter == 'Kroger':
    #     items = items.filter(store__name='Kroger')

    # if store_filter and store_filter != 'All':
    #     items = items.filter(store__name=store_filter)

    # if price_filter and price_filter != 'Any':
    #     if price_filter == '100+':
    #         items = items.filter(price__gte=100)
    #     else:
    #         low, high = map(float, price_filter.split('-'))
    #         items = items.filter(price__gte=low, price__lte=high)

    # print("Initial items count:", items.count())

    # if search_query:
    #     items = items.filter(name__icontains=search_query)
        
    # # if store_filter == 'Kroger':
    # #     context = {
    # #         'store_filter': store_filter,
    # #         'price_filter': price_filter,
    # #         'search_query': search_query,
    # #     }
        
    # #     context['saved_kroger_items'] = Items.objects.using('gsharedb') \
    # #         .filter(store__name='Kroger').order_by('name')
            
    # #     zip_code = (request.GET.get('zip_code') or '').strip()
    # #     term = (request.GET.get('search_term') or '').strip()
    # #     context['zip_code'] = zip_code
    # #     context['search_term'] = term

    # #     if zip_code and term:
    # #         try:
    # #             locations = kroger_api.find_kroger_locations_by_zip(zip_code)
    # #             if locations:
    # #                 loc_id = locations[0]['locationId']
    # #                 context['kroger_products'] = kroger_api.search_kroger_products(loc_id, term)
    # #             else:
    # #                 messages.error(request, f"No stores found for {zip_code}.")
    # #         except Exception:
    # #             messages.error(request, "Kroger search failed.")
    # #     else:
    # #         messages.info(request, "Enter a zip code and search term to find Kroger products.")
            
            
    # #     context['filtered_items'] = items
    # #     return render(request, "cart.html", context)
        
    # paginator = Paginator(items, 15)  # Show 10 items per page
    # page_number = request.GET.get('page')
    # page_obj = paginator.get_page(page_number)

    # context = {
    #     'page_obj': page_obj,
    #     'store_filter': store_filter,
    #     'price_filter': price_filter,
    #     'search_query': search_query,
    # }
    
    # return render(request, "cart.html", context)

    # # profile = get_custom_user(request)
    # # try:
    # #     order = Order.objects.get(user=profile, status='cart')
    # #     items = order.order_items.select_related('item__store')
    # # except Order.DoesNotExist:
    # #     order = None
    # #     items = []
    # # return render(request, "cart.html", {
    # #     'active_cart': order,
    # #     'cart_items': items,
    # #     'custom_user': profile,
    # # })
def upsert_kroger_store_from_location(loc):
    addr = loc.get("address", {}) or {}

    street = addr.get("addressLine1")
    city   = addr.get("city")
    state  = addr.get("state")
    postal = addr.get("zipCode")
    country = addr.get("countryCode", "US")

    store_name = loc.get("name") or "Kroger"

    parts = [p for p in [street, city, state, postal, country] if p]
    full_location = ", ".join(parts) if parts else None
    
    geo = loc.get("geolocation") or {}
    lat = geo.get("latitude")
    lng = geo.get("longitude")

    defaults = {
        "name": store_name,     
        "street": street,
        "city": city,
        "state": state,
        "postal_code": postal,
        "country": country,
        "location": full_location,
        
        "latitude": lat,
        "longitude": lng,
    }

    store, created = Stores.objects.using('gsharedb').update_or_create(
        street=street,
        postal_code=postal,
        defaults=defaults,
    )

    return store

    
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

    store_id = request.session.get('kroger_store_id')
    if not store_id:
        messages.error(request, "No Kroger store selected. Please run a Kroger search again.")
        return redirect('cart')

    kroger_store = Stores.objects.using('gsharedb').filter(id=store_id).first()
    if kroger_store is None:
        messages.error(request, "Selected Kroger store not found. Please search again.")
        return redirect('cart')

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

    store_id = request.session.get('kroger_store_id')
    if not store_id:
        messages.error(request, "No Kroger store selected. Please run a Kroger search again.")
        return redirect('cart')

    kroger_store = Stores.objects.using('gsharedb').filter(id=store_id).first()
    if kroger_store is None:
        messages.error(request, "Selected Kroger store not found. Please search again.")
        return redirect('cart')

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
            defaults={'price': price_dec, 'stock': 0, 'description': name}
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
    print(f"drive info: {drive_info}")
    if not drive_info:
        return None
    
    time_to_store = drive_info["duration_value"] / 60
    round_trip = time_to_store * 2
    
    shopping_time = num_items * 1.5
    
    total_time = round_trip + shopping_time
    print(f"total time: {total_time}")
    
    variation = random.uniform(0.9, 1.1)
    total_time *= variation
    
    
    return {
        'distance': drive_info["distance_text"],
        'drive_time': round(round_trip, 1),
        'shopping_time': round(shopping_time, 1),
        'total_estimate': round(total_time, 1),
    }
    
def drive_time(user_address, store_address, api_key):
    print(f"api info: {api_key}")
    url = f"https://maps.googleapis.com/maps/api/distancematrix/json?origins={user_address}&destinations={store_address}&key={api_key}"
    
    print(f"user_address info: {user_address}")

    print(f"store_address info: {store_address}")
    response = requests.get(url)
    data = response.json()
    print(f"data info: {data}")
    
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
    
    # stores = Stores.objects.all()
    # user_address = user.address
    
    info = {}
    
    viewer = get_user("email", request.user.email)
    
    oiv = orders_in_viewport(min_lat, min_lng, max_lat, max_lng, viewer=viewer)
    
    for order in oiv:
        address = order['delivery_address']
        if not address:
            continue
        
        user = get_user("id", order['user']['id'])
        
        delivery = Deliveries.objects.using('gsharedb').filter(order_id=order['order_id']) \
            .select_related('delivery_person').first()

        driver_id = delivery.delivery_person.id if delivery and delivery.delivery_person else None
        driver_name = delivery.delivery_person.name if delivery and delivery.delivery_person else None
        
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
            'user': user.name,
            'user_id': user.id,
            
            #store info
            'store_id': order.get('store_id'),
            'store_name': order.get('store_name', ''),
            'store_address': order.get('store_address', ''),
            'store_lat': order.get('store_lat'),
            'store_lng': order.get('store_lng'),
            
            'status': order['status'],        
            'driver_id': driver_id,           
            'driver_name': driver_name,
        }

        # Group by user name (or user.id if you prefer)
        if address not in info:
            info[address] = []
        info[address].append(order_data)

    user_name = get_user("email", request.user.email).name

    print("username:", user_name)

    # Convert to grouped list format for easy JSON use
    grouped_info = [
        {'address': addr, 'orders': orders}
        for addr, orders in info.items()
    ]
    
    response = JsonResponse(grouped_info, safe=False)
    response['X-User-Name'] = str(user_name)
    return response


def people_data(request, min_lat, min_lng, max_lat, max_lng):
    min_lat = float(min_lat)
    min_lng = float(min_lng)
    max_lat = float(max_lat)
    max_lng = float(max_lng)
    
    # users = _users_in_viewport(min_lat, min_lng, max_lat, max_lng, limit=500)
        
    orders = orders_in_viewport(min_lat, min_lng, max_lat, max_lng, limit=20)
    
    people_info = []

    for order in orders:
        address = order.get('delivery_address')
        if not address:
            continue

        user_info = order.get('user')
        if not user_info:
            continue

        # Optional: load extra user data if needed
        # user = get_user("id", user_info['id'])

        items = get_order_items_by_order_id(order['order_id'])
        
        #  maybe find the distance between user and order owner here

        subtotal = 0
        total_items = 0
        for item in items:
            # assuming item format: (id, order_id, quantity, ..., price, ...)
            quantity = float(item[2])
            price = float(item[5])
            subtotal += quantity * price
            total_items += int(quantity)

        # attach full order data to each person
        order_data = {
            'order_id': order['order_id'],
            'item_total': total_items,
            'subtotal': round(subtotal, 2),
        }

        person_entry = {
            'id': user_info['id'],
            'name': user_info['name'],
            'address': address,
            'latitude': user_info.get('latitude'),
            'longitude': user_info.get('longitude'),
            'order_data': order_data,
        }

        people_info.append(person_entry)

    return JsonResponse({'people': people_info}, safe=False)

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
        'user_lat': float(user.latitude) if user.latitude is not None else None,
        'user_lng': float(user.longitude) if user.longitude is not None else None,
        'viewer_id': user.id,
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
        
        tax = Decimal(calculate_tax(subtotal)) / 100
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
    
def pending_orders(request):
    print("pending orders")
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    profile = get_user("email", request.user.email)
    if not profile:
        print("no user")
        return JsonResponse({'error': 'Profile not found'}, status=404)

    orders = get_orders(profile, 'pending')
    print(orders)
    if not orders:
        return JsonResponse({'items': [], 'order': {'subtotal': 0, 'tax': 0, 'total': 0}, 'id': None})

    order_list = []
    for order in orders:
        items = get_order_items(order) if order else []
        order_id = order.id
        delivery = get_delivery_for_order(order)
        print("delivery:", delivery)
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
        
        tax = Decimal(calculate_tax(subtotal)) / 100
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
            'delivery_person': delivery.delivery_person.name if delivery and delivery.delivery_person else None,
            'delivery_person_id': delivery.delivery_person.id if delivery and delivery.delivery_person else None,
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

    my_orders = get_orders(profile, 'inprogress')
    print(my_orders)
    if not my_orders:
        return JsonResponse({'items': [], 'order': {'subtotal': 0, 'tax': 0, 'total': 0}, 'id': None})

    my_order_list = []
    
    for order in my_orders:
        items = get_order_items(order) if order else []
        if (items.__len__() == 0):
            continue
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
        
        tax = Decimal(calculate_tax(subtotal)) / 100
        grand_total = round(subtotal + tax, 2)

        order_summary = {
            'subtotal': subtotal,
            'tax': tax,
            'total': grand_total,
        }
        my_order_list.append({
            'id': order.id,
            'summary': order_summary,
            'items': items_with_totals,
        })
        
    my_deliveries = []
    deliveries = get_orders_by_delivery_person(profile, 'inprogress')
    print("deliveries:", deliveries)
        
    for order in deliveries:
        print("order:", order)
        items = get_order_items(order) if order else []
        if(items.__len__() == 0):
            continue
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
        
        tax = Decimal(calculate_tax(subtotal)) / 100
        grand_total = round(subtotal + tax, 2)

        order_summary = {
            'subtotal': subtotal,
            'tax': tax,
            'total': grand_total,
        }
        my_deliveries.append({
            'id': order.id,
            'summary': order_summary,
            'items': items_with_totals,
        })
    print(my_deliveries)
    return JsonResponse({
        'orders': my_order_list,
        'deliveries': my_deliveries
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
            
        tax = Decimal(calculate_tax(subtotal)) / 100
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
        
    tax = Decimal(calculate_tax(subtotal))/100  # Convert to dollars for display
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
            
        tax = Decimal(calculate_tax(subtotal))/100  # Convert to dollars for display
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
        
    tax = Decimal(calculate_tax(subtotal)) / 100  # Convert to dollars for display
    grand_total = round(subtotal + tax, 2)

    order_summary = {
        'subtotal': subtotal,
        'tax': tax,
        'total': grand_total,
    }

    return render(request, "shoppingcart.html", {
        'items': items_with_totals,
        'order': order_summary,
        'id': order[0].id if order else None,

    })

@login_required
def myorders(request):
    user = get_user("email", request.user.email)
    all_orders = []
    orders_delivered = get_orders(user, "delivered")
    
    all_orders.extend(orders_delivered)

    
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
    
    context = {
        'user': reviewee,
        'userReviews': userReviews[0],
    }

    if request.method == 'POST':
        reviewText = (request.POST.get('review') or '').strip()
        try:
            # Ensure rating is within valid range (1-5)
            reviewRating = int(request.POST.get('rating') or 1)
            reviewRating = max(1, min(5, reviewRating))
        except (TypeError, ValueError):
            reviewRating = 1
        
        subjectText = (request.POST.get("title") or '').strip()

        add_feedback(reviewee, authUser, reviewText, subjectText, reviewRating)
        messages.success(request, "Review posted.")
        context['userReviews'] = get_user_ratings(userID)[0]
        return render(request, 'aboutUserPage.html', context=context)

    return render(request, 'aboutUserPage.html', context=context)


@login_required
def payments(request, order_id):
    user = get_user("email", request.user.email)
    order = get_object_or_404(Orders.objects.using('gsharedb'), pk=order_id)
    print("order here: " + str(order))
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
            tax = Decimal(calculate_tax(subtotal)) / 100  # Convert to dollars for display
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
            tax = Decimal(calculate_tax(subtotal)) / 100  # Convert to dollars for display
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
        'orderID': order_id,
    }
    return render(request, "paymentsPage.html", context)

@login_required
def paymentsCheckout(request, order_id):
    # change order status to placed upon successful payment
    
    orders = []
    order = get_object_or_404(Orders.objects.using('gsharedb'), pk=order_id)
    delivery = get_delivery_for_order(order)
    dperson = delivery.delivery_person
    userAddress = dperson.address

    dropOffLocation = order.delivery_address

    orderSize = len(get_order_items(order))

    store = "455 S 500 E SLC UT"
    # api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    from django.conf import settings

    api_key = settings.GOOGLE_MAPS_API_KEY
    deliveryCost = pickup_price(userAddress, dropOffLocation, orderSize, store, api_key)["total_cost"]
    print(f'delivery cost: {deliveryCost}')
    try:
        stripe.api_key = settings.STRIPE_SECRET_KEY
        profile = get_user("email", request.user.email)
        try:
            
            orders.append(order)
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
            # oi: (order_id, item_id, quantity, price, name, item_price, store_id)
            itemObjects.append({
                'price_data': {
                    'currency': 'usd',
                    'product_data': {'name': oi[4]},
                    'unit_amount': int(oi[3] * 100),  # cents
                },
                'quantity': oi[2],
            })

        # BELOW IS fallback for tax: add a manual 7% tax line (when not using Automatic Tax)
        subtotal_cents = int(sum(oi[2] * oi[3] for oi in orderItems) * 100)
        tax_cents = int(round(subtotal_cents * Decimal('0.07')))
        if tax_cents > 0:
            itemObjects.append({
                'price_data': {
                    'currency': 'usd',
                    'product_data': {'name': 'Sales Tax'},
                    'unit_amount': tax_cents,
                },
                'quantity': 1,
            })

        subtotal_cents = int(deliveryCost * 100)
        print(f"subtotal cents: {subtotal_cents}")
        del_cents = int(round(subtotal_cents * Decimal('0.07')))
        print(f"del_cents cents: {del_cents}")

        itemObjects.append({
                'price_data': {
                    'currency': 'usd',
                    'product_data': {'name': 'Delivery Fees'},
                    'unit_amount': subtotal_cents,
                },
                'quantity': 1,
            })

         # ABOVE IS fallback for tax: add a manual 7% tax line (when not using Automatic Tax)

        # Replace the old Session.create(...) with Automatic Tax + address collection
        checkoutSession = stripe.checkout.Session.create(
            line_items=itemObjects,
            mode='payment',

            ## KEEP BELOW ONLY ONCE PAYMENTS ARE IN LIVE MODE, THIS WONT WORK IN TEST MODE
            #automatic_tax={'enabled': True},  # Enable Stripe Automatic Tax
            #billing_address_collection='required',  # collect billing address
            #shipping_address_collection={'allowed_countries': ['US']},  # collect shipping address
            ## KEEP ABOVE ONLY ONCE PAYMENTS ARE IN LIVE MODE, THIS WONT WORK IN TEST MODE
            customer_email=request.user.email,  # helps with tax and receipts
            
            success_url=f"https://www.gshare.me/payment_success/{order.id}/", # should do this if success: # consider moving this to webhook on payment success
            cancel_url=f"https://www.gshare.me/payments/{order.id}",
        )
        print("Session URL: " + checkoutSession.url)
        return redirect(checkoutSession.url)
    except stripe.error.StripeError as e:
        messages.error(request, f"Payment error: {e.user_message}")
        messages.error(request, f"Payment error: {e.user_message}")
        return redirect('payments', order_id=order.id)
    except Exception as e:
        print(f"A serious error occurred: {e}. We have been notified.")
        messages.error(request, "An unexpected error occurred. Please try again.")
        return redirect('payments', order_id=order.id)  # Added return



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
    
    context = {
        'user': reviewee,
        'userReviews': userReviews[0],
    }

    context['avg_rating'] = userReviews[1]
    if userReviews[0] == None:
        context['review_count'] = 0
    else:
        context['review_count'] = userReviews[0].count

    if request.method == 'POST':
        reviewText = (request.POST.get('review') or '').strip()
        try:
            # Ensure rating is within valid range (1-5)
            reviewRating = int(request.POST.get('rating') or 1)
            reviewRating = max(1, min(5, reviewRating))
        except (TypeError, ValueError):
            reviewRating = 1
        
        subjectText = (request.POST.get("title") or '').strip()

        add_feedback(reviewee, authUser, reviewText, subjectText, reviewRating)
        userReviews = get_user_ratings(userID)
        messages.success(request, "Review posted.")
        context['userReviews'] = userReviews[0]
        print(get_user_ratings(userID)[0])
        context['avg_rating'] = userReviews[1]
        if userReviews[0] == None:
            context['review_count'] = 0
        else:
            context['review_count'] = userReviews[0].count
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

def payment_success(request, order_id):
    change_order_status(order_id, "delivered") 
    return redirect("order_history")

@login_required
def group_map(request, slug):
    group = get_object_or_404(ChatGroup, slug=slug)
    return render(request, 'maps.html', {
        'group': group,
        'google_maps_api_key': settings.GOOGLE_MAPS_API_KEY,
        'user_address': getattr(getattr(request.user, 'profile', None), 'address', ''),
    })
    
@login_required
def join_group(request, slug):
    group = get_object_or_404(ChatGroup, slug=slug)
    group.members.add(request.user)
    return redirect('group_map', slug=slug)

# Voice Orders
def getItemNamesForUser(user, statuses = ['delivered']):
    namesAndId = []
    for status in statuses:
        orders = get_orders(user, status)
        for order in orders:
            items = get_order_items(order)
            for row in items:
                namesAndId.append((row[4], row[1]))

    return namesAndId


def apply_voice_cart_items(profile, cart):
    if not profile:
        return {"success": False, "error": "Profile not found"}

    items = cart.get("items") or []
    if not isinstance(items, list) or not items:
        return {"success": False, "error": "Sorry, I have not been able deduce any items from your requests so far."}

    order = None
    total = 0

    with transaction.atomic(using='gsharedb'):
        with connections['gsharedb'].cursor() as cur:
            for entry in items:
                if not isinstance(entry, dict):
                    continue
                item_id = entry.get("ID") or entry.get("id")
                quantity = entry.get("quantity") or 1
                try:
                    quantity = int(quantity)
                except (TypeError, ValueError):
                    quantity = 1
                if not item_id or quantity <= 0:
                    continue
                try:
                    item = Items.objects.using('gsharedb').get(id=item_id)
                except Items.DoesNotExist:
                    continue

                if order is None:
                    order = Orders.objects.using('gsharedb').filter(user=profile, status='cart').first()
                    if not order:
                        order = Orders.objects.using('gsharedb').create(
                            user=profile,
                            status='cart',
                            order_date=timezone.now(),
                            store=item.store,
                            total_amount=0,
                            delivery_address=profile.address,
                        )

                cur.execute(
                    """
                    INSERT INTO order_items (order_id, item_id, quantity, price)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        quantity = quantity + VALUES(quantity)
                    """,
                    [order.id, item.id, quantity, str(item.price or 0)],
                )

            if order is None:
                return {"success": False, "error": "No valid items to add"}

            cur.execute(
                """
                SELECT COALESCE(SUM(quantity * price), 0)
                FROM order_items
                WHERE order_id = %s
                """,
                [order.id],
            )
            total = cur.fetchone()[0] or 0

    order.total_amount = total
    order.order_date = timezone.now()
    order.save(using='gsharedb')

    return {"success": True, "order_id": order.id}


@login_required
def voice_order_chat(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
    messages = payload.get("messages") or []
    mode = (payload.get("mode") or "chat").strip()
    if not messages:
        return JsonResponse({"success": False, "error": "No messages provided"}, status=400)
    if mode == "finalize" and len(messages) > 12:
        messages = messages[-12:]

    user = get_user("email", request.user.email)
    userPastItems = getItemNamesForUser(user, ["delivered"])
    allItems = getAllItemsFromDatabase()
    context_lines = []
    if userPastItems:
        context_lines.append("User past items (name and ID):")
        for name, item_id in userPastItems:
            context_lines.append(f"- {name} (ID: {item_id})")
    if allItems:
        context_lines.append("")
        context_lines.append("Store items (name, store, price, ID):")
        for name, item_id, store_name, price in allItems:
            price_display = str(price) if price is not None else ""
            context_lines.append(f"- {name} | {store_name} | price: {price_display} | ID: {item_id}")
    context_suffix = "\n\n" + "\n".join(context_lines) if context_lines else ""

    if mode == "finalize":
        final_messages = []
        if context_lines:
            context_text = "Use these item lists to match items by name to IDs, stores, and prices when constructing your JSON cart. Always copy the item names exactly as written when you fill in the JSON." + context_suffix
            final_messages.append({"role": "system", "content": context_text})
        convo_lines = []
        for m in messages:
            role = m["role"]
            content = m["content"] or ""
            if role == "user":
                prefix = "User"
            elif role == "assistant":
                prefix = "Assistant"
            else:
                prefix = "Other"
            convo_lines.append(f"{prefix}: {content}")
        convo_text = "\n".join(convo_lines)
        final_messages.append({
            "role": "user",
            "content": (
                "Here is the recent conversation between the customer (User) and the assistant (Assistant) about their grocery order:\n\n"
                + convo_text
                + "\n\nUse the item lists provided earlier in this chat (user past items and store items) to choose exact items and IDs. "
                + "Now respond with ONLY one JSON object in the exact format described in the system message. "
                + "Do not include any explanation, comments, or text before or after the JSON. If you include anything other than the JSON object, it will break."
            ),
        })
        resp = call_groq(
            messages=final_messages,
            temperature=0.2,
            stream=False,
            system_instructions=VOICE_ORDER_FINALIZE_INSTRUCTIONS,
        )
        data = resp.json()
        raw = data["choices"][0]["message"]["content"].strip()
        if not raw:
            return JsonResponse({"success": False, "error": "Empty response from AI"}, status=502)
        try:
            cart_json = json.loads(raw)
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "error": "AI did not return valid JSON", "raw": raw}, status=502)

        apply_result = apply_voice_cart_items(user, cart_json)
        if not apply_result.get("success"):
            return JsonResponse(
                {
                    "success": False,
                    "error": apply_result.get("error") or "Could not add items to your cart",
                    "assistant": raw,
                    "cart": cart_json,
                },
                status=400,
            )

        return JsonResponse(
            {
                "success": True,
                "assistant": raw,
                "cart": cart_json,
                "order_id": apply_result.get("order_id"),
            }
        )

    if context_lines:
        context_text = "Use the following item lists when referring to items. Always copy the item name exactly as written when you write 'Selected option' or 'Other options'." + context_suffix
        messages.insert(0, {"role": "system", "content": context_text})
    resp = call_groq(
        messages=messages,
        temperature=0.6,
        stream=False,
        system_instructions=VOICE_ORDER_CHAT_INSTRUCTIONS,
    )
    data = resp.json()
    assistant_msg = data["choices"][0]["message"]["content"].strip()
    if not assistant_msg:
        return JsonResponse({"success": False, "error": "Empty response from AI"}, status=502)
    return JsonResponse({"success": True, "assistant": assistant_msg})

def getAllItemsFromDatabase():
    items_qs = Items.objects.using('gsharedb').select_related('store').values(
        'id', 'name', 'store__name', 'price'
    )

    result = []
    for row in items_qs:
        result.append((row.get('name'), row.get('id'), row.get('store__name'), row.get('price')))

    return result
