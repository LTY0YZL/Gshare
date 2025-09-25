from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator


class Users(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    email = models.CharField(unique=True, max_length=100, null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    address = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True) 
    area_code = models.IntegerField(db_column = 'addressCode',max_length=10, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'users'

class Stores(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    location = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'stores'

class Items(models.Model):
    id = models.AutoField(primary_key=True)
    store = models.ForeignKey('Stores', on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    stock = models.IntegerField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'items'

class Orders(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey('Users', on_delete=models.DO_NOTHING)
    store = models.ForeignKey('Stores', on_delete=models.DO_NOTHING)
    order_date = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=50, null=True, blank=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    delivery_address = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'orders'

class Feedback(models.Model):
    reviewee = models.ForeignKey('Users', on_delete=models.CASCADE, null=True, blank=True)
    reviewer = models.ForeignKey('Users', on_delete=models.CASCADE, related_name='feedback_reviewer_set', null=True, blank=True)
    feedback = models.CharField(max_length=255, null=True, blank=True)
    order = models.OneToOneField('Orders', on_delete=models.CASCADE, primary_key=True)
    rating = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])

    class Meta:
        managed = False
        db_table = 'FEEDBACK'
        unique_together = (('reviewee', 'reviewer'),)

class OrderItems(models.Model):
    order = models.ForeignKey('Orders', on_delete=models.CASCADE, db_column='order_id')  # Map to order_id
    item = models.ForeignKey('Items', on_delete=models.CASCADE, db_column='item_id')    # Map to item_id
    quantity = models.IntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        managed = False  # Tell Django not to manage the database schema
        db_table = 'order_items'  # Map to the existing table
        unique_together = (('order', 'item'),)  # Enforce uniqueness on order_id and item_id

    # Explicitly define the composite primary key
    def save(self, *args, **kwargs):
        raise NotImplementedError("This model is read-only.")

class Deliveries(models.Model):
    id = models.AutoField(primary_key=True)
    order = models.ForeignKey('Orders', on_delete=models.CASCADE)
    delivery_person = models.ForeignKey('Users', on_delete=models.CASCADE, null=True, blank=True)
    status = models.CharField(max_length=50, null=True, blank=True)
    pickup_time = models.DateTimeField(null=True, blank=True)
    delivery_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'deliveries'
