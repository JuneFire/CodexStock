# -*- coding: utf-8 -*-
"""把 data/history/*.json 导入 MySQL 历史库，无效快照跳过。

用法:
    python migrate_history.py
"""
import glob
import json
import os
import re
import sys

import db

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_DIR = os.path.join(PROJECT_ROOT, "data", "history")


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        return False, "不是有效对象"
    if not snapshot.get("ok") or not snapshot.get("validForAuction"):
        return False, "validForAuction 不为 true"
    if not snapshot.get("stocks"):
        return False, "股票列表为空"
    date = snapshot.get("date") or ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return False, "日期格式错误"
    fetched = snapshot.get("fetchedAt") or ""
    m = re.match(r"(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2})", fetched)
    if not m or m.group(1) != date:
        return False, "fetchedAt 与日期不一致"
    hm = int(m.group(2)) * 60 + int(m.group(3))
    if not (9 * 60 + 25 <= hm < 9 * 60 + 30):
        return False, "抓取时间不在竞价窗口内"
    return True, ""


def main():
    if not db.ensure_schema():
        print("[migrate] MySQL 不可用: %s" % db.last_error())
        return 1
    files = sorted(glob.glob(os.path.join(HISTORY_DIR, "*.json")))
    ok_count = skip_count = 0
    for path in files:
        name = os.path.basename(path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                snapshot = json.load(f)
        except Exception as exc:
            print("[migrate] 跳过 %s: 解析失败 %s" % (name, exc))
            skip_count += 1
            continue
        valid, reason = validate_snapshot(snapshot)
        if not valid:
            print("[migrate] 跳过 %s: %s" % (name, reason))
            skip_count += 1
            continue
        if db.save_snapshot(snapshot):
            print("[migrate] 导入 %s 成功（%d 只）" % (name, len(snapshot["stocks"])))
            ok_count += 1
        else:
            print("[migrate] 导入 %s 失败: %s" % (name, db.last_error()))
            skip_count += 1
    print("[migrate] 完成：导入 %d，跳过 %d" % (ok_count, skip_count))
    return 0


if __name__ == "__main__":
    sys.exit(main())