import logging
from django.utils import timezone
from datetime import timedelta
from .models import RecurringCart, Orders, OrderItems

logger = logging.getLogger(__name__)

def create_recurring_orders():
    logger.info("--- Running Recurring Order Check ---")
    today = timezone.now().date()

    due_carts = RecurringCart.objects.using('gsharedb').filter(
        status='enabled', 
        next_order_date__lte=today
    ).prefetch_related('items__item__store', 'user')

    if not due_carts.exists():
        logger.info("No recurring carts are due today.")
        return "No carts due."

    for cart in due_carts:
        logger.info(f"Processing recurring cart: '{cart.name}' for user {cart.user.name}")

        if not cart.items.exists():
            logger.warning(f"Skipping '{cart.name}' because it has no items.")
            continue

        new_order = Orders.objects.using('gsharedb').create(
            user=cart.user,
            store=cart.items.first().item.store,
            status='placed',  
            order_date=timezone.now(),
            delivery_address=cart.user.address
        )

        total_amount = 0
        for item in cart.items.all():
            OrderItems.objects.using('gsharedb').create(
                order=new_order,
                item=item.item,
                quantity=item.quantity,
                price=item.item.price
            )
            if item.item.price:
                total_amount += item.item.price * item.quantity

        new_order.total_amount = total_amount
        new_order.save(using='gsharedb')

        logger.info(f"Advancing next_order_date for cart #{cart.id}...")
        if cart.frequency == 'weekly':
            cart.next_order_date += timedelta(days=7)
        elif cart.frequency == 'biweekly':
            cart.next_order_date += timedelta(days=14)
        elif cart.frequency == 'monthly':
            cart.next_order_date += timedelta(days=30)

        try:
            cart.save(using='gsharedb')
            logger.info(f"-> SUCCESS: Saved cart #{cart.id}. New next_order_date is {cart.next_order_date}.")
        except Exception as e:
            logger.error(f"-> FAILED to save cart #{cart.id}: {e}")

        logger.info(f"Successfully created Order #{new_order.id} from '{cart.name}'.")

    return f"Processed {len(due_carts)} recurring carts."