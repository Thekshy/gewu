# ADR-0002 向量存储：Milvus FLAT 为默认，PG 余弦为降级

日期：2026-09-04　状态：已采纳（P3）

## 背景

冻结单体用 SQLite 暴力余弦（千级 chunk 毫秒级）。微服务化要求外部向量库，
且 **/api/search top-k 必须与单体逐位一致**（P3 验收）。

## 决策

- 默认 `MILVUS_ADDR` 配置时用 **Milvus standalone，FLAT 精确索引 + IP 度量**：
  入库前 L2 归一化 ⇒ 内积 = 余弦，与单体暴力余弦数学等价；FLAT 不做近似
  （IVF/HNSW 会引入召回差异，违背逐位一致验收）。
- 降级实现（未配置 Milvus / 资源受限）：**PG 表 + 服务进程内暴力余弦**
  （kb_vectors BLOB，float32 小端，与单体 SQLite vectors 表同构）。
  规格中的「pgvector」角色由该实现承担：不依赖 pgvector 扩展换取部署
  可移植性，检索数学相同（FLAT 等价）。千级规模暴力扫描延迟毫秒级。
- **平局次序**：Milvus 并列分数的返回序不保证按 chunk_id——检索后在服务侧
  按 (score, chunk_id) 稳定化重排，对齐单体的构造性确定序。
- 换嵌入模型（维度变化）时维度不匹配向量跳过（与单体一致）。

## 后果

- compose 向量栈（etcd/minio/milvus）走 `--profile milvus`，零 key/BM25-only
  模式不依赖它。
- 未来扩到十万级 chunk 再评估 IVF（另立 ADR，需重跑检索 A/B）。
