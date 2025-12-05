from core.models import Users
from core.utils.orders_for_driver import get_active_orders_for_driver

def user_can_use_scan(django_user) -> bool:
    if not django_user.is_authenticated:
        return False

    try:
        u = Users.objects.using("gsharedb").get(email=django_user.email)
    except Users.DoesNotExist:
        return False

    return bool(get_active_orders_for_driver(u.id))