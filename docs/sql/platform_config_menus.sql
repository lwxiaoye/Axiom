-- 联网搜索 / OCR 管理配置页的菜单注册（jeecg sys_permission，业务 MySQL 库执行）
-- 幂等：重复执行不会重复插入。执行前先设置 @parent_menu_id。
--
-- 找到父级菜单 id（联网搜索/OCR 配置想挂在哪个一级/二级菜单下，通常与「Embedding 检索配置」同级）：
--   SELECT id, name, url, component FROM sys_permission WHERE component LIKE 'newapi/%';
-- 取其 parent_id 作为下面的 @parent_menu_id（与 embedding 同级），或直接用某个菜单分组 id。

SET @parent_menu_id       = 'REPLACE_WITH_PARENT_MENU_ID';   -- 必填：父菜单 id
SET @admin_role_id        = 'f6817f48af4fb3af11b9e8bf182f618b'; -- 管理员角色 id（按需修改）

SET @websearch_menu_id    = '2026070600010000000000000001';
SET @websearch_view_id    = '2026070600010000000000000002';
SET @websearch_save_id    = '2026070600010000000000000003';
SET @ocr_menu_id          = '2026070600010000000000000011';
SET @ocr_view_id          = '2026070600010000000000000012';
SET @ocr_save_id          = '2026070600010000000000000013';

-- ---- 联网搜索配置菜单 ----
INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @websearch_menu_id,@parent_menu_id,'联网搜索配置','/newapi/websearch','newapi/websearch/index',1,'WebSearchConfigPage',NULL,1,NULL,'1',80.00,0,'ant-design:global-outlined',0,0,0,0,'联网搜索三段式管线配置','admin',NOW(),NULL,NULL,0,0,'1',0
WHERE NOT EXISTS (SELECT 1 FROM `sys_permission` WHERE `id`=@websearch_menu_id);

INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @websearch_view_id,@websearch_menu_id,'查看联网搜索配置',NULL,NULL,0,NULL,NULL,2,'platform:websearch:view','1',1.00,0,NULL,1,0,0,0,NULL,'admin',NOW(),NULL,NULL,0,0,'1',0
WHERE NOT EXISTS (SELECT 1 FROM `sys_permission` WHERE `id`=@websearch_view_id);

INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @websearch_save_id,@websearch_menu_id,'保存联网搜索配置',NULL,NULL,0,NULL,NULL,2,'platform:websearch:save','1',2.00,0,NULL,1,0,0,0,NULL,'admin',NOW(),NULL,NULL,0,0,'1',0
WHERE NOT EXISTS (SELECT 1 FROM `sys_permission` WHERE `id`=@websearch_save_id);

-- ---- OCR 配置菜单 ----
INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @ocr_menu_id,@parent_menu_id,'OCR 识别配置','/newapi/ocr','newapi/ocr/index',1,'OcrConfigPage',NULL,1,NULL,'1',81.00,0,'ant-design:file-text-outlined',0,0,0,0,'文档 OCR 策略配置','admin',NOW(),NULL,NULL,0,0,'1',0
WHERE NOT EXISTS (SELECT 1 FROM `sys_permission` WHERE `id`=@ocr_menu_id);

INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @ocr_view_id,@ocr_menu_id,'查看 OCR 配置',NULL,NULL,0,NULL,NULL,2,'platform:ocr:view','1',1.00,0,NULL,1,0,0,0,NULL,'admin',NOW(),NULL,NULL,0,0,'1',0
WHERE NOT EXISTS (SELECT 1 FROM `sys_permission` WHERE `id`=@ocr_view_id);

INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @ocr_save_id,@ocr_menu_id,'保存 OCR 配置',NULL,NULL,0,NULL,NULL,2,'platform:ocr:save','1',2.00,0,NULL,1,0,0,0,NULL,'admin',NOW(),NULL,NULL,0,0,'1',0
WHERE NOT EXISTS (SELECT 1 FROM `sys_permission` WHERE `id`=@ocr_save_id);

-- ---- 授权给管理员角色 ----
INSERT INTO `sys_role_permission` (`id`,`role_id`,`permission_id`,`data_rule_ids`,`operate_date`,`operate_ip`)
SELECT REPLACE(UUID(),'-',''),@admin_role_id,p.permission_id,NULL,NOW(),NULL
FROM (
  SELECT @websearch_menu_id permission_id
  UNION ALL SELECT @websearch_view_id
  UNION ALL SELECT @websearch_save_id
  UNION ALL SELECT @ocr_menu_id
  UNION ALL SELECT @ocr_view_id
  UNION ALL SELECT @ocr_save_id
) p
WHERE EXISTS (SELECT 1 FROM `sys_role` WHERE `id`=@admin_role_id)
AND NOT EXISTS (
  SELECT 1 FROM `sys_role_permission` rp
  WHERE rp.`role_id`=@admin_role_id AND rp.`permission_id`=p.permission_id
);
