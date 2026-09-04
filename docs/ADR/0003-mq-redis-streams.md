# ADR-0003 摄入 MQ：Redis Streams（consumer group）

日期：2026-09-04　状态：已采纳（P3）

## 背景

摄入需要解耦为流水线（上传→解析→切分→embedding→入库），但 `make ingest`
的**阻塞语义**是评测与 A/B 对照的前提（确定性索引状态）。选型需轻量、
compose 内已有。

## 决策

- 任务流 `gewu:ingest:jobs`（consumer group `rag-ingest`）+ 回执流
  `gewu:ingest:done`。rag 服务既是发布方（Ingest RPC）也是消费方（后台循环）。
- 阻塞语义实现：Ingest RPC 发布 N 个任务后 XREAD（BLOCK，"$" 只等新回执）
  直到收齐 N 个回执，超时（10 分钟）报错——CLI（make ingest-ms）逐行打印进度。
- 异步上传 API（新增演示面）：写 uploads 目录 + 发布单文档任务，立即返回。
- Redis 已在依赖清单内（配置缓存/预算），不引入 Kafka/RabbitMQ。

## 后果

- 单 rag 实例消费即可（当前规模）；多实例时 consumer group 天然分摊，
  回执按 run_id 过滤。
- Redis 不可用时摄入不可用（检索不受影响）；部署依赖如实体现在 compose。
