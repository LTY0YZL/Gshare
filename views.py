from django.http import JsonResponse
from .models import User, Store, Item, Order, OrderItem, Delivery, Feedback

def users(request):
    users = list(User.objects.all().values())
    return JsonResponse({'users': users})

def stores(request):
    stores = list(Store.objects.all().values())
    return JsonResponse({'stores': stores})

def items(request):
    items = list(Item.objects.all().values())
    return JsonResponse({'items': items})

def orders(request):
    orders = list(Order.objects.all().values())
    return JsonResponse({'orders': orders})

def order_items(request):
    order_items = list(OrderItem.objects.all().values())
    return JsonResponse({'order_items': order_items})

def deliveries(request):
    deliveries = list(Delivery.objects.all().values())
    return JsonResponse({'deliveries': deliveries})

def feedback(request):
    feedbacks = list(Feedback.objects.all().values())
    return JsonResponse({'feedback': feedbacks})