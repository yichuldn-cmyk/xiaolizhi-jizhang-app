"""页面与表单控制器。写操作显式校验身份、归属及 CSRF。"""
import csv
import io
import json
import re
import secrets
import time
from datetime import datetime
from functools import wraps

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .importer import parse_bill
from .services import (KINDS, budget_status, categories, household_entries, money_to_cents,
                       month_bounds, now, summarize, visible_entries)

bp = Blueprint("web", __name__)


def login_required(view):
    """未登录统一跳转登录页，不接受外部重定向地址。"""
    @wraps(view)
    def wrapped(*args, **kwargs):
        return view(*args, **kwargs) if g.user else redirect(url_for("web.login"))
    return wrapped


def owned_entry(entry_id):
    row = get_db().execute("SELECT * FROM entries WHERE id=? AND user_id=?", (entry_id, g.user["id"])).fetchone()
    if row is None:
        abort(404)
    return row


def current_month():
    month = request.values.get("month", now().strftime("%Y-%m"))
    month_bounds(month)
    return month


def current_scope():
    """将公开查询参数约束到明确的三种统计范围。"""
    scope = request.args.get("scope", "all")
    if scope not in ("all", "me", "others", ""):
        raise ValueError("成员范围须为全家、本人或家人。")
    return scope or "all"


@bp.app_context_processor
def template_context():
    return {"kind_names": KINDS, "today": now().date().isoformat()}


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        name, password = request.form.get("name", "").strip(), request.form.get("password", "")
        if not re.fullmatch(r"[a-z0-9_]{3,32}", username) or not 1 <= len(name) <= 20 or not 10 <= len(password) <= 128:
            raise ValueError("账号须为3～32位字母数字下划线；昵称1～20字；密码10～128位。")
        db = get_db()
        with db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise ValueError("此账号已被使用，请更换。")
            household_id = db.execute("INSERT INTO households(name) VALUES (?)", (name + "的家庭",)).lastrowid
            uid = db.execute("INSERT INTO users(username,name,password,household_id) VALUES (?,?,?,?)",
                             (username, name, generate_password_hash(password, method="pbkdf2:sha256:600000"), household_id)).lastrowid
            db.execute("UPDATE households SET owner_id=? WHERE id=?", (uid, household_id))
        session.clear()
        session.update(uid=uid, csrf=secrets.token_hex(24))
        session.permanent = True
        return redirect(url_for("web.home"))
    return render_template("auth.html", registering=True)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()[:32]
        db, stamp = get_db(), int(time.time())
        identity = f"{request.remote_addr}:{username}"
        db.execute("DELETE FROM login_attempts WHERE attempted<?", (stamp - 900,))
        attempts = db.execute("SELECT COUNT(*) FROM login_attempts WHERE identity=?", (identity,)).fetchone()[0]
        if attempts >= 10:
            abort(429, description="尝试次数过多，请15分钟后重试。")
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user is None or not check_password_hash(user["password"], request.form.get("password", "")[:128]):
            db.execute("INSERT INTO login_attempts VALUES (?,?)", (identity, stamp))
            db.commit()
            flash("账号或密码不正确。", "error")
        else:
            db.execute("DELETE FROM login_attempts WHERE identity=?", (identity,))
            db.commit()
            session.clear()
            session.update(uid=user["id"], csrf=secrets.token_hex(24))
            session.permanent = True
            return redirect(url_for("web.home"))
    return render_template("auth.html", registering=False)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("web.login"))


@bp.get("/")
@login_required
def home():
    month = current_month()
    scope = current_scope()
    rows = household_entries(g.user, month) if scope == "all" else visible_entries(g.user, month, scope)
    if scope == "others":
        rows = [r for r in rows if r["user_id"] != g.user["id"]]
    stats = summarize(rows)
    budgets = budget_status(g.user, month)
    total = next((b for b in budgets if b["category"] == "总预算"), None)
    db = get_db()
    family = db.execute("SELECT * FROM households WHERE id=?", (g.user["household_id"],)).fetchone()
    members = db.execute("SELECT id,name FROM users WHERE household_id=?", (family["id"],)).fetchall()
    updates = db.execute("SELECT channel,MAX(occurred) AS latest FROM entries WHERE user_id=? AND batch_id IS NOT NULL AND deleted=0 GROUP BY channel", (g.user["id"],)).fetchall()
    remind = bool(g.user["reminder_enabled"] and now().strftime("%H:%M") >= g.user["reminder"])
    return render_template("home.html", active="home", month=month, scope=scope, stats=stats,
                           total=total, budgets=budgets, rows=rows[:5], family=family,
                           members=members, updates=updates, remind=remind)


@bp.get("/entries")
@login_required
def entries():
    month = current_month()
    scope, query, category = (request.args.get(k, "") for k in ("scope", "q", "category"))
    rows = visible_entries(g.user, month, scope, query[:100], category)
    return render_template("entries.html", active="entries", month=month, scope=scope, query=query,
                           category=category, rows=rows, categories=categories(g.user))


@bp.route("/entry/new", methods=["GET", "POST"])
@bp.route("/entry/<int:entry_id>", methods=["GET", "POST"])
@login_required
def entry(entry_id=None):
    record = owned_entry(entry_id) if entry_id else None
    if record and record["deleted"]:
        abort(404)
    if request.method == "POST":
        form = request.form
        kind, cat = form.get("kind"), form.get("category")
        allowed_categories = categories(g.user) + ([record["category"]] if record else [])
        if kind not in KINDS or cat not in allowed_categories:
            raise ValueError("请选择有效的类型和分类。")
        occurred = datetime.fromisoformat(form.get("occurred", ""))
        if occurred.tzinfo:
            raise ValueError("请填写北京时间。")
        merchant = form.get("merchant", "").strip()
        channel = form.get("channel", "").strip()
        if not 1 <= len(merchant) <= 120 or not 1 <= len(channel) <= 40:
            raise ValueError("请填写名称（最多120字）和账户（最多40字）。")
        values = (occurred.isoformat(" ", timespec="seconds"), kind, money_to_cents(form.get("amount", "")),
                  cat, merchant, channel, form.get("note", "")[:300], int(form.get("shared") == "1"))
        db = get_db()
        with db:
            if record:
                db.execute("UPDATE entries SET occurred=?,kind=?,amount=?,category=?,merchant=?,channel=?,note=?,shared=? WHERE id=? AND user_id=?", (*values, entry_id, g.user["id"]))
            else:
                # 浏览器重复提交同一表单不会新增两次。
                nonce = form.get("nonce", "")
                if nonce not in session.get("entry_nonces", []):
                    raise ValueError("此表单已保存或过期，请重新打开。")
                db.execute("INSERT INTO entries(occurred,kind,amount,category,merchant,channel,note,shared,user_id,fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?)", (*values, g.user["id"], nonce))
                session["entry_nonces"] = [n for n in session["entry_nonces"] if n != nonce]
        flash("账单已保存。", "success")
        return redirect(url_for("web.entries", month=occurred.strftime("%Y-%m")))
    nonce = secrets.token_hex(16)
    session["entry_nonces"] = (session.get("entry_nonces", []) + [nonce])[-10:]
    return render_template("entry.html", active="entries", record=record, categories=categories(g.user),
                           nonce=nonce, default_date=now().strftime("%Y-%m-%dT%H:%M"))


@bp.post("/entry/<int:entry_id>/delete")
@login_required
def delete_entry(entry_id):
    owned_entry(entry_id)
    db = get_db()
    db.execute("UPDATE entries SET deleted=1 WHERE id=?", (entry_id,))
    db.commit()
    flash("已移至回收站，可在“我的”恢复。", "success")
    return redirect(url_for("web.entries"))


@bp.post("/entry/<int:entry_id>/restore")
@login_required
def restore_entry(entry_id):
    row = owned_entry(entry_id)
    db = get_db()
    if row["batch_id"] and db.execute("SELECT undone FROM batches WHERE id=?", (row["batch_id"],)).fetchone()[0]:
        raise ValueError("所属导入批次已撤销，请重新导入该批账单。")
    db.execute("UPDATE entries SET deleted=0 WHERE id=?", (entry_id,))
    db.commit()
    return redirect(url_for("web.settings"))


@bp.route("/import", methods=["GET", "POST"])
@login_required
def import_bill():
    if request.method == "POST":
        file, channel = request.files.get("file"), request.form.get("channel", "")
        if file is None or not file.filename or channel not in ("微信", "支付宝", "其他"):
            raise ValueError("请选择账单文件及来源。")
        records, errors, skipped = parse_bill(file.read(), file.filename, channel)
        db = get_db()
        known = {r[0] for r in db.execute("SELECT fingerprint FROM entries WHERE user_id=?", (g.user["id"],))}
        seen, fresh, duplicates = set(), [], 0
        for row in records:
            fingerprint = row["fingerprint"]
            if fingerprint in known or fingerprint in seen:
                duplicates += 1
            else:
                fresh.append(row)
                seen.add(fingerprint)
        token = secrets.token_urlsafe(24)
        stamp = int(time.time())
        db.execute("DELETE FROM previews WHERE expires<?", (stamp,))
        db.execute("INSERT INTO previews VALUES (?,?,?,?,?)", (token, g.user["id"], file.filename[:150], json.dumps(fresh, ensure_ascii=False), stamp + 1800))
        db.commit()
        return render_template("import.html", active="entries", preview=True, rows=fresh[:50],
                               count=len(fresh), errors=errors[:100], error_count=len(errors),
                               skipped=skipped, duplicates=duplicates, token=token)
    return render_template("import.html", active="entries", preview=False)


@bp.post("/import/confirm")
@login_required
def confirm_import():
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        preview = db.execute("SELECT * FROM previews WHERE token=? AND user_id=? AND expires>?", (request.form.get("token"), g.user["id"], int(time.time()))).fetchone()
        if preview is None:
            raise ValueError("导入预览已确认或已过期，请重新上传。")
        records = json.loads(preview["payload"])
        batch = db.execute("INSERT INTO batches(user_id,filename,created) VALUES (?,?,?)", (g.user["id"], preview["filename"], now().isoformat())).lastrowid
        count = 0
        for row in records:
            result = db.execute("""INSERT OR IGNORE INTO entries(user_id,occurred,kind,amount,category,merchant,channel,note,shared,fingerprint,batch_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (g.user["id"], row["occurred"], row["kind"], row["amount"], row["category"], row["merchant"], row["channel"], row["note"], int(request.form.get("shared") == "1"), row["fingerprint"], batch))
            count += result.rowcount
        db.execute("UPDATE batches SET count=? WHERE id=?", (count, batch))
        db.execute("DELETE FROM previews WHERE token=?", (preview["token"],))
    flash(f"成功导入 {count} 笔。待确认分类及家庭内部转账请在明细中核对。", "success")
    month = records[0]["occurred"][:7] if records else now().strftime("%Y-%m")
    return redirect(url_for("web.entries", month=month))


@bp.post("/import/<int:batch_id>/undo")
@login_required
def undo_import(batch_id):
    db = get_db()
    with db:
        batch = db.execute("SELECT * FROM batches WHERE id=? AND user_id=?", (batch_id, g.user["id"])).fetchone()
        if batch is None:
            abort(404)
        # 仅删除此批次插入的记录，不影响重复检查跳过的既有记录。
        db.execute("DELETE FROM entries WHERE batch_id=? AND user_id=?", (batch_id, g.user["id"]))
        db.execute("UPDATE batches SET undone=1 WHERE id=?", (batch_id,))
    flash("导入批次已撤销，可重新导入。", "success")
    return redirect(url_for("web.settings"))


@bp.get("/stats")
@login_required
def stats():
    month = current_month()
    scope = current_scope()
    rows = household_entries(g.user, month) if scope == "all" else visible_entries(g.user, month, scope)
    year, number = map(int, month.split("-"))
    previous = f"{year - (number == 1):04d}-{12 if number == 1 else number - 1:02d}"
    previous_rows = household_entries(g.user, previous) if scope == "all" else visible_entries(g.user, previous, scope)
    prior = summarize(previous_rows)["expense"]
    summary = summarize(rows)
    change = round((summary["expense"] - prior) / prior * 100, 1) if prior > 0 else None
    return render_template("stats.html", active="stats", month=month, scope=scope, stats=summary, change=change)


@bp.route("/budgets", methods=["GET", "POST"])
@login_required
def budgets():
    db, month = get_db(), current_month()
    owner = db.execute("SELECT owner_id FROM households WHERE id=?", (g.user["household_id"],)).fetchone()[0] == g.user["id"]
    if request.method == "POST":
        if not owner:
            abort(403, description="仅家庭管理员可设置家庭预算。")
        category = request.form.get("category", "")
        if category not in ["总预算"] + categories(g.user):
            raise ValueError("请选择有效分类。")
        amount = money_to_cents(request.form.get("amount", ""), allow_zero=True)
        db.execute("INSERT INTO budgets VALUES (?,?,?,?) ON CONFLICT(household_id,month,category) DO UPDATE SET amount=excluded.amount", (g.user["household_id"], month, category, amount))
        db.commit()
        flash("预算已更新；设为0表示停用。", "success")
        return redirect(url_for("web.budgets", month=month))
    return render_template("budgets.html", active="budget", month=month, budgets=budget_status(g.user, month), owner=owner, categories=categories(g.user))


@bp.route("/family", methods=["GET", "POST"])
@login_required
def family():
    db = get_db()
    household = db.execute("SELECT * FROM households WHERE id=?", (g.user["household_id"],)).fetchone()
    if request.method == "POST":
        if household["owner_id"] != g.user["id"]:
            abort(403, description="仅家庭管理员可生成邀请。")
        token = secrets.token_urlsafe(24)
        db.execute("INSERT INTO invites VALUES (?,?,?,0)", (token, household["id"], int(time.time()) + 86400))
        db.commit()
        # 相对链接避免基于不可信 Host 生成外部地址。
        session["invite"] = token
        return redirect(url_for("web.family"))
    members = db.execute("SELECT id,name FROM users WHERE household_id=?", (household["id"],)).fetchall()
    return render_template("family.html", active="settings", family=household, members=members,
                           invite=session.get("invite"))


@bp.route("/join", methods=["GET", "POST"])
@login_required
def join():
    token = request.values.get("token", "").strip()
    db = get_db()
    invitation = db.execute("SELECT i.*,h.name FROM invites i JOIN households h ON h.id=i.household_id WHERE i.token=? AND i.used=0 AND i.expires>?", (token, int(time.time()))).fetchone()
    if request.method == "POST":
        with db:
            db.execute("BEGIN IMMEDIATE")
            invitation = db.execute("SELECT * FROM invites WHERE token=? AND used=0 AND expires>?", (token, int(time.time()))).fetchone()
            if not invitation:
                raise ValueError("邀请码无效、已使用或已过期。")
            if invitation["household_id"] == g.user["household_id"]:
                raise ValueError("你已在此家庭中。")
            others = db.execute("SELECT COUNT(*) FROM users WHERE household_id=? AND id!=?", (g.user["household_id"], g.user["id"])).fetchone()[0]
            if others:
                raise ValueError("请先退出当前家庭，再加入新家庭；管理员需等待其他成员退出。")
            db.execute("DELETE FROM invites WHERE household_id=?", (g.user["household_id"],))
            # 切换家庭时取消旧记录共享，避免历史记录意外带入新家庭。
            db.execute("UPDATE entries SET shared=0 WHERE user_id=?", (g.user["id"],))
            db.execute("UPDATE users SET household_id=? WHERE id=?", (invitation["household_id"], g.user["id"]))
            db.execute("UPDATE invites SET used=1 WHERE token=?", (token,))
        session.pop("invite", None)
        flash("已加入家庭。历史账单保持私有，可逐笔选择共享。", "success")
        return redirect(url_for("web.family"))
    return render_template("join.html", active="settings", invitation=invitation, token=token)


@bp.post("/family/leave")
@login_required
def leave_family():
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        owner = db.execute("SELECT owner_id FROM households WHERE id=?", (g.user["household_id"],)).fetchone()[0]
        others = db.execute("SELECT COUNT(*) FROM users WHERE household_id=? AND id!=?", (g.user["household_id"], g.user["id"])).fetchone()[0]
        if owner == g.user["id"] and others:
            raise ValueError("家庭管理员需等待其他成员退出后，才能退出。")
        new_id = db.execute("INSERT INTO households(name,owner_id) VALUES (?,?)", (g.user["name"] + "的家庭", g.user["id"])).lastrowid
        db.execute("UPDATE entries SET shared=0 WHERE user_id=?", (g.user["id"],))
        db.execute("UPDATE users SET household_id=? WHERE id=?", (new_id, g.user["id"]))
        if owner == g.user["id"]:
            db.execute("DELETE FROM invites WHERE household_id=?", (g.user["household_id"],))
    session.pop("invite", None)
    flash("已退出，个人账单保留并取消共享。", "success")
    return redirect(url_for("web.family"))


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    db = get_db()
    if request.method == "POST":
        reminder = request.form.get("reminder", "21:00")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", reminder):
            raise ValueError("提醒时间格式须为 HH:MM。")
        custom = [s.strip() for s in request.form.get("categories", "").split("，") if s.strip()]
        if len(custom) > 20 or any(len(s) > 12 for s in custom):
            raise ValueError("自定义分类最多20个，每个不超过12字，以中文逗号分隔。")
        db.execute("UPDATE users SET reminder=?,reminder_enabled=?,categories=? WHERE id=?", (reminder, int(request.form.get("enabled") == "1"), json.dumps(list(dict.fromkeys(custom)), ensure_ascii=False), g.user["id"]))
        db.commit()
        flash("设置已保存。", "success")
        return redirect(url_for("web.settings"))
    batches = db.execute("SELECT * FROM batches WHERE user_id=? ORDER BY id DESC LIMIT 30", (g.user["id"],)).fetchall()
    trash = db.execute("SELECT * FROM entries WHERE user_id=? AND deleted=1 ORDER BY id DESC LIMIT 30", (g.user["id"],)).fetchall()
    return render_template("settings.html", active="settings", batches=batches, trash=trash,
                           custom="，".join(json.loads(g.user["categories"])))


@bp.get("/export")
@login_required
def export():
    rows = visible_entries(g.user, current_month(), request.args.get("scope", "all"))
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["时间", "类型", "金额", "分类", "商家", "来源", "成员", "共享", "备注"])
    def safe(value):
        text = str(value)
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
    for row in rows:
        writer.writerow([safe(v) for v in (row["occurred"], KINDS[row["kind"]], f"{row['amount']/100:.2f}", row["category"], row["merchant"], row["channel"], row["name"], "是" if row["shared"] else "否", row["note"])])
    return Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="ledger.csv"'})


@bp.get("/example")
@login_required
def example():
    from pathlib import Path
    data = (Path(__file__).resolve().parent.parent / "examples" / "示例账单.csv").read_bytes()
    return Response(data, mimetype="text/csv", headers={"Content-Disposition": 'attachment; filename="example.csv"'})
