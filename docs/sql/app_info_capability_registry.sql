-- Capability Registry 权威表（架构 §12.1）。与 Java 业务库同库 ai_boot。
-- 关联 app_info（varchar(36)）；一个 app_info 可发布多个 Capability（app_info_id 非唯一）。
-- 幂等：CREATE TABLE IF NOT EXISTS，可重复执行。
-- 说明：本表设计上由 Java 发布/删除流程写入 + 发签名变更事件；在 Java 侧接管前，
--       agent-api 暂代写入（仅 external_catalog 广场应用，从 app_info 同步）+ 消费签名事件。
--       枚举合法性由应用层/接口校验（MySQL 5.7 不依赖 CHECK）。

CREATE TABLE IF NOT EXISTS `app_info_capability_registry` (
  `id`                       VARCHAR(36)  NOT NULL COMMENT '主键',
  `app_info_id`              VARCHAR(36)  NOT NULL COMMENT '关联 app_info.id（非唯一，一对多）',
  `tenant_id`                VARCHAR(32)  DEFAULT '0' COMMENT '租户',
  `capability_code`          VARCHAR(64)  NOT NULL COMMENT '能力稳定编码（租户内唯一）',
  `capability_name`          VARCHAR(128) DEFAULT NULL COMMENT '能力名称',
  `source_system`            VARCHAR(32)  DEFAULT NULL COMMENT 'agent_workbench / external_catalog',
  `source_app_id`            VARCHAR(64)  DEFAULT NULL COMMENT '稳定来源应用 id（工作台画布 id 等）',
  `source_published_version` INT          DEFAULT 0   COMMENT '来源发布版本',
  -- 路由
  `route_description`        TEXT         COMMENT '路由描述',
  `trigger_examples`         TEXT         COMMENT '正向触发示例（JSON 数组）',
  `negative_examples`        TEXT         COMMENT '反向/不适用示例（JSON 数组）',
  `tags`                     VARCHAR(255) DEFAULT NULL COMMENT '标签',
  `capability_type`          VARCHAR(32)  DEFAULT NULL COMMENT 'subagent / skill / knowledge',
  -- 边界
  `execution_scope`          VARCHAR(32)  DEFAULT NULL COMMENT 'campus_internal / external_app',
  `provider`                 VARCHAR(64)  DEFAULT NULL,
  `data_sharing_policy`      VARCHAR(32)  DEFAULT NULL,
  `launch_mode`              VARCHAR(32)  DEFAULT NULL COMMENT 'call_subagent / redirect / iframe',
  -- 运行
  `runtime_type`             VARCHAR(32)  DEFAULT NULL COMMENT 'python_workflow / external_link',
  `endpoint`                 VARCHAR(500) DEFAULT NULL,
  `remote_app_id`            VARCHAR(128) DEFAULT NULL,
  `secret_ref`               VARCHAR(128) DEFAULT NULL COMMENT '仅存引用，明文 Secret 不进本表',
  `protocol_version`         VARCHAR(32)  DEFAULT NULL,
  `input_schema`             MEDIUMTEXT   COMMENT 'JSON Schema',
  `output_schema`            MEDIUMTEXT   COMMENT 'JSON Schema',
  -- 安全
  `risk_level`               VARCHAR(16)  DEFAULT NULL COMMENT 'low / medium / high',
  `approval_policy`          VARCHAR(32)  DEFAULT NULL,
  -- 运维
  `enabled`                  TINYINT(1)   DEFAULT 1,
  `version`                  INT          DEFAULT 1,
  `timeout_seconds`          INT          DEFAULT 30,
  `health_status`            VARCHAR(16)  DEFAULT 'unknown' COMMENT 'healthy / degraded / down / unknown',
  `owner`                    VARCHAR(64)  DEFAULT NULL,
  -- 审计
  `create_by`                VARCHAR(50)  DEFAULT NULL,
  `create_time`              DATETIME     DEFAULT CURRENT_TIMESTAMP,
  `update_by`                VARCHAR(50)  DEFAULT NULL,
  `update_time`              DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tenant_capability_code` (`tenant_id`, `capability_code`),
  KEY `idx_app_info_id` (`app_info_id`),
  KEY `idx_source` (`tenant_id`, `source_system`, `source_app_id`),
  KEY `idx_scope_enabled` (`execution_scope`, `enabled`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Capability Registry 权威表（§12.1）';
