"""核心业务与越权回归测试；数据全部写入临时目录。"""
import io
import json
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook
from werkzeug.security import generate_password_hash

from app import create_app
from app.db import get_db
from app.importer import parse_bill
from app.services import budget_status, household_entries, money_to_cents, summarize, visible_entries

CSV = "时间,类型,金额,商家,订单号\n2026-11-15 12:00:00,支出,32.10,午餐,A01\n"


class LedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = generate_password_hash("a-good-password", method="pbkdf2:sha256:600000")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"DATA_DIR": self.temp.name})
        self.env.start()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": str(Path(self.temp.name) / "ledger.sqlite3")})
        self.context = self.app.app_context()
        self.context.push()
        db = get_db()
        db.execute("INSERT INTO households VALUES (1,'测试家庭',1)")
        db.execute("INSERT INTO households VALUES (2,'其他家庭',3)")
        for uid, household in [(1, 1), (2, 1), (3, 2)]:
            db.execute("INSERT INTO users(id,username,name,password,household_id) VALUES (?,?,?,?,?)", (uid, f"user{uid}", f"成员{uid}", self.password, household))
        db.commit()
        self.client = self.client_as(1)

    def tearDown(self):
        self.context.pop()
        self.env.stop()
        self.temp.cleanup()

    def client_as(self, uid):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session.update(uid=uid, csrf="valid")
        return client

    def post(self, url, data=None, client=None):
        return (client or self.client).post(url, data={"csrf": "valid", **(data or {})})

    def seed(self, uid=1, kind="expense", amount=1000, shared=1, merchant="测试", category="餐饮"):
        db = get_db()
        eid = db.execute("INSERT INTO entries(user_id,occurred,kind,amount,category,merchant,channel,shared,fingerprint) VALUES (?,'2026-11-15 12:00:00',?,?,?,?,?,?,?)", (uid, kind, amount, category, merchant, "微信", shared, os.urandom(8).hex())).lastrowid
        db.commit()
        return eid

    def user(self):
        return get_db().execute("SELECT * FROM users WHERE id=1").fetchone()

    def preview(self, client=None):
        return self.post("/import", {"channel": "微信", "file": (io.BytesIO(CSV.encode()), "bill.csv")}, client=client)

    def test_money_exact_and_invalid(self):
        self.assertEqual(money_to_cents("0.29"), 29)
        self.assertEqual(money_to_cents("￥1,234.56"), 123456)
        for value in ["nan", "-1", "1e4", "1.001", "0", "inf"]:
            with self.assertRaises(ValueError):
                money_to_cents(value)

    def test_visibility_and_household_budget(self):
        self.seed(1, shared=0)
        self.seed(1, shared=1)
        self.seed(2, shared=1)
        self.seed(2, shared=0)
        self.seed(3, shared=1)
        self.assertEqual(len(visible_entries(self.user(), "2026-11")), 3)
        self.assertEqual(len(household_entries(self.user(), "2026-11")), 2)
        db = get_db()
        db.execute("INSERT INTO budgets VALUES (1,'2026-11','总预算',10000)")
        db.commit()
        self.assertEqual(budget_status(self.user(), "2026-11")[0]["percent"], 20)

    def test_transfer_and_refund(self):
        self.seed(amount=10000)
        self.seed(kind="refund", amount=2000)
        self.seed(kind="transfer", amount=99999)
        self.seed(kind="income", amount=50000)
        stats = summarize(visible_entries(self.user(), "2026-11"))
        self.assertEqual((stats["expense"], stats["income"], stats["balance"]), (8000, 50000, 42000))

    def test_csrf_and_ownership(self):
        eid = self.seed(2, shared=1)
        self.assertEqual(self.client.post(f"/entry/{eid}/delete").status_code, 400)
        self.assertEqual(self.post(f"/entry/{eid}/delete").status_code, 404)
        self.assertEqual(self.client.get(f"/entry/{eid}").status_code, 404)

    def test_only_owner_budget(self):
        response = self.post("/budgets", {"month": "2026-11", "category": "总预算", "amount": "10"}, self.client_as(2))
        self.assertEqual(response.status_code, 403)

    def test_parse_csv_and_gb18030(self):
        for encoding in ["utf-8-sig", "gb18030"]:
            rows, errors, skipped = parse_bill(CSV.encode(encoding), "a.csv", "微信")
            self.assertEqual(rows[0]["amount"], 3210)
            self.assertEqual(rows[0]["category"], "餐饮")
            self.assertFalse(errors)
            self.assertEqual(skipped, 0)

    def test_real_header_preamble_and_failure(self):
        text = "微信支付账单\n导出说明\n交易时间,交易类型,交易对方,商品,收/支,金额(元),当前状态,交易单号\n2026-11-15 12:00:00,商户消费,餐厅,午餐,支出,￥32.00,支付成功,A1\n2026-11-15 12:00:00,商户消费,餐厅,午餐,支出,￥32.00,交易关闭,A2\n2026-11-15 12:00:00,其他,未知,未知,/,2.00,支付成功,A3\n"
        rows, errors, skipped = parse_bill(text.encode(), "wx.csv", "微信")
        self.assertEqual((len(rows), len(errors), skipped), (1, 1, 1))

    def test_xlsx(self):
        workbook = Workbook()
        workbook.active.append(["交易时间", "收/支", "金额（元）", "交易对方"])
        workbook.active.append(["2026-11-15 12:00:00", "支出", 6, "地铁"])
        output = io.BytesIO()
        workbook.save(output)
        rows, errors, _ = parse_bill(output.getvalue(), "账单.xlsx", "支付宝")
        self.assertEqual(rows[0]["amount"], 600)
        self.assertFalse(errors)

    def test_import_two_phase_dedup_undo(self):
        response = self.preview()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM entries").fetchone()[0], 0)
        token = get_db().execute("SELECT token FROM previews").fetchone()[0]
        self.assertEqual(self.post("/import/confirm", {"token": token, "shared": "1"}).status_code, 302)
        self.assertEqual(self.post("/import/confirm", {"token": token}).status_code, 400)
        self.preview()
        fresh = json.loads(get_db().execute("SELECT payload FROM previews").fetchone()[0])
        self.assertEqual(fresh, [])
        batch = get_db().execute("SELECT id FROM batches").fetchone()[0]
        self.assertEqual(self.post(f"/import/{batch}/undo", client=self.client_as(2)).status_code, 404)
        self.assertEqual(self.post(f"/import/{batch}/undo").status_code, 302)
        self.preview()
        self.assertEqual(len(json.loads(get_db().execute("SELECT payload FROM previews ORDER BY rowid DESC").fetchone()[0])), 1)

    def test_foreign_preview_forbidden(self):
        self.preview()
        token = get_db().execute("SELECT token FROM previews").fetchone()[0]
        self.assertEqual(self.post("/import/confirm", {"token": token}, self.client_as(2)).status_code, 400)

    def test_invite_once_and_privacy(self):
        client = self.client_as(3)
        self.seed(3, shared=1)
        self.post("/family")
        token = get_db().execute("SELECT token FROM invites").fetchone()[0]
        self.assertEqual(self.post("/join", {"token": token}, client).status_code, 302)
        row = get_db().execute("SELECT household_id FROM users WHERE id=3").fetchone()
        self.assertEqual(row[0], 1)
        self.assertEqual(get_db().execute("SELECT shared FROM entries WHERE user_id=3").fetchone()[0], 0)
        self.assertEqual(self.post("/join", {"token": token}, client).status_code, 400)

    def test_leave_stops_sharing(self):
        self.seed(2, shared=1)
        self.assertEqual(self.post("/family/leave", client=self.client_as(2)).status_code, 302)
        self.assertEqual(len(visible_entries(self.user(), "2026-11")), 0)
        self.assertEqual(self.post("/family/leave").status_code, 302)

    def test_export_escapes_formulas(self):
        self.seed(merchant="=HYPERLINK(1)")
        self.seed(2, shared=0, merchant="PRIVATE SECRET")
        response = self.client.get("/export?month=2026-11")
        self.assertIn("'=HYPERLINK(1)", response.get_data(as_text=True))
        self.assertNotIn("PRIVATE SECRET", response.get_data(as_text=True))

    def test_pages_render_and_unsafe_inputs(self):
        for url in ["/", "/entries", "/stats", "/budgets", "/family", "/join", "/settings", "/entry/new", "/import"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get("/?month=invalid").status_code, 400)
        self.assertEqual(self.app.test_client().get("/entries").status_code, 302)
        self.assertEqual(self.post("/import", {"channel": "微信", "file": (io.BytesIO(b"no"), "x.exe")}).status_code, 400)

    def test_create_edit_delete_restore(self):
        self.client.get("/entry/new")
        with self.client.session_transaction() as session:
            nonce = session["entry_nonces"][-1]
        fields = dict(nonce=nonce, kind="expense", category="餐饮", amount="1.29", merchant="午餐", channel="微信", occurred="2026-11-15T12:00", shared="1")
        self.assertEqual(self.post("/entry/new", fields).status_code, 302)
        self.assertEqual(self.post("/entry/new", fields).status_code, 400)
        eid = get_db().execute("SELECT id FROM entries").fetchone()[0]
        self.assertEqual(self.post(f"/entry/{eid}", {**fields, "amount": "2.29"}).status_code, 302)
        self.post(f"/entry/{eid}/delete")
        self.assertFalse(visible_entries(self.user(), "2026-11"))
        self.post(f"/entry/{eid}/restore")
        self.assertEqual(visible_entries(self.user(), "2026-11")[0]["amount"], 229)

    def test_registration_login(self):
        client = self.app.test_client()
        client.get("/register")
        with client.session_transaction() as session:
            csrf = session["csrf"]
        response = client.post("/register", data={"csrf": csrf, "username": "new_user", "name": "小明", "password": "new-good-password"})
        self.assertEqual(response.status_code, 302)
        stored = get_db().execute("SELECT password FROM users WHERE username='new_user'").fetchone()[0]
        self.assertNotEqual(stored, "new-good-password")
        with client.session_transaction() as session:
            csrf = session["csrf"]
        client.post("/logout", data={"csrf": csrf})
        client.get("/login")
        with client.session_transaction() as session:
            csrf = session["csrf"]
        response = client.post("/login", data={"csrf": csrf, "username": "new_user", "password": "new-good-password"})
        self.assertEqual(response.status_code, 302)


    def test_layout_preference_and_invalid_cookie(self):
        self.client.set_cookie("layout_mode", "mobile")
        for path in ("/", "/entries", "/stats", "/settings"):
            body = self.client.get(path).get_data(as_text=True)
            self.assertIn('data-layout="mobile"', body)
            self.assertIn('aria-label="显示布局"', body)
            self.assertIn('data-layout-choice="desktop"', body)
        self.client.set_cookie("layout_mode", "desktop")
        self.assertIn('data-layout="desktop"', self.client.get("/").get_data(as_text=True))
        self.client.set_cookie("layout_mode", "invalid")
        self.assertIn('data-layout="auto"', self.client.get("/").get_data(as_text=True))

    def test_backup_integrity_and_restore(self):
        from manage import backup
        self.seed(amount=1234)
        destination = Path(self.temp.name) / "snapshot.sqlite3"
        backup(destination)
        with closing(sqlite3.connect(destination)) as restored:
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(restored.execute("SELECT amount FROM entries").fetchone()[0], 1234)
        with self.assertRaises(ValueError):
            backup(destination)


    def test_populated_home_and_entries(self):
        self.seed(merchant="午餐", shared=1)
        for path in ("/?month=2026-11", "/entries?month=2026-11", "/stats?month=2026-11"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn("支出", response.get_data(as_text=True))
        home = self.client.get("/?month=2026-11").get_data(as_text=True)
        self.assertIn('href="/entry/new"', home)


if __name__ == "__main__":
    unittest.main()
