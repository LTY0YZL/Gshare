from django.db import models

class User(models.Model):
    id = models.AutoField(primary_key=True)
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
        managed = False
        db_table = 'users'

    def __str__(self):
        return self.name


class Store(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    location = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'stores'

    def __str__(self):
        return self.name


class Item(models.Model):
    id = models.AutoField(primary_key=True)
    store = models.ForeignKey(Store, models.DO_NOTHING, db_column='store_id')
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.IntegerField()

    class Meta:
        managed = False
        db_table = 'items'

    def __str__(self):
        return self.name


class Order(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, models.DO_NOTHING, db_column='user_id')
    store = models.ForeignKey(Store, models.DO_NOTHING, db_column='store_id')
    order_date = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=50, blank=True, null=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    delivery_address = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'orders'

    def __str__(self):
        return f"Order {self.id}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, models.DO_NOTHING, db_column='order_id', primary_key=True)
    item = models.ForeignKey(Item, models.DO_NOTHING, db_column='item_id')
    quantity = models.IntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        managed = False
        db_table = 'order_items'
        unique_together = (('order', 'item'),)

    def __str__(self):
        return f"{self.quantity} x {self.item.name}"


class Delivery(models.Model):
    id = models.AutoField(primary_key=True)
    order = models.ForeignKey(Order, models.DO_NOTHING, db_column='order_id')
    delivery_person = models.ForeignKey(
        User,
        models.DO_NOTHING,
        db_column='delivery_person_id',
        blank=True,
        null=True
    )
    status = models.CharField(max_length=50, blank=True, null=True)
    pickup_time = models.DateTimeField(blank=True, null=True)
    delivery_time = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'deliveries'

    def __str__(self):
        return f"Delivery for Order {self.order.id}"


class Feedback(models.Model):
    reviewee = models.ForeignKey(
        User,
        models.DO_NOTHING,
        db_column='reviewee_id',
        related_name='feedback_received'
    )
    reviewer = models.ForeignKey(
        User,
        models.DO_NOTHING,
        db_column='reviewer_id',
        related_name='feedback_given'
    )
    feedback = models.CharField(max_length=255)
    order = models.OneToOneField(
        Order,
        models.DO_NOTHING,
        db_column='order_id',
        primary_key=True,
        related_name='feedback'
    )

    class Meta:
        managed = False
        db_table = 'FEEDBACK'
        unique_together = (('reviewee', 'reviewer'),)

    def __str__(self):
        return f"Feedback for Order {self.order.id}"