-- ============================================================================
-- 分区维护：wim_axle_record 按月分区（幂等，可重复执行）
-- ----------------------------------------------------------------------------
-- 执行时机：① 装库时由 docker-entrypoint-initdb.d 自动执行（建 2025-11 → 2027-12）
--           ② 此后由 M4 的 Airflow DAG（每月 1 日）调用 ensure_wim_partition()
-- 设计依据：DDL v0.1 · C1「生产建议提前建 12 个月 + 默认分区」
-- ============================================================================
BEGIN;

-- 建一个月分区（已存在则跳过）
CREATE OR REPLACE FUNCTION ensure_wim_partition(p_month date)
RETURNS text AS $$
DECLARE
    v_start timestamptz := date_trunc('month', p_month);
    v_end   timestamptz := date_trunc('month', p_month) + interval '1 month';
    v_part  text        := format('wim_axle_record_p%s', to_char(v_start, 'YYYYMM'));
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = v_part AND relkind IN ('r', 'p')) THEN
        RETURN v_part || ' 已存在';
    END IF;

    EXECUTE format(
        'CREATE TABLE %I PARTITION OF wim_axle_record FOR VALUES FROM (%L) TO (%L)',
        v_part, v_start, v_end);

    RETURN v_part || ' 已创建';
END;
$$ LANGUAGE plpgsql;

-- 覆盖期：试验段数据起始月 → 结题后一年（够用；之后由调度脚本续建）
DO $$
DECLARE
    m date;
BEGIN
    FOR m IN SELECT generate_series(date '2025-11-01', date '2028-12-01', interval '1 month')::date
    LOOP
        RAISE NOTICE '%', ensure_wim_partition(m);
    END LOOP;
END $$;

-- 兜底分区：任何落在已建区间外的数据先"接住"再报警，避免插入失败直接断链路。
--   ⚠ 注意：PostgreSQL 中若兜底分区已有数据，再建同区间新分区会失败。
--   处理办法（M4 质量例程）：发现数据落在兜底分区 → 补建对应月分区 →
--   把行搬运过去 → 再报警。骨架期先保证"不丢数据"。
CREATE TABLE IF NOT EXISTS wim_axle_record_pdefault
    PARTITION OF wim_axle_record DEFAULT;
CREATE INDEX IF NOT EXISTS idx_wim_pdefault_ts ON wim_axle_record_pdefault(pass_time);

-- 分区清单自检（人工核对用）
CREATE OR REPLACE VIEW v_wim_partitions AS
SELECT c.relname                              AS partition_name,
       pg_get_expr(c.relpartbound, c.oid)     AS bound,
       pg_total_relation_size(c.oid)          AS size_bytes
FROM pg_class c
JOIN pg_inherits i ON i.inhrelid = c.oid
JOIN pg_class p    ON p.oid = i.inhparent
WHERE p.relname = 'wim_axle_record'
ORDER BY c.relname;

COMMIT;
