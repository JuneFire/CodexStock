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
import ai_client                    # noqa: E402
import l3_strategy                  # noqa: E402
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

AI_ENABLED = os.environ.get("PREPLAN_AI", "").strip() in ("1", "true", "yes")
AI_CACHE = os.path.join(ROOT, "data", "feishu", "ai_cache.json")

REVERSE_WORDS = ("反核", "次弱", "地板", "跌停开", "低吸", "反包", "低位")
BULL = ["低吸", "买入", "参与", "上车", "加仓", "一字", "卡位", "空间", "反包", "套利", "溢价",
        "仓位", "做T", "隔夜", "新高", "补涨", "卡异动", "先手", "强锁", "弹性", "晋级", "顶一",
        "给到仓位", "存在空间", "可关注", "关注趋势", "预期", "冲高", "板", "顶", "尝试",
        "可以做", "套利空间", "新高预期", "上限", "低接", "炸板低接", "做平台"]
BEAR = ["走人即可", "走人", "卖点", "止盈", "兑现", "回避", "断板", "放弃", "不参与", "冲高走",
        "减仓", "出局", "落袋", "不追", "放人", "高抛", "清仓", "无持续性", "只存在卖", "卡位失败",
        "被卡", "跑路", "走弱", "控仓", "补跌", "杀跌", "走完", "出货", "停牌", "天地板"]
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


def _lark_cmd():
    """Windows 用 node + 本地 run.js；Linux 服务器直接用 lark-cli 可执行文件"""
    if os.path.isfile(RUN):
        return ["node", RUN]
    return ["lark-cli"]


def lark(*args, timeout=90):
    out, err, rc = sh(_lark_cmd() + list(args), timeout=timeout)
    try:
        return json.loads(out)
    except Exception:
        return {"ok": False, "raw": out[:400], "err": err[:400], "rc": rc}


def norm(text):
    """统一清洗：去标签/零宽字符 + 反混淆（mǎi→买、bαn→板、苍→仓…）"""
    return gs.clean(text or "")


def recent_messages(chat_id, count=20):
    d = lark("im", "+chat-messages-list", "--chat-id", chat_id, "--page-size", str(count),
             "--order", "desc", "--no-reactions", "--format", "json", "--as", "user")
    msgs = ((d.get("data") or {}).get("messages") or [])
    msgs.sort(key=lambda m: m["create_time"])
    return msgs


def messages_between(chat_id, start_iso, end_iso):
    d = lark("im", "+chat-messages-list", "--chat-id", chat_id, "--page-size", "50",
             "--order", "asc", "--start", start_iso, "--end", end_iso,
             "--page-all", "--page-limit", "10", "--no-reactions",
             "--format", "json", "--as", "user", timeout=180)
    msgs = ((d.get("data") or {}).get("messages") or [])
    msgs.sort(key=lambda m: m["create_time"])
    return msgs


PLAN_HINTS = ("预案", "策略", "短线方面", "具体机会解析", "大局观", "今日多头", "情绪：",
              "题材：", "连板", "断板", "低吸", "反核")


def looks_like_plan(text):
    if len(text) < 80:
        return False
    hit = sum(1 for k in PLAN_HINTS if k in text)
    return hit >= 2 or ("预案" in text) or ("策略" in text[:60])


def ai_is_plan(text):
    """AI 判定：这段是不是A股早盘预案/计划（只在启发式拿不准时调用）"""
    if not AI_ENABLED:
        return None
    out, err = ai_client.chat_json(
        "判断给定文本是不是A股「早盘预案/交易计划/复盘计划」（会给出第二天要参与的股票与方向）。"
        '只输出 JSON：{"is_plan":true|false,"reason":"不超过15字"}',
        text[:1200], retries=1, max_tokens=80)
    if not out:
        log("AI_PLAN_FAIL %s" % err)
        return None
    return bool(out.get("is_plan"))


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


CJK = re.compile(r"[一-鿿]")


ACT_NEAR = ("竞价", "反核", "低吸", "买", "卖", "看", "板", "开", "走", "加", "减", "砸",
            "断", "强", "弱", "卡", "吸", "打", "做", "兑", "仓", "分时", "预期")


def _alias_ok(text, pos, ln):
    """2 字简称（冰山冷热→冰山）容易误配，要求：右侧非汉字 / 或紧跟动作词 / 或左侧是标点"""
    if ln > 2:
        return True
    end = pos + ln
    if end >= len(text):
        return True
    if not CJK.match(text[end]):
        return True
    if any(w in text[end:end + 8] for w in ACT_NEAR):
        return True
    if pos == 0 or not CJK.match(text[pos - 1]):
        return True
    return False


def name_positions(text, keys):
    """返回 [(pos, len, name)]，按出现位置排序，用于推断"下一只票在哪"来截断窗口"""
    out = []
    for nm in keys:
        for m in re.finditer(re.escape(nm), text):
            if not _alias_ok(text, m.start(), len(nm)):
                continue
            out.append((m.start(), len(nm), nm))
    out.sort()
    return out


def polarity_bounded(text, name, positions, maxlen=40):
    """只看「名字之后、下一只票之前」的部分判极性（bear 优先）"""
    hits = [p for p in positions if p[2] == name]
    if not hits:
        return "neutral", ""
    for pos, ln, nm in hits:
        end = pos + ln
        nxt = next((p[0] for p in positions if p[0] > end), len(text))
        ctx = text[end: min(end + maxlen, nxt)]
        snip = text[max(0, pos - 6): min(end + maxlen, nxt)].replace(chr(10), " ")
        if any(w in ctx for w in BEAR):
            return "bear", snip
        if any(w in ctx for w in BULL):
            return "bull", snip
    return "neutral", ""


def polarity(text, names, forward=True):
    best = ("neutral", "")
    for a in names:
        for m in re.finditer(re.escape(a), text):
            if forward:
                if m.start() > 0 and text[m.start() - 1] in "的之":
                    continue
                ctx = text[m.end(): m.end() + 45]
            else:
                ctx = text[max(0, m.start() - 30): m.end() + 30]
            if any(w in ctx for w in BEAR):
                return "bear", text[max(0, m.start() - 6): m.end() + 26]
            if any(w in ctx for w in BULL) and best[0] == "neutral":
                best = ("bull", text[max(0, m.start() - 6): m.end() + 26])
    return best


def parse_plans(texts, names_ctx):
    """texts: [(时间, 文本)]；返回 (bull, bear, notes)"""
    n2c, keys = names_ctx
    bull, bear, notes = {}, {}, []
    fk = {"bull": {}, "bear": {}}
    evid, code_of = {}, {}
    joined = "\n".join(t for _, t in texts)
    flat = "".join(t for _, t in texts)          # 去掉换行，抵消 OCR 折行
    # 1b) 只保留「决策段」：从 具体机会解析/短线方面 起，到 总结/以上 止
    #     （大局观/情绪段只是描述，同一只票在那里被提一次不代表方向）
    _i = flat.find("具体机会解析")
    if _i < 0:
        for _mk in ("短线方面", "题材方面", "趋势票"):
            _i = flat.find(_mk)
            if _i >= 0:
                break
    if _i > 0:
        flat = flat[_i:]
        for _end in ("总结", "以上仅", "以上是"):
            _j = flat.find(_end)
            if _j > 0:
                flat = flat[:_j]
                break
    positions = name_positions(flat, keys)        # 必须在裁剪之后算，否则索引错位
    # 2) 分段：卡片类消息常常没有换行，长句再按 。；切分
    raw_lines = re.split(chr(10), joined)
    segs = []
    for ln in raw_lines:
        if len(ln) > 200:
            segs.extend([x for x in re.split("[。；]", ln) if x.strip()])
        else:
            segs.append(ln)
    cur = "other"
    for ln in segs:
        s = ln.strip()
        if not s:
            continue
        if any(k in s for k in DEF_FENG) or ("多头" in s and "空头" in s):
            # 情绪风标句：不进 bull/bear，但「空头看」名单是 L3 的历史口径来源
            kp = s.find("空头看")
            for nm in [k for k in keys if k in s]:
                if not _alias_ok(s, s.find(nm), len(nm)):
                    continue
                code = code_of.get(nm) or n2c.get(nm)
                if not code:
                    continue
                pos = s.find(nm)
                side = "bear" if (kp >= 0 and pos > kp) else "bull"
                fk[side][code] = {"name": nm, "section": "fengkiao", "snippet": s[:60], "src": "rule"}
            continue
        for k, v in SECTIONS.items():
            if k in s and len(s) <= 220:
                cur = v
                break
        if cur == "end":
            break
        hits = []
        for nm in keys:
            if nm in n2c:
                code_of.setdefault(nm, n2c[nm])
            if nm in s and not any(nm in h or h in nm for h in hits):
                hits.append(nm)
        for nm in hits[:8]:
            pol, sn = polarity_bounded(flat, nm, positions)
            evid.setdefault(nm, []).append({"section": cur, "polarity": pol,
                                            "line": s[:70], "snip": sn})
    # 3) 汇总证据：优先决策段（short/theme/trend），段内有空则退到 other
    for nm, ev in evid.items():
        prefs = [e for e in ev if e["section"] in ("short", "theme", "trend")]
        pool = prefs or ev
        bears = [e for e in pool if e["polarity"] == "bear"]
        bulls = [e for e in pool if e["polarity"] == "bull"]
        chosen = bears[0] if bears else (bulls[0] if bulls else None)
        if not chosen:
            notes.append({"line": pool[0]["line"], "section": pool[0]["section"], "name": nm})
            continue
        code = code_of.get(nm)
        if not code:
            continue
        rec = {"name": nm, "section": chosen["section"],
               "snippet": (chosen.get("snip") or chosen["line"])[:70], "src": "rule"}
        if chosen["polarity"] == "bull":
            bull[code] = rec
            bear.pop(code, None)
        else:
            bear[code] = rec
            bull.pop(code, None)
    for d in (bull, bear):
        for code in list(d):
            fk["bull"].pop(code, None)
            fk["bear"].pop(code, None)      # 决策段已判定的票，不再算风标
    return bull, bear, notes, fk


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


C2N = {}


TIER_STRONG = ("低吸", "买点", "买入", "打底", "埋伏", "加仓", "接回", "强锁", "恐慌吸", "水下吸")
TIER_MID = ("反包", "修复", "转强", "套利", "空间", "新高", "补涨", "卡异动", "反核",
            "换手板", "溢价", "止跌", "跟踪", "走强", "关注")


def tier_tag(snippet):
    """给推送里的每只票标：强/中/弱/否决（让人一眼看出红牛到底说没说买）"""
    t = snippet or ""
    if not t:
        return "无原文"
    if any(w in t for w in ("断板", "兑现", "补跌", "走弱", "清仓", "减仓", "走人", "走完", "不接")):
        return "否决"
    if any(w in t for w in TIER_STRONG):
        return "强"
    if any(w in t for w in TIER_MID):
        return "中"
    return "弱"


def full_name(code, fallback=""):
    """代码 → 全名（避免推送里出现「冰山」这种简称）"""
    if not C2N:
        try:
            raw = json.load(open(gs.STOCKDICT, encoding="utf-8"))
            for nm, c in raw.items():
                C2N[str(c).zfill(6)] = nm
        except Exception:
            pass
    return C2N.get(str(code).zfill(6)) or fallback or code


def load_names():
    uniq, keys, name_of, short2 = gs.build_dict()
    raw = json.load(open(gs.STOCKDICT, encoding="utf-8"))
    n2c = {k: str(v).zfill(6) for k, v in raw.items()}
    # 简称（冰山冷热→冰山、哈药股份→哈药…）：唯一映射才启用（组信号层已做泛词过滤）
    for a, c in (short2 or {}).items():
        if a not in AMBIGUOUS and a not in n2c:
            n2c[a] = c
    for a, c in (uniq or {}).items():
        if a not in AMBIGUOUS and a not in n2c:
            n2c[a] = c
    keys = sorted([k for k in n2c if len(k) >= 2 and not any(ch.isdigit() for ch in k)
                   and k not in AMBIGUOUS], key=len, reverse=True)
    return n2c, keys


_AI_SYS = ("你是 A 股短线复盘助手。给定若干净化后的预案文本片段（可能含 OCR 错字）与候选股票名，"
           "判断每个片段里被点名的股票是【看多】【看空】还是【仅提及】。"
           "规则：出现 低吸/参与/做T/卡位/关注/博弈/上车/仓位/晋级/先手/弹性 等→看多；"
           "出现 回避/断板/走人/卖点/放弃/减仓/兑现/不参与/控仓/无持续性 等→看空；"
           "仅描述事实（几板、题材、涨跌）没有动作词→仅提及。"
           "只允许从候选名里选，不得臆造。严格输出 JSON："
           '{"decisions":[{"i":行号,"name":"候选名","polarity":"bull|bear|mention","reason":"不超过20字"}]}')


def _ai_cache_load():
    try:
        return json.load(open(AI_CACHE, encoding="utf-8"))
    except Exception:
        return {}


def _ai_cache_save(c):
    os.makedirs(os.path.dirname(AI_CACHE), exist_ok=True)
    try:
        json.dump(c, open(AI_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def ai_review(lines, n2c, keys, section_of=None):
    """对「规则判为中性但出现候选名」的行做 LLM 复核。

    lines: [(行号, 文本)]；返回 {行号: (name, polarity, reason)}
    """
    if not AI_ENABLED or not lines:
        return {}
    import hashlib
    cands, packed = [], []
    for i, text in lines:
        hit = [nm for nm in keys if nm in text][:6]
        if not hit:
            continue
        packed.append((i, text, hit))
        for nm in hit:
            if nm not in cands:
                cands.append(nm)
    if not packed:
        return {}
    cache = _ai_cache_load()
    key = hashlib.sha1(json.dumps([t for _, t, _ in packed], ensure_ascii=False).encode()).hexdigest()[:16]
    if key in cache:
        return {int(k): tuple(v) for k, v in cache[key].items()}
    user = json.dumps({
        "候选名": cands[:40],
        "片段": ["%d|%s" % (i, re.sub(r"\s+", " ", t)[:220]) for i, t, _ in packed],
    }, ensure_ascii=False)
    out, err = ai_client.chat_json(_AI_SYS, user, retries=1)
    if not out:
        log("AI_FAIL %s" % err)
        return {}
    res = {}
    for d in out.get("decisions") or []:
        try:
            i = int(d.get("i"))
        except Exception:
            continue
        nm = d.get("name") or ""
        pol = d.get("polarity")
        if nm not in n2c:
            continue
        res[i] = (nm, "bull" if pol == "bull" else ("bear" if pol == "bear" else "neutral"),
                  d.get("reason") or "")
    log("AI_OK 复核 %d 段 → 判定 %d 条" % (len(packed), len(res)))
    cache[key] = {str(k): v for k, v in res.items()}
    _ai_cache_save(cache)
    return res


def compute_layers(hn_bull, hn_bear, lq_bull, lq_bear, fk_bear=None):
    A = set(hn_bull) - set(hn_bear)
    lq_short = {c for c, x in lq_bull.items() if x.get("section") == "short"}
    lq_bear = set(lq_bear) | set(fk_bear or {})
    L3 = {c for c in (A & lq_bear) if any(w in (hn_bull.get(c, {}).get("snippet") or "")
                                          for w in REVERSE_WORDS)}
    L5 = (A & lq_short) - lq_bear
    return A, L3, L5


def explain_layers(hn_bull, hn_bear, lq_bull, lq_bear, fk_bear=None):
    """输出每条 L3/L5 的判定证据（纯规则，便于事后核对）"""
    A, L3, L5 = compute_layers(hn_bull, hn_bear, lq_bull, lq_bear, fk_bear)
    out = []
    for c in sorted(L3):
        snip = (hn_bull.get(c) or {}).get("snippet") or ""
        hit = next((w for w in REVERSE_WORDS if w in snip), "?")
        lqs = (lq_bear.get(c) or {}).get("snippet") or ""
        out.append("L3 %s %s | 红牛:看多(%s)·命中反核词「%s」 | 来去:空头风标(%s)·%s"
                   % (c, (hn_bull.get(c) or {}).get("name", ""),
                      (hn_bull.get(c) or {}).get("src", "rule"), hit,
                      (lq_bear.get(c) or {}).get("src", "rule"), _flat(lqs, 30)))
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


def build_message(date, hn_bull, hn_bear, lq_bull, lq_bear, fk_bear=None):
    A, L3, L5 = compute_layers(hn_bull, hn_bear, lq_bull, lq_bear, fk_bear)
    if not A:
        return None
    lines = ["**%s 预案信号预警**" % date, ""]
    # ---- L1：红牛预案看多主清单 ----
    lines.append("📋 **L1 红牛预案看多（%d 只）**" % len(A))
    for c in sorted(A, key=lambda x: full_name(x, (hn_bull.get(x) or {}).get("name", ""))):
        nm = full_name(c, (hn_bull.get(c) or {}).get("name", c))
        tag = ""
        if c in L3:
            tag = " 🔴L3"
        elif c in L5:
            tag = " 🟡L5"
        tt = tier_tag((hn_bull.get(c) or {}).get("snippet", ""))
        lines.append("　· %s(%s) [%s]%s" % (nm, c, tt, tag))
    lines.append("　→ 执行：**09:25 竞价后选涨幅最高的一只，开盘满仓**；D+1 收盘卖（隔日卖）")
    lines.append("　→ 历史（T+1 合规，56 笔）：1 万 → 6.49 万 / **+548.6%** / 胜率 62.5% / 回撤 -19.4%")
    lines.append("")
    if L3:
        lines.append("🔴 **L3 超跌反核（重点）**")
        lines.append("　" + fmt_list(L3, hn_bull, lq_bull, lq_bear))
        for c in sorted(L3):
            hn_s = (hn_bull.get(c) or {}).get("snippet") or ""
            lq_s = (lq_bear.get(c) or {}).get("snippet") or ""
            lines.append("　· %s｜红牛：%s｜来去：%s" % (c, _flat(hn_s, 42), _flat(lq_s, 36)))
        lines.append("　→ 执行：**开盘全仓买入**；盘中跌破买入价 **-5%** 立即止损")
        lines.append("　→ D+1 收绿（收盘<前收）清仓；收红尾盘卖 **1/3**")
        lines.append("　→ 剩余：每日新高才持有，不新高当日收盘清；D+3 再卖 1/2；D+5 尾盘清")
        lines.append("　→ 历史（满仓复利/未计费）：23 笔，+378.6%，最大回撤 -14.3%，胜率 60.9%")
        lines.append("")
    if L5:
        lines.append("🟡 **L5 短线接力**")
        for c in sorted(L5, key=lambda x: full_name(x, x)):
            nm = full_name(c, (lq_bull.get(c) or {}).get("name", c))
            lq_s = (lq_bull.get(c) or {}).get("snippet") or ""
            lines.append("　· %s(%s)｜来去：%s" % (nm, c, _flat(lq_s, 44)))
        lines.append("　→ 执行：**非一字或竞价额≥封单额×10%** 才买，持 3–5 日")
        lines.append("　→ 历史：可买子集 n=11，D+1 72.7% / D+5 +5.10%")
        lines.append("")
    ai_cnt = sum(1 for d in (hn_bull, hn_bear, lq_bull, lq_bear)
                 for v in d.values() if str(v.get("src", "")).startswith("ai"))
    if ai_cnt:
        lines.append("_（其中 %d 条由 AI 复核补充判定，规则层未决）_" % ai_cnt)
    return "\n".join(lines)


def collect_side(chat_id, date, tag):
    """采集某群当日 00:00 → now 的预案内容（文字 + 内嵌图片 OCR）"""
    start = "%sT00:00:00+08:00" % date
    end = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    try:
        msgs = messages_between(chat_id, start, end)
    except Exception as e:
        log("FETCH_FAIL %s %s" % (tag, str(e)[:120]))
        return [], {"msgs": 0, "texts": 0, "imgs": 0}
    os.makedirs(IMG_DIR, exist_ok=True)
    texts, n_img, n_txt = [], 0, 0
    for m in msgs:
        raw = m.get("content") or ""
        # 3a) 文本类消息（text/post/interactive 卡片）
        t = norm(raw)
        hhmm = m["create_time"][11:16]
        in_morning = "06:00" <= hhmm <= "09:35"
        if not in_morning:
            continue                     # 只采集早盘时段（预案只在这时发）
        if len(t) >= 30:
            ok = looks_like_plan(t) or in_morning
            if not ok and len(t) >= 150:
                v = ai_is_plan(t)
                if v is True:
                    ok = True
                    log("AI 判定为预案：%s %s" % (tag, m["create_time"][11:16]))
            if ok:
                texts.append((m["create_time"], t))
                n_txt += 1
                log("  文本预案 %s %s len=%d" % (tag, m["create_time"][11:16], len(t)))
        # 3b) 内嵌图片（post 里的 ![Image](img_v3_…) 或 image 消息）
        for fk in set(re.findall(r"img_v3_[0-9a-zA-Z_\-]+", raw)):
            n_img += 1
            out = os.path.join(IMG_DIR, "%s_%s_%s.png" % (tag, m["message_id"][-8:], fk[-6:]))
            p = download_image({"message_id": m["message_id"], "content": raw}, out)
            if not p:
                continue
            ot = norm(ocr_text(p))
            if ot and (looks_like_plan(ot) or len(ot) >= 40):
                texts.append((m["create_time"], ot))
                log("  图片预案 %s %s len=%d" % (tag, m["create_time"][11:16], len(ot)))
    return texts, {"msgs": len(msgs), "texts": n_txt, "imgs": n_img}


AUDIT_CACHE = os.path.join(ROOT, "data", "feishu", "ai_audit_cache.json")
_AUDIT_SYS = (
    "你是A股短线预案解析器。输入若干条「股票名 + 预案片段」，判断该股票在片段中的方向。"
    "输出 JSON 对象，形如 {\"verdicts\":[{\"i\":0,\"name\":\"某股\",\"v\":\"bull\",\"why\":\"低吸买点\"}]}，不要解释。"
    "bull=明确看多/建议参与(低吸/买点/打底/埋伏/加仓/接回/强锁买点)；"
    "bear=明确看空或提示出局(断板/兑现/补跌/走弱/减仓/清仓/走人/不参与/回避)；"
    "neutral=仅提及/条件未定/纯描述。"
    "「先手为主」「观察」「看戏」「分歧预期」一律 neutral。"
)


def _audit_cache_load():
    try:
        return json.load(open(AUDIT_CACHE, encoding="utf-8"))
    except Exception:
        return {}


def ai_audit(items):
    """对当日红牛看多票逐只做 LLM 复核。

    items: [(code, name, snippet)]；返回 {code: (verdict, why)}，verdict ∈ bull/bear/neutral
    结果按 (name+snippet) 哈希缓存，同一片段不重复付费。
    """
    if not items:
        return {}
    if not ai_client.available():
        return {}
    import hashlib
    cache = _audit_cache_load()
    todo, res = [], {}
    for code, name, snip in items:
        k = hashlib.sha1((str(name) + "|" + str(snip)).encode("utf-8")).hexdigest()[:16]
        if k in cache:
            res[code] = tuple(cache[k])
        else:
            todo.append((code, name, snip, k))
    if todo:
        payload = {"数量": len(todo),
                   "条目": ["%s|%s" % (t[1], re.sub(r"\s+", " ", t[2] or "")[:160]) for t in todo]}
        out, err = ai_client.chat_json(_AUDIT_SYS, json.dumps(payload, ensure_ascii=False),
                                       retries=1, max_tokens=800)
        arr = None
        if isinstance(out, dict):
            for kk in ("verdicts", "r", "data", "结果"):
                if isinstance(out.get(kk), list):
                    arr = out[kk]
                    break
        elif isinstance(out, list):
            arr = out
        if arr is None:
            log("AI_AUDIT_FAIL %s" % (err or str(out)[:80]))
        else:
            for idx, it in enumerate(arr):
                if not isinstance(it, dict):
                    continue
                i = it.get("i")
                v = str(it.get("v") or it.get("verdict") or "").strip().lower()
                if v not in ("bull", "bear", "neutral"):
                    continue
                tgt = None
                if isinstance(i, int) and 0 <= i < len(todo):
                    tgt = todo[i]
                elif it.get("name"):
                    tgt = next((t for t in todo if t[1] == it["name"]), None)
                if not tgt:
                    continue
                res[tgt[0]] = (v, str(it.get("why") or "")[:14])
                cache[tgt[3]] = [v, str(it.get("why") or "")[:14]]
            try:
                json.dump(cache, open(AUDIT_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
            except Exception:
                pass
        log("AI_AUDIT %d 只（缓存 %d，新判 %d）" % (len(res), len(cache) - len(todo), len(todo)))
    return res


TIER_MARK = {"bull": "✅多", "bear": "⛔空", "neutral": "·中性"}


def send_l1_update(date, items, added, version, final=False):
    """L1 分批推送。items: [{code,name,tier,tag,why}]；added: 本次新增的 code 列表"""
    head = ("**%s L1 红牛看多·定稿（%s）**" % (date, datetime.now().strftime("%H:%M"))
            if final else
            "**%s L1 红牛看多·更新 #%d（%s）**" % (date, version, datetime.now().strftime("%H:%M")))
    lines = [head, ""]
    if added and not final:
        names = [x["name"] for x in items if x["code"] in added]
        lines.append("🆕 新增 **%d 只**：%s" % (len(added), "、".join(names)))
        lines.append("")
    for x in items:
        marks = []
        if x.get("tier"):
            marks.append("[%s]" % x["tier"])
        if x.get("ai"):
            marks.append(TIER_MARK.get(x["ai"], ""))
        if x.get("tag"):
            marks.append(x["tag"])
        lines.append("　· %s(%s) %s" % (x["name"], x["code"], " ".join(m for m in marks if m)))
        if x.get("why"):
            lines.append("　　　AI：%s" % x["why"])
        if x.get("snip"):
            lines.append("　　　原文：%s" % x["snip"][:52])
    ok = [x for x in items if x.get("ai") != "bear"]
    lines.append("")
    if final:
        lines.append("**→ 09:25 竞价后从上面 %d 只里选涨幅最高的一只，开盘满仓**" % len(ok))
        lines.append("　· ⛔空 的票不进候选（AI 判定红牛原文实际看空/提示出局）")
        lines.append("　· 出场：次日收盘卖（T+1 合规）；历史 56 笔 +548.6% / 胜率 62.5% / 回撤 -19.4%")
    else:
        lines.append("_（预案仍在更新，09:10 出定稿；此条为增量提醒）_")
    return send_feishu(chr(10).join(lines))


def last_run_summary():
    """读取当日最后一次采集摘要（兜底提醒用）"""
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def send_auction(date, l3_cands, l1_cands):
    """09:26 竞价定档：分别对 L3 / L1 排序，给出该买哪只 + 卖点计划（L3 优先）"""
    allx = list(l3_cands) + [x for x in l1_cands if x["code"] not in {y["code"] for y in l3_cands}]
    if not allx:
        return False
    q = l3_strategy.quotes([x["code"] for x in allx])
    for x in allx:
        x.update(q.get(x["code"], {}))

    def rank(lst):
        v = [x for x in lst if x.get("gap") is not None]
        return sorted(v, key=lambda z: -z["gap"])

    L3 = rank(l3_cands)
    l1_ok = [x for x in l1_cands if x.get("ai") != "bear"]
    veto = [x for x in l1_cands if x.get("ai") == "bear"]
    L1 = rank(l1_ok)
    lines = ["**%s 竞价定档（09:25）**" % date, ""]
    if L3:
        lines.append("🔴 **L3 超跌反核（优先）**")
        for i, x in enumerate(L3, 1):
            lines.append("%s%d. %s(%s) 竞价 **%+.2f%%**" % ("👉 " if i == 1 else "　　", i,
                                                        full_name(x["code"], x["name"]), x["code"], x["gap"]))
        lines.append("")
    if veto:
        lines.append("⛔ **AI 判空已排除**：%s" % "、".join(
            "%s(%s)" % (full_name(x["code"], x["name"]), x["code"]) for x in veto))
        lines.append("")
    if L1:
        lines.append("📋 **L1 红牛看多（%d 只，按竞价排序）**" % len(L1))
        for i, x in enumerate(L1[:10], 1):
            lines.append("%s%d. %s(%s) **%+.2f%%**" % ("👉 " if (i == 1 and not L3) else "　　",
                                                     i, full_name(x["code"], x["name"]), x["code"], x["gap"]))
        if len(L1) > 10:
            lines.append("　　…另有 %d 只" % (len(L1) - 10))
        lines.append("")
    if L3:
        best = L3[0]
        lines.append("**→ 今日买：%s(%s)** —— L3 优先（满仓单票）" % (best["name"], best["code"]))
        lines.append("　· 止损：D+1 盘中跌破成本 -5% 即卖（T+1 当天不能卖）")
        lines.append("　· 出场：D+1 收盘卖半仓，剩余半仓 D+2 收盘卖")
        lines.append("　· 历史（23 笔）：+310.6% / 胜率 65.2% / 回撤 -9.7%")
    elif L1:
        best = L1[0]
        lines.append("**→ 今日买：%s(%s)** —— 无 L3，用 L1（满仓单票）" % (best["name"], best["code"]))
        lines.append("　· 出场：**D+1 收盘全出**（隔日卖，T+1 合规）")
        lines.append("　· 可选止损：D+1 盘中跌破成本 -5% 即卖")
        lines.append("　· 历史（56 笔）：+548.6% / 胜率 62.5% / 回撤 -19.4%")
    return send_feishu(chr(10).join(lines))


def send_fallback(date, summary):
    """兜底：到点仍没发出任何预警时，说明原因（预案未齐/无信号）"""
    lines = ["**%s 预案预警（兜底提醒）**" % date, ""]
    hn, lq = summary.get("hn", {}), summary.get("lq", {})
    if not summary.get("both"):
        lines.append("⚠️ **两家预案没凑齐，今天没做 L3/L5 判断**")
        lines.append("　红牛：消息 %s 条 / 预案段 %s" % (hn.get("msgs", 0), hn.get("plans", 0)))
        lines.append("　来去：消息 %s 条 / 预案段 %s" % (lq.get("msgs", 0), lq.get("plans", 0)))
        lines.append("　可能原因：预案改成了新格式（非图片/非标准文本）、群消息被撤、或发送时间晚于 09:20")
        lines.append("　→ 请顺手看一眼两个群，我按需调整解析规则")
    else:
        lines.append("两家预案已解析成功，但**今天没有 L3/L5 信号**")
        lines.append("　红牛 多%s / 空%s；来去 多%s / 空%s（风标 空%s）"
                     % (hn.get("bull", 0), hn.get("bear", 0), lq.get("bull", 0), lq.get("bear", 0),
                        summary.get("fk_bear", 0)))
    if summary.get("l1"):
        lines.append("　（红牛看多合计 %d 只，可对照 L1）" % summary["l1"])
    return send_feishu(chr(10).join(lines))


def once(dry_run=False, date=None, send=True):
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
    texts, stat = {}, {}
    for tag, chat in (("hn", CHAT_HONGNIU), ("lq", CHAT_LAIQIU)):
        texts[tag], stat[tag] = collect_side(chat, today, tag)
    smry = {"date": today,
            "hn": {"msgs": stat["hn"]["msgs"], "plans": len(texts["hn"])},
            "lq": {"msgs": stat["lq"]["msgs"], "plans": len(texts["lq"])},
            "both": bool(texts["hn"] and texts["lq"])}
    st0 = last_run_summary()
    st0.update(smry)
    json.dump(st0, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    log("采集：红牛 消息%d/文本%d/图片%d → 预案段%d｜来去 消息%d/文本%d/图片%d → 预案段%d"
        % (stat["hn"]["msgs"], stat["hn"]["texts"], stat["hn"]["imgs"], len(texts["hn"]),
           stat["lq"]["msgs"], stat["lq"]["texts"], stat["lq"]["imgs"], len(texts["lq"])))
    if not texts["hn"]:
        log("红牛预案还没来，等待")
        return None
    hn_bull, hn_bear, hn_notes, hn_fk = parse_plans(texts["hn"], (n2c, keys))
    lq_bull, lq_bear, lq_notes, lq_fk = parse_plans(texts["lq"], (n2c, keys)) if texts["lq"] else ({}, {}, [], {"bull": {}, "bear": {}})

    # ---- L1 分批推送（红牛看多，AI 逐只复核，09:10 定稿）----
    L1_set0 = set(hn_bull) - set(hn_bear)
    audit = ai_audit([(c, (hn_bull.get(c) or {}).get("name", c),
                       (hn_bull.get(c) or {}).get("snippet", "")) for c in sorted(L1_set0)])
    st_l1 = last_run_summary()
    if st_l1.get("date") != today:
        st_l1 = {"date": today}
    pushed = set(st_l1.get("l1_pushed") or [])
    items = []
    for c in sorted(L1_set0, key=lambda x: full_name(x, x)):
        rec = hn_bull.get(c) or {}
        av = (audit.get(c) or ("", ""))[0]
        items.append({"code": c, "name": full_name(c, rec.get("name", c)),
                      "tier": tier_tag(rec.get("snippet", "")), "ai": av,
                      "why": (audit.get(c) or ("", ""))[1], "snip": rec.get("snippet", "")})
    added = [x["code"] for x in items if x["code"] not in pushed]
    now_hm = datetime.now().strftime("%H:%M")
    last_ts = st_l1.get("l1_last_ts") or ""
    final_at = os.environ.get("L1_FINAL_AT", "09:00")
    ver = int(st_l1.get("l1_ver") or 0)
    do_final = bool(items) and now_hm >= final_at and not st_l1.get("l1_final")
    incremental = os.environ.get("L1_INCREMENTAL", "0").strip() in ("1", "true", "yes")
    gap_ok = (not last_ts) or (time.time() - float(last_ts) > 90)
    if send and (do_final or (incremental and added)) and gap_ok:
        ver += 1
        if send_l1_update(today, items, added, ver, final=do_final):
            st_l1.update({"l1_ver": ver, "l1_pushed": sorted(set(pushed) | set(x["code"] for x in items)),
                          "l1_last_ts": time.time(), "l1_count": len(items),
                          "l1_final": True if do_final else st_l1.get("l1_final", False)})
            json.dump(st_l1, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
            log("L1 推送 #%d：%d 只（新增 %d，定稿=%s）" % (ver, len(items), len(added), do_final))
        else:
            log("L1 推送失败")
    if not texts["lq"]:
        log("来去预案未到，先只做 L1（红牛 %d 只）" % len(L1_set0))
        return None
    lq_bull, lq_bear, lq_notes, lq_fk = parse_plans(texts["lq"], (n2c, keys))
    if AI_ENABLED:
        for tag, bull, bear, notes in (("红牛", hn_bull, hn_bear, hn_notes),
                                       ("来去", lq_bull, lq_bear, lq_notes)):
            lines = [(i, n["line"]) for i, n in enumerate(notes)]
            ai = ai_review(lines, n2c, keys)
            for i, (nm, pol, why) in ai.items():
                sec = notes[i]["section"]
                code = n2c[nm]
                if code in bull or code in bear:
                    continue
                if pol == "bull":
                    bull[code] = {"name": nm, "section": sec, "snippet": notes[i]["line"][:60],
                                  "src": "ai:" + why[:16]}
                elif pol == "bear":
                    bear[code] = {"name": nm, "section": sec, "snippet": notes[i]["line"][:60],
                                  "src": "ai:" + why[:16]}
    log("解析：红牛 多%d/空%d，来去 多%d/空%d" % (len(hn_bull), len(hn_bear), len(lq_bull), len(lq_bear)))
    log("风标：来去 多头%d/空头%d" % (len(lq_fk["bull"]), len(lq_fk["bear"])))
    st1 = last_run_summary()
    _, L3_set, _L5 = compute_layers(hn_bull, hn_bear, lq_bull, lq_bear, lq_fk["bear"])
    st1["L3"] = [{"code": c, "name": (hn_bull.get(c) or {}).get("name", c),
                  "gap": None, "hn": (hn_bull.get(c) or {}).get("snippet", "")[:50]}
                 for c in sorted(L3_set)]
    L1_set = set(hn_bull) - set(hn_bear)
    st1["L1"] = [{"code": c, "name": full_name(c, (hn_bull.get(c) or {}).get("name", c)),
                  "gap": None, "ai": (audit.get(c) or ("", ""))[0],
                  "tier": tier_tag((hn_bull.get(c) or {}).get("snippet", "")),
                  "hn": (hn_bull.get(c) or {}).get("snippet", "")[:50]}
                 for c in sorted(L1_set)]
    log("候选：L1 %d 只 / L3 %d 只 / L5 %d 只" % (len(L1_set), len(L3_set), len(_L5)))
    st1.update({"hn": {"msgs": stat["hn"]["msgs"], "plans": len(texts["hn"]),
                       "bull": len(hn_bull), "bear": len(hn_bear)},
                "lq": {"msgs": stat["lq"]["msgs"], "plans": len(texts["lq"]),
                       "bull": len(lq_bull), "bear": len(lq_bear)},
                "fk_bear": len(lq_fk["bear"]), "l1": len(set(hn_bull) - set(hn_bear)),
                "both": True})
    json.dump(st1, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    for line in explain_layers(hn_bull, hn_bear, lq_bull, lq_bear, lq_fk["bear"]):
        log("  证据 " + line)
    md = build_message(today, hn_bull, hn_bear, lq_bull, lq_bear, lq_fk["bear"])
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
    if not send:
        log("（--no-send）不推送，仅输出")
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
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--interval", type=int, default=300, help="平时轮询间隔秒（09:20 后自动 30s）")
    ap.add_argument("--until", default="09:30")
    ap.add_argument("--l1-final", default="09:00", help="L1 定稿推送时间（默认 09:00）")
    ap.add_argument("--l1-incremental", action="store_true", help="预案到一条推一条（默认只在定稿时推一次）")
    a = ap.parse_args()

    if a.test_send:
        send_feishu("**测试** 预案 L3/L5 预警通道正常 ✅")
        return
    if a.dry_run:
        once(dry_run=True, date=a.date or datetime.now().strftime("%Y-%m-%d"))
        return
    if a.once:
        once(send=not a.no_send)
        return
    if a.loop:
        os.environ.setdefault("L1_FINAL_AT", a.l1_final)
        if a.l1_incremental:
            os.environ["L1_INCREMENTAL"] = "1"
        hh, mm = map(int, a.until.split(":"))
        deadline = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
        log("开始监听（平时每 %ds，09:20 后每 30s，截止 %s）｜L1 定稿 %s｜增量推送 %s"
            % (a.interval, a.until, a.l1_final, "开" if a.l1_incremental else "关"))
        auction_done = False
        while datetime.now() < deadline:
            try:
                once()
                st = last_run_summary()
                if (not auction_done and st.get("date") == datetime.now().strftime("%Y-%m-%d")
                        and (st.get("L3") or st.get("L1")) and datetime.now().strftime("%H:%M") >= "09:26"):
                    log("竞价定档推送（L3 %d 只 / L1 %d 只）" % (len(st.get("L3") or []), len(st.get("L1") or [])))
                    if send_auction(st["date"], list(st.get("L3") or []), list(st.get("L1") or [])):
                        auction_done = True
                        st["auction_sent"] = True
                        json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
            except Exception as e:
                log("ERR %s" % str(e)[:200])
            # 09:20 后加密轮询，保证 09:26 竞价定档准时
            iv = 30 if datetime.now().strftime("%H:%M") >= "09:20" else a.interval
            time.sleep(iv)
        log("监听结束")
        today = datetime.now().strftime("%Y-%m-%d")
        st = last_run_summary()
        if st.get("date") == today and not st.get("sent_hash"):
            log("今日未推送 → 发兜底提醒")
            send_fallback(today, st)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
