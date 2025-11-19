# core/context_processors.py
from django.contrib.auth.models import AnonymousUser

from core.models import Users
from core.utils.orders_for_driver import get_active_orders_for_driver


def scan_permission(request):
    """
    Adds `can_use_scan` to every template context.

    can_use_scan = True  -> show real Scan link
    can_use_scan = False -> show disabled button / popup
    """
    can_use_scan = False

    user = request.user
    if isinstance(user, AnonymousUser) or not user.is_authenticated:
        return {"can_use_scan": False}

    try:
        # assuming auth User id == Users.id (this is what your other code does)
        u = Users.objects.using("gsharedb").get(pk=user.id)
    except Users.DoesNotExist:
        return {"can_use_scan": False}

    # use your existing helper — it already knows what “active” means
    active_orders = get_active_orders_for_driver(u.id)
    can_use_scan = bool(active_orders)

    return {"can_use_scan": can_use_scan}
