# 预案 L3/L5 预警 · 部署（走代码仓库）

## 一、本地提交（已做）
```bash
git add scripts/preplan_alert.py scripts/preplan_rebuild.py scripts/daily_scan.py \
        deploy/preplan-alert/
git commit -m "feat(alert): 盘前预案 L3/L5 预警 + 服务器部署脚本"
git push origin codex/develop
```

## 二、服务器上三步
```bash
cd /opt/stock_system/CodexStock
git pull origin codex/develop
bash deploy/preplan-alert/install.sh
```

install.sh 会自动：装 `rapidocr-onnxruntime` → 补 `stockdict_ak.json` → 检查 `config.json` 的 `feishu.webhook`
→ 装/检查 lark-cli → 写 cron（**每交易日 08:30 启动，循环到 09:20，每 60s 检查**）→ 跑一次自检。

## 三、服务器上唯一还需要的一次授权（读群消息）
```bash
lark-cli auth login --scope "im:message.readonly" --no-wait --json   # 拿 verification_url
# 手机扫码 → 再执行
lark-cli auth login --device-code <device_code>
```
- 发送走**群自定义机器人 webhook**（`config.json` 里已有），**不需要管理员审批**
- token 与机器 DPoP keystore 绑定，不能从 Windows 拷贝；access 2h、refresh 7 天，cron 每天跑会自动续期
- 连续 7 天不跑才需要重新扫码

## 四、验证
```bash
cd /opt/stock_system/CodexStock
tail -f logs/preplan_alert.log           # 看 "预案未齐 / 解析 / WEBHOOK OK"
grep WEBHOOK logs/preplan_alert.log | tail -5
```
交易日 08:30 后，若两边预案都到齐且出现 L3/L5，群里会立刻收到消息。

## 五、注意
- `data/laiqiu/` 与 `config.json` 在 .gitignore 里，所以字典文件放在 `deploy/preplan-alert/stockdict_ak.json`，由 install.sh 复制到位
- webhook 不写进仓库（避免泄露），沿用服务器上已有的 `config.json`
- 服务器时区必须是 `Asia/Shanghai`，否则 08:30 会错位：`timedatectl set-timezone Asia/Shanghai`
