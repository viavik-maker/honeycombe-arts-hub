"""The shop: products, stock, orders through invoices, card and pay-on-collection, cancelling."""
import uuid

from hah import db, money, ratelimit, shop
from tests.booking_helpers import FakeStripe, set_settings
from tests.family_helpers import last_email_to, ok, register_family
from tests.support import ServerTestCase


def order(fam, items, pay_mode="on_collection", key=None):
    return fam.post_json("/api/shop/order", {"items": items, "pay_mode": pay_mode,
                                             "idempotency_key": key or uuid.uuid4().hex, "notes": "Size M"})


class ShopTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()
        set_settings(booking_live=True, shop_live=False)

    def product(self, staff, **kw):
        body = dict(title="Hub T-shirt", price_pence=500, stock=3, max_per_order=5, status="live")
        body.update(kw)
        return ok(staff.post_json("/api/staff/shop/products", body)).json()["product"]

    def test_order_collect_and_pay(self):
        staff = self.admin(roles=("manager",))
        p = self.product(staff)
        fam = register_family()
        self.assertFalse(ok(fam.get("/api/shop")).json()["live"])
        self.assertEqual(order(fam, [{"product": p["id"], "quantity": 1}]).status, 403)  # closed
        set_settings(shop_live=True)
        self.assertIn(p["id"], [x["id"] for x in ok(fam.get("/api/shop")).json()["products"]])
        key = uuid.uuid4().hex
        r = ok(order(fam, [{"product": p["id"], "quantity": 2}], key=key)).json()["order"]
        self.assertEqual((r["total_pence"], r["invoice"]["balance_pence"]), (1000, 1000))
        self.assertEqual(ok(order(fam, [{"product": p["id"], "quantity": 2}], key=key)).json()["order"]["ref"], r["ref"])
        self.assertIn("2 × Hub T-shirt", last_email_to(fam.email))
        again = order(fam, [{"product": p["id"], "quantity": 2}])
        self.assertEqual(again.status, 409)
        self.assertIn("only 1 of Hub T-shirt left", again.json()["error"])
        self.assertEqual(order(fam, [{"product": p["id"], "quantity": 9}]).status, 409)  # over the per-order limit
        self.assertEqual(ok(fam.get("/api/account/shop-orders")).json()["orders"][0]["ref"], r["ref"])
        other = register_family()
        self.assertEqual(ok(other.get("/api/account/shop-orders")).json()["orders"], [])
        # staff: ready (family emailed), payment at collection, collected
        orders = ok(staff.get("/api/staff/shop/orders?q=" + r["ref"])).json()["orders"]
        self.assertEqual(orders[0]["notes"], "Size M")
        ok(staff.post_json("/api/staff/shop/orders/%s/status" % r["ref"], {"to": "ready"}))
        self.assertIn("is ready", last_email_to(fam.email))
        ok(staff.post_json("/api/staff/payments", {"invoice_number": r["invoice"]["number"], "method": "cash",
                                                   "amount_pence": 1000}))
        ok(staff.post_json("/api/staff/shop/orders/%s/status" % r["ref"], {"to": "collected"}))
        self.assertEqual(staff.post_json("/api/staff/shop/orders/%s/status" % r["ref"], {"to": "cancelled"}).status, 400)
        self.assertEqual([x["sold"] for x in ok(staff.get("/api/staff/shop/products")).json()["products"]
                          if x["id"] == p["id"]], [2])
        self.assertEqual(self.admin(roles=("session_staff",)).get("/api/staff/shop/orders").status, 403)
        # the accounting export sees it like any other invoice
        self.assertIn("Shop: Hub T-shirt", self.admin(roles=("finance",)).get("/api/staff/finance/accounting.csv").text)

    def test_cancel_refunds_and_restocks(self):
        set_settings(shop_live=True)
        staff = self.admin(roles=("manager",))
        p = self.product(staff, stock=5)
        fam = register_family()
        r = ok(order(fam, [{"product": p["id"], "quantity": 2}])).json()["order"]
        ok(staff.post_json("/api/staff/payments", {"invoice_number": r["invoice"]["number"], "method": "cash",
                                                   "amount_pence": 1000}))
        res = ok(staff.post_json("/api/staff/shop/orders/%s/status" % r["ref"], {"to": "cancelled", "reason": "Wrong size"})).json()
        self.assertEqual(res["refunded_pence"], 1000)
        with db.read() as c:
            aid = c.execute("SELECT id FROM accounts WHERE email=?", (fam.email,)).fetchone()[0]
            self.assertEqual(money.credit_balance(c, aid), 1000)  # paid in cash: back as account credit
            self.assertEqual(c.execute("SELECT stock FROM shop_products WHERE id=?", (p["id"],)).fetchone()[0], 5)

    def test_card_orders_and_unpaid_ones_lapse(self):
        set_settings(shop_live=True)
        staff = self.admin(roles=("manager",))
        p = self.product(staff, stock=4)
        with FakeStripe():
            fam = register_family()
            r = ok(order(fam, [{"product": p["id"], "quantity": 1}], pay_mode="card")).json()
            self.assertTrue(r["redirect"].startswith("https://stripe.test/pay/"))
        with db.tx() as c:
            c.execute("UPDATE shop_orders SET created_at='2000-01-01T00:00:00Z' WHERE ref=?", (r["order"]["ref"],))
        self.assertIsNone(shop.cancel_unpaid())  # its card payment is still open: left alone
        with db.tx() as c:
            c.execute("UPDATE checkouts SET expires_at='2000-01-01T00:40:00Z' WHERE invoice_id=(SELECT invoice_id"
                      " FROM shop_orders WHERE ref=?)", (r["order"]["ref"],))
        self.assertIn("cancelled 1", shop.cancel_unpaid())
        self.assertIn("cancelled it", last_email_to(fam.email))
        with db.read() as c:
            self.assertEqual(c.execute("SELECT stock FROM shop_products WHERE id=?", (p["id"],)).fetchone()[0], 4)
            inv = c.execute("SELECT i.* FROM invoices i JOIN shop_orders o ON o.invoice_id=i.id WHERE o.ref=?",
                            (r["order"]["ref"],)).fetchone()
            self.assertEqual(money.balance(inv), 0)  # credited, nothing owed

    def test_cancelling_twice_restocks_once(self):
        set_settings(shop_live=True)
        staff = self.admin(roles=("manager",))
        p = self.product(staff, stock=3)
        fam = register_family()
        r = ok(order(fam, [{"product": p["id"], "quantity": 2}])).json()["order"]
        ok(staff.post_json("/api/staff/payments", {"invoice_number": r["invoice"]["number"], "method": "cash",
                                                   "amount_pence": 1000}))
        first = ok(staff.post_json("/api/staff/shop/orders/%s/status" % r["ref"], {"to": "cancelled"})).json()
        again = ok(staff.post_json("/api/staff/shop/orders/%s/status" % r["ref"], {"to": "cancelled"})).json()
        self.assertEqual((first["refunded_pence"], again["refunded_pence"]), (1000, 0))
        with db.read() as c:
            self.assertEqual(c.execute("SELECT stock FROM shop_products WHERE id=?", (p["id"],)).fetchone()[0], 3)
            aid = c.execute("SELECT id FROM accounts WHERE email=?", (fam.email,)).fetchone()[0]
            self.assertEqual(money.credit_balance(c, aid), 1000)

    def test_repeated_lines_count_together(self):
        set_settings(shop_live=True)
        staff = self.admin(roles=("manager",))
        pack = self.product(staff, title="Art pack", stock=6, max_per_order=5)
        card = self.product(staff, title="Card", stock=None, max_per_order=2)
        fam = register_family()
        r = order(fam, [{"product": pack["id"], "quantity": 3}, {"product": pack["id"], "quantity": 3}])
        self.assertEqual(r.status, 409)
        self.assertIn("up to 5 of Art pack", r.json()["error"])
        self.assertEqual(order(fam, [{"product": card["id"], "quantity": 2}] * 3).status, 409)
        r = ok(order(fam, [{"product": pack["id"], "quantity": 2}, {"product": pack["id"], "quantity": 2}])).json()
        self.assertEqual(r["order"]["lines"], [{"title": "Art pack", "quantity": 4, "unit_pence": 500,
                                                "amount_pence": 2000}])
        with db.read() as c:
            self.assertEqual(c.execute("SELECT stock FROM shop_products WHERE id=?", (pack["id"],)).fetchone()[0], 2)
        r = order(fam, [{"product": pack["id"], "quantity": 2}, {"product": pack["id"], "quantity": 1}])
        self.assertEqual(r.status, 409)
        self.assertIn("only 2 of Art pack left", r.json()["error"])
