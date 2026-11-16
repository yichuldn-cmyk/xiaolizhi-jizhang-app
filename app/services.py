"""业务规则：整数分、可见范围、月度统计与分类；不处理 HTTP。"""
import calendar
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .db import get_db

CATEGORIES = ("餐饮", "居家", "购物", "交通", "娱乐", "医疗", "教育", "工资", "其他", "待确认")
KINDS = {"expense": "支出", "income": "收入", "transfer": "转账", "refund": "退款"}
RULES = {
    "餐饮": ("餐", "饭", "咖啡", "奶茶", "面馆", "外卖"),
    "居家": ("超市", "买菜", "水费", "电费", "燃气", "房租", "物业"),
    "交通": ("地铁", "公交", "打车", "滴滴", "加油", "停车", "铁路"),
    "购物": ("淘宝", "京东", "拼多多", "服装", "商场"),
    "娱乐": ("电影", "游戏", "视频", "音乐"),
    "医疗": ("医院", "药房", "药店"), "教育": ("学费", "培训", "书店"),
    "工资": ("工资", "薪资"),
}


def now():
    """统一使用北京时间；避免服务器时区改变月份边界。"""
    return datetime.now(timezone(timedelta(hours=8)))


def money_to_cents(text, allow_zero=False):
    """严格解析人民币金额；最多两位小数，拒绝指数、负数和非有限值。"""
    value = str(text).strip().replace(",", "").replace("¥", "").replace("￥", "")
    if not re.fullmatch(r"\d{1,9}(?:\.\d{1,2})?", value):
        raise ValueError("金额须为非负数字，最多两位小数且小于十亿元。")
    cents = int(Decimal(value) * 100)
    if cents == 0 and not allow_zero:
        raise ValueError("金额必须大于零。")
    return cents


def month_bounds(month):
    """返回合法月份的起止 ISO 时间（含末日）。"""
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("月份格式应为 YYYY-MM。")
    date = datetime.strptime(month, "%Y-%m")
    last = calendar.monthrange(date.year, date.month)[1]
    return f"{month}-01 00:00:00", f"{month}-{last:02d} 23:59:59"


def categories(user):
    return list(CATEGORIES) + json.loads(user["categories"])


def classify(merchant, note=""):
    text = merchant + " " + note
    return next((cat for cat, words in RULES.items() if any(w in text for w in words)), "待确认")


def visible_entries(user, month, scope="all", query="", category=""):
    """本人全部 + 同家庭他人显式共享账单；不泄露他人私有记录。"""
    start, end = month_bounds(month)
    rows = get_db().execute(
        """SELECT e.*, u.name FROM entries e JOIN users u ON u.id=e.user_id
        WHERE e.deleted=0 AND e.occurred BETWEEN ? AND ?
        AND (e.user_id=? OR (u.household_id=? AND e.shared=1))
        ORDER BY e.occurred DESC,e.id DESC""",
        (start, end, user["id"], user["household_id"]),
    ).fetchall()
    return [r for r in rows if (scope != "me" or r["user_id"] == user["id"])
            and (scope != "others" or r["user_id"] != user["id"])
            and (not query or query.casefold() in (r["merchant"] + r["note"]).casefold())
            and (not category or r["category"] == category)]


def household_entries(user, month):
    """家庭预算仅计共享账单；本人私有记录也不进入家庭统计。"""
    return [r for r in visible_entries(user, month) if r["shared"]]


def summarize(rows):
    """退款按发生月份冲减分类支出；转账不计消费。"""
    expense = income = 0
    by_category, by_day = {}, {}
    for row in rows:
        if row["kind"] == "income":
            income += row["amount"]
        if row["kind"] in ("expense", "refund"):
            value = row["amount"] * (-1 if row["kind"] == "refund" else 1)
            expense += value
            cat, day = row["category"], row["occurred"][:10]
            by_category[cat] = by_category.get(cat, 0) + value
            by_day[day] = by_day.get(day, 0) + value
    return {"expense": expense, "income": income, "balance": income - expense,
            "categories": sorted(by_category.items(), key=lambda x: -x[1]),
            "days": sorted(by_day.items())}


def budget_status(user, month):
    """家庭总预算和分类预算独立比较；仅非零预算计算使用率。"""
    summary = summarize(household_entries(user, month))
    values = dict(summary["categories"])
    budgets = get_db().execute(
        "SELECT * FROM budgets WHERE household_id=? AND month=? ORDER BY category",
        (user["household_id"], month),
    ).fetchall()
    return [{"category": b["category"], "amount": b["amount"],
             "used": summary["expense"] if b["category"] == "总预算" else values.get(b["category"], 0),
             "percent": round(100 * (summary["expense"] if b["category"] == "总预算" else values.get(b["category"], 0)) / b["amount"], 1) if b["amount"] else None}
            for b in budgets]
