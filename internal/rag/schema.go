package rag

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

// execSchema 建扩展/函数/表/索引（IF NOT EXISTS / OR REPLACE，重复调用幂等）。
// DDL 为静态字面量，无任何外部输入。分词与关键词检索逻辑全部收口在 SQL 侧
// （rag_tokenize / rag_fts_search），调用侧只传原文参数。
func execSchema(ctx context.Context, pool *pgxpool.Pool) error {
	stmts := []string{
		`CREATE EXTENSION IF NOT EXISTS vector`,
		// rag_tokenize：中文二元语法 + 拉丁词小写（与 Go 侧 Tokenize 语义一致，
		// 集合等价）。入库 tsv 与查询分词共用，两端天然对齐。
		//（汉字区间用字面量「一-鿿」即 U+4E00–U+9FFF，避免转义歧义。）
		`CREATE OR REPLACE FUNCTION rag_tokenize(t text) RETURNS text[] LANGUAGE sql IMMUTABLE AS $fn$
			SELECT COALESCE(array_agg(DISTINCT tok), ARRAY[]::text[]) FROM (
				SELECT lower(w[1]) AS tok
				FROM regexp_matches(t, '[a-zA-Z0-9]+', 'g') AS w
				UNION ALL
				SELECT h.arr[i] || h.arr[i+1] AS tok
				FROM (SELECT array_agg(m[1]) AS arr
				      FROM regexp_matches(t, '[一-鿿]', 'g') AS m) h,
				     generate_subscripts(h.arr, 1) AS i
				WHERE h.arr IS NOT NULL AND i < array_length(h.arr, 1)
			) toks
		$fn$`,
		// rag_tokenized_text：array_to_string 在 PG 标记为 STABLE，生成列要求
		// IMMUTABLE，包一层并声明（纯函数，确实确定）。
		`CREATE OR REPLACE FUNCTION rag_tokenized_text(t text) RETURNS text LANGUAGE sql IMMUTABLE AS $fn$
			SELECT array_to_string(rag_tokenize(t), ' ')
		$fn$`,
		`CREATE TABLE IF NOT EXISTS docs (
			id      TEXT PRIMARY KEY,
			title   TEXT NOT NULL,
			source  TEXT NOT NULL,
			updated TEXT NOT NULL DEFAULT ''
		)`,
		// tsv 生成列直接从 text 分词（生成列不能引用另一生成列，故无 tokenized
		// 中间列）。父块行也有 tsv，但 rag_fts_search 按 is_parent=0 过滤——
		// 「子块匹配/父块回答」漏斗不被父块污染，与 SQLite 版语义一致。
		`CREATE TABLE IF NOT EXISTS chunks (
			id           BIGSERIAL PRIMARY KEY,
			doc_id       TEXT NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
			seq          INTEGER NOT NULL,
			text         TEXT NOT NULL,
			tsv          tsvector GENERATED ALWAYS AS (to_tsvector('simple', rag_tokenized_text(text))) STORED,
			parent_id    TEXT,
			section_path TEXT NOT NULL DEFAULT '',
			is_parent    BOOLEAN NOT NULL DEFAULT false
		)`,
		// halfvec(2048)：HNSW 索引上限 2000 维，2048 维向量须用半精度 halfvec
		//（HNSW 支持到 4000 维；半精度对检索质量的影响可忽略，pgvector 官方推荐路径）。
		`CREATE TABLE IF NOT EXISTS vectors (
			chunk_id  BIGINT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
			embedding halfvec(2048) NOT NULL
		)`,
		`CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING GIN (tsv)`,
		`CREATE INDEX IF NOT EXISTS chunks_parent ON chunks(parent_id)`,
		// 向量入库前已 L2 归一化 → cosine 与内积等价；千级规模 HNSW 秒建。
		`CREATE INDEX IF NOT EXISTS vectors_hnsw ON vectors USING hnsw (embedding halfvec_cosine_ops)`,
		// rag_fts_search：关键词检索。查询原文分词后逐 token 转 tsquery（token
		// 出自分词器、不含 tsquery 语法），任一 token 命中即召回（OR 语义，长
		// query 不会被 AND 过滤成空），得分 = 各命中 token 的 ts_rank_cd 之和。
		`CREATE OR REPLACE FUNCTION rag_fts_search(cfg regconfig, qtext text, lim integer)
		RETURNS TABLE(id bigint, score float8) LANGUAGE sql STABLE AS $$
			WITH q AS (SELECT unnest(rag_tokenize(qtext)) AS tok)
			SELECT c.id, SUM(ts_rank_cd(c.tsv, to_tsquery(cfg, q.tok)))::float8
			FROM chunks c JOIN q ON c.tsv @@ to_tsquery(cfg, q.tok)
			WHERE c.is_parent = 0
			GROUP BY c.id
			ORDER BY 2 DESC, 1 ASC
			LIMIT lim
		$$`,
		// rag_upsert_doc：写路径收口为存储函数——幂等替换一个文档的全部 chunk
		// 与向量（FK 级联删旧），父块不写向量。载荷为 JSONB（应用侧 json.Marshal，
		// vec 以 JSON 数组文本直接 cast halfvec）。parent_idx 语义与 Go 版一致：
		// 指向同批先于它出现的父块下标，非法即报错。
		`CREATE OR REPLACE FUNCTION rag_upsert_doc(p_doc jsonb, p_records jsonb)
		RETURNS integer LANGUAGE plpgsql AS $fn$
		DECLARE
			r    jsonb;
			i    int := 0;
			cid  bigint;
			pid  bigint;
			pidx int;
			ids  bigint[] := ARRAY[]::bigint[];
		BEGIN
			DELETE FROM chunks WHERE doc_id = p_doc->>'id';
			INSERT INTO docs (id, title, source, updated)
			VALUES (p_doc->>'id', COALESCE(p_doc->>'title', p_doc->>'id'),
			        COALESCE(p_doc->>'source', ''), COALESCE(p_doc->>'updated', ''))
			ON CONFLICT (id) DO UPDATE
			SET title = EXCLUDED.title, source = EXCLUDED.source, updated = EXCLUDED.updated;
			FOR r IN SELECT * FROM jsonb_array_elements(p_records) LOOP
				i := i + 1;
				pidx := COALESCE((r->>'parent_idx')::int, -1);
				IF pidx >= 0 THEN
					IF pidx + 1 > array_length(ids, 1) OR ids[pidx + 1] IS NULL THEN
						RAISE EXCEPTION 'chunk % 的父块下标 % 非法（父块须先于子块写入）', i - 1, pidx;
					END IF;
					pid := ids[pidx + 1];
				ELSE
					pid := NULL;
				END IF;
				INSERT INTO chunks (doc_id, seq, text, parent_id, section_path, is_parent)
				VALUES (p_doc->>'id', i - 1, r->>'text', pid,
				        COALESCE(r->>'section_path', ''),
				        COALESCE((r->>'is_parent')::boolean, false))
				RETURNING id INTO cid;
				ids := array_append(ids, cid);
				IF NOT COALESCE((r->>'is_parent')::boolean, false)
				   AND r ? 'vec' AND jsonb_typeof(r->'vec') = 'array' THEN
					INSERT INTO vectors (chunk_id, embedding)
					VALUES (cid, (r->>'vec')::halfvec);
				END IF;
			END LOOP;
			RETURN array_length(ids, 1);
		END
		$fn$`,
	}
	for _, ddl := range stmts {
		if _, err := pool.Exec(ctx, ddl); err != nil {
			return fmt.Errorf("建索引库 schema 失败（%.60s…）: %w", ddl, err)
		}
	}
	return nil
}
