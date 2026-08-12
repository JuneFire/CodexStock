# -*- coding: utf-8 -*-
"""历史竞价数据 MySQL 存储层。

配置优先级：config.json 的 mysql 字段 > 环境变量 MYSQL_HOST/MYSQL_PORT/MYSQL_USER/MYSQL_PASSWORD/MYSQL_DB。
数据库不可用时，写操作返回 False 并记录错误，不影响原有 JSON 存档。
"""
import json
import os
import re
import threading

try:
    import pymysql
    import pymysql.cursors
except ImportError:  # pragma: no cover
    pymysql = None

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "",
    "database": "cn_stock_quant",
    "charset": "utf8mb4",
}
ENV_MAP = {
    "host": "MYSQL_HOST",
    "port": "MYSQL_PORT",
    "user": "MYSQL_USER",
    "password": "MYSQL_PASSWORD",
    "database": "MYSQL_DB",
}

_lock = threading.Lock()
_config = None
_available = False
_last_error = ""

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS auction_day (
        id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        trade_date DATE NOT NULL UNIQUE,
        fetched_at DATETIME NOT NULL,
        source VARCHAR(32) NOT NULL DEFAULT '东方财富',
        auto TINYINT NOT NULL DEFAULT 0,
        valid TINYINT NOT NULL DEFAULT 1,
        market_json JSON NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS auction_stock (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        day_id INT UNSIGNED NOT NULL,
        code VARCHAR(10) NOT NULL,
        name VARCHAR(64) NOT NULL,
        industry VARCHAR(64) NOT NULL DEFAULT '',
        price DECIMAL(12,3) NULL,
        change_pct DECIMAL(8,3) NULL,
        open DECIMAL(12,3) NULL,
        prev_close DECIMAL(12,3) NULL,
        auction_amount DECIMAL(20,2) NULL,
        auction_volume DECIMAL(20,2) NULL,
        turnover DECIMAL(10,3) NULL,
        volume_ratio DECIMAL(10,3) NULL,
        float_cap DECIMAL(20,2) NULL,
        total_cap DECIMAL(20,2) NULL,
        yesterday_amount DECIMAL(20,2) NULL,
        yesterday_turnover DECIMAL(10,3) NULL,
        yesterday_close DECIMAL(12,3) NULL,
        ratio_to_yesterday DECIMAL(10,3) NULL,
        amount_strength DECIMAL(10,3) NULL,
        auction_turnover DECIMAL(10,3) NULL,
        score DECIMAL(6,2) NULL,
        `rank` INT NULL,
        tags VARCHAR(128) NULL,
        close_pct DECIMAL(8,3) NULL,
        close_price DECIMAL(12,3) NULL,
        UNIQUE KEY uq_day_code (day_id, code),
        KEY idx_code (code),
        KEY idx_day_score (day_id, score)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS sentiment_review (
        trade_date DATE NOT NULL PRIMARY KEY,
        indices_json JSON NULL,
        breadth_json JSON NULL,
        pools_json JSON NULL,
        lianban_json JSON NULL,
        manual_json JSON NULL,
        horses_json JSON NULL,
        fetched_at DATETIME NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS auction_seal (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        trade_date DATE NOT NULL,
        code VARCHAR(10) NOT NULL,
        name VARCHAR(64) NOT NULL DEFAULT '',
        sample_time CHAR(5) NOT NULL,
        bid1_price DECIMAL(12,3) NULL,
        bid1_volume DECIMAL(20,2) NULL,
        ask1_price DECIMAL(12,3) NULL,
        ask1_volume DECIMAL(20,2) NULL,
        last_price DECIMAL(12,3) NULL,
        prev_close DECIMAL(12,3) NULL,
        seal_amount DECIMAL(20,2) NULL,
        seal_rank INT NULL,
        fetched_at DATETIME NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_date_code_time (trade_date, code, sample_time),
        KEY idx_date_time (trade_date, sample_time),
        KEY idx_date_seal (trade_date, seal_amount)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]

REVIEW_UPSERT_SQL = """
INSERT INTO sentiment_review (trade_date, indices_json, breadth_json, pools_json, lianban_json, manual_json, horses_json, fetched_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    indices_json = VALUES(indices_json),
    breadth_json = VALUES(breadth_json),
    pools_json = VALUES(pools_json),
    lianban_json = VALUES(lianban_json),
    manual_json = VALUES(manual_json),
    horses_json = VALUES(horses_json),
    fetched_at = VALUES(fetched_at)
"""

DAY_UPSERT_SQL = """
INSERT INTO auction_day (trade_date, fetched_at, source, auto, valid, market_json)
VALUES (%s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    fetched_at = VALUES(fetched_at),
    source = VALUES(source),
    auto = VALUES(auto),
    valid = VALUES(valid),
    market_json = VALUES(market_json)
"""

STOCK_INSERT_SQL = """
INSERT INTO auction_stock (
    day_id, code, name, industry, price, change_pct, open, prev_close,
    auction_amount, auction_volume, turnover, volume_ratio, float_cap, total_cap,
    yesterday_amount, yesterday_turnover, yesterday_close,
    ratio_to_yesterday, amount_strength, auction_turnover, score, `rank`, tags
) VALUES (
    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
)
"""

CSV_HEADERS = [
    "日期", "代码", "名称", "板块", "竞价价", "竞价涨幅", "竞价金额(元)", "竞价量(手)",
    "竞价换手(%)", "量比", "流通市值(元)", "昨日成交额(元)", "昨日占比(%)",
    "金额强度(bp)", "超预期分", "排名", "状态",
]


def load_config():
    global _config
    if _config is not None:
        return _config
    cfg = dict(DEFAULT_CONFIG)
    config_path = os.path.join(PROJECT_ROOT, "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            mysql = data.get("mysql") or {}
            cfg.update({k: v for k, v in mysql.items() if v is not None and v != ""})
        except Exception:
            pass
    for key, env_name in ENV_MAP.items():
        if env_name in os.environ and os.environ[env_name] != "":
            cfg[key] = os.environ[env_name]
    try:
        cfg["port"] = int(cfg["port"])
    except (TypeError, ValueError):
        cfg["port"] = 3306
    _config = cfg
    return _config


def _raw_connect(database=None):
    if pymysql is None:
        raise RuntimeError("pymysql 未安装，请先执行 pip install -r requirements.txt")
    cfg = load_config()
    return pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=database,
        charset=cfg["charset"],
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def _connect():
    return _raw_connect(load_config()["database"])


def ensure_schema():
    global _available, _last_error
    try:
        cfg = load_config()
        try:
            conn = _connect()
        except Exception:
            raw = _raw_connect(database=None)
            try:
                db_name = cfg["database"].replace("`", "")
                with raw.cursor() as cur:
                    cur.execute(
                        "CREATE DATABASE IF NOT EXISTS `%s` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" % db_name
                    )
                raw.commit()
            finally:
                raw.close()
            conn = _connect()
        try:
            with conn.cursor() as cur:
                for stmt in SCHEMA:
                    cur.execute(stmt)
                # 旧表补齐新列（部分 MySQL 版本不支持 ADD COLUMN IF NOT EXISTS，用异常容错）
                for col in ("close_pct DECIMAL(8,3) NULL", "close_price DECIMAL(12,3) NULL",
                            "horses_json JSON NULL"):
                    try:
                        cur.execute("ALTER TABLE auction_stock ADD COLUMN %s" % col)
                    except Exception:
                        pass
                    try:
                        cur.execute("ALTER TABLE sentiment_review ADD COLUMN %s" % col)
                    except Exception:
                        pass
            conn.commit()
        finally:
            conn.close()
        _available = True
        _last_error = ""
        return True
    except Exception as exc:
        _available = False
        _last_error = str(exc)
        return False


def is_available():
    return _available


def last_error():
    return _last_error


def status():
    return {"ok": _available, "error": _last_error}


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _reject_reason(snapshot, allow_anytime=False):
    if not isinstance(snapshot, dict):
        return "快照不是有效对象"
    if not snapshot.get("ok"):
        return "快照 ok 标记为 False"
    if not snapshot.get("validForAuction") and not allow_anytime:
        return "快照 validForAuction 不为 true"
    if not snapshot.get("stocks"):
        return "股票列表为空"
    date = snapshot.get("date") or ""
    fetched = snapshot.get("fetchedAt") or ""
    m = re.match(r"(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2})", fetched)
    if not m or m.group(1) != date:
        return "fetchedAt 与日期不一致"
    if allow_anytime:
        return ""  # 测试模式：放开竞价窗口校验
    hm = int(m.group(2)) * 60 + int(m.group(3))
    if not (9 * 60 + 25 <= hm < 9 * 60 + 30):
        return "抓取时间不在竞价窗口内（9:25-9:30），已拒绝写入"
    return ""


def _stock_values(day_id, s):
    return (
        day_id,
        str(s.get("code") or ""),
        str(s.get("name") or ""),
        str(s.get("industry") or ""),
        _num(s.get("price")),
        _num(s.get("changePct")),
        _num(s.get("open")),
        _num(s.get("prevClose")),
        _num(s.get("auctionAmount")),
        _num(s.get("auctionVolume")),
        _num(s.get("turnover")),
        _num(s.get("volumeRatio")),
        _num(s.get("floatCap")),
        _num(s.get("totalCap")),
        _num(s.get("yesterdayAmount")),
        _num(s.get("yesterdayTurnover")),
        _num(s.get("yesterdayClose")),
        _num(s.get("ratioToYesterday")),
        _num(s.get("amountStrength")),
        _num(s.get("auctionTurnover")),
        _num(s.get("score")),
        s.get("rank"),
        ",".join(str(t) for t in (s.get("tags") or [])),
    )


def save_snapshot(snapshot, allow_anytime=False):
    global _available, _last_error
    reason = _reject_reason(snapshot, allow_anytime=allow_anytime)
    if reason:
        _last_error = reason
        return False
    with _lock:
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        DAY_UPSERT_SQL,
                        (
                            snapshot["date"],
                            snapshot.get("fetchedAt"),
                            snapshot.get("source", "东方财富"),
                            1 if snapshot.get("auto") else 0,
                            1,
                            json.dumps(snapshot.get("market") or {}, ensure_ascii=False),
                        ),
                    )
                    cur.execute("SELECT id FROM auction_day WHERE trade_date = %s", (snapshot["date"],))
                    row = cur.fetchone()
                    day_id = row["id"]
                    cur.execute("DELETE FROM auction_stock WHERE day_id = %s", (day_id,))
                    rows = [_stock_values(day_id, s) for s in snapshot.get("stocks") or []]
                    if rows:
                        cur.executemany(STOCK_INSERT_SQL, rows)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            _available = True
            _last_error = ""
            return True
        except Exception as exc:
            _available = False
            _last_error = str(exc)
            return False


def _date_str(v):
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    return str(v)[:10]


def _datetime_str(v):
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)[:19]


def _dates_from_rows(rows):
    return [
        {
            "date": _date_str(r["trade_date"]),
            "fetchedAt": _datetime_str(r["fetched_at"]),
            "source": r["source"],
            "auto": bool(r["auto"]),
            "valid": bool(r["valid"]),
            "stockCount": int(r["stock_count"] or 0),
        }
        for r in rows
    ]


def list_dates(limit=120):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.trade_date, d.fetched_at, d.source, d.auto, d.valid,
                       COUNT(s.id) AS stock_count
                FROM auction_day d
                LEFT JOIN auction_stock s ON s.day_id = d.id
                GROUP BY d.id, d.trade_date, d.fetched_at, d.source, d.auto, d.valid
                ORDER BY d.trade_date DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
    return _dates_from_rows(rows)


def list_dates_page(page=1, page_size=30):
    """分页历史日期，按日期倒序。返回 (dates, total)。"""
    page = max(int(page), 1)
    page_size = max(int(page_size), 1)
    offset = (page - 1) * page_size
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM auction_day")
            total = int((cur.fetchone() or {}).get("c") or 0)
            cur.execute(
                """
                SELECT d.trade_date, d.fetched_at, d.source, d.auto, d.valid,
                       COUNT(s.id) AS stock_count
                FROM auction_day d
                LEFT JOIN auction_stock s ON s.day_id = d.id
                GROUP BY d.id, d.trade_date, d.fetched_at, d.source, d.auto, d.valid
                ORDER BY d.trade_date DESC
                LIMIT %s OFFSET %s
                """,
                (page_size, offset),
            )
            rows = cur.fetchall()
    return _dates_from_rows(rows), total


def _stock_from_row(r):
    return {
        "code": r["code"],
        "name": r["name"],
        "industry": r["industry"],
        "price": _num(r["price"]),
        "changePct": _num(r["change_pct"]),
        "open": _num(r["open"]),
        "prevClose": _num(r["prev_close"]),
        "auctionAmount": _num(r["auction_amount"]),
        "auctionVolume": _num(r["auction_volume"]),
        "turnover": _num(r["turnover"]),
        "volumeRatio": _num(r["volume_ratio"]),
        "floatCap": _num(r["float_cap"]),
        "totalCap": _num(r["total_cap"]),
        "yesterdayAmount": _num(r["yesterday_amount"]),
        "yesterdayTurnover": _num(r["yesterday_turnover"]),
        "yesterdayClose": _num(r["yesterday_close"]),
        "ratioToYesterday": _num(r["ratio_to_yesterday"]),
        "amountStrength": _num(r["amount_strength"]),
        "auctionTurnover": _num(r["auction_turnover"]),
        "score": _num(r["score"]),
        "rank": r["rank"],
        "tags": r["tags"].split(",") if r["tags"] else [],
        "closePct": _num(r.get("close_pct")),
        "closePrice": _num(r.get("close_price")),
    }


def get_day(date):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM auction_day WHERE trade_date = %s", (date,))
            day = cur.fetchone()
            if not day:
                return None
            cur.execute(
                "SELECT * FROM auction_stock WHERE day_id = %s ORDER BY score DESC, id ASC",
                (day["id"],),
            )
            stocks = cur.fetchall()
    market = {}
    try:
        market = json.loads(day["market_json"]) if day.get("market_json") else {}
    except (TypeError, ValueError):
        market = {}
    return {
        "ok": True,
        "date": _date_str(day["trade_date"]),
        "fetchedAt": _datetime_str(day["fetched_at"]),
        "source": day["source"],
        "auto": bool(day["auto"]),
        "validForAuction": bool(day["valid"]),
        "market": market,
        "stocks": [_stock_from_row(r) for r in stocks],
    }


def get_stock_history(code, limit=120):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.trade_date, s.*
                FROM auction_stock s
                JOIN auction_day d ON d.id = s.day_id
                WHERE s.code = %s
                ORDER BY d.trade_date DESC
                LIMIT %s
                """,
                (code, limit),
            )
            rows = cur.fetchall()
    out = []
    for r in rows:
        item = _stock_from_row(r)
        item["date"] = _date_str(r["trade_date"])
        out.append(item)
    return out


def _csv_field(v):
    s = "" if v is None else str(v)
    if any(ch in s for ch in ',"\n\r'):
        return '"' + s.replace('"', '""') + '"'
    return s


def format_csv(date, stocks):
    lines = [",".join(CSV_HEADERS)]
    for s in stocks:
        lines.append(",".join([
            _csv_field(date),
            _csv_field(s.get("code")),
            _csv_field(s.get("name")),
            _csv_field(s.get("industry")),
            _csv_field(_num(s.get("price"))),
            _csv_field(_num(s.get("changePct"))),
            _csv_field(_num(s.get("auctionAmount"))),
            _csv_field(_num(s.get("auctionVolume"))),
            _csv_field(_num(s.get("auctionTurnover"))),
            _csv_field(_num(s.get("volumeRatio"))),
            _csv_field(_num(s.get("floatCap"))),
            _csv_field(_num(s.get("yesterdayAmount"))),
            _csv_field(_num(s.get("ratioToYesterday"))),
            _csv_field(_num(s.get("amountStrength"))),
            _csv_field(_num(s.get("score"))),
            _csv_field(s.get("rank")),
            _csv_field("|".join(s.get("tags") or [])),
        ]))
    return "\n".join(lines) + "\n"


def export_csv(date):
    day = get_day(date)
    if not day:
        return None
    return format_csv(day["date"], day["stocks"])


# ---------- 情绪周期复盘 ----------

def _json_dumps(obj):
    try:
        return json.dumps(obj, ensure_ascii=False) if obj is not None else None
    except (TypeError, ValueError):
        return None


def _json_loads(text):
    if not text:
        return None
    try:
        value = json.loads(text)
        if isinstance(value, (dict, list)):
            return value
        return None
    except (TypeError, ValueError):
        return None


def save_review(date, indices=None, breadth=None, pools=None, lianban=None, manual=None, horses=None, fetched_at=None):
    """保存某日情绪周期复盘（各 JSON 列）。MySQL 不可用返回 False 不抛。"""
    global _available, _last_error
    if fetched_at is None:
        from datetime import datetime
        fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        REVIEW_UPSERT_SQL,
                        (
                            date,
                            _json_dumps(indices),
                            _json_dumps(breadth),
                            _json_dumps(pools),
                            _json_dumps(lianban),
                            _json_dumps(manual),
                            _json_dumps(horses),
                            fetched_at,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            _available = True
            _last_error = ""
            return True
        except Exception as exc:
            _available = False
            _last_error = str(exc)
            return False


def read_review(date):
    """读取某日复盘 dict | None。"""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM sentiment_review WHERE trade_date = %s", (date,))
            row = cur.fetchone()
    if not row:
        return None
    return {
        "date": _date_str(row["trade_date"]),
        "fetchedAt": _datetime_str(row["fetched_at"]),
        "indices": _json_loads(row["indices_json"]),
        "breadth": _json_loads(row["breadth_json"]),
        "pools": _json_loads(row["pools_json"]),
        "lianban": _json_loads(row["lianban_json"]),
        "manual": _json_loads(row["manual_json"]),
        "horses": _json_loads(row.get("horses_json")),
    }


def list_review_dates(limit=120):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trade_date, fetched_at FROM sentiment_review ORDER BY trade_date DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
    return [{"date": _date_str(r["trade_date"]), "fetchedAt": _datetime_str(r["fetched_at"])} for r in rows]


# ---------- 竞价封单额 ----------

SEAL_UPSERT_SQL = """
INSERT INTO auction_seal (
    trade_date, code, name, sample_time, bid1_price, bid1_volume, ask1_price, ask1_volume,
    last_price, prev_close, seal_amount, seal_rank, fetched_at
) VALUES (
    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    bid1_price = VALUES(bid1_price),
    bid1_volume = VALUES(bid1_volume),
    ask1_price = VALUES(ask1_price),
    ask1_volume = VALUES(ask1_volume),
    last_price = VALUES(last_price),
    prev_close = VALUES(prev_close),
    seal_amount = VALUES(seal_amount),
    seal_rank = VALUES(seal_rank),
    fetched_at = VALUES(fetched_at)
"""


def _seal_values(r):
    return (
        r["tradeDate"],
        str(r.get("code") or ""),
        str(r.get("name") or ""),
        r["sampleTime"],
        _num(r.get("bid1Price")),
        _num(r.get("bid1Volume")),
        _num(r.get("ask1Price")),
        _num(r.get("ask1Volume")),
        _num(r.get("lastPrice")),
        _num(r.get("prevClose")),
        _num(r.get("sealAmount")),
        r.get("sealRank"),
        r.get("fetchedAt"),
    )


def save_seal(records):
    """批量 upsert 封单记录。MySQL 不可用返回 False 不抛。"""
    global _available, _last_error
    if not records:
        _last_error = "封单记录为空"
        return False
    with _lock:
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    cur.executemany(SEAL_UPSERT_SQL, [_seal_values(r) for r in records])
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            _available = True
            _last_error = ""
            return True
        except Exception as exc:
            _available = False
            _last_error = str(exc)
            return False


CLOSE_PCT_SQL = """
UPDATE auction_stock SET close_pct = %s, close_price = %s
WHERE day_id = %s AND code = %s
"""


def save_close_pct(date, pct_map):
    """把 {code: 收盘涨幅%} 批量更新到当日 auction_stock 行。MySQL 不可用返回 False。"""
    global _available, _last_error
    if not pct_map:
        _last_error = "收盘数据为空"
        return False
    with _lock:
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM auction_day WHERE trade_date = %s", (date,))
                    day = cur.fetchone()
                    if not day:
                        _last_error = "当日无竞价快照"
                        return False
                    day_id = day["id"]
                    rows = []
                    for code, pct in pct_map.items():
                        code = str(code).zfill(6)
                        pct_val = _num(pct)
                        if pct_val is None:
                            continue
                        rows.append((pct_val, None, day_id, code))
                    if rows:
                        cur.executemany(CLOSE_PCT_SQL, rows)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            _available = True
            _last_error = ""
            return True
        except Exception as exc:
            _available = False
            _last_error = str(exc)
            return False


def _seal_from_row(r):
    return {
        "code": r["code"],
        "name": r["name"],
        "sampleTime": r["sample_time"],
        "bid1Price": _num(r["bid1_price"]),
        "bid1Volume": _num(r["bid1_volume"]),
        "ask1Price": _num(r["ask1_price"]),
        "ask1Volume": _num(r["ask1_volume"]),
        "lastPrice": _num(r["last_price"]),
        "prevClose": _num(r["prev_close"]),
        "sealAmount": _num(r["seal_amount"]),
        "sealRank": r["seal_rank"],
        "fetchedAt": _datetime_str(r["fetched_at"]),
    }


def get_seal(date):
    """某日全部封单行，按时点、封单额降序排。"""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM auction_seal WHERE trade_date = %s ORDER BY sample_time, seal_amount DESC",
                (date,),
            )
            rows = cur.fetchall()
    return [_seal_from_row(r) for r in rows]


def list_seal_dates(limit=120):
    """有封单数据的日期列表（含三个时点完成情况）。"""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT trade_date,
                       COUNT(DISTINCT code) AS pool_count,
                       GROUP_CONCAT(DISTINCT sample_time ORDER BY sample_time) AS times
                FROM auction_seal
                GROUP BY trade_date
                ORDER BY trade_date DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
    return [
        {
            "date": _date_str(r["trade_date"]),
            "poolCount": int(r["pool_count"] or 0),
            "times": (r["times"] or "").split(","),
        }
        for r in rows
    ]