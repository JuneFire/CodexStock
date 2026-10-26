#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从两个飞书群提取「提到个股 + 方向极性」信号，口径统一。

数据源：
  来去由心   data/laiqiu/laiqiu.db  (messages 表: chat_id oc_c7a9f0d834a68a04dd8b67d10e25008b / oc_f6db12de14a26dbfb4007fb32614497c)
  借势而为   data/feishu/jieshi.jsonl (自定义机器人转发，正文前缀 【老师】/借势而为：)

输出：data/laiqiu/backtest/group_signals.json
  每条 {group, kind, date, time, code, name, alias, polarity, score, text}
  group  : laiqiu | jieshi
  kind   : plan(预案/风标/短线板) | teacher(老师观点) | research(研报转发) | text(其他文字)
  polarity: pos | neg | neutral
"""
import json
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "laiqiu", "laiqiu.db")
JIESHI = os.path.join(ROOT, "data", "feishu", "jieshi.jsonl")
STOCKDICT = os.path.join(ROOT, "data", "laiqiu", "stockdict_ak.json")
OUT = os.path.join(ROOT, "data", "laiqiu", "backtest", "group_signals.json")

LAIQIU_CHATS = ("oc_c7a9f0d834a68a04dd8b67d10e25008b", "oc_f6db12de14a26dbfb4007fb32614497c")

# ---------- 文本清洗 ----------
ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufe0f]")
TAG = re.compile(r"<[^>]+>")
OBFUS = [("gμ", "股"), ("Gμ", "股"), ("μ", "股"), ("shυ", "数"), ("Shυ", "数"),
         ("shι", "市"), ("Shι", "市"), ("shΙ", "市"), ("ShΙ", "市"),
         ("bαn", "板"), ("Bαn", "板"), ("die", "跌"), ("Die", "跌"),
         ("xian", "现"), ("弮", "券"), ("quαn", "券"),
         # 2026-10-26 短线群（红牛/林尚沃/J神）常见的拼音回避与谐音
         ("mǎi", "买"), ("Mǎi", "买"), ("mαi", "买"), ("mαí", "买"),
         ("ban", "板"), ("Ban", "板"), ("BAN", "板"),
         ("苍", "仓"), ("套li", "套利"), ("li润", "利润"),
         ("水下xi", "水下吸"), ("xi货", "吸货")]


def clean(t):
    t = t or ""
    t = TAG.sub(" ", t)
    t = ZERO_WIDTH.sub("", t)
    for a, b in OBFUS:
        t = t.replace(a, b)
    t = t.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    # 单独出现的 xi / XI 作「吸」处理（短线群高频：低xi = 低吸）
    t = re.sub(r"(?<![A-Za-z])(?:xi|XI|Xi)(?![A-Za-z])", "吸", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------- 词典 ----------
SENT_SPLIT = re.compile(r"[。！？!?；;\n]+")

POS_W = ["低吸", "水下吸", "买点", "买入", "加仓", "加码", "上车", "布局", "埋伏", "看好",
         "看多", "做多", "打板", "关注", "可关注", "重点品种", "重点", "核心梯队", "核心",
         "龙头", "机会", "持有", "拿着", "拿住", "弹性", "补涨", "启动", "主升", "新高",
         "受益", "标配", "首选", "推荐", "增持", "超预期", "拐点", "值得看", "强call",
         "重点讲", "低吸无碍", "抢筹", "反包", "翅板", "长势", "领涨", "再上一波", "起飞"]
NEG_W = ["卖出", "卖点", "止盈", "止损", "减仓", "清仓", "出局", "撤离", "回避", "不碰",
         "别碰", "别追", "不追", "不接", "不去", "风险", "见顶", "证伪", "兑现", "砸盘",
         "看空", "做空", "空仓", "谨慎", "小心", "退潮", "断板", "炸板", "泡沫", "崩",
         "减亏", "走弱", "杀跌", "抛售", "不宜", "不建议", "别买", "水跳", "不妙", "躲一下"]

# 极性词按长度降序，保证「低吸」先于「吸」
POS_W.sort(key=len, reverse=True)
NEG_W.sort(key=len, reverse=True)

# ---------- 短线群专用盘口黑话（只用于 hongniu / linshangwo / jshen，不改旧口径）----------
SHORT_POS = ["低吸", "水下吸", "吸", "可以吸", "上车", "接力", "打板", "上板", "拉板",
             "首板", "首版", "一进二", "一进三", "卡异动", "异动", "排板", "排队", "排",
             "买", "买入", "加自选", "持仓", "持苍", "加仓", "补仓", "顶", "干", "搞",
             "大肉", "肉", "浮盈", "转强", "走强", "止跌", "修复", "反包", "翘板",
             "如期", "符合预期", "超预期", "强", "拿下", "带走", "跟", "盯", "稳"]
SHORT_NEG = ["卖出", "出掉", "先手出", "跑路", "走人", "交枪", "割肉", "割", "清仓", "清完",
             "减仓", "减", "止盈", "落袋", "兼现", "兑现", "卖", "炸板", "断板", "破位",
             "走弱", "掉队", "泄", "吃泄药", "不吸", "没吸", "先不吸", "不买", "不接",
             "放弃", "保命", "回避", "不做", "砸", "出货", "退潮", "谨慎"]
SHORT_POS.sort(key=len, reverse=True)
SHORT_NEG.sort(key=len, reverse=True)

EXCLUDE_ALIAS = set("""
中国 东方 新兴 南方 北方 上海 北京 深圳 广州 天津 重庆 江苏 浙江 山东 广东 四川 湖北 湖南
华夏 长江 黄河 大众 人民 新华 中信 中金 国泰 华泰 招商 平安 兴业 民生 光大 建投 海通 广发
今天 明天 昨天 后天 上午 下午 早盘 午盘 尾盘 开盘 收盘 竞价 龙头 主线 核心 机会 风险
低位 高位 首板 连板 断板 一字 修复 反弹 指数 大盘 市场 情绪 资金 板块 题材 方向 趋势
周五 周一 周二 周三 周四 周末 节日 节前 节后 今日 昨日 明日
""".split())

# 只对「明显是后缀」的词做去尾，避免 今天国际→今天 这类泛词
SAFE_SUFFIX = ("股份", "科技", "集团", "电子", "新材", "智能", "软件", "通信", "传媒",
               "机械", "重工", "装备", "汽车", "电器", "电气", "化工", "食品",
               "光电", "精密", "微电", "半导", "生物", "药业", "医药",
               # 2026-10-26 扩充：短线群常用的简称尾巴（东安动力→东安、冰山冷热→冰山）
               "动力", "精工", "精机", "数控", "冷热", "环境", "环能", "水务", "燃气",
               "热力", "建设", "投资", "资本", "控股", "国际", "实业", "制造", "机电",
               "仪电", "光学", "激光", "声学", "磁材", "材料", "新能", "新能源", "信息",
               "网络", "数据", "文化", "旅业", "旅游", "酒业", "乳业", "牧业", "种业",
               "农业", "化肥", "建材", "钢铁", "有色", "矿业", "煤炭", "石化", "能源")

# 2 字别名里的泛词（去尾后与行业/形容词碰撞），命中则弃用该别名
GENERIC2 = set("""
股份 科技 集团 电子 新材 智能 软件 通信 传媒 机械 重工 装备 汽车 电器 电气 化工 食品
光电 精密 微电 半导 生物 药业 医药 材料 环保 能源 电力 消费 金融 银行 保险 证券
地产 基建 建材 钢铁 有色 黄金 煤炭 石化 造纸 包装 印刷 纺织 服装 珠宝 零售 超市
旅游 餐饮 酒店 影视 出版 教育 医疗 保健 疫苗 中药 白酒 啤酒 乳业 农业 种业 饲料
养殖 化肥 农药 橡胶 塑料 光伏 风电 储能 电池 芯片 算力 数据 网络 游戏 军工 航天
航空 卫星 物流 港口 航运 铁路 公路 建筑 装饰 家纺 家电 动力 领先 创新 绿色 智慧
未来 数字 先进 优质 高端 低位 高位 首板 连板 断板 一字 反包 修复 反弹 控制 管理
""".split())


def build_dict():
    raw = json.load(open(STOCKDICT, encoding="utf-8"))          # name -> code
    alias = {}   # alias -> set(codes)
    name_of = {}
    for name, code in raw.items():
        code = str(code).zfill(6)
        name_of[code] = name
        cands = {name}
        if len(name) >= 4 and name[-2:] in SAFE_SUFFIX:
            cands.add(name[:-2])
        for a in cands:
            if len(a) < 2 or a in EXCLUDE_ALIAS or a in GENERIC2:
                continue
            alias.setdefault(a, set()).add(code)
    # 只保留唯一映射的别名；全名冲突时也丢弃
    uniq = {a: next(iter(cs)) for a, cs in alias.items() if len(cs) == 1}
    # 按长度降序用于最长匹配
    keys = sorted(uniq.keys(), key=len, reverse=True)

    # 短线群爱用「去尾再砍一半」的两字简称（凡拓数创→凡拓、科森科技→科森），
    # 这类别名只在紧邻盘口动作词时才启用，避免 统一/第一 这类泛字碰撞。
    short2 = {}
    for code, name in name_of.items():
        if len(name) < 4 or name[-2:] not in SAFE_SUFFIX:
            continue
        p = name[:-2]
        if len(p) != 2 or p in GENERIC2 or p in EXCLUDE_ALIAS or p in uniq:
            continue
        if p in short2 and short2[p] != code:
            short2[p] = None          # 冲突则弃用
        else:
            short2.setdefault(p, code)
    short2 = {k: v for k, v in short2.items() if v}
    return uniq, keys, name_of, short2


def polarity_of(seg):
    p = sum(1 for w in POS_W if w in seg)
    n = sum(1 for w in NEG_W if w in seg)
    if p > n:
        return "pos", p - n
    if n > p:
        return "neg", n - p
    return "neutral", 0


CODE_RE = re.compile(r"\b(\d{6})\b")
CJK = re.compile(r"[\u4e00-\u9fff]")
WIN = 12          # 极性词探测半径（字符）


def polarity_at(seg, s, e, extra_pos=(), extra_neg=()):
    """只看提及前后 WIN 字符内的极性词，避免整句误判。

    extra_pos/extra_neg 供短线群黑话使用（SHORT_POS/SHORT_NEG），旧口径传空则不改变行为。
    """
    ctx = seg[max(0, s - WIN): e + WIN]
    p = sum(1 for w in POS_W if w in ctx) + sum(1 for w in extra_pos if w in ctx)
    n = sum(1 for w in NEG_W if w in ctx) + sum(1 for w in extra_neg if w in ctx)
    if p > n:
        return "pos", p - n
    if n > p:
        return "neg", n - p
    return "neutral", 0


def _ok_boundary(alias, seg, s, e):
    """2 字别名（去尾简称）要求右侧不是汉字，避免 统一了/第一医药 这类误匹配。"""
    if len(alias) > 2:
        return True
    if e < len(seg) and CJK.match(seg[e]):
        return False
    if s > 0 and CJK.match(seg[s - 1]):
        return False
    return True


def extract(text, uniq, keys, name_of, code_set, extra_pos=(), extra_neg=(), short2=None,
            max_per_seg=3):
    """返回 [(code, name, alias, polarity, score, sentence)]，按句聚合、每句最多 3 只。"""
    action = tuple(extra_pos) + tuple(extra_neg)          # 两字简称必须贴着动作词
    out = []
    for seg in SENT_SPLIT.split(text):
        seg = seg.strip()
        if not seg or len(seg) < 3:
            continue
        found = []
        hit_spans = []
        for a in keys:                     # 最长优先，已被占用的区间跳过
            if a not in seg:
                continue
            for m in re.finditer(re.escape(a), seg):
                s, e = m.span()
                if any(s < h[1] and e > h[0] for h in hit_spans):
                    continue
                if not _ok_boundary(a, seg, s, e):
                    continue
                hit_spans.append((s, e))
                found.append((a, uniq[a], s, e))
                break
        for m in CODE_RE.finditer(seg):
            c = m.group(1)
            if c in code_set and not any(code == c for _, code, _, _ in found):
                found.append((c, c, m.start(), m.end()))
        # 两字简称（仅短线群）：命中位置前后 ATX 字内必须出现动作词
        if short2:
            for a, code in short2.items():
                if a not in seg:
                    continue
                for m in re.finditer(re.escape(a), seg):
                    s, e = m.span()
                    if any(s < h[1] and e > h[0] for h in hit_spans):
                        continue
                    if not _ok_boundary(a, seg, s, e):
                        continue
                    near = seg[max(0, s - 5): e + 5]
                    if not any(w in near for w in action):
                        continue
                    hit_spans.append((s, e))
                    found.append((a, code, s, e))
                    break
        if not found:
            continue
        for alias, code, s, e in found[:max_per_seg]:
            pol, score = polarity_at(seg, s, e, extra_pos, extra_neg)
            out.append((code, name_of.get(code, alias), alias, pol, score, seg[:160]))
    return out


# 直播记录形式的文本：只保留主理人发言段
TRANSCRIPT = re.compile(r"(?:\d{2}:\d{2}:\d{2}\s*)?([\u4e00-\u9fff\w…·]{1,12}):")
SPEAKER_OK = ("知行合一", "王世辰", "何志坚", "来去由心", "吴晶", "内参投研")


def owner_only(text):
    if len(re.findall(r"\d{2}:\d{2}:\d{2}", text)) < 2:
        return text
    parts = TRANSCRIPT.split(text)
    keep = []
    for i in range(1, len(parts), 2):
        who = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        if any(k in who for k in SPEAKER_OK):
            keep.append(body)
    return " ".join(keep)


# ---------- 来去由心 ----------
def load_laiqiu():
    con = sqlite3.connect(DB)
    q = """select date, ts, chat_id, section, sender_name, sender_type, answer, question, raw, msg_type
             from messages where chat_id in (?,?) and date >= '2025-08-01' order by ts"""
    rows = con.execute(q, LAIQIU_CHATS).fetchall()
    con.close()
    return rows


# ---------- 借势而为 ----------
def load_jieshi():
    rows = []
    with open(JIESHI, encoding="utf-8") as f:
        for line in f:
            m = json.loads(line)
            if m.get("msg_type") not in ("text", "post"):
                continue
            rows.append(m)
    return rows


# ---------- 三个短线群（2026-10-25 由 scripts/feishu_dump.py 落地）----------
SHORT_CHATS = {
    "hongniu": ("oc_808d538d57fb79e1abf5b4149175a886", "data/feishu/hongniu.jsonl"),
    "linshangwo": ("oc_76ba0d6c5c8a8f2bdbba20b63a9f068c", "data/feishu/linshangwo.jsonl"),
    "jshen": ("oc_f5333a3dfea971574d5dc7262c2272c5", "data/feishu/jshen.jsonl"),
}


def load_short_group(path):
    rows = []
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        for line in f:
            m = json.loads(line)
            if m.get("msg_type") not in ("text", "post", "interactive"):
                continue
            if m.get("deleted"):
                continue
            rows.append(m)
    return rows


RESEARCH_HINT = ("【", "净利", "预计", "同比", "环比", "评级", "目标价", "会议", "电话会", "路演",
                 "研报", "点评", "更新", "观点：")


def jieshi_kind(text):
    if text.startswith("【老师】"):
        body = text[4:]
        if len(body) > 300 or re.match(r"^\s*【[^】]{2,12}】", body) or ("净利" in body and len(body) > 180):
            return "research"
        return "teacher"
    if text.startswith("借势而为"):
        return "teacher"
    return "text"


def main():
    uniq, keys, name_of, short2 = build_dict()
    code_set = set(name_of.keys())
    print("alias", len(uniq), "codes", len(code_set))
    sigs = []

    # 来去由心
    n_lq = 0
    for date, ts, chat_id, section, sname, stype, answer, question, raw, mtype in load_laiqiu():
        body = answer or raw or ""
        body = owner_only(clean(body))
        if not body:
            continue
        kind = "text" if section in ("王世辰", "交易逻辑训练专栏", "") else "text"
        for code, name, alias, pol, score, seg in extract(body, uniq, keys, name_of, code_set):
            sigs.append({"group": "laiqiu", "kind": kind, "date": date, "time": (ts or "")[11:16],
                         "code": code, "name": name, "alias": alias, "polarity": pol,
                         "score": score, "text": seg})
            n_lq += 1
    print("laiqiu raw signals", n_lq)

    # 借势而为
    n_js = 0
    for m in load_jieshi():
        text = clean(m.get("content"))
        if not text:
            continue
        kind = jieshi_kind(text)
        if kind == "text":
            continue                      # 散户/其他转发先不计
        body = text[4:] if text.startswith("【老师】") else text.split("：", 1)[-1]
        body = owner_only(body)
        for code, name, alias, pol, score, seg in extract(body, uniq, keys, name_of, code_set):
            sigs.append({"group": "jieshi", "kind": kind, "date": m["create_time"][:10],
                         "time": m["create_time"][11:16], "code": code, "name": name,
                         "alias": alias, "polarity": pol, "score": score, "text": seg})
            n_js += 1
    print("jieshi raw signals", n_js)

    # 三个短线群：只保留有明确方向（pos/neg）的信号，天然过滤散户闲聊噪音
    for g, (chat_id, path) in SHORT_CHATS.items():
        n = 0
        for m in load_short_group(path):
            body = owner_only(clean(m.get("content")))
            if not body or len(body) < 3:
                continue
            for code, name, alias, pol, score, seg in extract(body, uniq, keys, name_of, code_set,
                                                              SHORT_POS, SHORT_NEG, short2,
                                                              max_per_seg=8):
                # neutral 也保留（kind=mention），供「提及层」样本使用；方向层只看 pos/neg
                sigs.append({"group": g, "kind": "mention" if pol == "neutral" else "text", "date": m["create_time"][:10],
                             "time": m["create_time"][11:16], "code": code, "name": name,
                             "alias": alias, "polarity": pol, "score": score, "text": seg})
                n += 1
        print("%s raw signals %d" % (g, n))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(sigs, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print("wrote", OUT, len(sigs))


if __name__ == "__main__":
    main()
