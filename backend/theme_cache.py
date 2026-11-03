# -*- coding: utf-8 -*-
"""个股概念（题材）缓存 —— 给 09:25 的「题材 T-1 涨停家数」筛子供数。

## 为什么需要缓存

S5 条件之一是「该股题材在 T-1 的涨停家数 >= 1」（见
`scripts/analyze_sanyi_clean.py`）。它需要两样东西：

  1. T-1 涨停池的涨停原因文本 —— 同花顺 `limit-up-pool`，随时可取（backend/market_env.py）
  2. 该股自己的概念列表 —— 只有悟道给（`themes` 字段）

但**悟道免费版 09:15-10:30 停服**，09:25 拿不到；同花顺竞价快照又没有概念字段。

所以把「代码 -> 概念列表」缓存到磁盘，09:25 只读文件、零网络：

  - 历史回填：读 `data/auction_top/*.json`（悟道归档，每行自带 themes）
  - 每日 15:05：`merge_archive()` 读当日归档 + `merge_wudao()` 调悟道补当天新票
  - 09:25：`themes_of(code)` 纯读缓存

实测覆盖（242 天归档回填后）：56 笔历史三一里 **54 笔**能在「D 日之前」的缓存里
查到概念，缺的 2 笔都是样本最开头几天（2025-11-05、2026-03-16）。
"""
from __future__ import print_function

import glob
import json
import os
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(_ROOT, "data", "cache", "stock_themes.json")
ARCHIVE_DIR = os.path.join(_ROOT, "data", "auction_top")

_lock = threading.RLock()
_cache = {"path": None, "data": None}


def _empty():
    return {"updatedAt": None, "count": 0, "stocks": {}}


def load(force=False):
    """读缓存。返回 {"updatedAt", "count", "stocks": {code: {"themes": [...], "asOf": "date"}}}"""
    with _lock:
        if not force and _cache["data"] is not None and _cache["path"] == CACHE_PATH:
            return _cache["data"]
        data = None
        try:
            if os.path.isfile(CACHE_PATH):
                with open(CACHE_PATH, encoding="utf-8") as f:
                    data = json.load(f) or None
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("stocks"), dict):
            data = _empty()
        _cache["path"] = CACHE_PATH
        _cache["data"] = data
        return data


def save(data=None):
    data = data if data is not None else load()
    data["count"] = len(data.get("stocks") or {})
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, CACHE_PATH)
        return True
    except OSError as exc:
        print("[theme] 缓存写入失败: %r" % (exc,), flush=True)
        return False


def themes_of(code, data=None):
    """取某只票的概念列表；无缓存返回 []。"""
    code = str(code or "").zfill(6)
    if len(code) != 6 or not code.isdigit():
        return []
    st = (data if data is not None else load()).get("stocks") or {}
    item = st.get(code)
    if not isinstance(item, dict):
        return []
    ths = item.get("themes")
    return [t for t in ths if isinstance(t, str) and t] if isinstance(ths, list) else []


def _norm_themes(value):
    if isinstance(value, str):
        value = [x.strip() for x in value.replace(";", ",").split(",")]
    if not isinstance(value, list):
        return []
    out = []
    for t in value:
        t = (t or "").strip() if isinstance(t, str) else ""
        if len(t) >= 2 and t not in out:
            out.append(t)
    return out[:12]


def merge_rows(rows, date_str, data=None):
    """把一批行的 themes 并入缓存（同一 code 只保留 asOf 最新的那份）。返回新增/更新条数。"""
    data = data if data is not None else load()
    stocks = data.setdefault("stocks", {})
    n = 0
    for r in rows or []:
        code = str((r or {}).get("code") or "").zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        ths = _norm_themes(r.get("themes") or r.get("concept") or r.get("concepts"))
        if not ths:
            continue
        cur = stocks.get(code)
        if cur and (cur.get("asOf") or "") > (date_str or ""):
            continue
        if cur and cur.get("themes") == ths and cur.get("asOf") == date_str:
            continue
        stocks[code] = {"themes": ths, "asOf": date_str}
        n += 1
    if n:
        data["updatedAt"] = date_str
    return n


def merge_archive(date_str, data=None):
    """读 data/auction_top/<date>.json 的 themes 并入缓存。"""
    path = os.path.join(ARCHIVE_DIR, date_str + ".json")
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            records = (json.load(f) or {}).get("records") or []
    except (OSError, ValueError):
        return 0
    return merge_rows(records, date_str, data)


def merge_wudao(date_str, data=None, key=None):
    """调悟道 auction_opening_snapshot 取当日归档没有的新票 themes（1 次调用/天）。

    只能在 09:15-10:30 以外调用（免费版该时段停服），实际挂在 15:05 收盘任务里。
    失败静默返回 0，绝不影响主流程。
    """
    try:
        try:
            import wudao_feed
        except Exception:
            from backend import wudao_feed
        payload = wudao_feed.fetch_payload(date_str, key=key)
        rows = wudao_feed.rows_from_payload(payload)
    except Exception as exc:
        print("[theme] 悟道题材补全失败: %r" % (exc,), flush=True)
        return 0
    return merge_rows([wudao_feed.to_local_row(r) for r in rows], date_str, data)


def backfill(data=None, verbose=True):
    """用全部历史归档回填缓存。返回 (文件数, 更新条数)。"""
    data = data if data is not None else load()
    files = sorted(glob.glob(os.path.join(ARCHIVE_DIR, "*.json")))
    total = 0
    for path in files:
        d = os.path.basename(path)[:-5]
        total += merge_archive(d, data)
    if verbose:
        print("[theme] 回填 %d 天归档，缓存 %d 只股票" % (len(files), len(data.get("stocks") or {})))
    return len(files), total


def stats():
    data = load()
    stocks = data.get("stocks") or {}
    return {"count": len(stocks), "updatedAt": data.get("updatedAt")}


if __name__ == "__main__":
    import sys
    if "--backfill" in sys.argv:
        backfill()
        save()
        print(json.dumps(stats(), ensure_ascii=False))
    else:
        print(json.dumps(stats(), ensure_ascii=False))
