from django.test import TestCase
# app/tests/test_utils.py
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase, RequestFactory
from django.db import IntegrityError
from django.http import JsonResponse

# ⬇️ UPDATE this to your actual module path if needed
from app.utils import (
    calculate_tax,
    get_user_ratings,
    get_most_recent_order,      # final def (the one looping Deliveries)
    get_user,
    edit_user,
    create_user_signin,
    Edit_order_items,
    get_orders,
    get_orders_by_status,
    get_order_items,
    get_order_items_by_order_id,
    change_order_status,
    change_order_status_json,
    get_my_deliveries,
    add_feedback,               # second def (with subject + rating clamp)
    get_feedback_for_user,
    get_feedback_by_order,
)

# UPDATE imports to where your models live
from app.models import (
    Users, Orders, Deliveries, OrderItems, Items, Feedback
)


class UtilsTests(TestCase):
    # allow using multiple DBs in tests
    databases = {"default", "gsharedb"}

    def setUp(self):
        self.factory = RequestFactory()

        # --- seed users ---
        self.alice = Users.objects.using('gsharedb').create(
            name="Alice", email="alice@example.com", username="alice",
            address="123 Test St", latitude=40.48, longitude=-111.92, phone="111-1111"
        )
        self.bob = Users.objects.using('gsharedb').create(
            name="Bob", email="bob@example.com", username="bob",
            address="456 Test Ave", latitude=40.49, longitude=-111.90, phone="222-2222"
        )
        self.dan = Users.objects.using('gsharedb').create(
            name="Dan", email="dan@example.com", username="dan",
            address="789 Test Blvd", latitude=40.50, longitude=-111.95, phone="333-3333"
        )

        # --- seed orders ---
        self.order1 = Orders.objects.using('gsharedb').create(
            user=self.alice, status="placed", total_amount=Decimal("25.00")
        )
        self.order2 = Orders.objects.using('gsharedb').create(
            user=self.alice, status="cart", total_amount=Decimal("5.00")
        )
        self.order3 = Orders.objects.using('gsharedb').create(
            user=self.bob, status="placed", total_amount=Decimal("10.00")
        )

        # --- seed items + order items ---
        self.item1 = Items.objects.using('gsharedb').create(
            name="Milk", price=Decimal("2.49"), store_id=1
        )
        self.item2 = Items.objects.using('gsharedb').create(
            name="Bread", price=Decimal("3.00"), store_id=1
        )
        OrderItems.objects.using('gsharedb').create(
            order=self.order1, item=self.item1, quantity=1
        )

        # --- seed deliveries ---
        self.deliv1 = Deliveries.objects.using('gsharedb').create(
            order=self.order1, delivery_person=self.dan, status="inprogress"
        )
        self.deliv2 = Deliveries.objects.using('gsharedb').create(
            order=self.order3, delivery_person=self.dan, status="inprogress"
        )

        # --- seed feedback (for get_user_ratings, etc.) ---
        Feedback.objects.using('gsharedb').create(
            reviewee=self.alice, reviewer=self.bob,
            feedback="great!", rating=5, description_subject="nice"
        )
        Feedback.objects.using('gsharedb').create(
            reviewee=self.alice, reviewer=self.dan,
            feedback="ok", rating=3, description_subject="ok"
        )

    # ------------------- calculate_tax -------------------

    def test_calculate_tax_rounds_to_nearest_cent(self):
        self.assertEqual(calculate_tax(Decimal("25.00")), 175)   # 7% of 25 = 1.75 → 175¢
        self.assertEqual(calculate_tax(Decimal("0.01")), 0)      # 0.0007 → 0¢
        self.assertEqual(calculate_tax(Decimal("2.355")), 16)    # 0.16485 → 16¢

    # ------------------- get_user_ratings -------------------

    def test_get_user_ratings_with_feedback(self):
        qs, avg = get_user_ratings(self.alice.id)
        self.assertIsNotNone(qs)
        self.assertAlmostEqual(avg, 4.0)  # (5 + 3) / 2

    def test_get_user_ratings_none_when_no_feedback(self):
        qs, avg = get_user_ratings(self.bob.id)  # bob is only reviewer above
        self.assertIsNone(qs)
        self.assertEqual(avg, 0.0)

    # ------------------- get_user -------------------

    def test_get_user_found(self):
        u = get_user("email", "alice@example.com")
        self.assertIsNotNone(u)
        self.assertEqual(u.id, self.alice.id)

    def test_get_user_not_found(self):
        self.assertIsNone(get_user("email", "ghost@example.com"))

    # ------------------- edit_user -------------------

    def test_edit_user_success(self):
        updated = edit_user("alice@example.com", "phone", "999-0000")
        self.assertIsNotNone(updated)
        self.assertEqual(
            Users.objects.using('gsharedb').get(pk=self.alice.pk).phone,
            "999-0000"
        )

    def test_edit_user_not_found(self):
        self.assertIsNone(edit_user("ghost@example.com", "phone", "999-0000"))

    # ------------------- create_user_signin -------------------

    @patch("app.utils.geoLoc", return_value=(40.481, -111.919))
    def test_create_user_signin_success(self, mock_geoloc):
        created = create_user_signin(
            name="New User",
            email="new@example.com",
            address="14848 South Brennan Street",
            username="newbie",
            phone="123"
        )
        self.assertIsNotNone(created)
        self.assertEqual(created.email, "new@example.com")
        self.assertAlmostEqual(created.latitude, 40.481)
        self.assertAlmostEqual(created.longitude, -111.919)
        mock_geoloc.assert_called_once()

    @patch("app.utils.geoLoc", return_value=(0, 0))
    def test_create_user_signin_skips_when_latlng_zero_or_address_not_provided(self, mock_geoloc):
        created = create_user_signin(
            name="NoGeo",
            email="nogeo@example.com",
            address="Not provided",  # fn short-circuits geocode and insert
            username="nogeo"
        )
        self.assertIsNone(created)

    @patch("app.utils.geoLoc", return_value=(40.0, -112.0))
    def test_create_user_signin_integrity_error_raises(self, _mock_geoloc):
        Users.objects.using('gsharedb').create(
            name="Dup", email="dup@example.com", username="dup",
            address="X", latitude=1, longitude=1
        )
        with self.assertRaises(IntegrityError):
            create_user_signin(
                name="Dup2",
                email="dup@example.com",
                address="Y",
                username="dup2"
            )

    # ------------------- Edit_order_items -------------------

    def test_edit_order_items_success(self):
        ok = Edit_order_items(self.order1.id, self.item1.id, 3)
        self.assertTrue(ok)
        oi = OrderItems.objects.using('gsharedb').get(order=self.order1, item=self.item1)
        self.assertEqual(oi.quantity, 3)

    def test_edit_order_items_not_found(self):
        ok = Edit_order_items(self.order1.id, 99999, 3)
        self.assertFalse(ok)

    # ------------------- get_orders / get_orders_by_status -------------------

    def test_get_orders_for_user_and_status(self):
        res = get_orders(self.alice, "placed")
        self.assertTrue(res)  # queryset is truthy
        self.assertEqual(list(res.values_list("id", flat=True)), [self.order1.id])

    def test_get_orders_empty_returns_list(self):
        self.assertEqual(get_orders(self.alice, "delivered"), [])

    def test_get_orders_by_status_nonempty(self):
        res = get_orders_by_status("placed")
        self.assertTrue(res)
        self.assertSetEqual(set(res.values_list("id", flat=True)), {self.order1.id, self.order3.id})

    def test_get_orders_by_status_empty_returns_list(self):
        self.assertEqual(get_orders_by_status("ghost-status"), [])

    # ------------------- get_most_recent_order (final def) -------------------

    def test_get_most_recent_order_matches_user_and_status(self):
        got = get_most_recent_order(self.alice, self.dan, "inprogress")
        self.assertIsNotNone(got)
        self.assertEqual(got.id, self.order1.id)

    def test_get_most_recent_order_none_when_no_match(self):
        self.assertIsNone(get_most_recent_order(self.alice, self.dan, "delivered"))

    # ------------------- get_order_items* (raw SQL) -------------------

    @patch("app.utils.connections")
    def test_get_order_items_uses_sql_and_returns_rows(self, mock_conns):
        fake_cursor = MagicMock()
        fake_cursor.__enter__.return_value = fake_cursor
        fake_cursor.fetchall.return_value = [
            (self.order1.id, self.item1.id, 2, None, self.item1.name, str(self.item1.price), self.item1.store_id)
        ]
        mock_conns.__getitem__.return_value.cursor.return_value = fake_cursor

        rows = get_order_items(self.order1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], self.order1.id)
        fake_cursor.execute.assert_called()
        sql, params = fake_cursor.execute.call_args[0][0], fake_cursor.execute.call_args[0][1]
        self.assertIn("WHERE oi.order_id = %s", sql)
        self.assertEqual(params, [self.order1.id])

    @patch("app.utils.connections")
    def test_get_order_items_by_order_id(self, mock_conns):
        fake_cursor = MagicMock()
        fake_cursor.__enter__.return_value = fake_cursor
        fake_cursor.fetchall.return_value = []
        mock_conns.__getitem__.return_value.cursor.return_value = fake_cursor

        self.assertEqual(get_order_items_by_order_id(self.order1.id), [])

    # ------------------- change_order_status + JSON view -------------------

    def test_change_order_status_success(self):
        self.assertTrue(change_order_status(self.order1.id, "delivered"))
        self.assertEqual(
            Orders.objects.using('gsharedb').get(pk=self.order1.pk).status,
            "delivered"
        )

    def test_change_order_status_not_found(self):
        self.assertFalse(change_order_status(999999, "delivered"))

    def test_change_order_status_json_post(self):
        req = self.factory.post("/fake", data={})
        resp = change_order_status_json(req, self.order1.id, "inprogress")
        self.assertEqual(resp.status_code, 200)
        self.assertJSONEqual(resp.content, {"success": True})

    def test_change_order_status_json_invalid_method(self):
        req = self.factory.get("/fake")
        resp = change_order_status_json(req, self.order1.id, "inprogress")
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b'Invalid request', resp.content)

    # ------------------- get_my_deliveries -------------------

    def test_get_my_deliveries_nonempty(self):
        res = get_my_deliveries(self.alice, "inprogress")
        self.assertTrue(res)
        self.assertEqual(list(res.values_list("order_id", flat=True)), [self.order1.id])

    def test_get_my_deliveries_empty_returns_list(self):
        self.assertEqual(get_my_deliveries(self.alice, "delivered"), [])

    # ------------------- feedback helpers -------------------

    def test_add_feedback_bounds_rating_and_sets_subject(self):
        fb = add_feedback(
            reviewee=self.bob,
            reviewer=self.alice,
            feedback_text="meh",
            subject="S",
            rating=9,  # should clamp to 5
        )
        self.assertIsNotNone(fb)
        self.assertEqual(fb.rating, 5)
        self.assertEqual(fb.description_subject, "S")

    def test_add_feedback_integrity_error_returns_none(self):
        with patch("app.utils.Feedback.objects.using") as mock_using:
            mock_mgr = MagicMock()
            mock_using.return_value = mock_mgr
            mock_mgr.create.side_effect = IntegrityError("dup")
            fb = add_feedback(self.bob, self.alice, "x", "y", 3)
            self.assertIsNone(fb)

    def test_get_feedback_for_user_nonempty(self):
        qs = get_feedback_for_user(self.alice)
        self.assertTrue(qs)

    def test_get_feedback_for_user_empty_returns_list(self):
        # create a fresh user with no feedback as reviewee
        charlie = Users.objects.using('gsharedb').create(
            name="Charlie", email="charlie@example.com", username="charlie",
            address="Z", latitude=1, longitude=1
        )
        self.assertEqual(get_feedback_for_user(charlie), [])

    def test_get_feedback_by_order_pair(self):
        fb = get_feedback_by_order(reviewee=self.alice, reviewer=self.bob)
        self.assertIsNotNone(fb)
        self.assertEqual(fb.rating, 5)

    def test_get_feedback_by_order_none(self):
        self.assertIsNone(get_feedback_by_order(reviewee=self.bob, reviewer=self.alice))
