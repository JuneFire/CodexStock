# 竞价选股器

每天 9:25 集合竞价结束后，从东方财富抓取竞价金额排名前 200 的个股，结合昨日成交额与流通市值计算竞价超预期强度，输出 Top10。

## 启动

```powershell
python backend/server.py
```

然后打开 <http://127.0.0.1:8010>。

启动后每个交易日 9:25-9:30 会自动抓取一次并保存；不需要自动抓取时运行 `python backend/server.py --no-auto`。

竞价金额与竞价换手只在交易日 9:25 竞价结束后、开盘前有效；手动抓取也限定在该窗口，其他时间会提示错误，避免把盘中累计成交额当成竞价数据。

页面顶部点击“抓取竞价数据”，服务端会完成：
1. 通过东方财富行情接口抓取全市场竞价成交额前 200；
2. 逐只拉取最近 3 根日 K，取上一交易日成交额与换手率；
3. 计算竞价占昨日成交比例、竞价金额强度（竞价金额/流通市值，单位 bp）、竞价换手，并按 30/30/20/12/8 权重输出 0-100 超预期分；
4. 按分数排序生成 Top10，并保存到 `data/latest.json` 与 `data/history/<日期>.json`。

## 本地模式

不启动服务也可以直接双击 `index.html` 使用演示数据，或下载 CSV 模板后导入自己的竞价数据。演示数据仅用于界面与筛选逻辑预览，不代表真实行情。

## CSV 模板

点击顶栏“模板”下载，列包含：

`代码,名称,板块,竞价价,竞价涨幅,竞价金额(元),竞价量(手),竞价换手(%),量比,流通市值(元),昨日成交额(元),昨日占比(%),金额强度(bp),超预期分,状态`

导入时支持 UTF-8 与 GBK 编码；金额列带“万/亿”后缀也会自动换算为元。

## 说明

- 东财快照中的成交额在 9:25-9:30 抓取时代表集合竞价成交额，开盘后为全天累计值，因此建议每天 9:25 后立即抓取。
- 昨日成交额通过公开日 K 接口逐只获取，200 只约需 30-60 秒。
- 抓取失败时页面会提示，可先用“演示数据”或 CSV 导入继续使用。

## 历史竞价数据（MySQL）

> 当前已暂时搁置：服务运行时不连接 MySQL，也不提供历史页面入口。相关代码保留在 `backend/db.py`、`backend/migrate_history.py`、`frontend/history.html`/`frontend/history.js` 和 `docs/schema.sql`，后续需要时再启用。

行情接口不提供历史竞价数据，历史快照由本地逐日累积保存到 MySQL（每天保留最后一次有效竞价快照）。写入 MySQL 前会再次校验抓取时间必须在 9:25-9:30 竞价窗口内且与快照日期一致，防止 9:30 后拿到的累计数据污染历史库；9:30 后即使生成了 JSON 也不会写入 MySQL。数据库不可用时不影响抓取，页面会提示“MySQL 未连接”，原有 `data/history/日期.json` 存档仍然保留。

### 配置

1. 安装依赖：

```powershell
pip install -r requirements.txt
```

2. 复制 `config.example.json` 为 `config.json`，填写本地 MySQL 账号：

```json
{
  "mysql": {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "你的密码",
    "database": "cn_stock_quant"
  }
}
```

也可以使用环境变量 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DB`。服务启动时会自动创建数据库和表（`auction_day`、`auction_stock`），完整建表语句见 `docs/schema.sql`。

3. 启动服务：

```powershell
python backend/server.py
```

打开 <http://127.0.0.1:8010>，顶栏“历史”按钮进入历史页面；每个交易日抓取成功后会自动写入 MySQL。

### 导入历史 JSON

`data/history/` 里已有的合法快照可以一次性导入：

```powershell
python backend/migrate_history.py
```

无效快照（例如抓取时间不在 9:25-9:30 竞价窗口内）会跳过并提示。

### 接口

- `GET /api/history/dates`：历史日期列表
- `GET /api/history/date?date=YYYY-MM-DD`：某日完整快照
- `GET /api/history/stock?code=600000`：个股多日历史
- `GET /api/history/export.csv?date=YYYY-MM-DD`：导出当日 CSV（UTF-8 BOM，Excel 可直接打开）

### 测试

```powershell
python -m unittest tests.test_db -v
```

连接本地 MySQL 的集成测试：

```powershell
$env:RUN_DB_TESTS = "1"
python -m unittest tests.test_db_integration -v
```