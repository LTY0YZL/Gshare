from core.utils.permissions import user_can_use_scan

def scan_permission(request):
    return {"can_use_scan": user_can_use_scan(request.user)}