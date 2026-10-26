#!/usr/bin/env bash
# 在本地（Windows Git-Bash 或 Linux）生成可上传的部署包
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=dist/plan-alert.tar.gz
mkdir -p dist build/plan-alert/{scripts,data/laiqiu,deploy/preplan-alert,logs}
cp scripts/preplan_alert.py scripts/group_signals.py scripts/group_backtest.py build/plan-alert/scripts/
cp data/laiqiu/stockdict_ak.json build/plan-alert/data/laiqiu/
cp deploy/preplan-alert/install.sh deploy/preplan-alert/README.md build/plan-alert/deploy/preplan-alert/
python - <<'PY'
import json
cfg = json.load(open('config.json', encoding='utf-8'))
mini = {"feishu": {"webhook": cfg.get("feishu", {}).get("webhook", "")}}
json.dump(mini, open('build/plan-alert/config.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print("config.json -> 仅保留 feishu.webhook（不打包 mysql 密码）")
PY
tar -czf "$OUT" -C build plan-alert
echo "已生成 $OUT （$(du -h "$OUT" | cut -f1)）"
