# -*- coding: utf-8 -*-
"""盘前预案 L3/L5 预警：监听红牛一号 + 来去由心 的当日预案，出现信号立即飞书推送。

用法
    python scripts/preplan_alert.py --once              # 跑一次检查（可用于测试）
    python scripts/preplan_alert.py --loop              # 循环到 09:20（定时任务用这个）
    python scripts/preplan_alert.py --dry-run --date 2026-10-23   # 用归档数据演练，不发消息
    python scripts/preplan_alert.py --test-send         # 发一条测试消息

逻辑
  1) 每 60s 拉两个群的最新消息，发现当日的预案图片就下载 + OCR
  2) 分别解析：红牛 → bull/bear；来去由心 → 具体机会解析各段（短线/题材/趋势）bull/bear
  3) 两边都拿到当日预案后计算：
       L3 = 红牛看多 ∩ 来去看空(空头风标) ∩ 红牛原文含"反核/次弱/地板/跌停开/低吸/反包"
       L5 = 红牛看多 ∩ 来去【短线方面】看多
       L1 = 红牛看多（仅统计用）
  4) 有 L3/L5 就发飞书（默认发到「量化系统通知」群），并写状态避免重复推送
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import group_signals as gs          # noqa: E402
import group_backtest as gb         # noqa: E402

gb.fetch_kline = lambda *a, **k: []

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = r"E:/Program Files/npm-global/node_modules/@larksuite/cli/scripts/run.js"
CHAT_LAIQIU = "oc_c7a9f0d834a68a04dd8b67d10e25008b"      # 来去由心（老师发言·新）
CHAT_HONGNIU = "oc_808d538d57fb79e1abf5b4149175a886"     # 红牛一号 房东猫 新生代柚子
CHAT_NOTIFY = "oc_747e8ff8540da7ee38a160b5bbacf687"      # 量化系统通知（用户自己的群）
IMG_DIR = os.path.join(ROOT, "data", "feishu", "alert_img")
STATE = os.path.join(ROOT, "data", "feishu", "alert_state.json")
LOG = os.path.join(ROOT, "logs", "preplan_alert.log")

REVERSE_WORDS = ("反核", "次弱", "地板", "跌停开", "低吸", "反包", "低位")
BULL = ["低吸", "买入", "参与", "上车", "加仓", "一字", "卡位", "空间", "反包", "套利", "溢价",
        "仓位", "做T", "隔夜", "新高", "补涨", "卡异动", "先手", "强锁", "弹性", "晋级", "顶一",
        "给到仓位", "存在空间", "可关注", "关注趋势", "预期", "冲高", "板", "顶", "尝试",
        "可以做", "套利空间", "新高预期", "上限", "先手为主", "低接", "炸板低接", "做平台"]
BEAR = ["走人即可", "走人", "卖点", "止盈", "兑现", "回避", "断板", "放弃", "不参与", "冲高走",
        "减仓", "出局", "落袋", "不追", "放人", "高抛", "清仓", "无持续性", "只存在卖", "卡位失败",
        "被卡", "跑路", "走弱", "控仓"]
BULL.sort(key=len, reverse=True)
BEAR.sort(key=len, reverse=True)
DEF_FENG = ("风标", "不代表标的", "多头较弱", "多头、空头")
SECTIONS = {"短线方面": "short", "题材方面": "theme", "趋势票": "trend",
            "其他方向": "other", "其他对流": "other", "总结": "end"}
AMBIGUOUS = {"机器人", "半导体", "光伏", "储能", "军工", "芯片", "算力", "液冷", "光模块", "白酒"}


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    print(line)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def sh(args, timeout=90):
    r = subprocess.run(args, capture_output=True, timeout=timeout)
    return r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace"), r.returncode


def lark(*args, timeout=90):
    out, err, rc = sh(["node", RUN] + list(args), timeout=timeout)
    try:
        return json.loads(out)
    except Exception:
        return {"ok": False, "raw": out[:400], "err": err[:400], "rc": rc}


def recent_messages(chat_id, count=20):
    d = lark("im", "+chat-messages-list", "--chat-id", chat_id, "--page-size", str(count),
             "--order", "desc", "--no-reactions", "--format", "json", "--as", "user")
    msgs = ((d.get("data") or {}).get("messages") or [])
    msgs.sort(key=lambda m: m["create_time"])
    return msgs


def download_image(msg, out):
    fk = re.search(r"img_v3_[0-9a-zA-Z_\-]+", msg.get("content") or "")
    if not fk:
        return None
    if os.path.isfile(out):
        return out
    d = lark("im", "+messages-resources-download", "--as", "user",
             "--message-id", msg["message_id"], "--file-key", fk.group(0),
             "--type", "image", "--output", out, timeout=120)
    return out if os.path.isfile(out) else None


def ocr_text(path):
    try:
        from rapidocr_onnxruntime import RapidOCR
        global _OCR
        try:
            _OCR
        except NameError:
            _OCR = RapidOCR()
        res, _ = _OCR(path)
        return " ".join(x[1] for x in res) if res else ""
    except Exception as e:
        log("OCR_FAIL %s" % str(e)[:80])
        return ""


def polarity(text, names, forward=True):
    best = ("neutral", "")
    for a in names:
        for m in re.finditer(re.escape(a), text):
            if forward:
                if m.start() > 0 and text[m.start() - 1] in "的之":
                    continue
                ctx = text[m.end(): m.end() + 30]
            else:
                ctx = text[max(0, m.start() - 30): m.end() + 30]
            if any(w in ctx for w in BEAR):
                return "bear", text[max(0, m.start() - 6): m.end() + 26]
            if any(w in ctx for w in BULL) and best[0] == "neutral":
                best = ("bull", text[max(0, m.start() - 6): m.end() + 26])
    return best


def parse_plans(texts, names_ctx):
    """texts: [(时间, 文本)]；返回 (bull{code:name}, bear{code:name}, raw_notes)"""
    n2c, keys = names_ctx
    bull, bear, notes = {}, {}, []
    joined = "\n".join(t for _, t in texts)
    # 1) 风标句剔除（两头都适用）
    feng = ""
    for seg in re.split(chr(10) + "|[。；]", joined):
        if any(k in seg for k in DEF_FENG) or ("多头" in seg and "空头" in seg):
            feng += seg + "\n"
    # 2) 分段（来去有段落；红牛没有则全部 other）
    cur = "other"
    for ln in re.split(chr(10), joined):
        s = ln.strip()
        if not s:
            continue
        for k, v in SECTIONS.items():
            if s.startswith(k):
                cur = v
                break
        if cur == "end":
            break
        hits = []
        for nm in keys:
            if nm in s and not any(nm in h or h in nm for h in hits):
                hits.append(nm)
        for nm in hits[:4]:
            if nm in feng:            # 只出现在风标句里 → 忽略
                continue
            code = n2c[nm]
            pol, sn = polarity(s, (nm,))
            if pol == "bull":
                bull.setdefault(code, {"name": nm, "section": cur, "snippet": s[:60]})
            elif pol == "bear":
                bear.setdefault(code, {"name": nm, "section": cur, "snippet": s[:60]})
    return bull, bear, notes


def _default_webhook():
    """优先环境变量；否则用 config.json 里项目已有的群自定义机器人 webhook（无需任何授权）"""
    env = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if env:
        return env
    try:
        cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
        return (cfg.get("feishu") or {}).get("webhook", "").strip()
    except Exception:
        return ""


WEBHOOK = _default_webhook()


def send_webhook(md):
    """用群自定义机器人 webhook 发（服务器部署推荐：无需 user token）"""
    import urllib.request
    body = json.dumps({"msg_type": "text",
                       "content": {"text": md.replace("**", "").replace("　", "  ")}},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(WEBHOOK, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            j = json.loads(r.read().decode("utf-8"))
        ok = j.get("code") == 0 or j.get("StatusCode") == 0
        log("WEBHOOK %s %s" % ("OK" if ok else "FAIL", str(j)[:160]))
        return ok
    except Exception as e:
        log("WEBHOOK ERR %s" % str(e)[:120])
        return False


def send_feishu(md):
    if WEBHOOK:
        return send_webhook(md)
    d = lark("im", "+messages-send", "--as", "user", "--chat-id", CHAT_NOTIFY,
             "--markdown", md, timeout=90)
    ok = bool(d.get("ok"))
    log("SEND %s %s" % ("OK" if ok else "FAIL", json.dumps(d, ensure_ascii=False)[:200]))
    return ok


def load_names():
    uniq, keys, name_of, short2 = gs.build_dict()
    raw = json.load(open(gs.STOCKDICT, encoding="utf-8"))
    n2c = {k: str(v).zfill(6) for k, v in raw.items()}
    keys = sorted([k for k in n2c if len(k) >= 3 and not any(ch.isdigit() for ch in k)
                   and k not in AMBIGUOUS], key=len, reverse=True)
    return n2c, keys


def compute_layers(hn_bull, hn_bear, lq_bull, lq_bear):
    A = set(hn_bull) - set(hn_bear)
    lq_short = {c for c, x in lq_bull.items() if x.get("section") == "short"}
    lq_bear = set(lq_bear)
    L3 = {c for c in (A & lq_bear) if any(w in (hn_bull.get(c, {}).get("snippet") or "")
                                          for w in REVERSE_WORDS)}
    L5 = (A & lq_short) - lq_bear
    return A, L3, L5


def explain_layers(hn_bull, hn_bear, lq_bull, lq_bear):
    """输出每条 L3/L5 的判定证据（纯规则，便于事后核对）"""
    A, L3, L5 = compute_layers(hn_bull, hn_bear, lq_bull, lq_bear)
    out = []
    for c in sorted(L3):
        snip = (hn_bull.get(c) or {}).get("snippet") or ""
        hit = next((w for w in REVERSE_WORDS if w in snip), "?")
        lqs = (lq_bear.get(c) or {}).get("snippet") or ""
        out.append("L3 %s %s | 红牛:看多·命中反核词「%s」 | 来去:空头风标·%s"
                   % (c, (hn_bull.get(c) or {}).get("name", ""), hit, _flat(lqs, 30)))
    for c in sorted(L5):
        lqs = (lq_bull.get(c) or {}).get("snippet") or ""
        out.append("L5 %s %s | 红牛:看多 | 来去:短线方面看多·%s"
                   % (c, (lq_bull.get(c) or {}).get("name", ""), _flat(lqs, 30)))
    return out


def _flat(x, n=40):
    return re.sub(r"\s+", " ", (x or "")).strip()[:n]


def fmt_list(codes, bull, lq_bull, lq_bear):
    out = []
    for c in sorted(codes):
        nm = (bull.get(c) or {}).get("name") or (lq_bull.get(c) or {}).get("name") or c
        out.append("%s(%s)" % (nm, c))
    return "、".join(out)


def build_message(date, hn_bull, hn_bear, lq_bull, lq_bear):
    A, L3, L5 = compute_layers(hn_bull, hn_bear, lq_bull, lq_bear)
    if not L3 and not L5:
        return None
    lines = ["**%s 预案信号预警**" % date, ""]
    if L3:
        lines.append("🔴 **L3 超跌反核（重点）**")
        lines.append("　" + fmt_list(L3, hn_bull, lq_bull, lq_bear))
        for c in sorted(L3):
            hn_s = (hn_bull.get(c) or {}).get("snippet") or ""
            lq_s = (lq_bear.get(c) or {}).get("snippet") or ""
            lines.append("　· %s｜红牛：%s｜来去：%s" % (c, _flat(hn_s, 42), _flat(lq_s, 36)))
        lines.append("　→ 执行：**开盘跌幅 ≥5% 才买**，仓位减半，破前低即走")
        lines.append("　→ 历史：n=27，D+1 胜率 81.5% / 均值 +4.88%")
        lines.append("")
    if L5:
        lines.append("🟡 **L5 短线接力**")
        lines.append("　" + fmt_list(L5, hn_bull, lq_bull, lq_bear))
        for c in sorted(L5):
            lq_s = (lq_bull.get(c) or {}).get("snippet") or ""
            lines.append("　· %s｜来去：%s" % (c, _flat(lq_s, 44)))
        lines.append("　→ 执行：**非一字或竞价额≥封单额×10%** 才买，持 3–5 日")
        lines.append("　→ 历史：可买子集 n=11，D+1 72.7% / D+5 +5.10%")
        lines.append("")
    lines.append("**红牛看多合计 %d 只**（L1 参考：n=124，D+1 62.9%%）" % len(A))
    return "\n".join(lines)


def once(dry_run=False, date=None):
    n2c, keys = load_names()
    if dry_run:
        # 用归档演练
        lq = json.load(open(os.path.join(ROOT, "data", "laiqiu", "plans_ocr", "fixed.json"),
                            encoding="utf-8")).get(date or "", {})
        lq_bull = {x["code"]: x for x in lq.get("bull", [])}
        lq_bear = {x["code"]: x for x in lq.get("bear", [])}
        # 补齐 bear 的原文片段（fixed.json 里 bear 的 snippet 为空）
        rec0 = os.path.join(ROOT, "data", "laiqiu", "plans_ocr", "%s.json" % (date or ""))
        if os.path.isfile(rec0):
            d0 = json.load(open(rec0, encoding="utf-8"))
            body = ""
            for src in d0.get("sources") or []:
                fp = os.path.join(ROOT, "data", "laiqiu", "ocr", "%s.json" % src["key"])
                if os.path.isfile(fp):
                    body += (json.load(open(fp, encoding="utf-8")).get("text") or "") + chr(10)
            for c, x in lq_bear.items():
                if not x.get("snippet"):
                    i = body.find(x.get("name") or "")
                    if i >= 0:
                        x["snippet"] = body[max(0, i - 20): i + 30]
        hn_bull, hn_bear = {}, {}
        fp = os.path.join(ROOT, "data", "feishu", "hongniu", "plans_fixed.jsonl")
        plans_by_key = {}
        for line in open(os.path.join(ROOT, "data", "feishu", "hongniu", "plans.jsonl"), encoding="utf-8"):
            p = json.loads(line)
            plans_by_key[p["key"]] = p
        for line in open(fp, encoding="utf-8"):
            x = json.loads(line)
            if x["day"] != (date or ""):
                continue
            t = (plans_by_key.get(x.get("src")) or {}).get("text") or ""
            n = x["name"]
            i = t.find(n)
            rec = {"name": n, "snippet": t[max(0, i - 8):i + 40] if i >= 0 else ""}
            if x["polarity"] == "bull":
                hn_bull[x["code"]] = rec
            elif x["polarity"] == "bear":
                hn_bear[x["code"]] = rec
        md = build_message(date, hn_bull, hn_bear, lq_bull, lq_bear)
        print(md or "(当日无 L3/L5 信号)")
        if md and not dry_run:
            send_feishu(md)
        return md

    today = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(IMG_DIR, exist_ok=True)
    texts = {"hn": [], "lq": []}
    for tag, chat in (("hn", CHAT_HONGNIU), ("lq", CHAT_LAIQIU)):
        msgs = recent_messages(chat, 25)
        for m in msgs:
            if m["create_time"][:10] != today:
                continue
            if m.get("msg_type") != "image":
                continue
            out = os.path.join(IMG_DIR, "%s_%s.png" % (tag, m["message_id"][-8:]))
            p = download_image(m, out)
            if not p:
                continue
            t = ocr_text(p)
            if "预案" in t or "策略" in t or "短线方面" in t or "具体机会" in t:
                texts[tag].append((m["create_time"], t))
    if not texts["hn"] or not texts["lq"]:
        log("预案未齐：红牛 %d 张 / 来去 %d 张" % (len(texts["hn"]), len(texts["lq"])))
        return None
    hn_bull, hn_bear, _ = parse_plans(texts["hn"], (n2c, keys))
    lq_bull, lq_bear, _ = parse_plans(texts["lq"], (n2c, keys))
    log("解析：红牛 多%d/空%d，来去 多%d/空%d" % (len(hn_bull), len(hn_bear), len(lq_bull), len(lq_bear)))
    for line in explain_layers(hn_bull, hn_bear, lq_bull, lq_bear):
        log("  证据 " + line)
    md = build_message(today, hn_bull, hn_bear, lq_bull, lq_bear)
    if not md:
        log("当日无 L3/L5 信号")
        return None
    st = {}
    if os.path.isfile(STATE):
        try:
            st = json.load(open(STATE, encoding="utf-8"))
        except Exception:
            st = {}
    if st.get("date") == today and st.get("sent_hash") == hash(md):
        log("已推送过，跳过")
        return md
    if send_feishu(md):
        json.dump({"date": today, "sent_hash": hash(md)}, open(STATE, "w", encoding="utf-8"))
    return md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", default="")
    ap.add_argument("--test-send", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--until", default="09:20")
    a = ap.parse_args()

    if a.test_send:
        send_feishu("**测试** 预案 L3/L5 预警通道正常 ✅")
        return
    if a.dry_run:
        once(dry_run=True, date=a.date or datetime.now().strftime("%Y-%m-%d"))
        return
    if a.once:
        once()
        return
    if a.loop:
        hh, mm = map(int, a.until.split(":"))
        deadline = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
        log("开始监听（每 %ds，截止 %s）" % (a.interval, a.until))
        while datetime.now() < deadline:
            try:
                once()
            except Exception as e:
                log("ERR %s" % str(e)[:200])
            time.sleep(a.interval)
        log("监听结束")
        return
    ap.print_help()


if __name__ == "__main__":
    main()
