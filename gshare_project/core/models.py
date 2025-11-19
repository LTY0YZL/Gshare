from django.db import models
from django.utils import timezone
from django.core.validators import MaxValueValidator, MinValueValidator
import os


class Users(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    email = models.CharField(unique=True, max_length=100, null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    address = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True) 
    latitude    = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, db_index=True)
    longitude   = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, db_index=True)
    username = models.CharField(max_length=50, db_column='usernames', unique=True)
    image_key = models.CharField(max_length=512, null=True, blank=True)
    #area_code = models.IntegerField(db_column = 'addressCode',max_length=10, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'users'

class Stores(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    street      = models.CharField(max_length=255, null=True, blank=True)
    city        = models.CharField(max_length=100, null=True, blank=True)
    state       = models.CharField(max_length=50, null=True, blank=True)
    postal_code = models.CharField(max_length=20, null=True, blank=True)
    country     = models.CharField(max_length=50, null=True, blank=True, default="US")
    location    = models.CharField(max_length=255, null=True, blank=True)
    
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'stores'

class Items(models.Model):
    id = models.AutoField(primary_key=True)
    store = models.ForeignKey('Stores', on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    stock = models.IntegerField(null=True, blank=True)
    
    description = models.TextField(null=True, blank=True)
    
    image_url = models.CharField(max_length=500, null=True, blank=True)

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
    feedback_id = models.AutoField(primary_key=True)  # new primary key
    reviewee = models.ForeignKey('Users', on_delete=models.CASCADE, db_column='reviewee_id', related_name='received_feedbacks')
    reviewer = models.ForeignKey('Users', on_delete=models.CASCADE, db_column='reviewer_id', related_name='given_feedbacks')
    feedback = models.CharField(max_length=255, db_column='Feedback', null=True, blank=True)
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    description_subject = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        managed = False   # if the table already exists in MySQL
        db_table = 'FEEDBACK'
        constraints = [
            models.UniqueConstraint(fields=['reviewee', 'reviewer'], name='reviewee_id'),
            models.CheckConstraint(check=models.Q(rating__gte=1, rating__lte=5), name='rating'),
        ]

    def __str__(self):
        return f"Feedback #{self.feedback_id} from {self.reviewer_id} to {self.reviewee_id}"

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
    # def save(self, *args, **kwargs):
    #     raise NotImplementedError("This model is read-only.")

class Deliveries(models.Model):
    id = models.AutoField(primary_key=True)
    order = models.ForeignKey('Orders', on_delete=models.CASCADE, related_name='deliveries')
    delivery_person = models.ForeignKey('Users', on_delete=models.CASCADE, null=True, blank=True)
    status = models.CharField(max_length=50, null=True, blank=True)
    pickup_time = models.DateTimeField(null=True, blank=True)
    delivery_time = models.DateTimeField(null=True, blank=True)
    
    buyer_confirmed = models.BooleanField(default=False)
    driver_confirmed = models.BooleanField(default=False)

    class Meta:
        managed = False
        db_table = 'deliveries'

class GroupOrders(models.Model):
    # matches your existing table
    group_id = models.AutoField(primary_key=True)
    description = models.TextField()
    password_hash = models.CharField(max_length=255)

    members = models.ManyToManyField(
        'Users',
        through='GroupMembers',
        related_name='groups_joined',
    )

    class Meta:
        managed = False          # set True only if Django should create/migrate it
        db_table = 'group_orders'

class GroupMembers(models.Model):
    group = models.ForeignKey(GroupOrders,on_delete=models.CASCADE,db_column='group_id',related_name='memberships',null=True,blank=True)
    user = models.ForeignKey('Users',on_delete=models.CASCADE,db_column='user_id',related_name='group_memberships',null=True,blank=True)
    order = models.ForeignKey('Orders',on_delete=models.SET_NULL,db_column='order_id',related_name='groupmembers',null=True,blank=True)

    class Meta:
        managed = False
        db_table = 'group_members'
        
class RecurringCart(models.Model):
    STATUS_CHOICES = [
        ('enabled', 'Enabled'),
        ('paused', 'Paused'),
    ]
    FREQUENCY_CHOICES = [
        ('weekly', 'Weekly'),
        ('biweekly', 'Bi-Weekly'),
        ('monthly', 'Monthly'),
    ]

    user = models.ForeignKey(Users, on_delete=models.CASCADE, related_name='recurring_carts')
    name = models.CharField(max_length=100)
    frequency = models.CharField(max_length=10, choices=FREQUENCY_CHOICES, default='weekly')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='enabled')
    next_order_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        managed = False
        db_table = 'core_recurringcart'

    def __str__(self):
        return f"{self.name} for {self.user.name}"

class RecurringCartItem(models.Model):
    recurring_cart = models.ForeignKey(RecurringCart, on_delete=models.CASCADE, related_name='items')
    item = models.ForeignKey(Items, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        managed = False
        db_table = 'core_recurringcartitem'
        
    def __str__(self):
        return f"{self.quantity} x {self.item.name}"
    

class ProductImage(models.Model):
    user = models.OneToOneField(Users,on_delete=models.CASCADE,related_name="profile_image",db_constraint=False, null=True, blank=True,  )  # set to True if you control the users table & want an FK constraint
    image = models.ImageField(upload_to="products/")        # stored in S3, path saved in DB
    file_name = models.CharField(max_length=255, blank=True) # keep original/base name
    alt_text = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Auto-fill file_name from uploaded file/key if not provided
        if self.image and not self.file_name:
            self.file_name = os.path.basename(self.image.name)
        super().save(*args, **kwargs)

    class Meta:
        managed = False
        db_table = 'core_productimage'

    def __str__(self):
        return self.file_name or self.image.name
    
class UploadedImage(models.Model):
    key = models.CharField(max_length=512, unique=True)      
    content_type = models.CharField(max_length=128, blank=True)
    original_name = models.CharField(max_length=256, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "uploaded_image"  
        managed = False
        
    def __str__(self):
        return self.key
    
class Receipt(models.Model):
    id = models.BigAutoField(primary_key=True)

    uploader = models.ForeignKey(
        'Users',
        models.DO_NOTHING,
        db_column='uploader_id',
        null=True,
        blank=True,
    )

    s3_bucket = models.CharField(max_length=128)
    s3_key = models.CharField(max_length=512)
    uploaded_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, default='pending')
    error = models.TextField(blank=True, default='')
    gemini_json = models.JSONField(null=True, blank=True)
    inferred_order_id = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = 'core_receipt'   # matches your existing table
        managed = False

    def __str__(self):
        return f"Receipt #{self.id}"


class ReceiptLine(models.Model):
    # FK to the receipt row
    receipt = models.ForeignKey(
        Receipt,
        related_name='lines',
        on_delete=models.DO_NOTHING,
        db_column='receipt_id',   # <- matches the column in receipt_line
    )
    name = models.CharField(max_length=256)
    quantity = models.FloatField(default=1)
    unit_price = models.FloatField(null=True, blank=True)
    total_price = models.FloatField(null=True, blank=True)
    meta = models.JSONField(null=True, blank=True)  # brand, code, etc.

    class Meta:
        db_table = 'core_receiptline'
        managed = False

    def __str__(self):
        return f"{self.name} (receipt {self.receipt_id})"


class ReceiptChatMessage(models.Model):
    receipt = models.ForeignKey(
        Receipt,
        related_name='chat',
        on_delete=models.DO_NOTHING,
        db_column='receipt_id',   # <- matches the column in receipt_chat_message
    )
    role = models.CharField(
        max_length=10,
        choices=[('user', 'user'), ('assistant', 'assistant'), ('system', 'system')],
    )
    content = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'core_receiptchatmessage'
        managed = False

    def __str__(self):
        return f"{self.role}: {self.content[:40]}"
