from django.db import connections

def get_active_orders_for_driver(driver_user_id: int):
    with connections['gsharedb'].cursor() as cur:
        cur.execute("""
            SELECT o.id, o.status
            FROM orders o
            WHERE o.delivery_user_id=%s
              AND o.status IN ('placed','ready_for_delivery','delivering')
            ORDER BY o.id DESC
        """, [driver_user_id])
        orders = [{"id": r[0], "status": r[1], "items": []} for r in cur.fetchall()]
        if not orders: return []
        ids = ",".join(str(o["id"]) for o in orders)
        cur.execute(f"""
            SELECT oi.order_id, oi.item_id, oi.quantity, i.name
            FROM order_items oi JOIN items i ON i.id=oi.item_id
            WHERE oi.order_id IN ({ids})
        """)
        by_id = {o["id"]: o for o in orders}
        for oid, item_id, qty, name in cur.fetchall():
            by_id[oid]["items"].append({"order_id": oid, "item_id": item_id, "quantity": float(qty or 0), "item_name": name})
        return orders