-- =============================================================================
--  核查.sql —— 在 pgAdmin 的 Query Tool 里整份打开、逐段执行
--
--  连接：主机 localhost  端口 55432  库 road_pavement  用户 rp
--        （口令在 scaffold/.env 的 PG_PASSWORD；库在 Docker 里，端口映射到宿主机 55432）
--
--  这份脚本**只读**：全是 select，不写库、不改结构。放心跑。
-- =============================================================================


-- ─────────────────────────────────────────────────────────────────────────────
-- ① 总览：契约口径的物理表数应当是 44
--
-- ⚠ 别用 information_schema 数。它会把时序分区子表和分区父表也算进去
--   （实测 84 = 44 普通表 + 39 分区子表 + 1 分区父表），拿 84 去比契约里的
--   EXPECTED_PHYSICAL_TABLES = 44 会以为对不上。分区是设计使然，不是错。
-- ─────────────────────────────────────────────────────────────────────────────
-- ⚠⚠ 这里有个**骗人的巧合**，务必看清：
--   · 契约目录 catalog.ALL_TABLES 的 44 = **我们的 44 张表**（含分区**父表**
--     wim_axle_record，不含 PostGIS 自带的 spatial_ref_sys）。
--   · 而库里 "relkind='r' 且非分区子表" **也是 44** —— 但那是「我们的 43 张
--     + PostGIS 的 spatial_ref_sys」。**数字对上了，理由却是错的。**
--   所以别用 relkind='r' 数。正确口径是下面这个：
--     relkind in ('r','p') 且非分区子表，再排掉 spatial_ref_sys。
--   更权威的比对是契约测试 —— 它拿 catalog.ALL_TABLES 跟库**逐名**对，不是数个数。
select count(*) filter (where c.relkind in ('r', 'p') and not c.relispartition
                          and c.relname <> 'spatial_ref_sys') as "我们的物理表(应=44)",
       count(*) filter (where c.relkind = 'p')                       as "分区父表(wim_axle_record)",
       count(*) filter (where c.relkind = 'r' and c.relispartition)  as "分区子表",
       count(*) filter (where c.relname = 'spatial_ref_sys')         as "PostGIS 自带表"
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
 where n.nspname = 'public';


-- ─────────────────────────────────────────────────────────────────────────────
-- ② 你关心的两张设计输入表的行数
-- ─────────────────────────────────────────────────────────────────────────────
select 'superelev_transition 超高变化点' as 表, count(*) as 行数 from superelev_transition
union all
select 'roadbed_width 路幅宽度', count(*) from roadbed_width;


-- ─────────────────────────────────────────────────────────────────────────────
-- ③ ★ 关键核查：两张表的唯一键**都必须含桩号**
--
-- 期望看到：
--   superelev_transition → superelev_transition_section_id_station_km_key  (station_km)
--   roadbed_width        → roadbed_width_section_id_side_station_km_key    (side, station_km)
--
-- 如果看到的是 …_transition_seq_key 或 …_interval_seq_key，说明还是旧的区间键，
-- 迁移没生效（重跑 scaffold/sql/85_migrate_v03_ge.sql）。
-- ─────────────────────────────────────────────────────────────────────────────
select r.relname                as 表,
       c.conname                as 约束名,
       case c.contype when 'p' then '主键' when 'u' then '唯一' else c.contype::text end as 类型,
       (select string_agg(a.attname, ', ' order by a.attnum)
          from unnest(c.conkey) k(attnum)
          join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum) as 包含列
  from pg_constraint c
  join pg_class r on r.oid = c.conrelid
 where r.relname in ('superelev_transition', 'roadbed_width')
   and c.contype in ('u', 'p')
 order by 1, 2;


-- ─────────────────────────────────────────────────────────────────────────────
-- ④ ★ 路幅宽度：**一行一个桩号**（不是一行一个区间）
--
-- 期望 4 行：左 0.000 / 左 5.701461 / 右 0.000 / 右 5.701461
--   · seq_no  该侧第几个数据行
--   · group_seq 该侧第几个桩号区间（教程「每两行为一组」的组号；
--               同一 group_seq 的两行 = 这个区间的起、终点，**区间是推得的、不落库**）
--   · 没有 start_station_km / end_station_km 两列 —— 这是重点，旧设计才有
-- ─────────────────────────────────────────────────────────────────────────────
select side                     as 侧别,
       seq_no                   as 行序,
       group_seq                as 组号,
       station_km               as 桩号_km,
       median_width_m           as 中央分隔带,
       half_carriageway_width_m as 半侧路面,
       extra_lane_flag          as 附加车道标识,
       hard_shoulder_width_m    as 硬路肩,
       earth_shoulder_width_m   as 土路肩,
       extra_lane_file          as 附加车道文件
  from roadbed_width
 order by side, station_km;

-- 确认真的没有区间两列（期望返回 0 行）
select column_name from information_schema.columns
 where table_name = 'roadbed_width'
   and column_name in ('start_station_km', 'end_station_km', 'interval_seq');

-- 路基总宽由左右两行**跨行**推出（表里刻意不存，因为生成列不能跨行）
select station_km as 桩号_km,
       max(median_width_m)                                             as 中央分隔带,
       sum(half_carriageway_width_m + hard_shoulder_width_m
           + earth_shoulder_width_m)                                   as 两侧合计,
       max(median_width_m) + sum(half_carriageway_width_m
           + hard_shoulder_width_m + earth_shoulder_width_m)           as 路基总宽
  from roadbed_width
 group by station_km
 order by station_km;


-- ─────────────────────────────────────────────────────────────────────────────
-- ⑤ 超高变化点（A16）：看桩号是不是递增的、9999 有没有被当数字存进来
--
-- ⚠ 教程 §13.5 里 9999 是**哨兵**（表示该值沿用上一个），不是数值。
--   所以本表允许 NULL（源文件里的 9999 存成 NULL），数值列不会出现 9999。
-- ─────────────────────────────────────────────────────────────────────────────
select transition_seq as 序号,
       station_km      as 桩号_km,
       lane_left_pct            as 左侧车道横坡,
       lane_right_pct           as 右侧车道横坡,
       hard_shoulder_left_pct   as 左硬路肩,
       hard_shoulder_right_pct  as 右硬路肩,
       earth_shoulder_left_pct  as 左土路肩,
       earth_shoulder_right_pct as 右土路肩
  from superelev_transition
 order by station_km
 limit 12;

-- 元检查：任何一列都不该出现 9999（出现了说明哨兵被当数值存了）
select count(*) as "含 9999 的行数(应=0)"
  from superelev_transition
 where 9999 in (lane_left_pct, lane_right_pct, hard_shoulder_left_pct,
                hard_shoulder_right_pct, earth_shoulder_left_pct, earth_shoulder_right_pct);


-- ─────────────────────────────────────────────────────────────────────────────
-- ⑥ 设计变化点的桩号**不是** .STA 桩号序列的子集 —— 这正是两表不挂 station_id 外键的原因
--
-- 期望：76 个超高变化点里只有 34 个落在 station_sequence 里。
-- 若强行挂外键，就得往一等实体 station_sequence 里塞「不是桩号序列的点」。
-- ─────────────────────────────────────────────────────────────────────────────
select count(*) as 超高变化点总数,
       count(*) filter (where exists (
           select 1 from station_sequence ss
            where ss.section_id = st.section_id
              and ss.station_local_km = st.station_km)) as 落在桩号序列里的
  from superelev_transition st;

-- ⚠ 期望**不是** 0：本表 4 个桩号里，左右两侧的 0.000000 都在桩号序列里（那是路线起点），
--   而 5.701461 **不在** —— 它是 .WID 里的**设计变化点**，不是 .STA 的桩号。
--   正是这一点（设计变化点 ⊄ 桩号序列）决定了本表不能挂 station_id 外键。
select rw.side as 侧别, rw.station_km as 桩号_km,
       exists (select 1 from station_sequence ss
                where ss.section_id = rw.section_id
                  and ss.station_local_km = rw.station_km) as 在桩号序列里
  from roadbed_width rw
 order by rw.side, rw.station_km;


-- ─────────────────────────────────────────────────────────────────────────────
-- ⑦ 数据现状总览（GE 域）
-- ─────────────────────────────────────────────────────────────────────────────
select 'station_sequence 桩号序列'    as 表, count(*) as 行数 from station_sequence
union all select 'alignment_pi 交点', count(*) from alignment_pi
union all select 'alignment_element 线元', count(*) from alignment_element
union all select 'profile_grade_point 竖曲线', count(*) from profile_grade_point
union all select 'profile_ground_point 地面线', count(*) from profile_ground_point
union all select 'superelev_transition 超高', count(*) from superelev_transition
union all select 'roadbed_width 路幅宽度', count(*) from roadbed_width
union all select 'geometry_point 逐桩几何(派生,未灌)', count(*) from geometry_point
order by 1;


-- ─────────────────────────────────────────────────────────────────────────────
-- ⑧ 几何完整度等级（L0–L4）是**算出来的，不落库**
--    所以查不到一列叫 geometry_level —— 它由 M6 网关现算。
--    判据（任一规则命中即达该级，L4 最高）：
--      L4 cross_section        （.HDM 横断面，尚未实现 → 本工程止步 L3）
--      L3 profile_grade_point + profile_ground_point（两个都要有）
--      L2 alignment_pi 或 alignment_element
--      L1 station_sequence
--    ⚠ 超高与路幅宽度**不参与**定级 —— L0–L4 只管平/纵/横。
-- ─────────────────────────────────────────────────────────────────────────────
select section_id as 路段,
       (select count(*) from station_sequence      s where s.section_id = g.section_id) as 桩号序列,
       (select count(*) from alignment_pi          s where s.section_id = g.section_id) as 交点,
       (select count(*) from alignment_element     s where s.section_id = g.section_id) as 线元,
       (select count(*) from profile_grade_point   s where s.section_id = g.section_id) as 竖曲线,
       -- ⚠ profile_ground_point / geometry_point **没有 section_id**（靠 station_id 锚定），
       --   必须经 station_sequence 绕一下 —— 这正是 GE 域锚定列不统一的地方。
       (select count(*) from profile_ground_point p
          join station_sequence ss on ss.id = p.station_id
         where ss.section_id = g.section_id) as 地面线
  from (select distinct section_id from station_sequence) g
 order by 1;


-- ─────────────────────────────────────────────────────────────────────────────
-- ⑨ 路段档案（哪条路是哪条）
-- ─────────────────────────────────────────────────────────────────────────────
-- ⚠ road_section 上**没有** road_class / design_speed / lane_count 三列 ——
--   等级/设计速度/车道数在 road_line 上（按**路线**定），路面宽在 section_design_attr 上
--   （按**路段**定）。M6 网关是 join 之后才吐成一层的。
select s.id, s.section_name as 路段, l.line_code as 路线编号, l.line_name as 路线,
       s.start_station_text as 起点, s.end_station_text as 终点,
       s.length_m as 长度_m,
       l.road_class as 等级, l.design_speed as 设计速度, l.lane_count as 车道数,
       a.roadway_width_m as 路面宽_m
  from road_section s
  left join road_line l on l.id = s.line_id
  left join section_design_attr a on a.section_id = s.id
 order by s.id;


-- ─────────────────────────────────────────────────────────────────────────────
-- ⑩ ★ 设计参数控制（.CTR）—— I 节 9 张表（v0.4 新增）
--    教程 §13.10：18 类格式 / 36 个关键字。本工程 19 个有数据、17 个为空，
--    另有一个 ZDMDG.DAT 教程全文搜不到（经确认跳过，只登记不建表）。
-- ─────────────────────────────────────────────────────────────────────────────
select 'I1 slope_segment 边坡分段'          as 表, count(*) as 行数 from slope_segment
union all select 'I2 ditch_segment 边沟/排水沟',    count(*) from ditch_segment
union all select 'I3 standard_cross_section 标准断面', count(*) from standard_cross_section
union all select 'I4 roadbed_trench 路槽',          count(*) from roadbed_trench
union all select 'I5 structure_control 桥涵隧道',    count(*) from structure_control
union all select 'I6 earthwork_composition 土石成份', count(*) from earthwork_composition
union all select 'I7 land_use_width 用地宽度',       count(*) from land_use_width
union all select 'I8 extra_fill 超填/清表(源为空)',  count(*) from extra_fill
union all select 'I9 design_control_text 地质/水准(源为空)', count(*) from design_control_text
order by 1;


-- ─────────────────────────────────────────────────────────────────────────────
-- ⑪ ★★ 交叉验证：.WID（A17）与 .CTR（I3）必须对上
--    这是这套解析链**最硬的一条证据**：两个来源完全不同的文件
--    （.wid 是路幅宽度文件、.ctr 是设计参数控制文件，格式与解析器都不同）
--    在同一组宽度上必须给出同一个数。对不上 ⇒ 两条链里至少一条错了，
--    而**单看任何一条都看不出来**。
--
--    ⚠ 口径必须说准（这里第一版就说过头了）：
--      · 能对的是 **4 个宽度**，不是"整张表"。
--      · **横坡对不了** —— roadbed_width 根本没有横坡列（实测 \d roadbed_width 确认）。
--        所以"横坡 2.0/2.0/3.0 两边一致"是**错的**：那三个数只在 .CTR 里有。
--      · **列名不一样**（同一个量两处叫法不同，属已知的命名待统一项）：
--          roadbed_width.median_width_m            ↔ standard_cross_section.median_half_width_m
--          roadbed_width.half_carriageway_width_m  ↔ standard_cross_section.lane_width_m
--        硬路肩/土路肩两边同名。
--    ⚠ 不能按桩号 join：分段桩号本来就不同（.WID 5.701461 / .CTR 5.805421），粒度不同。
--      .WID 每侧 2 行（同值），.CTR 每侧 1 行 —— 故按 side 聚成一行再比。
-- ─────────────────────────────────────────────────────────────────────────────
with w as (
  select side, max(median_width_m) median, max(half_carriageway_width_m) lane,
         max(hard_shoulder_width_m) hard, max(earth_shoulder_width_m) earth
    from roadbed_width group by side),
     c as (
  select side, max(median_half_width_m) median, max(lane_width_m) lane,
         max(hard_shoulder_width_m) hard, max(earth_shoulder_width_m) earth
    from standard_cross_section group by side)
select w.side as 侧,
       w.median as "WID 中分带", c.median as "CTR 中分带",
       w.lane   as "WID 行车道", c.lane   as "CTR 行车道",
       w.hard   as "WID 硬路肩", c.hard   as "CTR 硬路肩",
       w.earth  as "WID 土路肩", c.earth  as "CTR 土路肩"
  from w join c on c.side = w.side order by 1;

-- 只报"对不上"的 —— 上面那张表人眼看，这张给脚本看（0 行 = 4 个宽度全对上）
with w as (
  select side, max(median_width_m) median, max(half_carriageway_width_m) lane,
         max(hard_shoulder_width_m) hard, max(earth_shoulder_width_m) earth
    from roadbed_width group by side),
     c as (
  select side, max(median_half_width_m) median, max(lane_width_m) lane,
         max(hard_shoulder_width_m) hard, max(earth_shoulder_width_m) earth
    from standard_cross_section group by side)
select w.side as 侧, '宽度不一致' as 问题
  from w join c on c.side = w.side
 where w.median is distinct from c.median or w.lane is distinct from c.lane
    or w.hard   is distinct from c.hard   or w.earth is distinct from c.earth;


-- ─────────────────────────────────────────────────────────────────────────────
-- ⑫ ★ 关键核查：I 节 9 张表的唯一键**都必须含桩号**（与 A16/A17 同一条约定）
--    你当初的指正：宽度表/超高表错了，都应该以**桩号**为主键。
--    I 节沿用同一条约定 —— 下面列出实库里的真实唯一约束，可逐条核对。
-- ─────────────────────────────────────────────────────────────────────────────
select r.relname as 表, c.conname as 约束名,
       array_to_string(array_agg(a.attname order by k.ord), ', ') as 列
  from pg_constraint c
  join pg_class r on r.oid = c.conrelid
  join unnest(c.conkey) with ordinality k(attnum, ord) on true
  join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum
 where c.contype = 'u'
   and r.relname in ('slope_segment', 'ditch_segment', 'standard_cross_section',
                     'roadbed_trench', 'structure_control', 'earthwork_composition',
                     'land_use_width', 'extra_fill', 'design_control_text')
 group by r.relname, c.conname
 order by 1;


-- ─────────────────────────────────────────────────────────────────────────────
-- ⑬ ★★ v0.5 两张逐桩表：土方断面（.tf）与路基设计断面（.lj）
--    与 I 节（.CTR）**不同**：这两张是**逐桩**表，实测行数与 .STA 桩号序列完全相同，
--    所以对账比的是**集合相等**（少一个桩号 = 那个断面的数据丢了），
--    而不是像 .CTR 那样只比范围（.CTR 的分段桩号本来就不必覆盖全线）。
--    ★ 下面每一条「不一致」查询都**必须返回 0 行**才算通过。
-- ─────────────────────────────────────────────────────────────────────────────

\echo '── ⑬-1 条数 + station_id 挂载率（三列都必须相等）──'
select 'earthwork_section' as 表, count(*) as 行数,
       count(station_id) as 有station_id, count(station_km) as 有station_km,
       count(distinct station_id) as 不同桩号
  from earthwork_section where section_id = 6
union all
select 'roadbed_design_point', count(*), count(station_id), count(station_km),
       count(distinct station_id)
  from roadbed_design_point where section_id = 6;

\echo '── ⑬-2 ★ station_id 是否真指向本行那个桩号（不是随便挂一个）—— 必须 0 行 ──'
select 'earthwork_section' as 表, e.station_km as 本行桩号,
       s.station_local_km as station_id指向的桩号
  from earthwork_section e join station_sequence s on s.id = e.station_id
 where e.section_id = 6 and abs(s.station_local_km - e.station_km) > 1e-9
union all
select 'roadbed_design_point', d.station_km, s.station_local_km
  from roadbed_design_point d join station_sequence s on s.id = d.station_id
 where d.section_id = 6 and abs(s.station_local_km - d.station_km) > 1e-9;

\echo '── ⑬-3 ★★ 与 .STA 桩号集合**相等**（逐桩表的核心断言）—— 必须 0 行 ──'
with sta as (select id from station_sequence where section_id = 6)
select '桩号序列里有、earthwork_section 里没有' as 问题, count(*) as 个数
  from sta where id not in (select station_id from earthwork_section where section_id = 6)
union all
select 'earthwork_section 里有、桩号序列里没有',
       count(*) from earthwork_section e where e.section_id = 6 and e.station_id not in (select id from sta)
union all
select '桩号序列里有、roadbed_design_point 里没有',
       count(*) from sta where id not in (select station_id from roadbed_design_point where section_id = 6)
union all
select 'roadbed_design_point 里有、桩号序列里没有',
       count(*) from roadbed_design_point d where d.section_id = 6 and d.station_id not in (select id from sta);

\echo '── ⑬-4 ★ 桩号精度回归：全库 station*_km 列的小数位必须都 ≥ 6（1 mm）—— 必须 0 行 ──'
--   这一条是**真事故**换来的：初版给 earthwork_section.station_km 写了 numeric(10,4)（0.1 m），
--   而全库其余 30 处同族列都是 numeric(12,6)。值被舍入，导致 ⑬-2 一度报 39 行"对不上"。
--   同族列必须同一个标准 —— "对本工程够用"不是标准（本工程 .tf 的桩号是 20 m 一个）。
select table_name as 表, column_name as 列,
       numeric_precision || ',' || numeric_scale as 精度
  from information_schema.columns
 where column_name ~ 'station.*_km$' and numeric_scale < 6
   and table_schema = 'public';

\echo '── ⑬-5 落库值抽样：与源文件逐字对得上吗 ──'
\echo '   .tf 前两行（源文件：0.000 / 5.019 / 0.132 / 0.000 / 1 与 0.020 / 3.440 / 0.555 / 0.142 / 1）'
select station_km, cut_area_m2, fill_area_m2, center_fill_cut_m, drainage_ditch_flag
  from earthwork_section where section_id = 6 order by station_km limit 2;
\echo '   .lj 前两行（源文件：0.000 / 57.262 / 57.262 / 3.500 / 3.500）'
select station_km, ground_elev_m, design_elev_m, left_lane_width_m, right_lane_width_m
  from roadbed_design_point where section_id = 6 order by station_km limit 2;

\echo '── ⑬-6 ★ 唯一约束必须含桩号（与 A16/A17/I 节同一条约定）──'
select r.relname as 表, c.conname as 约束名,
       array_to_string(array_agg(a.attname order by k.ord), ', ') as 列
  from pg_constraint c
  join pg_class r on r.oid = c.conrelid
  join unnest(c.conkey) with ordinality k(attnum, ord) on true
  join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum
 where c.contype = 'u'
   and r.relname in ('earthwork_section', 'roadbed_design_point')
 group by r.relname, c.conname
 order by 1;

\echo '── ⑬-7 列数核对（J1 74 列数据 + 4，J2 24 列数据 + 4）──'
select table_name as 表, count(*) as 总列数,
       count(*) filter (where column_name in ('id','section_id','station_id','remark')) as 元列,
       count(*) filter (where column_name not in ('id','section_id','station_id','remark')) as 数据列
  from information_schema.columns
 where table_name in ('earthwork_section', 'roadbed_design_point')
 group by table_name order by 1;
