# ADR-0008 存储迁移：SQLite → PostgreSQL

日期：2026-09-04　状态：已采纳（P2 会话/配置/预算，P3 知识库，P4 业务库）

## 决策

- 迁移范围与语义：
  - tx_sessions / tx_messages（P2）：TTL 由查询条件 + Ensure 先删过期行实现
    （语义逐字对齐内存版，双实现共用同一测试套件锁定）。
  - agent_config（P2）：单行表，默认行 = prompts.go 逐字快照，首开种子。
  - budget_usage（P2）：见 ADR-0007。
  - kb_docs / kb_chunks（P3）：UpsertDoc 幂等（先删旧 chunk 与向量再插）、
    BIGSERIAL 主键。
  - venues / bookings / leave_tickets（P4）：DDL 与校验顺序逐字
    （PARITY §8）；`created_at` 中国时区秒精度（TIMESTAMPTZ 写入
    UTC+8 墙钟、秒截断）。
- **序列语义**：SQLite AUTOINCREMENT 与 PG BIGSERIAL 在 DELETE 后都不复位
  ——reset 后单号继续增长，两侧一致；评测断言不含单号字面值（已核对）。

## 后果

- 单体的 data/*.db 与微服务 PG 并存到 P4 结束；A/B 对照期间两侧各自重置
  （评测每例前 reset，天然隔离）。
- internal 错误文案可能含 PG 驱动文本（PARITY-MS #6 同族，仅调试可见）。
