#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""可续跑地拉取飞书群消息 -> jsonl + checkpoint。

用法:
    python scripts/feishu_dump.py --chat-id oc_xxx --slug jieshi [--max-pages 30]

每页 50 条（CLI 上限），写入 data/feishu/<slug>.jsonl（按 message_id 去重），
进度存 data/feishu/<slug>.ckpt.json（page_token / done / pages / items）。
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = r"E:/Program Files/npm-global/node_modules/@larksuite/cli/scripts/run.js"
OUTDIR = os.path.join(ROOT, "data", "feishu")


def load_ckpt(path):
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"page_token": "", "pages": 0, "items": 0, "done": False}


def save_ckpt(path, ck):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ck, f, ensure_ascii=False)


def fetch_page(chat_id, token):
    cmd = ["node", RUN, "im", "+chat-messages-list",
           "--chat-id", chat_id, "--page-size", "50", "--order", "desc",
           "--no-reactions", "--format", "json"]
    if token:
        cmd += ["--page-token", token]
    r = subprocess.run(cmd, capture_output=True)
    txt = r.stdout.decode("utf-8", errors="replace")
    try:
        return json.loads(txt)
    except Exception:
        print("PARSE_FAIL", r.returncode, txt[:300], r.stderr.decode("utf-8", "replace")[:300])
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat-id", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--max-pages", type=int, default=30)
    args = ap.parse_args()

    os.makedirs(OUTDIR, exist_ok=True)
    out = os.path.join(OUTDIR, args.slug + ".jsonl")
    ckpt_path = os.path.join(OUTDIR, args.slug + ".ckpt.json")
    ck = load_ckpt(ckpt_path)
    if ck["done"]:
        print("ALREADY DONE items=%d pages=%d" % (ck["items"], ck["pages"]))
        return

    seen = set()
    if os.path.isfile(out):
        with open(out, encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(json.loads(line)["message_id"])
                except Exception:
                    pass

    pages = 0
    with open(out, "a", encoding="utf-8") as f:
        while pages < args.max_pages:
            d = fetch_page(args.chat_id, ck["page_token"])
            if not d or not d.get("ok"):
                print("STOP err page=%d" % ck["pages"])
                break
            msgs = (d.get("data") or {}).get("messages") or []
            new = 0
            for m in msgs:
                if m["message_id"] in seen:
                    continue
                seen.add(m["message_id"])
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
                new += 1
            ck["pages"] += 1
            ck["items"] = len(seen)
            ck["page_token"] = ((d.get("meta") or {}).get("pagination") or {}).get("next_token") or ""
            ck["done"] = not (d.get("data") or {}).get("has_more")
            save_ckpt(ckpt_path, ck)
            pages += 1
            if ck["done"]:
                break
    save_ckpt(ckpt_path, ck)
    print("pages+=%d total_pages=%d items=%d done=%s token=%s"
          % (pages, ck["pages"], ck["items"], ck["done"], (ck["page_token"] or "")[:12]))


if __name__ == "__main__":
    main()
