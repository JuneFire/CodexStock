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

## 六、可选：云端 LLM 兜底（2026-10-27 已上线）

规则层是主判定（词表 + 股票名单，确定性），云端 LLM 只在**规则判为"未决"的句子**上兜底：

| 项 | 说明 |
|---|---|
| 开关 | 环境变量 `PREPLAN_AI=1`（cron 里已加） |
| 模型 | DeepSeek（OpenAI 兼容），配置写在 `config.json` 的 `ai` 段：`base_url / api_key / model / timeout` |
| 调用粒度 | **每份预案 1 次**，把该份里所有"未决句"打包成一次请求 |
| 输入限制 | 只把**候选股票名**（规则从文本里捞出的）+ 句子给模型，禁止它臆造名单外的票 |
| 输出 | 严格 JSON：`{"decisions":[{"i":行号,"name":"","polarity":"bull|bear|mention","reason":""}]}` |
| 合并策略 | AI 只能**补**规则判 neutral 的票；规则已判定的不覆盖；冲突只记日志 |
| 缓存 | `data/feishu/ai_cache.json`，同一段文本不重复调用 |
| 降级 | key 缺失/超时/坏 JSON → 记 `AI_FAIL`，**照旧用规则结果推送** |
| 成本 | 每天 2–4 次调用、约 2–4k tokens，按 DeepSeek 价格约 ¥0.01–0.03/天 |

关闭方式：把 cron 里的 `PREPLAN_AI=1` 去掉即可，脚本会完全回到纯规则。

证据日志（`logs/preplan_alert.log`）会标出来源：
```
证据 L3 600178 东安动力 | 红牛:看多(rule)·命中反核词「次弱」 | 来去:空头风标(rule)·…
证据 L5 ... | 红牛:看多(ai:看能否一进二) | 来去:短线方面看多(rule)·…
```
