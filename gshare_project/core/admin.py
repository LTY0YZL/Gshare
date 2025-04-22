from django.contrib import admin
from .models import User, Store, Item, Order, Feedback, OrderItem, Delivery

admin.site.register(User)
admin.site.register(Store)
admin.site.register(Item)
admin.site.register(Order)
admin.site.register(Feedback)
admin.site.register(OrderItem)
admin.site.register(Delivery)
