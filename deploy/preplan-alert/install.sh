#!/usr/bin/env bash
# 预案 L3/L5 预警 · 服务器部署（在已有仓库目录里执行）
#   cd /opt/stock_system/CodexStock && bash deploy/preplan-alert/install.sh
# 依赖：python3（venv）、node/npm（仅读取群消息需要）、已有 config.json（含 feishu.webhook）
set -euo pipefail
cd "$(dirname "$0")/../.."          # 仓库根目录
REPO_ROOT="$(pwd)"
VENV="${VENV:-/root/venv}"
[ -x "$VENV/bin/python" ] || VENV="$REPO_ROOT/.venv"
echo "仓库: $REPO_ROOT"
echo "venv: $VENV"

echo "== 1. Python 依赖 =="
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q rapidocr-onnxruntime onnxruntime opencv-python-headless numpy \
  || "$VENV/bin/pip" install -q rapidocr-onnxruntime numpy

echo "== 2. 股票名字典 =="
mkdir -p data/laiqiu logs
if [ ! -f data/laiqiu/stockdict_ak.json ]; then
  cp deploy/preplan-alert/stockdict_ak.json data/laiqiu/
  echo "  已从 deploy/ 复制 stockdict_ak.json"
else
  echo "  已存在，跳过"
fi

echo "== 3. 群 webhook 检查 =="
if [ -f config.json ] && grep -q '"webhook"' config.json; then
  echo "  config.json 里已有 feishu.webhook ✅（发送不需要任何授权）"
else
  echo "  ⚠️ config.json 缺少 feishu.webhook，请手工补："
  echo '  { "feishu": { "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx" } }'
fi

echo "== 4. lark-cli（读取预案图片必需）=="
if command -v lark-cli >/dev/null 2>&1 || [ -f "$(npm root -g 2>/dev/null)/@larksuite/cli/scripts/run.js" ]; then
  echo "  已安装"
else
  npm i -g @larksuite/cli
fi
echo "  服务器上需一次性授权"读取"权限（不需要管理员）："
echo "    lark-cli auth login --scope \"im:message.readonly\" --no-wait --json"
echo "    手机扫码 → lark-cli auth login --device-code <code>"

echo "== 5. cron（每交易日 08:30 启动，循环到 09:20）=="
CRON_LINE="30 8 * * 1-5 cd $REPO_ROOT && $VENV/bin/python scripts/preplan_alert.py --loop --until 09:20 >> logs/preplan_alert.log 2>&1"
( crontab -l 2>/dev/null | grep -v preplan_alert || true; echo "$CRON_LINE" ) | crontab -
crontab -l | tail -2

echo "== 6. 自检 =="
NO_NET=1 "$VENV/bin/python" scripts/preplan_alert.py --once || true
echo
echo "完成。日志：tail -f $REPO_ROOT/logs/preplan_alert.log"
