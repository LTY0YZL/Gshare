from django.db import connections

def get_active_orders_for_driver(driver_user_id: int):
    """
    Return active orders + items for a driver.

    Structure:
    [
      {
        "id": order_id,
        "status": "delivering",
        "items": [
          {"order_id": ..., "item_id": ..., "quantity": float, "item_name": str},
          ...
        ]
      },
      ...
    ]
    """
    if not driver_user_id:
        return []

    with connections["gsharedb"].cursor() as cur:
        cur.execute(
            """
            SELECT d.order_id, d.status
            FROM deliveries d
            WHERE d.delivery_person_id = %s AND d.status IN ('inprogress', 'delivering')
            ORDER BY d.id DESC
            """,
            [driver_user_id],
        )
        rows = cur.fetchall()
        print(rows)

        orders = [{"id": r[0], "status": r[1], "items": []} for r in rows]
        if not orders:
            return []

        ids = ",".join(str(o["id"]) for o in orders)

        cur.execute(
            f"""
            SELECT oi.order_id, oi.item_id, oi.quantity, i.name
            FROM order_items oi
            JOIN items i ON i.id = oi.item_id
            WHERE oi.order_id IN ({ids})
            """
        )
        by_id = {o["id"]: o for o in orders}
        for order_id, item_id, qty, name in cur.fetchall():
            by_id[order_id]["items"].append(
                {
                    "order_id": order_id,
                    "item_id": item_id,
                    "quantity": float(qty or 0),
                    "item_name": name,
                }
            )

    return orders