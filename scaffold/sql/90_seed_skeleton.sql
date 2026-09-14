-- ============================================================================
-- 骨架栈种子数据（幂等，可重复执行）
-- ----------------------------------------------------------------------------
-- 目的：让「一条 WIM 报文」有地方落、有设备可映射——接入服务的 device_code
--       必须能在 sensor_install.serial_no 里查到（这是设备自管的注册入口。
--       P1 会新增 device_status 运行状态表，此处先用 sensor_install 承担注册）。
-- 说明：本文件只插"数据"（档案/字典/设备），不改任何表结构——遵守设计冻结。
-- ============================================================================
BEGIN;

-- ---------------------------------------------------------------- 字典（设备/物理量/轴型）
INSERT INTO dict_sensor_type (code, name, category, remark) VALUES
  ('WIM_QUARTZ',    '石英晶体称重传感器', '交通荷载', 'WIM 轴载站（ZDG-40-SY-2 同类）'),
  ('STRAIN_ASPHALT','沥青埋入式应变计',   '结构响应', '路面结构层内应变监测'),
  ('ACCEL_3AXIS',   '三向加速度计',       '结构响应', '振动响应（RE 域）'),
  ('TEMP_RH',       '温湿度计',           '气象环境', '路面/空气温湿度')
ON CONFLICT (code) DO NOTHING;

INSERT INTO dict_quantity (code, name, unit, remark) VALUES
  ('axle_load', '轴载',   'kg',   'WIM 过车荷载（骨架栈使用）'),
  ('strain',    '应变',   'με',   '结构响应'),
  ('accel',     '加速度', 'g',    '结构响应'),
  ('temp',      '温度',   '℃',    '结构/环境温度'),
  ('rh',        '湿度',   '%RH',  '环境湿度'),
  ('deflection','挠度',   'mm',   '结构变形')
ON CONFLICT (code) DO NOTHING;

INSERT INTO dict_axle_type (code, name, remark) VALUES
  ('A2', '2 轴汽车',   NULL),
  ('T3', '3 轴货车',   NULL),
  ('T4', '4 轴货车',   NULL),
  ('T5', '5 轴货车',   'G228 实测主力车型'),
  ('T6', '6 轴货车',   '重载主力车型')
ON CONFLICT (code) DO NOTHING;

INSERT INTO dict_stat_metric (code, name, remark) VALUES
  ('max', '最大值', NULL), ('min', '最小值', NULL), ('mean', '平均值', NULL),
  ('rms', '均方根', NULL), ('p95', '95 分位', NULL), ('esal_cnt', '当量轴次累计', NULL)
ON CONFLICT (code) DO NOTHING;

INSERT INTO dict_disease_type (code, name, remark) VALUES
  ('crack', '裂缝', NULL), ('pothole', '坑槽', NULL), ('rutting', '车辙', 'G228 高温重载段落重点'),
  ('patching', '修补', NULL), ('loose', '松散', NULL)
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------- 档案（路线 → 路段 → 结构层）
INSERT INTO road_line (line_code, line_name, admin_region, road_class, design_speed,
                       design_load, lane_count, lane_width_m, manage_org, remark)
VALUES ('G228', '国道 228 线滨海大通道（福清段）', '福建省福州市福清市', '一级公路', 80,
        'BZZ-100', 4, 3.75, '福清公路中心', '2025Y095 试验段所在路线')
ON CONFLICT (line_code) DO NOTHING;

INSERT INTO road_section (line_id, section_name, start_stake, end_stake, start_km, end_km,
                          length_m, direction, pavement_type, climate_zone)
SELECT l.id, '滨海大通道试验段', 'K4635+000', 'K4654+701', 4635.000, 4654.701,
       19701.0, '双向', '沥青混凝土', '南方湿热滨海'
FROM road_line l
WHERE l.line_code = 'G228'
  AND NOT EXISTS (SELECT 1 FROM road_section s WHERE s.section_name = '滨海大通道试验段');

INSERT INTO structure_layer (section_id, layer_no, layer_name, layer_role, material, thickness_cm)
SELECT s.id, v.layer_no, v.layer_name, v.layer_role, v.material, v.thickness_cm
FROM road_section s
CROSS JOIN (VALUES
  (1, 'AC-13C 改性沥青抗滑表层', '面层',   'SBS 改性沥青混凝土',   4.0),
  (2, 'AC-20C 中面层',           '面层',   '改性沥青混凝土',       6.0),
  (3, 'ATB-25 沥青碎石',         '基层',   '密级配沥青碎石',      12.0),
  (4, '级配碎石上基层',          '基层',   '级配碎石',            16.0),
  (5, '5% 水泥稳定碎石底基层',   '底基层', '水泥稳定碎石',        32.0)
) AS v(layer_no, layer_name, layer_role, material, thickness_cm)
WHERE s.section_name = '滨海大通道试验段'
ON CONFLICT (section_id, layer_no) DO NOTHING;

-- ---------------------------------------------------------------- 监测断面（空间锚点）
INSERT INTO monitor_cross_section (section_id, stake_text, stake_km, lane_no, purpose, install_date, status, remark)
SELECT s.id, v.stake_text, v.stake_km, v.lane_no, v.purpose, v.install_date::date, 'active', v.remark
FROM road_section s
CROSS JOIN (VALUES
  ('K4640+000', 4640.000, 2::smallint, '轴载调查（WIM）', '2025-06-01', 'mdsite=wim01'),
  ('K4635+710', 4635.710, 2::smallint, '结构响应监测（应变断面）', '2025-06-01', 'mdsite=cs4635_710')
) AS v(stake_text, stake_km, lane_no, purpose, install_date, remark)
WHERE s.section_name = '滨海大通道试验段'
  AND NOT EXISTS (SELECT 1 FROM monitor_cross_section m WHERE m.stake_km = v.stake_km);

-- ---------------------------------------------------------------- 设备（sensor_install：MQTT device_code 的注册处）
INSERT INTO sensor_install (cross_section_id, sensor_type_code, sensor_model, manufacturer,
                            serial_no, install_mode, install_depth_cm, position_desc, install_date, status, remark)
SELECT m.id, v.sensor_type_code, v.sensor_model, v.manufacturer, v.serial_no,
       v.install_mode, v.install_depth_cm, v.position_desc, v.install_date::date, 'active', v.remark
FROM monitor_cross_section m
JOIN (VALUES
  (4640.000, 'WIM_QUARTZ',     'ZDG-40-SY-2', '万集/同类', 'WIM01',     '表面式', NULL::numeric, 'K4640+000 行车道 2 轮迹带', '2025-06-01', 'site_id=wim01'),
  (4635.710, 'STRAIN_ASPHALT', 'KM-100HAS',   '京都先端/同类', 'STRAIN01', '埋入式', 4.0,      'K4635+710 行车道 2 底面', '2025-06-01', 'site_id=cs4635_710')
) AS v(stake_km, sensor_type_code, sensor_model, manufacturer, serial_no, install_mode,
       install_depth_cm, position_desc, install_date, remark)
  ON v.stake_km = m.stake_km
WHERE NOT EXISTS (SELECT 1 FROM sensor_install si WHERE si.serial_no = v.serial_no);

-- ---------------------------------------------------------------- 通道（逻辑测点：质量日志与高频时序都挂在 channel 上）
INSERT INTO sensor_channel (install_id, channel_no, quantity_code, unit, sample_rate_hz,
                            acquire_mode, storage_policy, alarm_enable, remark)
SELECT si.id, v.channel_no, v.quantity_code, v.unit, v.sample_rate_hz, v.acquire_mode, v.storage_policy, true, v.remark
FROM sensor_install si
JOIN (VALUES
  ('WIM01',     1::smallint, 'axle_load', 'kg',   1.0,   'trigger',   'full', '过车事件通道'),
  ('STRAIN01',  1::smallint, 'strain',    'με',   1000.0,'continuous','full', '横向/纵向应变（P2 接 IoTDB）'),
  ('STRAIN01',  2::smallint, 'temp',      '℃',    1.0,   'continuous','full', '应变计温度补偿通道')
) AS v(serial_no, channel_no, quantity_code, unit, sample_rate_hz, acquire_mode, storage_policy, remark)
  ON v.serial_no = si.serial_no
WHERE NOT EXISTS (
  SELECT 1 FROM sensor_channel sc WHERE sc.install_id = si.id AND sc.channel_no = v.channel_no
);

COMMIT;

-- 自检：
--   SELECT serial_no, cross_section_id FROM sensor_install;
--   SELECT count(*) FROM wim_axle_record;   -- 应为 0，接入后增长
