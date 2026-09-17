-- 智能体后台管理台 + 发布审核台 的菜单注册（jeecg sys_permission，业务 MySQL 库执行）。
-- 幂等：重复执行不会重复插入。执行前先设置 @parent_menu_id 与 @admin_role_id。
--
-- 找父级菜单 id（想挂在哪个 AI 管理分组下，通常与联网搜索/OCR 同级）：
--   SELECT id, name, url, component FROM sys_permission WHERE component LIKE 'newapi/%' OR component LIKE 'workflow/%';
-- 取其 parent_id 作为 @parent_menu_id。
--
-- 权限说明：后端 /agent-api/workflow/admin/* 与 /review/* 的门禁按 agent-api 的
--   AGENT_ADMIN_ROLE_IDS / AGENT_REVIEWER_ROLE_IDS（role_id 白名单）+ 内置 admin。
--   前端菜单可见性由此处 sys_permission 授权控制；两者需对齐同一批角色。

SET @parent_menu_id  = 'REPLACE_WITH_PARENT_MENU_ID';        -- 必填：父菜单 id
SET @admin_role_id   = 'f6817f48af4fb3af11b9e8bf182f618b';   -- 平台管理员角色 id（按需修改）
SET @reviewer_role_id = 'f6817f48af4fb3af11b9e8bf182f618b';  -- 审核员角色 id（可与管理员相同）

SET @manage_menu_id  = '2026070700020000000000000001';
SET @manage_view_id  = '2026070700020000000000000002';
SET @manage_op_id    = '2026070700020000000000000003';
SET @skin_menu_id    = '2026070700020000000000000021';
SET @skin_view_id    = '2026070700020000000000000022';
SET @skin_op_id      = '2026070700020000000000000023';
SET @review_menu_id  = '2026070700020000000000000011';
SET @review_view_id  = '2026070700020000000000000012';
SET @review_op_id    = '2026070700020000000000000013';

-- ---- 智能体应用管理（跨用户，平台管理员） ----
INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @manage_menu_id,@parent_menu_id,'智能体应用管理','/workflow/manage','workflow/manage/index',1,'AgentAppManagePage',NULL,1,NULL,'1',90.00,0,'ant-design:appstore-outlined',0,0,0,0,'跨用户管理全部工作台智能体应用 + 版本回滚','admin',NOW(),NULL,NULL,0,0,'1',0
WHERE NOT EXISTS (SELECT 1 FROM `sys_permission` WHERE `id`=@manage_menu_id);

INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @manage_view_id,@manage_menu_id,'查看智能体应用',NULL,NULL,0,NULL,NULL,2,'agent:admin:view','1',1.00,0,NULL,1,0,0,0,NULL,'admin',NOW(),NULL,NULL,0,0,'1',0
WHERE NOT EXISTS (SELECT 1 FROM `sys_permission` WHERE `id`=@manage_view_id);

INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @manage_op_id,@manage_menu_id,'下架/删除/回滚',NULL,NULL,0,NULL,NULL,2,'agent:admin:manage','1',2.00,0,NULL,1,0,0,0,NULL,'admin',NOW(),NULL,NULL,0,0,'1',0
WHERE NOT EXISTS (SELECT 1 FROM `sys_permission` WHERE `id`=@manage_op_id);

-- ---- 子智能体皮肤管理（全局皮肤库，不按租户拆分） ----
INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @skin_menu_id,@parent_menu_id,'子智能体皮肤管理','/workflow/skins','workflow/skins/index',1,'SubAgentSkinManagePage',NULL,1,NULL,'1',90.50,0,'ant-design:skin-outlined',0,0,0,0,'全局子智能体皮肤包导入、查看、编辑、删除、导出和三端预览','admin',NOW(),NULL,NULL,0,0,'1',0
WHERE NOT EXISTS (SELECT 1 FROM `sys_permission` WHERE `id`=@skin_menu_id);

INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @skin_view_id,@skin_menu_id,'查看子智能体皮肤',NULL,NULL,0,NULL,NULL,2,'agent:skin:view','1',1.00,0,NULL,1,0,0,0,NULL,'admin',NOW(),NULL,NULL,0,0,'1',0
WHERE NOT EXISTS (SELECT 1 FROM `sys_permission` WHERE `id`=@skin_view_id);

INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @skin_op_id,@skin_menu_id,'管理子智能体皮肤包',NULL,NULL,0,NULL,NULL,2,'agent:skin:manage','1',2.00,0,NULL,1,0,0,0,NULL,'admin',NOW(),NULL,NULL,0,0,'1',0
WHERE NOT EXISTS (SELECT 1 FROM `sys_permission` WHERE `id`=@skin_op_id);

-- ---- 发布审核台（审核员） ----
INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @review_menu_id,@parent_menu_id,'发布审核台','/workflow/review','workflow/review/index',1,'AgentReviewPage',NULL,1,NULL,'1',91.00,0,'ant-design:audit-outlined',0,0,0,0,'审核智能体/工作流发布申请（通过/驳回）','admin',NOW(),NULL,NULL,0,0,'1',0
WHERE NOT EXISTS (SELECT 1 FROM `sys_permission` WHERE `id`=@review_menu_id);

INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @review_view_id,@review_menu_id,'查看待审',NULL,NULL,0,NULL,NULL,2,'agent:review:view','1',1.00,0,NULL,1,0,0,0,NULL,'admin',NOW(),NULL,NULL,0,0,'1',0
WHERE NOT EXISTS (SELECT 1 FROM `sys_permission` WHERE `id`=@review_view_id);

INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @review_op_id,@review_menu_id,'通过/驳回',NULL,NULL,0,NULL,NULL,2,'agent:review:action','1',2.00,0,NULL,1,0,0,0,NULL,'admin',NOW(),NULL,NULL,0,0,'1',0
WHERE NOT EXISTS (SELECT 1 FROM `sys_permission` WHERE `id`=@review_op_id);

-- ---- 授权给角色 ----
-- 管理台 → 管理员角色
INSERT INTO `sys_role_permission` (`id`,`role_id`,`permission_id`,`data_rule_ids`,`operate_date`,`operate_ip`)
SELECT REPLACE(UUID(),'-',''),@admin_role_id,p.permission_id,NULL,NOW(),NULL
FROM (
  SELECT @manage_menu_id permission_id
  UNION ALL SELECT @manage_view_id
  UNION ALL SELECT @manage_op_id
  UNION ALL SELECT @skin_menu_id
  UNION ALL SELECT @skin_view_id
  UNION ALL SELECT @skin_op_id
) p
WHERE EXISTS (SELECT 1 FROM `sys_role` WHERE `id`=@admin_role_id)
AND NOT EXISTS (SELECT 1 FROM `sys_role_permission` rp WHERE rp.`role_id`=@admin_role_id AND rp.`permission_id`=p.permission_id);

-- 审核台 → 审核员角色
INSERT INTO `sys_role_permission` (`id`,`role_id`,`permission_id`,`data_rule_ids`,`operate_date`,`operate_ip`)
SELECT REPLACE(UUID(),'-',''),@reviewer_role_id,p.permission_id,NULL,NOW(),NULL
FROM (
  SELECT @review_menu_id permission_id
  UNION ALL SELECT @review_view_id
  UNION ALL SELECT @review_op_id
) p
WHERE EXISTS (SELECT 1 FROM `sys_role` WHERE `id`=@reviewer_role_id)
AND NOT EXISTS (SELECT 1 FROM `sys_role_permission` rp WHERE rp.`role_id`=@reviewer_role_id AND rp.`permission_id`=p.permission_id);
