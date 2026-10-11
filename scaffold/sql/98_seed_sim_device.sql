-- ============================================================================
-- 仿真 WIM 设备登记（幂等，可重复执行）
-- ----------------------------------------------------------------------------
-- ★ 什么时候跑：**设计文件导入之后**。不是容器初始化的一部分。
--   本文件**没有**挂进 docker-entrypoint-initdb.d —— 故意不挂。
--   理由：它要挂靠的那条路段（road_section「毕设」）是 design_import 从学生的
--   设计文件里建出来的，容器初始化那一刻它还不存在。挂上去只会静默插零条。
--
-- 为什么需要它：
--   Chrono 跑的是 road_section「毕设」K0+000→K5+805，而全库现有的两个监测断面
--   都挂在「滨海大通道试验段」上。把仿真载荷发到 WIM01 名下，等于往库里写一条
--   「车在滨海大通道 K4640+000 过车」的记录 —— 而车从没去过那条路。
--   所以给仿真路单独登记一个设备，溯源就落在 data_source 上，不必伪造地理位置。
--
-- 为什么 device_code 是唯一的溯源通道：
--   wim_axle.v1 契约的**三层**（信封 / payload / axles[]）都是
--   additionalProperties: false —— 报文里里外外**没有**标注「这是仿真」的位置。
--   设备名里的 SIM 不是装饰，它承载语义。
--
-- 用法：
--   docker exec -i -e PGPASSWORD=... rp-pg psql -U <user> -d <db> -v ON_ERROR_STOP=1 \
--     -f - < scaffold/sql/98_seed_sim_device.sql
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------- 前置检查
-- ★ 找不到「毕设」就**报错**，不要静默跳过。
--   实测教训：把这段依赖写进 90_seed_skeleton.sql 时，全新库上它匹配到零行、
--   一声不响地插了 0 条，而活库上看起来「通过」—— 因为活库里的「毕设」
--   是别处导入的。静默的零条比报错难查得多。
DO $$
DECLARE
  n_section int;
BEGIN
  SELECT count(*) INTO n_section FROM road_section WHERE section_name = '毕设';
  IF n_section = 0 THEN
    RAISE EXCEPTION
      '找不到「毕设」路段：本脚本必须在设计文件导入（design_import）之后运行。';
  END IF;
  RAISE NOTICE '前置检查通过：「毕设」路段 % 条。', n_section;
END $$;

-- ---------------------------------------------------------------- 监测断面（挂仿真路）
-- purpose / remark 都写成**自证**的文本：看见它的人不必读别处就知道它不是实体资产。
INSERT INTO monitor_cross_section
  (section_id, station_text, station_km, lane_no, purpose, install_date, status, remark)
SELECT s.id, 'K2+900', 2.900, 2,
       '轴载调查（WIM）— 仿真回放，非实体资产',
       DATE '2026-10-11', 'active', 'mdsite=simwim01'
FROM road_section s
WHERE s.section_name = '毕设'
  -- ★ 守卫带上 section_id。既有种子那条只用 station_km —— 两条路桩号撞了就会漏插。
  AND NOT EXISTS (
    SELECT 1 FROM monitor_cross_section m
    WHERE m.section_id = s.id AND m.station_km = 2.900
  );

-- ---------------------------------------------------------------- 设备（sensor_install.serial_no = MQTT device_code）
INSERT INTO sensor_install
  (cross_section_id, sensor_type_code, sensor_model, manufacturer, serial_no,
   install_mode, position_desc, install_date, status, remark)
SELECT m.id, 'WIM_QUARTZ', 'Chrono 仿真（无实体）', '本平台', 'SIMWIM01',
       '仿真', 'K2+900 行车道 2 轮迹带（Chrono 回放，无实体传感器）',
       DATE '2026-10-11', 'active', 'site_id=simwim01'
FROM monitor_cross_section m
WHERE m.remark = 'mdsite=simwim01'
  AND NOT EXISTS (SELECT 1 FROM sensor_install si WHERE si.serial_no = 'SIMWIM01');

-- ---------------------------------------------------------------- 通道（逻辑测点）
-- ★ 这一条不能省：ingest 的设备映射是 sensor_install **JOIN** sensor_channel，
--   不是 LEFT JOIN。只登记设备不登记通道，JOIN 不到就进不了映射表，
--   报文会被判「设备未注册」并计数进 rejected。
INSERT INTO sensor_channel
  (install_id, channel_no, quantity_code, unit, sample_rate_hz,
   acquire_mode, storage_policy, alarm_enable, remark)
SELECT si.id, 1, 'axle_load', 'kg', 1.00,
       'trigger', 'full', true, '过车事件通道（仿真回放）'
FROM sensor_install si
WHERE si.serial_no = 'SIMWIM01'
  AND NOT EXISTS (
    SELECT 1 FROM sensor_channel sc
    WHERE sc.install_id = si.id AND sc.channel_no = 1
  );

COMMIT;

-- 自检：
--   复现 ingest 的 MAP_SQL（三台设备都应出现，且 SIMWIM01 带 axle_load）：
--     SELECT si.serial_no, si.cross_section_id, sc.quantity_code
--     FROM sensor_install si JOIN sensor_channel sc ON sc.install_id = si.id
--     WHERE si.status = 'active' ORDER BY si.id, sc.id;
--
--   溯源链（应落在「毕设」上，不是「滨海大通道试验段」）：
--     SELECT si.serial_no, mcs.station_text, rs.section_name, mcs.purpose
--     FROM sensor_install si
--     JOIN monitor_cross_section mcs ON mcs.id = si.cross_section_id
--     JOIN road_section rs ON rs.id = mcs.section_id
--     WHERE si.serial_no = 'SIMWIM01';
