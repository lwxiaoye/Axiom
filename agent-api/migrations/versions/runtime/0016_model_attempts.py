"""Logical model calls, physical Provider attempts, and strict Run sequences.

Revision ID: runtime_0016_model_attempts
Revises: runtime_0015_model_cache_audit
"""

from alembic import op


revision = "runtime_0016_model_attempts"
down_revision = "runtime_0015_model_cache_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS agent_model_run_counters (
            run_id VARCHAR(64) PRIMARY KEY,
            last_sequence BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT NOW()
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS agent_model_logical_calls (
            id VARCHAR(64) PRIMARY KEY,
            run_id VARCHAR(64) NOT NULL,
            root_run_id VARCHAR(64) NOT NULL,
            thread_id VARCHAR(64),
            parent_logical_call_id VARCHAR(64),
            parent_tool_call_id VARCHAR(64),
            model VARCHAR(255) NOT NULL DEFAULT '',
            transport VARCHAR(32) NOT NULL DEFAULT '',
            endpoint_family VARCHAR(64) NOT NULL DEFAULT '',
            provider_key_fingerprint VARCHAR(64),
            purpose VARCHAR(64) NOT NULL DEFAULT 'main_loop',
            purpose_detail TEXT,
            fallback_reason VARCHAR(128),
            scope_key VARCHAR(255) NOT NULL DEFAULT '',
            status VARCHAR(32) NOT NULL DEFAULT 'started',
            context_epoch INTEGER NOT NULL DEFAULT 0,
            epoch_reason VARCHAR(64) NOT NULL DEFAULT 'initial',
            base_prompt_hash VARCHAR(64) NOT NULL DEFAULT '',
            tool_schema_hash VARCHAR(64) NOT NULL DEFAULT '',
            state_snapshot_hash VARCHAR(64) NOT NULL DEFAULT '',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            first_run_sequence BIGINT,
            last_run_sequence BIGINT,
            selected_attempt_id VARCHAR(64),
            created_at TIMESTAMP DEFAULT NOW(),
            completed_at TIMESTAMP,
            CONSTRAINT ck_agent_model_logical_call_purpose CHECK (
                purpose IN (
                    'main_loop','plain_answer','public_preamble','research_commentary',
                    'compaction_live','compaction_preflight','compaction_background',
                    'router','title','memory_extract','memory_summary','paid_search',
                    'browser_digest','subagent_model','workflow_node','acceptance',
                    'parent_summary','tool_internal'
                )
            )
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS agent_model_attempt_audits (
            id VARCHAR(64) PRIMARY KEY,
            logical_call_id VARCHAR(64) NOT NULL,
            retry_of_attempt_id VARCHAR(64),
            prefix_predecessor_attempt_id VARCHAR(64),
            run_id VARCHAR(64) NOT NULL,
            root_run_id VARCHAR(64) NOT NULL,
            thread_id VARCHAR(64),
            request_id VARCHAR(64) NOT NULL,
            run_request_sequence BIGINT NOT NULL,
            legacy_request_sequence INTEGER,
            attempt_index INTEGER NOT NULL,
            attempt_kind VARCHAR(64) NOT NULL DEFAULT 'initial',
            execution_segment VARCHAR(64) NOT NULL DEFAULT '',
            model VARCHAR(255) NOT NULL DEFAULT '',
            transport VARCHAR(32) NOT NULL DEFAULT '',
            endpoint_family VARCHAR(64) NOT NULL DEFAULT '',
            provider_key_fingerprint VARCHAR(64),
            purpose VARCHAR(64) NOT NULL DEFAULT 'main_loop',
            purpose_detail TEXT,
            scope_key VARCHAR(255) NOT NULL DEFAULT '',
            context_epoch INTEGER NOT NULL DEFAULT 0,
            epoch_reason VARCHAR(64) NOT NULL DEFAULT 'initial',
            base_prompt_hash VARCHAR(64) NOT NULL DEFAULT '',
            tool_schema_hash VARCHAR(64) NOT NULL DEFAULT '',
            state_snapshot_hash VARCHAR(64) NOT NULL DEFAULT '',
            source_manifest JSONB NOT NULL DEFAULT '[]'::jsonb,
            logical_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            wire_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            logical_payload_hash VARCHAR(64) NOT NULL DEFAULT '',
            wire_payload_hash VARCHAR(64) NOT NULL DEFAULT '',
            shadow_hash VARCHAR(64) NOT NULL DEFAULT '',
            match_status VARCHAR(16) NOT NULL DEFAULT 'match',
            mismatch_detail JSONB,
            prefix_diagnostics JSONB NOT NULL DEFAULT '{}'::jsonb,
            logical_item_count INTEGER NOT NULL DEFAULT 0,
            wire_item_count INTEGER NOT NULL DEFAULT 0,
            logical_canonical_chars BIGINT NOT NULL DEFAULT 0,
            wire_canonical_chars BIGINT NOT NULL DEFAULT 0,
            response_id VARCHAR(128),
            previous_response_id VARCHAR(128),
            provider_event_seen BOOLEAN NOT NULL DEFAULT FALSE,
            partial_text_seen BOOLEAN NOT NULL DEFAULT FALSE,
            terminal_seen BOOLEAN NOT NULL DEFAULT FALSE,
            trusted_usage BOOLEAN NOT NULL DEFAULT FALSE,
            terminal_status VARCHAR(32) NOT NULL DEFAULT 'started',
            http_status INTEGER,
            error_code VARCHAR(128),
            error_detail TEXT,
            input_tokens BIGINT,
            output_tokens BIGINT,
            reasoning_tokens BIGINT,
            cache_read_tokens BIGINT,
            cache_miss_tokens BIGINT,
            cache_write_tokens BIGINT,
            cache_miss_source VARCHAR(16) NOT NULL DEFAULT 'unknown',
            usage_schema VARCHAR(32) NOT NULL DEFAULT 'unknown',
            provider_amount_raw TEXT,
            provider_amount_unit VARCHAR(32),
            unknown_provider_charge BOOLEAN NOT NULL DEFAULT FALSE,
            billed_but_not_committed BOOLEAN NOT NULL DEFAULT FALSE,
            committed BOOLEAN,
            latency_ms BIGINT,
            legacy_backfilled BOOLEAN NOT NULL DEFAULT FALSE,
            started_at TIMESTAMP DEFAULT NOW(),
            created_at TIMESTAMP DEFAULT NOW(),
            completed_at TIMESTAMP,
            CONSTRAINT fk_agent_model_attempt_logical_call
                FOREIGN KEY (logical_call_id)
                REFERENCES agent_model_logical_calls (id)
                ON DELETE CASCADE,
            CONSTRAINT ck_agent_model_attempt_purpose CHECK (
                purpose IN (
                    'main_loop','plain_answer','public_preamble','research_commentary',
                    'compaction_live','compaction_preflight','compaction_background',
                    'router','title','memory_extract','memory_summary','paid_search',
                    'browser_digest','subagent_model','workflow_node','acceptance',
                    'parent_summary','tool_internal'
                )
            )
        )"""
    )

    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_agent_model_logical_calls_run_id "
        "ON agent_model_logical_calls (run_id)",
        "CREATE INDEX IF NOT EXISTS ix_agent_model_logical_calls_root_run_id "
        "ON agent_model_logical_calls (root_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_agent_model_logical_calls_thread_id "
        "ON agent_model_logical_calls (thread_id)",
        "CREATE INDEX IF NOT EXISTS ix_agent_model_logical_calls_purpose "
        "ON agent_model_logical_calls (purpose)",
        "CREATE INDEX IF NOT EXISTS ix_agent_model_logical_calls_key_fingerprint "
        "ON agent_model_logical_calls (provider_key_fingerprint)",
        "CREATE INDEX IF NOT EXISTS ix_agent_model_attempt_audits_run_id "
        "ON agent_model_attempt_audits (run_id)",
        "CREATE INDEX IF NOT EXISTS ix_agent_model_attempt_audits_root_run_id "
        "ON agent_model_attempt_audits (root_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_agent_model_attempt_audits_thread_id "
        "ON agent_model_attempt_audits (thread_id)",
        "CREATE INDEX IF NOT EXISTS ix_agent_model_attempt_audits_logical_call_id "
        "ON agent_model_attempt_audits (logical_call_id)",
        "CREATE INDEX IF NOT EXISTS ix_agent_model_attempt_audits_purpose "
        "ON agent_model_attempt_audits (purpose)",
        "CREATE INDEX IF NOT EXISTS ix_agent_model_attempt_audits_key_fingerprint "
        "ON agent_model_attempt_audits (provider_key_fingerprint)",
        "CREATE INDEX IF NOT EXISTS ix_agent_model_attempt_audits_scope_predecessor "
        "ON agent_model_attempt_audits "
        "(run_id, scope_key, model, transport, run_request_sequence)",
        "CREATE INDEX IF NOT EXISTS ix_agent_model_attempt_audits_terminal_status "
        "ON agent_model_attempt_audits (terminal_status)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_model_attempt_run_sequence "
        "ON agent_model_attempt_audits (run_id, run_request_sequence)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_model_attempt_call_index "
        "ON agent_model_attempt_audits (logical_call_id, attempt_index)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_model_attempt_request "
        "ON agent_model_attempt_audits (run_id, request_id)",
    ):
        op.execute(statement)

    # Existing request_sequence was process-local and could restart at one after HITL resume.
    # Backfill a collision-free sequence from durable creation order instead of copying it.
    op.get_bind().exec_driver_sql(
        """WITH legacy AS (
            SELECT
                ima.*,
                amca.id AS cache_id,
                amca.transport AS cache_transport,
                amca.context_epoch AS cache_context_epoch,
                amca.epoch_reason AS cache_epoch_reason,
                amca.base_prompt_hash AS cache_base_prompt_hash,
                amca.tool_schema_hash AS cache_tool_schema_hash,
                amca.state_snapshot_hash AS cache_state_snapshot_hash,
                amca.created_at AS cache_created_at,
                COALESCE(ar.root_run_id, ima.run_id) AS resolved_root_run_id,
                ROW_NUMBER() OVER (
                    PARTITION BY ima.run_id
                    ORDER BY ima.created_at, ima.id
                ) AS strict_sequence,
                CASE
                    WHEN COALESCE(amca.transport, '') <> '' THEN amca.transport
                    WHEN ima.visible_payload::jsonb ? 'input' THEN 'responses'
                    ELSE 'chat_completions'
                END AS resolved_transport
            FROM agent_model_input_audits ima
            LEFT JOIN agent_model_cache_audits amca
              ON amca.run_id = ima.run_id AND amca.request_id = ima.request_id
            LEFT JOIN agent_runs ar ON ar.id = ima.run_id
        ), existing_sequences AS (
            SELECT run_id, COALESCE(MAX(run_request_sequence), 0) AS existing_max_sequence
            FROM agent_model_attempt_audits
            GROUP BY run_id
        ), sequenced AS (
            SELECT
                legacy.*,
                COALESCE(existing_sequences.existing_max_sequence, 0) AS existing_max_sequence
            FROM legacy
            LEFT JOIN existing_sequences ON existing_sequences.run_id = legacy.run_id
        )
        INSERT INTO agent_model_logical_calls (
            id, run_id, root_run_id, thread_id, model, transport, endpoint_family, purpose,
            purpose_detail, scope_key, status, context_epoch, epoch_reason,
            base_prompt_hash, tool_schema_hash, state_snapshot_hash, attempt_count,
            first_run_sequence, last_run_sequence, selected_attempt_id,
            created_at, completed_at
        )
        SELECT
            'lg_' || md5(run_id || ':' || request_id),
            run_id,
            resolved_root_run_id,
            thread_id,
            model,
            resolved_transport,
            resolved_transport,
            'main_loop',
            'legacy_backfill',
            LEFT('main_loop|' || model || '|' || resolved_transport || '|' ||
                 COALESCE(cache_context_epoch, 0)::text, 255),
            CASE WHEN cache_id IS NULL THEN 'unknown' ELSE 'completed' END,
            COALESCE(cache_context_epoch, 0),
            COALESCE(cache_epoch_reason, 'initial'),
            COALESCE(cache_base_prompt_hash, ''),
            COALESCE(cache_tool_schema_hash, ''),
            COALESCE(cache_state_snapshot_hash, ''),
            1,
            existing_max_sequence + strict_sequence,
            existing_max_sequence + strict_sequence,
            'at_' || md5(run_id || ':' || request_id),
            created_at,
            cache_created_at
        FROM sequenced
        WHERE NOT EXISTS (
            SELECT 1
            FROM agent_model_attempt_audits existing
            WHERE existing.id = 'at_' || md5(sequenced.run_id || ':' || sequenced.request_id)
               OR (
                   existing.run_id = sequenced.run_id
                   AND existing.run_request_sequence = sequenced.existing_max_sequence + sequenced.strict_sequence
               )
               OR (
                   existing.run_id = sequenced.run_id
                   AND existing.request_id = sequenced.request_id
               )
        )
        ON CONFLICT (id) DO NOTHING"""
    )
    op.get_bind().exec_driver_sql(
        """WITH legacy AS (
            SELECT
                ima.*,
                amca.id AS cache_id,
                amca.transport AS cache_transport,
                amca.context_epoch AS cache_context_epoch,
                amca.epoch_reason AS cache_epoch_reason,
                amca.base_prompt_hash AS cache_base_prompt_hash,
                amca.tool_schema_hash AS cache_tool_schema_hash,
                amca.state_snapshot_hash AS cache_state_snapshot_hash,
                amca.prefix_diagnostics AS cache_prefix_diagnostics,
                amca.input_tokens AS cache_input_tokens,
                amca.output_tokens AS cache_output_tokens,
                amca.cache_read_tokens,
                amca.cache_miss_tokens,
                amca.cache_write_tokens,
                amca.cache_miss_source,
                amca.usage_schema,
                amca.created_at AS cache_created_at,
                COALESCE(ar.root_run_id, ima.run_id) AS resolved_root_run_id,
                ROW_NUMBER() OVER (
                    PARTITION BY ima.run_id
                    ORDER BY ima.created_at, ima.id
                ) AS strict_sequence,
                CASE
                    WHEN COALESCE(amca.transport, '') <> '' THEN amca.transport
                    WHEN ima.visible_payload::jsonb ? 'input' THEN 'responses'
                    ELSE 'chat_completions'
                END AS resolved_transport
            FROM agent_model_input_audits ima
            LEFT JOIN agent_model_cache_audits amca
              ON amca.run_id = ima.run_id AND amca.request_id = ima.request_id
            LEFT JOIN agent_runs ar ON ar.id = ima.run_id
        ), existing_sequences AS (
            SELECT run_id, COALESCE(MAX(run_request_sequence), 0) AS existing_max_sequence
            FROM agent_model_attempt_audits
            GROUP BY run_id
        ), prepared AS (
            SELECT
                legacy.*,
                COALESCE(existing_sequences.existing_max_sequence, 0) AS existing_max_sequence,
                'at_' || md5(legacy.run_id || ':' || legacy.request_id) AS attempt_id,
                LEFT('main_loop|' || legacy.model || '|' || legacy.resolved_transport || '|' ||
                     COALESCE(legacy.cache_context_epoch, 0)::text, 255) AS resolved_scope_key
            FROM legacy
            LEFT JOIN existing_sequences ON existing_sequences.run_id = legacy.run_id
        ), lined AS (
            SELECT
                prepared.*,
                LAG(attempt_id) OVER (
                    PARTITION BY run_id, resolved_scope_key
                    ORDER BY strict_sequence
                ) AS previous_attempt_id
            FROM prepared
        )
        INSERT INTO agent_model_attempt_audits (
            id, logical_call_id, prefix_predecessor_attempt_id, run_id, root_run_id,
            thread_id, request_id, run_request_sequence, legacy_request_sequence, attempt_index,
            attempt_kind, execution_segment, model, transport, endpoint_family,
            purpose, purpose_detail, scope_key,
            context_epoch, epoch_reason, base_prompt_hash, tool_schema_hash,
            state_snapshot_hash, source_manifest, logical_payload, wire_payload,
            logical_payload_hash, wire_payload_hash, shadow_hash, match_status,
            mismatch_detail, prefix_diagnostics, logical_item_count, wire_item_count,
            logical_canonical_chars, wire_canonical_chars, terminal_seen, trusted_usage,
            terminal_status, input_tokens, output_tokens, cache_read_tokens,
            cache_miss_tokens, cache_write_tokens, cache_miss_source, usage_schema,
            unknown_provider_charge, billed_but_not_committed, committed,
            legacy_backfilled, started_at, created_at, completed_at
        )
        SELECT
            attempt_id,
            'lg_' || md5(run_id || ':' || request_id),
            previous_attempt_id,
            run_id,
            resolved_root_run_id,
            thread_id,
            request_id,
            existing_max_sequence + strict_sequence,
            request_sequence,
            1,
            'legacy_backfill',
            'legacy',
            model,
            resolved_transport,
            resolved_transport,
            'main_loop',
            'legacy_backfill',
            resolved_scope_key,
            COALESCE(cache_context_epoch, 0),
            COALESCE(cache_epoch_reason, 'initial'),
            COALESCE(cache_base_prompt_hash, ''),
            COALESCE(cache_tool_schema_hash, ''),
            COALESCE(cache_state_snapshot_hash, ''),
            source_manifest::jsonb,
            jsonb_build_object(
                'schema', 'legacy_manifest_v1',
                'items', source_manifest::jsonb,
                'tool_schema_hash', COALESCE(cache_tool_schema_hash, '')
            ),
            jsonb_build_object(
                'schema', 'legacy_manifest_v1',
                'items', source_manifest::jsonb,
                'tool_schema_hash', COALESCE(cache_tool_schema_hash, '')
            ),
            payload_hash,
            payload_hash,
            shadow_hash,
            match_status,
            mismatch_detail::jsonb,
            COALESCE(cache_prefix_diagnostics::jsonb, '{"available":false}'::jsonb),
            CASE WHEN COALESCE((cache_prefix_diagnostics::jsonb)->>'current_item_count', '')
                      ~ '^[0-9]+$'
                 THEN ((cache_prefix_diagnostics::jsonb)->>'current_item_count')::integer
                 ELSE 0 END,
            CASE WHEN COALESCE((cache_prefix_diagnostics::jsonb)->>'current_item_count', '')
                      ~ '^[0-9]+$'
                 THEN ((cache_prefix_diagnostics::jsonb)->>'current_item_count')::integer
                 ELSE 0 END,
            CASE WHEN COALESCE((cache_prefix_diagnostics::jsonb)->>'current_canonical_chars', '')
                      ~ '^[0-9]+$'
                 THEN ((cache_prefix_diagnostics::jsonb)->>'current_canonical_chars')::bigint
                 ELSE 0 END,
            CASE WHEN COALESCE((cache_prefix_diagnostics::jsonb)->>'current_canonical_chars', '')
                      ~ '^[0-9]+$'
                 THEN ((cache_prefix_diagnostics::jsonb)->>'current_canonical_chars')::bigint
                 ELSE 0 END,
            cache_id IS NOT NULL,
            cache_id IS NOT NULL,
            CASE WHEN cache_id IS NULL THEN 'unknown' ELSE 'completed' END,
            cache_input_tokens,
            cache_output_tokens,
            cache_read_tokens,
            cache_miss_tokens,
            cache_write_tokens,
            COALESCE(cache_miss_source, 'unknown'),
            COALESCE(usage_schema, 'unknown'),
            cache_id IS NULL,
            FALSE,
            cache_id IS NOT NULL,
            TRUE,
            created_at,
            created_at,
            cache_created_at
        FROM lined
        WHERE NOT EXISTS (
            SELECT 1
            FROM agent_model_attempt_audits existing
            WHERE existing.id = lined.attempt_id
               OR (
                   existing.run_id = lined.run_id
                   AND existing.run_request_sequence = lined.existing_max_sequence + lined.strict_sequence
               )
               OR (
                   existing.run_id = lined.run_id
                   AND existing.request_id = lined.request_id
               )
               OR (
                   existing.logical_call_id = 'lg_' || md5(lined.run_id || ':' || lined.request_id)
                   AND existing.attempt_index = 1
               )
        )
        ON CONFLICT (id) DO NOTHING"""
    )
    op.execute(
        """INSERT INTO agent_model_run_counters (run_id, last_sequence, updated_at)
        SELECT run_id, MAX(run_request_sequence), NOW()
        FROM agent_model_attempt_audits
        GROUP BY run_id
        ON CONFLICT (run_id) DO UPDATE
        SET last_sequence = GREATEST(
                agent_model_run_counters.last_sequence,
                EXCLUDED.last_sequence
            ),
            updated_at = NOW()"""
    )


def downgrade() -> None:
    # Provider usage and lineage are operational evidence and are not dropped automatically.
    pass
