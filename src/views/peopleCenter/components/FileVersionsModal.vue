<template>
  <!-- 对话内文件卡的「版本历史」（P0 交付清单）：与「我的文件」页共用同一套版本接口，
       每个版本可下载；历史版本可恢复为当前版（生成新版本，不删历史）。 -->
  <a-modal
    :open="Boolean(file)"
    :title="file ? `版本历史 · ${file.filename}` : '版本历史'"
    :footer="null"
    :width="620"
    wrap-class-name="chat-versions-modal"
    @cancel="emit('close')"
  >
    <div v-if="loading" class="cv-state">加载版本历史…</div>
    <div v-else-if="error" class="cv-state cv-error">{{ error }}</div>
    <div v-else-if="!versions.length" class="cv-state">
      暂无版本记录——历史文件在下一次修改时会自动补录版本。
    </div>
    <ul v-else class="cv-versions">
      <li v-for="v in versions" :key="v.id" class="cv-row">
        <div class="cv-head">
          <strong class="cv-no">v{{ v.versionNo }}</strong>
          <span v-if="isCurrent(v)" class="cv-tag cv-current">当前版本</span>
          <span v-else-if="v.status === 'draft'" class="cv-tag cv-draft">草稿 · 审查未通过</span>
          <span class="cv-meta">{{ sourceLabel(v) }} · {{ formatFileSize(v.size) }} · {{ formatTime(v.createdAt) }}</span>
          <span class="cv-ops">
            <button type="button" class="cv-op" @click="onDownload(v)">下载</button>
            <a-popconfirm
              v-if="!isCurrent(v)"
              title="恢复该版本？将以此内容生成一个新版本作为当前版，后续历史全部保留。"
              ok-text="恢复"
              cancel-text="取消"
              @confirm="onRestore(v)"
            >
              <button type="button" class="cv-op" :disabled="busy">恢复为当前版</button>
            </a-popconfirm>
          </span>
        </div>
        <div v-if="v.changeSummary" class="cv-summary">{{ v.changeSummary }}</div>
      </li>
    </ul>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import type { GeneratedFile } from '../agentApi';
import {
  downloadFileVersion,
  listFileVersions,
  restoreFileVersion,
  type UserFileVersion,
} from '../myfiles.api';
import { formatFileSize } from '../composables/executionTimeline';
import {
  isAgentRunEmbedded,
  requestEmbeddedAgentRunDownloadVersion,
} from '../../agent/run/agentRunBack';

const props = defineProps<{
  /** 要查看版本的文件（null=关闭）。 */
  file: GeneratedFile | null;
}>();

const emit = defineEmits<{
  (e: 'close'): void;
  /** 恢复版本后通知父级（文件内容已变化，产物预览等可按需刷新） */
  (e: 'restored'): void;
}>();

const loading = ref(false);
const busy = ref(false);
const error = ref('');
const versions = ref<UserFileVersion[]>([]);

const currentVersionNo = computed(
  () => versions.value.filter((v) => v.status === 'active').reduce((m, v) => Math.max(m, v.versionNo), 0),
);

function isCurrent(v: UserFileVersion): boolean {
  return v.status === 'active' && v.versionNo === currentVersionNo.value;
}

function sourceLabel(v: UserFileVersion): string {
  const src = { uploaded: '上传', generated: 'AI 生成', restored: '恢复' }[v.source] || v.source;
  return v.createdBy === 'agent' ? `${src}（助手）` : src;
}

function formatTime(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

async function load(fileId: string) {
  loading.value = true;
  error.value = '';
  versions.value = [];
  try {
    const data = await listFileVersions(fileId);
    versions.value = data.versions || [];
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载版本历史失败';
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.file?.id,
  (id) => {
    if (id) void load(id);
  },
  { immediate: true },
);

async function onDownload(v: UserFileVersion) {
  if (!props.file) return;
  try {
    if (isAgentRunEmbedded()) {
      requestEmbeddedAgentRunDownloadVersion({
        fileId: props.file.id,
        id: v.id,
        versionNo: v.versionNo,
        filename: v.filename,
      });
      return;
    }
    await downloadFileVersion(props.file.id, v);
  } catch (e) {
    message.error(e instanceof Error ? e.message : '下载失败');
  }
}

async function onRestore(v: UserFileVersion) {
  if (!props.file || busy.value) return;
  busy.value = true;
  try {
    await restoreFileVersion(props.file.id, v.id);
    message.success(`已恢复 v${v.versionNo} 为当前版本（生成新版本，历史保留）`);
    await load(props.file.id);
    emit('restored');
  } catch (e) {
    message.error(e instanceof Error ? e.message : '恢复失败');
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
/* 全局 wireframe 主题把 .ant-modal-body 的 padding 归零：本弹窗自带内边距，避免内容贴边 */
:global(.chat-versions-modal .ant-modal-body) {
  padding: 16px 20px 20px;
}

.cv-state {
  padding: 28px 0;
  color: #8a8f99;
  font-size: 13px;
  text-align: center;
}

.cv-error {
  color: #c45252;
}

.cv-versions {
  margin: 0;
  padding: 0;
  list-style: none;
  max-height: 52vh;
  overflow-y: auto;
}

.cv-row {
  padding: 10px 2px;
  border-bottom: 1px solid #f0f1f4;
}

.cv-row:last-child {
  border-bottom: 0;
}

.cv-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.cv-no {
  color: #202228;
  font-size: 13px;
}

.cv-tag {
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 11px;
}

.cv-current {
  background: #eef4ee;
  color: #4f8a68;
}

.cv-draft {
  background: #fdf3e7;
  color: #b8791f;
}

.cv-meta {
  overflow: hidden;
  min-width: 0;
  flex: 1;
  color: #9aa0aa;
  font-size: 11.5px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.cv-ops {
  display: inline-flex;
  flex: none;
  gap: 6px;
}

.cv-op {
  padding: 2px 9px;
  border: 1px solid #e2e4e9;
  border-radius: 7px;
  background: #fff;
  color: #4d525c;
  font-size: 12px;
  cursor: pointer;
}

.cv-op:hover:not(:disabled) {
  border-color: #c9cbd2;
  color: #111;
}

.cv-op:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.cv-summary {
  margin-top: 4px;
  color: #6b7280;
  font-size: 12px;
  line-height: 1.6;
}
</style>
