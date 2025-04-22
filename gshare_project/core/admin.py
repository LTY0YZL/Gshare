from django.contrib import admin
from .models import Users, Stores, Items, Orders, Feedback, OrderItems, Deliveries

admin.site.register(Users)
admin.site.register(Stores)
admin.site.register(Items)
admin.site.register(Orders)
admin.site.register(Feedback)
admin.site.register(OrderItems)
admin.site.register(Deliveries)
