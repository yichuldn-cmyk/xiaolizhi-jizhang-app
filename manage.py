"""管理命令：独立演示数据与一致性备份，不混入业务路由。"""
import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import create_app
from app.db import get_db
from app.services import now


def seed_demo():
    """仅在 data/demo 且数据库为空时生成虚构记录，可重复调用。"""
    data = Path(os.environ.get("DATA_DIR", "")).resolve()
    expected = (Path(__file__).resolve().parent / "data" / "demo").resolve()
    if data != expected:
        raise ValueError("演示数据必须使用项目 data/demo 目录，请运行 start-demo.ps1。")
    app = create_app()
    with app.app_context():
        db = get_db()
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            print("演示账本已存在，保留已有修改。")
            return
        month = now().strftime("%Y-%m")
        password = generate_password_hash("demo-lizhi-2026", method="pbkdf2:sha256:600000")
        with db:
            db.execute("INSERT INTO households VALUES (1,'我们的小家',1)")
            for uid, username, name in [(1, "demo", "小荔枝"), (2, "family", "家人")]:
                db.execute("INSERT INTO users(id,username,name,password,household_id) VALUES (?,?,?,?,1)", (uid, username, name, password))
            rows = [(1, "expense", 3200, "餐饮", "午餐", "支付宝", 15),
                    (2, "expense", 12800, "居家", "超市采购", "微信", 15),
                    (1, "expense", 248000, "餐饮", "本月餐饮汇总", "微信", 12),
                    (2, "expense", 175600, "居家", "房租与日常居家", "支付宝", 10),
                    (1, "expense", 125600, "购物", "生活用品与衣物", "支付宝", 8),
                    (2, "expense", 62800, "交通", "出行与加油", "微信", 6),
                    (1, "income", 800000, "工资", "工资收入", "银行卡", 5)]
            for index, (uid, kind, amount, category, merchant, channel, day) in enumerate(rows):
                db.execute("INSERT INTO entries(user_id,occurred,kind,amount,category,merchant,channel,note,shared,fingerprint) VALUES (?,?,?,?,?,?,?,'虚构演示数据',1,?)", (uid, f"{month}-{day:02d} 12:00:00", kind, amount, category, merchant, channel, f"demo-{index}"))
            db.execute("INSERT INTO budgets VALUES (1,?,'总预算',1000000)", (month,))
        print("演示账号 demo / family，密码 demo-lizhi-2026；仅供本地演示。")


def backup(destination):
    """使用 SQLite 在线备份 API 获得一致快照；拒绝覆盖已有文件。"""
    target = Path(destination).resolve()
    if target.exists():
        raise ValueError("备份文件已存在，请使用新的文件名。")
    target.parent.mkdir(parents=True, exist_ok=True)
    app = create_app()
    with app.app_context(), closing(sqlite3.connect(target)) as output:
        get_db().backup(output)
        result = output.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise ValueError("备份完整性检查失败。")
    print(f"备份成功：{target}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="小荔枝管理工具")
    parser.add_argument("command", choices=["demo", "backup"])
    parser.add_argument("destination", nargs="?", help="备份文件目标路径")
    args = parser.parse_args()
    if args.command == "demo":
        seed_demo()
    elif args.destination:
        backup(args.destination)
    else:
        parser.error("backup 需要提供备份目标文件。")
