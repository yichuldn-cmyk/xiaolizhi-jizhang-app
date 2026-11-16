"""账单适配器：CSV/XLSX -> 统一记录。解析失败逐行返回，不静默丢账。"""
import csv
import hashlib
import io
import json
from datetime import datetime
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .services import CATEGORIES, classify, money_to_cents

ALIASES = {
    "occurred": ("交易时间", "交易创建时间", "创建时间", "时间", "日期"),
    "amount": ("金额(元)", "金额（元）", "金额", "交易金额"),
    "direction": ("收/支", "收支", "收支类型", "类型"),
    "merchant": ("交易对方", "对方", "商家", "名称"),
    "note": ("商品", "商品名称", "商品说明", "备注"),
    "status": ("当前状态", "交易状态", "状态"),
    "trade_id": ("交易单号", "交易订单号", "交易号", "订单号"),
    "category": ("分类",), "trade_type": ("交易类型",),
}
KIND_MAP = {"支出": "expense", "收入": "income", "转账": "transfer", "退款": "refund"}


def _clean(value):
    return str(value or "").strip().lstrip("\ufeff").strip("\t'")


def _rows(content, filename):
    """只接受文本 CSV 或无宏 XLSX；限制解压大小和行数。"""
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        # 官方文件存在 UTF-8 / GB18030 两类编码；仅编码错误触发回退。
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("gb18030")
        delimiter = "\t" if text.count("\t") > text.count(",") else ","
        return list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if suffix != ".xlsx":
        raise ValueError("仅支持 CSV 和 XLSX；请先解压账单文件，或将 XLS 转为 XLSX。")
    with ZipFile(io.BytesIO(content)) as archive:
        if sum(f.file_size for f in archive.infolist()) > 30 * 1024 * 1024:
            raise ValueError("Excel 解压后过大，请分批导入。")
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True, keep_links=False)
    sheet = workbook.active
    rows = []
    for index, row in enumerate(sheet.iter_rows(values_only=True)):
        if index >= 11000:
            workbook.close()
            raise ValueError("单次最多读取 11000 行，请分批导入。")
        rows.append(list(row[:40]))
    workbook.close()
    return rows


def parse_bill(content, filename, channel):
    """返回 (records, errors, skipped)；参数与字段兼容范围见参数文档。"""
    try:
        rows = _rows(content, filename)
    except (UnicodeError, BadZipFile, InvalidFileException, csv.Error, ParseError, DefusedXmlException, KeyError) as error:
        raise ValueError("账单编码或格式无法读取，请重新导出 CSV/XLSX。") from error
    if len(rows) > 11000:
        raise ValueError("单次最多读取 11000 行，请分批导入。")
    header_index, mapping = None, {}
    for index, row in enumerate(rows[:100]):
        headers = [_clean(c) for c in row]
        fields = {key: next((headers.index(a) for a in names if a in headers), None)
                  for key, names in ALIASES.items()}
        if all(fields[key] is not None for key in ("occurred", "amount", "direction")):
            header_index, mapping = index, fields
            break
    if header_index is None:
        raise ValueError("未找到时间、金额、收支表头。请使用原始导出文件或示例模板。")
    records, errors, skipped = [], [], 0
    for number, raw in enumerate(rows[header_index + 1:], header_index + 2):
        row = [_clean(c) for c in raw]
        if not any(row) or row[0].startswith(("---", "总计", "共", "说明", "导出")):
            continue
        field = lambda key: row[mapping[key]] if mapping[key] is not None and mapping[key] < len(row) else ""
        status, direction = field("status"), field("direction")
        if any(word in status for word in ("关闭", "失败", "撤销", "未支付")):
            skipped += 1
            continue
        kind = KIND_MAP.get(direction)
        if "退款" in field("trade_type") or direction == "退款":
            kind = "refund"
        if any(w in field("trade_type") for w in ("充值", "提现")):
            kind = "transfer"
        if not kind:
            errors.append(f"第 {number} 行：收支类型“{direction}”需要人工确认（非收支流水可改为转账）。")
            continue
        try:
            occurred = str(field("occurred")).replace("/", "-").replace("T", " ")
            date = datetime.fromisoformat(occurred)
            if date.tzinfo:
                raise ValueError("时间须为不含时区的北京时间")
            amount = money_to_cents(field("amount"))
        except ValueError as error:
            errors.append(f"第 {number} 行：时间或金额无效（{error}）。")
            continue
        merchant = field("merchant")[:120] or "未注明商家"
        note = field("note")[:300]
        category = field("category") if field("category") in CATEGORIES else classify(merchant, note)
        record = dict(occurred=date.isoformat(" ", timespec="seconds"), amount=amount,
                      kind=kind, merchant=merchant, note=note, category=category, channel=channel)
        # 交易号优先；同号退款与消费分开。无交易号时采用稳定字段指纹。
        identity = [channel, field("trade_id"), kind] if field("trade_id") else list(record.values())
        record["fingerprint"] = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        records.append(record)
    return records, errors, skipped
