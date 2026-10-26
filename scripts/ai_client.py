# -*- coding: utf-8 -*-
"""极简 OpenAI 兼容 LLM 客户端（零依赖，urllib）。

环境变量
    AI_BASE_URL  默认 https://api.deepseek.com/v1
    AI_API_KEY   必需（也可写在 config.json 的 ai.api_key）
    AI_MODEL     默认 deepseek-chat
    AI_TIMEOUT   默认 25 秒
"""
import json
import os
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cfg():
    base = os.environ.get("AI_BASE_URL", "").strip()
    key = os.environ.get("AI_API_KEY", "").strip()
    model = os.environ.get("AI_MODEL", "").strip()
    timeout = float(os.environ.get("AI_TIMEOUT", "25"))
    if not (base and key and model):
        try:
            cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8")).get("ai") or {}
        except Exception:
            cfg = {}
        base = base or cfg.get("base_url") or "https://api.deepseek.com/v1"
        key = key or cfg.get("api_key") or ""
        model = model or cfg.get("model") or "deepseek-chat"
        timeout = float(cfg.get("timeout") or timeout)
    return base.rstrip("/"), key, model, timeout


def available():
    _, key, _, _ = _cfg()
    return bool(key)


def chat_json(system, user, retries=1, max_tokens=1200):
    """返回 (dict|None, err)。要求模型输出 JSON。"""
    base, key, model, timeout = _cfg()
    if not key:
        return None, "no_api_key"
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    last = ""
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            base + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            txt = (d.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return json.loads(txt), ""
        except json.JSONDecodeError as e:
            last = "bad_json:%s" % str(e)[:80]
        except urllib.error.HTTPError as e:
            last = "http_%s:%s" % (e.code, e.read()[:120].decode("utf-8", "replace"))
        except Exception as e:
            last = str(e)[:120]
        if attempt < retries:
            time.sleep(2)
    return None, last
