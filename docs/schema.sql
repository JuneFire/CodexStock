-- =====================================================================
-- 竞价选股器 · 历史竞价数据表结构
-- 说明：
--   1. 每个交易日只保留最后一次有效竞价快照（每天一条 auction_day）。
--   2. 每个交易日内，每只股票一行明细（auction_stock）。
--   3. 写入 MySQL 前会校验 fetched_at 必须位于 9:25-9:30 竞价窗口，
--      防止 9:30 后的全天累计数据污染历史库。
-- 应用代码中的建表逻辑与 db.py 保持一致，此文件用于人工审阅/手动建库。
-- =====================================================================

CREATE DATABASE IF NOT EXISTS cn_stock_quant
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE cn_stock_quant;

-- ---------------------------------------------------------------------
-- 每日竞价快照：一天一条，保存快照级元数据和市场整体数据
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auction_day (
  id          INT UNSIGNED  AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  trade_date  DATE          NOT NULL            COMMENT '交易日（唯一）',
  fetched_at  DATETIME      NOT NULL            COMMENT '最近一次抓取时间',
  source      VARCHAR(32)   NOT NULL DEFAULT '东方财富' COMMENT '数据来源',
  auto        TINYINT       NOT NULL DEFAULT 0  COMMENT '1=自动抓取，0=手动抓取',
  valid       TINYINT       NOT NULL DEFAULT 1  COMMENT '是否为有效竞价快照',
  market_json JSON          NULL                COMMENT '指数/涨跌家数/竞价涨停数/竞价总额',
  created_at  TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at  TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  UNIQUE KEY uq_trade_date (trade_date)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '每日竞价快照';

-- ---------------------------------------------------------------------
-- 当日个股明细：每天每只股票一行，保存全部竞价指标
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auction_stock (
  id                   BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  day_id               INT UNSIGNED  NOT NULL            COMMENT '所属快照 auction_day.id',
  code                 VARCHAR(10)   NOT NULL            COMMENT '股票代码',
  name                 VARCHAR(64)   NOT NULL            COMMENT '股票名称',
  industry             VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '板块/行业',
  price                DECIMAL(12,3) NULL                COMMENT '竞价价',
  change_pct           DECIMAL(8,3)  NULL                COMMENT '竞价涨幅 %',
  open                 DECIMAL(12,3) NULL                COMMENT '开盘价',
  prev_close           DECIMAL(12,3) NULL                COMMENT '昨收价',
  auction_amount       DECIMAL(20,2) NULL                COMMENT '竞价金额（元）',
  auction_volume       DECIMAL(20,2) NULL                COMMENT '竞价量（手）',
  turnover             DECIMAL(10,3) NULL                COMMENT '东财口径换手 %',
  volume_ratio         DECIMAL(10,3) NULL                COMMENT '量比',
  float_cap            DECIMAL(20,2) NULL                COMMENT '流通市值（元）',
  total_cap            DECIMAL(20,2) NULL                COMMENT '总市值（元）',
  yesterday_amount     DECIMAL(20,2) NULL                COMMENT '昨日成交额（元）',
  yesterday_turnover   DECIMAL(10,3) NULL                COMMENT '昨日换手 %',
  yesterday_close      DECIMAL(12,3) NULL                COMMENT '昨日收盘价',
  ratio_to_yesterday   DECIMAL(10,3) NULL                COMMENT '竞价占昨日成交 %',
  amount_strength      DECIMAL(10,3) NULL                COMMENT '金额强度 bp',
  auction_turnover     DECIMAL(10,3) NULL                COMMENT '竞价换手 %',
  score                DECIMAL(6,2)  NULL                COMMENT '超预期分',
  `rank`               INT           NULL                COMMENT '当日排名',
  tags                 VARCHAR(128)  NULL                COMMENT '状态标签，逗号分隔',
  UNIQUE KEY uq_day_code (day_id, code),
  KEY idx_code (code),
  KEY idx_day_score (day_id, score)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '每日个股竞价明细';

-- 常用查询示例 ---------------------------------------------------------
-- 最近交易日列表（含股票数）：
--   SELECT d.trade_date, d.fetched_at, COUNT(s.id) AS stock_count
--   FROM auction_day d
--   LEFT JOIN auction_stock s ON s.day_id = d.id
--   GROUP BY d.id, d.trade_date, d.fetched_at
--   ORDER BY d.trade_date DESC;
--
-- 某只股票的多日历史：
--   SELECT d.trade_date, s.*
--   FROM auction_stock s
--   JOIN auction_day d ON d.id = s.day_id
--   WHERE s.code = '600000'
--   ORDER BY d.trade_date DESC;