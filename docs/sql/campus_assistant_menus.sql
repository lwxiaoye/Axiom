-- 将后台「知识库管理」升级为可展开父菜单，并新增「校园百事通配置」。
-- 幂等：重复执行不会重复插入，也不会把已迁移的父目录再次改成叶子。
-- 在业务 MySQL（ai_boot.sys_permission）执行。
--
-- 现状：现有「知识库管理」id=2026062100010000001 是 menu_type=0 的可路由目录
-- （url=/knowledge/base, component=knowledge/index），不是 menu_type=1 叶子。
-- 迁移后：
--   新父目录「知识库管理」
--     ├─ 知识库内容管理  （保留原 id/url/component/按钮权限）
--     ├─ 知识库运营统计  （/knowledge/analytics）
--     └─ 校园百事通配置  （/newapi/campus-assistant）

SET @admin_role_id = 'f6817f48af4fb3af11b9e8bf182f618b';

SET @parent_menu_id = '2026090100030000001';
SET @campus_menu_id = '2026090100030000011';
SET @campus_view_id = '2026090100030000012';
SET @campus_edit_id = '2026090100030000013';
SET @campus_publish_id = '2026090100030000014';
SET @analytics_menu_id = '2026090100030000021';

-- 优先认已改名的「知识库内容管理」；否则认带 component 的「知识库管理」（含 menu_type=0 目录页）。
SELECT id, parent_id, url, component, icon, sort_no
INTO @legacy_kb_id, @legacy_parent_id, @legacy_kb_url, @legacy_kb_component, @legacy_kb_icon, @legacy_kb_sort
FROM sys_permission
WHERE del_flag = 0
  AND (
    name = '知识库内容管理'
    OR (name = '知识库管理' AND IFNULL(component, '') <> '' AND IFNULL(component, '') NOT IN ('layouts/RouteView', 'layouts/default/index', 'LAYOUT'))
  )
ORDER BY CASE WHEN name = '知识库内容管理' THEN 0 ELSE 1 END, create_time ASC
LIMIT 1;

SET @dir_parent_id := IF(
  EXISTS (SELECT 1 FROM sys_permission WHERE id = @parent_menu_id AND del_flag = 0),
  (SELECT parent_id FROM sys_permission WHERE id = @parent_menu_id),
  IFNULL(@legacy_parent_id, '')
);

INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @parent_menu_id, @dir_parent_id, '知识库管理', '/knowledge', 'layouts/default/index', 1, '', '/knowledge/base', 0, NULL, '0', IFNULL(@legacy_kb_sort, 3.00), 0, IFNULL(@legacy_kb_icon, 'ant-design:book-outlined'), 0, 0, 0, 0, '知识库内容管理与校园百事通配置', 'admin', NOW(), NULL, NULL, 0, 0, '1', 0
WHERE @legacy_kb_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM sys_permission WHERE id = @parent_menu_id);

UPDATE sys_permission
SET name = '知识库内容管理',
    parent_id = @parent_menu_id,
    menu_type = 1,
    sort_no = 1.00
WHERE id = @legacy_kb_id
  AND @legacy_kb_id IS NOT NULL
  AND @parent_menu_id IS NOT NULL;

INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @analytics_menu_id, @parent_menu_id, '知识库运营统计', '/knowledge/analytics', 'knowledge/analytics/index', 1, 'KnowledgeOperationsAnalytics', NULL, 1, NULL, '0', 2.00, 0, 'ant-design:bar-chart-outlined', 1, 0, 0, 0, '知识库全局与单库运营统计', 'admin', NOW(), NULL, NULL, 0, 0, '1', 0
WHERE EXISTS (SELECT 1 FROM sys_permission WHERE id = @parent_menu_id)
AND NOT EXISTS (SELECT 1 FROM sys_permission WHERE id = @analytics_menu_id);

INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @campus_menu_id, @parent_menu_id, '校园百事通配置', '/newapi/campus-assistant', 'newapi/campusAssistant/index', 1, 'CampusAssistantConfigPage', NULL, 1, NULL, '0', 3.00, 0, 'ant-design:bank-outlined', 1, 0, 0, 0, '校园百事通知识库绑定、官方域名与发布', 'admin', NOW(), NULL, NULL, 0, 0, '1', 0
WHERE EXISTS (SELECT 1 FROM sys_permission WHERE id = @parent_menu_id)
AND NOT EXISTS (SELECT 1 FROM sys_permission WHERE id = @campus_menu_id);

INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @campus_view_id, @campus_menu_id, '查看校园百事通配置', NULL, NULL, 0, NULL, NULL, 2, 'campus:assistant:view', '1', 1.00, 0, NULL, 1, 0, 0, 0, NULL, 'admin', NOW(), NULL, NULL, 0, 0, '1', 0
WHERE NOT EXISTS (SELECT 1 FROM sys_permission WHERE id = @campus_view_id);

INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @campus_edit_id, @campus_menu_id, '编辑校园百事通草稿', NULL, NULL, 0, NULL, NULL, 2, 'campus:assistant:edit', '1', 2.00, 0, NULL, 1, 0, 0, 0, NULL, 'admin', NOW(), NULL, NULL, 0, 0, '1', 0
WHERE NOT EXISTS (SELECT 1 FROM sys_permission WHERE id = @campus_edit_id);

INSERT INTO `sys_permission` (`id`,`parent_id`,`name`,`url`,`component`,`is_route`,`component_name`,`redirect`,`menu_type`,`perms`,`perms_type`,`sort_no`,`always_show`,`icon`,`is_leaf`,`keep_alive`,`hidden`,`hide_tab`,`description`,`create_by`,`create_time`,`update_by`,`update_time`,`del_flag`,`rule_flag`,`status`,`internal_or_external`)
SELECT @campus_publish_id, @campus_menu_id, '发布/回滚校园百事通', NULL, NULL, 0, NULL, NULL, 2, 'campus:assistant:publish', '1', 3.00, 0, NULL, 1, 0, 0, 0, NULL, 'admin', NOW(), NULL, NULL, 0, 0, '1', 0
WHERE NOT EXISTS (SELECT 1 FROM sys_permission WHERE id = @campus_publish_id);

INSERT INTO `sys_role_permission` (`id`,`role_id`,`permission_id`,`data_rule_ids`,`operate_date`,`operate_ip`)
SELECT REPLACE(UUID(), '-', ''), @admin_role_id, p.permission_id, NULL, NOW(), NULL
FROM (
  SELECT @parent_menu_id permission_id
  UNION ALL SELECT @analytics_menu_id
  UNION ALL SELECT @campus_menu_id
  UNION ALL SELECT @campus_view_id
  UNION ALL SELECT @campus_edit_id
  UNION ALL SELECT @campus_publish_id
) p
WHERE EXISTS (SELECT 1 FROM sys_role WHERE id = @admin_role_id)
AND EXISTS (SELECT 1 FROM sys_permission WHERE id = p.permission_id)
AND NOT EXISTS (
  SELECT 1 FROM sys_role_permission rp
  WHERE rp.role_id = @admin_role_id AND rp.permission_id = p.permission_id
);
