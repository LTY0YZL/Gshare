from core.models import Deliveries

def delivery_status(request):
    if not request.user.is_authenticated:
        return {"has_active_delivery": False}

    try:
        has_delivery = Deliveries.objects.using("gsharedb").filter(
            driver_id=request.user.id,
            status__in=["accepting", "picked_up", "delivering"]
        ).exists()
    except:
        has_delivery = False

    return {"has_active_delivery": has_delivery}
