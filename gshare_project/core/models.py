from django.db import models

class User(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(max_length=100, unique=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    address = models.CharField(max_length=255, blank=True, null=True)
    user_type = models.CharField(
        max_length=10,
        choices=[
            ('customer', 'Customer'),
            ('delivery', 'Delivery'),
            ('both', 'Both'),
        ]
    )

    class Meta:
        managed = True
        db_table = 'users'

    def __str__(self):
        return self.name


class Store(models.Model):
    name     = models.CharField(max_length=100)
    location = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'stores'

    def __str__(self):
        return self.name


class Item(models.Model):
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name='items'
    )
    name  = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.IntegerField()

    class Meta:
        managed = True
        db_table = 'items'

    def __str__(self):
        return f"{self.name} ({self.store.name})"


class Order(models.Model):
    user = models.ForeignKey(
                User,
                on_delete=models.SET_NULL,
                null=True,
                blank=True,
                related_name='orders_placed'
            )
    store = models.ForeignKey(
                Store,
                on_delete=models.SET_NULL,
                null=True,
                blank=True,
                related_name='orders_at_store'
            )
    order_date = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
                max_length=50,
                choices=[
                    ('cart', 'In Cart'),
                    ('pending', 'Pending Pickup'),
                    ('delivering', 'Out for Delivery'),
                    ('completed', 'Completed'),
                    ('cancelled', 'Cancelled'),
                    ],
                    default='cart',
                )
    total_amount = models.DecimalField(
                        max_digits=10,
                        decimal_places=2,
                        blank=True,
                        null=True
                    )
    delivery_address = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'orders'
        ordering = ['-order_date']

    def __str__(self):
        uname = self.user.name if self.user else "Unknown"
        return f"Order {self.id} by {uname}"


class OrderItem(models.Model):
    order = models.ForeignKey(
                Order,
                on_delete=models.CASCADE,
                related_name='order_items'
            )
    item = models.ForeignKey(
                Item,
                on_delete=models.PROTECT,
                related_name='item_order_entries'
            )
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        managed = True
        db_table = 'order_items'
        unique_together = (('order', 'item'),)

    def __str__(self):
        return f"{self.quantity} x {self.item.name} (Order {self.order.id})"


class Delivery(models.Model):
    order = models.OneToOneField(
                Order,
                on_delete=models.CASCADE,
                related_name='delivery'
            )
    delivery_person = models.ForeignKey(
                        User,
                        on_delete=models.SET_NULL,
                        null=True,
                        blank=True,
                        limit_choices_to={'user_type__in': ['delivery', 'both']},
                        related_name='deliveries_assigned'
                    )
    status = models.CharField(max_length=50, blank=True, null=True)
    pickup_time = models.DateTimeField(blank=True, null=True)
    delivery_time = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'deliveries'

    def __str__(self):
        return f"Delivery for Order {self.order.id}"


class Feedback(models.Model):
    order = models.OneToOneField(
                Order,
                on_delete=models.CASCADE,
                primary_key=True,
                related_name='feedback'
            )
    reviewee = models.ForeignKey(
                User,
                on_delete=models.CASCADE,
                related_name='feedback_received'
            )
    reviewer = models.ForeignKey(
                User,
                on_delete=models.CASCADE,
                related_name='feedback_given'
            )
    feedback = models.TextField()
    rating = models.PositiveSmallIntegerField(
                choices=[(i, str(i)) for i in range(1, 6)],
                blank=True,
                null=True
            )

    class Meta:
        managed = True
        db_table = 'feedback'

    def __str__(self):
        return f"Feedback for Order {self.order.id} by {self.reviewer.name}"