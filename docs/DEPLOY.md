# 服务器部署指南

面向 Linux 服务器的部署说明。项目路径 `/opt/stock_system/CodexStock`，Python 虚拟环境在 `/root/venv`。

## 环境说明

| 项 | 值 |
|---|---|
| 项目路径 | `/opt/stock_system/CodexStock` |
| 虚拟环境 | `/root/venv`（终端默认在 `/root`，`source venv/bin/activate` 激活） |
| 服务端口 | 8010 |
| 数据库 | MySQL（可选，无库自动降级） |

## 一、首次部署

```bash
cd /opt/stock_system
git clone https://github.com/JuneFire/CodexStock.git
cd CodexStock
git checkout codex/develop

# 创建虚拟环境（若 /root/venv 已存在则跳过）
python3 -m venv /root/venv

# 安装依赖
/root/venv/bin/pip install -r requirements.txt
```

### 配置 config.json（必需）

`config.json` 被 `.gitignore` 排除，拉代码时不会带过来，需手动创建：

```bash
cd /opt/stock_system/CodexStock
```

创建 `config.json`：

```json
{
  "mysql": {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "你的MySQL密码",
    "database": "cn_stock_quant"
  }
}
```

- 不装 MySQL 也能跑：留空 `mysql` 字段，系统自动降级（见下文"无数据库运行"）。
- 首次启动会自动建库建表（`auction_day`、`auction_stock`、`sentiment_review`、`auction_seal` 等）。

## 二、启动

### 前台启动（测试用）

```bash
source /root/venv/bin/activate
cd /opt/stock_system/CodexStock
python backend/server.py
```

浏览器打开 `http://127.0.0.1:8010`。**注意**：直接关远程终端会终止服务。

### 后台运行（关终端不终止，nohup）

```bash
source /root/venv/bin/activate
cd /opt/stock_system/CodexStock
nohup python backend/server.py > server.log 2>&1 &
```

- 日志：`tail -f /opt/stock_system/CodexStock/server.log`
- 停止：`pkill -f "backend/server.py"`

### 后台运行（tmux，能看日志）

```bash
tmux new -s quant
source /root/venv/bin/activate
cd /opt/stock_system/CodexStock
python backend/server.py
# Ctrl+B 再按 D 脱离（detach），可关终端
```

重新连回：`tmux attach -t quant`

### systemd 长期运行（推荐，7×24）

创建 `/etc/systemd/system/quant.service`：

```ini
[Unit]
Description=A股量化选股器
After=network.target

[Service]
WorkingDirectory=/opt/stock_system/CodexStock
ExecStart=/root/venv/bin/python /opt/stock_system/CodexStock/backend/server.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
```

> **不用激活 venv**：`ExecStart` 直接写 venv 里 python 的完整路径，依赖（akshare/pymysql）跟着解释器走，自动加载。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now quant   # 开机自启 + 启动
sudo systemctl status quant         # 查看状态
```

管理命令：

```bash
sudo systemctl restart quant        # 重启
sudo systemctl stop quant           # 停止
journalctl -u quant -f              # 实时日志
```

## 三、局域网/公网访问

服务监听 `0.0.0.0`，局域网设备可直接访问 `http://<服务器IP>:8010`。

- 查服务器 IP：`ip addr`（如 `192.168.1.100`）
- 防火墙放行 8010：`sudo ufw allow 8010`（或 firewalld / 云安全组）
- 公网访问：云服务器安全组放行 8010，用公网 IP

> **安全提醒**：服务无登录认证，暴露到公网前建议加 Basic Auth 或反代密码，否则任何人都能看到数据。

## 四、无数据库运行

不装 MySQL 时系统自动降级：

| 功能 | 无 MySQL 表现 |
|---|---|
| 竞价抓取、快照落盘 | ✅ 正常（写 `data/latest.json` + `data/history/`） |
| 早盘预案生成、预案网页 | ✅ 正常（读 txt，不依赖 MySQL） |
| 头马/黑马 | ✅ 正常（读缓存文件） |
| 历史页 | ✅ 正常（降级读 `data/history/*.json`，`fallback: true`） |
| 复盘保存 / 手动字段 | ⚠️ 抓取正常但保存失败 |
| 封单历史 / 个股多日查询 | ⚠️ 写不进 `auction_seal` 表 |

## 五、常用启动参数

```bash
python backend/server.py              # 默认 8010
python backend/server.py --port 8011  # 改端口
python backend/server.py --no-auto    # 关闭自动抓取（手动点）
python backend/server.py --auto-anytime  # 测试模式，任意时间抓取
```

## 六、自动任务时间表

| 时间 | 任务 |
|---|---|
| 8:30 | 自动生成当天早盘预案 → `data/plan/<日期>.txt` |
| 9:15 / 9:20 / 9:25 | 全市场封单额 Top20 抓取 → `auction_seal` 表 |
| 9:25-9:30 | 竞价金额前 200 快照 → `data/latest.json` + MySQL |
| 15:05 | 收盘复盘自动生成（涨停池/连板/头马黑马）+ 收盘涨幅入库 |

## 七、导入历史 JSON（可选）

```bash
cd /opt/stock_system/CodexStock
/root/venv/bin/python backend/migrate_history.py
```

将 `data/history/` 下的历史快照批量导入 MySQL。
