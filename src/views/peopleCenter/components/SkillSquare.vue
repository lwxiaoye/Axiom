<template>
  <div class="skill-square">
    <div class="skill-header">
      <div>
        <span>SKILL SQUARE</span>
        <h2>Skill 广场</h2>
      </div>
      <div class="skill-search">
        <SearchOutlined />
        <input v-model="searchKeyword" placeholder="搜索 Skill..." @keyup.enter="handleSearch" />
      </div>
      <button class="skill-refresh" type="button" @click="handleRefresh">刷新</button>
    </div>

    <div v-if="loading" class="status-box">
      <LoadingOutlined /> 正在加载 Skills...
    </div>
    <div v-else-if="errorMessage" class="status-box error-state">
      <ExclamationCircleOutlined />
      <strong>Skill 加载失败</strong>
      <span>{{ errorMessage }}</span>
      <button type="button" @click="loadSkills">重新加载</button>
    </div>
    <div v-else-if="filteredSkills.length === 0" class="status-box">暂无可用 Skill</div>
    <div v-else class="skill-grid">
      <div
        v-for="skill in filteredSkills"
        :key="skill.id"
        class="skill-card"
        role="button"
        tabindex="0"
        title="查看 Skill 详情"
        @click="openDetail(skill)"
        @keyup.enter="openDetail(skill)"
      >
        <div class="skill-card-header">
          <div class="skill-icon" :style="{ background: skillVisual(skill).bg }">
            <component :is="skillVisual(skill).icon" />
          </div>
          <div class="skill-card-info">
            <strong>{{ skill.name }}</strong>
            <p>{{ skill.description || '暂无描述' }}</p>
            <div class="skill-meta">
              <span v-if="skill.version">v{{ skill.version }}</span>
              <span v-if="skill.author">{{ skill.author }}</span>
              <span>{{ formatSource(skill.source) }}</span>
            </div>
          </div>
        </div>
        <div class="skill-card-footer">
          <span class="skill-enabled">点击查看详情</span>
          <button class="skill-use" type="button" @click.stop="useSkill(skill)">
            在对话中使用
          </button>
        </div>
      </div>
    </div>

    <a-modal
      v-model:open="detailOpen"
      :title="detailSkill?.name || 'Skill 详情'"
      :footer="null"
      :width="720"
      wrap-class-name="skill-detail-modal"
      @cancel="closeDetail"
    >
      <div v-if="detailSkill" class="skill-detail">
        <div class="skill-detail-meta">
          <span v-if="detailSkill.version">v{{ detailSkill.version }}</span>
          <span v-if="detailSkill.author">{{ detailSkill.author }}</span>
          <span>{{ formatSource(detailSkill.source) }}</span>
        </div>

        <div class="skill-detail-section">
          <div class="skill-detail-label">技能作用</div>
          <p class="skill-detail-desc">{{ detailSkill.description || '暂无说明。' }}</p>
        </div>

        <div class="skill-detail-section">
          <div class="skill-detail-label">详细说明（SKILL.md）</div>
          <div v-if="readmeLoading" class="skill-detail-status"><LoadingOutlined /> 正在加载...</div>
          <div v-else-if="readmeError" class="skill-detail-status error">{{ readmeError }}</div>
          <div v-else-if="readmeContent" class="skill-detail-md">
            <MarkdownViewer :value="readmeContent" />
          </div>
          <div v-else class="skill-detail-status">该 Skill 暂无 SKILL.md 说明。</div>
        </div>

        <p class="skill-detail-hint">
          选择后带入主对话，回答时会按该技能的说明工作。若技能需要执行脚本、联网等主对话暂不支持的能力，相关步骤会如实说明、不会假装执行。
        </p>

        <div class="skill-detail-actions">
          <button class="skill-use" type="button" @click="useFromDetail">在对话中使用</button>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { ExclamationCircleOutlined, SearchOutlined, LoadingOutlined } from '@ant-design/icons-vue';
// 图标 + 品牌色与 composer + 菜单的「使用技能」面板共用一份（2026-07-28）
import { skillVisualOf, type SkillVisual } from '../composables/skillVisual';
import { MarkdownViewer } from '/@/components/Markdown';
import { getSkills, getSkillReadme, type SkillItem } from '../agentApi';

const emit = defineEmits<{
  (e: 'useSkill', skill: SkillItem): void;
}>();

const skills = ref<SkillItem[]>([]);
const loading = ref(false);
const searchKeyword = ref('');
const errorMessage = ref('');

// Skill 详情弹窗：基础信息 + SKILL.md 完整说明。
const detailOpen = ref(false);
const detailSkill = ref<SkillItem | null>(null);
const readmeContent = ref('');
const readmeLoading = ref(false);
const readmeError = ref('');
let readmeReqToken = 0; // 防连点串台：只认最后一次请求的结果

/**
 * 剥离 SKILL.md 开头的 YAML frontmatter（--- name/description/metadata --- 那一段）。
 * showdown 不认 frontmatter，会把它当正文渲染，且 `---` 被误当 setext 标题下划线，
 * 导致 name/description 与卡片顶部重复、还顶出一堆大标题。只展示正文。
 */
function stripFrontmatter(md: string): string {
  if (!md) return '';
  const m = md.match(/^﻿?\s*---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*\r?\n?/);
  return (m ? md.slice(m[0].length) : md).replace(/^\s+/, '');
}

async function openDetail(skill: SkillItem) {
  detailSkill.value = skill;
  detailOpen.value = true;
  readmeContent.value = '';
  readmeError.value = '';
  const recordId = skill.recordId || skill.id;
  if (!recordId) return;
  const token = ++readmeReqToken;
  readmeLoading.value = true;
  try {
    const md = await getSkillReadme(recordId);
    if (token !== readmeReqToken) return; // 已切到别的 skill
    readmeContent.value = stripFrontmatter(md);
  } catch (error) {
    if (token !== readmeReqToken) return;
    readmeError.value = error instanceof Error ? error.message : 'SKILL.md 加载失败';
  } finally {
    if (token === readmeReqToken) readmeLoading.value = false;
  }
}

function closeDetail() {
  detailOpen.value = false;
}

function useFromDetail() {
  if (detailSkill.value) emit('useSkill', detailSkill.value);
  detailOpen.value = false;
}

const filteredSkills = computed(() => {
  const keyword = searchKeyword.value.trim().toLowerCase();
  if (!keyword) return skills.value;
  return skills.value.filter(
    (skill) =>
      skill.name.toLowerCase().includes(keyword) ||
      (skill.description || '').toLowerCase().includes(keyword)
  );
});

async function loadSkills() {
  loading.value = true;
  errorMessage.value = '';
  try {
    skills.value = await getSkills();
  } catch (error) {
    console.error('Failed to load skills:', error);
    skills.value = [];
    errorMessage.value = error instanceof Error ? error.message : '请检查管理员端 Skill 接口';
  } finally {
    loading.value = false;
  }
}

function useSkill(skill: SkillItem) {
  emit('useSkill', skill);
}

/** 卡片图标 + 品牌色，判据与配色见 composables/skillVisual.ts（Skill 广场与对话框内共用） */
function skillVisual(skill: SkillItem): SkillVisual {
  return skillVisualOf(skill.name, skill.skillId);
}

function formatSource(source?: string) {
  const sourceNames: Record<string, string> = {
    upload: '上传安装',
    url: '网络安装',
    builtin: '内置 Skill',
  };
  return sourceNames[source || ''] || source || '已安装';
}

function handleSearch() {
  // Search is reactive, no additional action needed
}

function handleRefresh() {
  loadSkills();
}

onMounted(() => {
  loadSkills();
});
</script>

<style scoped>
.skill-square {
  max-width: 1100px;
  margin: 0 auto;
  padding: 24px;
}

.skill-header {
  display: grid;
  grid-template-columns: minmax(180px, 260px) minmax(260px, 460px) auto;
  align-items: center;
  gap: 18px;
  margin-bottom: 18px;
}

.skill-header span {
  color: #9198a5;
  letter-spacing: 4px;
  font-size: 12px;
}

.skill-header h2 {
  margin: 4px 0 0;
  font-size: 30px;
}

.skill-search {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  border: 1px dashed #dadde5;
  border-radius: 16px;
  background: #fff;
  padding: 0 14px;
  color: #7b8494;
}

.skill-search input {
  width: 100%;
  border: 0;
  outline: none;
  background: transparent;
  color: #111827;
}

.skill-refresh {
  border: 1px solid #e3e5ea;
  border-radius: 10px;
  background: #fff;
  padding: 8px 14px;
  cursor: pointer;
  width: 86px;
  height: 42px;
  text-align: center;
  justify-self: end;
}

.skill-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.skill-card {
  overflow: hidden;
  border: 1px solid #eceef3;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 8px 28px rgba(15, 23, 42, 0.04);
  transition: transform 0.2s, box-shadow 0.2s;
  padding: 18px;
  cursor: pointer;
}

.skill-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 14px 36px rgba(15, 23, 42, 0.08);
}

.skill-card:focus-visible {
  outline: 2px solid #818cf8;
  outline-offset: 2px;
}

.skill-detail {
  padding-top: 4px;
}

.skill-detail-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 14px;
}

.skill-detail-meta span {
  border-radius: 999px;
  background: #f1f2f4;
  padding: 3px 9px;
  color: #747a86;
  font-size: 11px;
}

.skill-detail-section {
  border-top: 1px solid #eef0f4;
  padding-top: 16px;
}

.skill-detail-section + .skill-detail-section {
  margin-top: 18px;
}

.skill-detail-label {
  margin-bottom: 8px;
  color: #9198a5;
  font-size: 12px;
  letter-spacing: 1px;
}

.skill-detail-desc {
  margin: 0;
  color: #3a3f47;
  font-size: 14px;
  line-height: 1.8;
}

/* SKILL.md 正文：过长时区域内滚动；代码块横向滚动、图片不溢出 */
.skill-detail-md {
  max-height: 44vh;
  overflow: auto;
}

.skill-detail-md :deep(.vditor-reset) {
  font-size: 14px;
  line-height: 1.75;
  color: #3a3f47;
}

.skill-detail-md :deep(.markdown-viewer > :first-child),
.skill-detail-md :deep(.vditor-reset > :first-child) {
  margin-top: 0;
}

.skill-detail-md :deep(pre) {
  overflow-x: auto;
  padding: 12px 14px;
  border-radius: 8px;
  background: #f6f7f9;
}

.skill-detail-md :deep(code) {
  word-break: break-word;
}

.skill-detail-md :deep(img) {
  max-width: 100%;
  height: auto;
}

.skill-detail-status {
  display: grid;
  place-items: center;
  min-height: 80px;
  gap: 8px;
  color: #7b8494;
  font-size: 13px;
}

.skill-detail-status.error {
  color: #c2413b;
}

.skill-detail-hint {
  margin: 16px 0 0;
  padding: 12px 14px;
  border-radius: 10px;
  background: #f6f7f9;
  color: #6b7280;
  font-size: 12px;
  line-height: 1.7;
}

.skill-detail-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid #eef0f4;
}

.skill-card-header {
  display: flex;
  gap: 14px;
  margin-bottom: 14px;
}

.skill-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 20px;
  flex-shrink: 0;
}

.skill-card-info {
  flex: 1;
  min-width: 0;
}

.skill-card-info strong {
  display: block;
  font-size: 16px;
  margin-bottom: 4px;
}

.skill-card-info p {
  display: -webkit-box;
  min-height: 39px;
  margin: 0;
  overflow: hidden;
  color: #687080;
  font-size: 13px;
  line-height: 1.5;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.skill-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.skill-meta span {
  border-radius: 999px;
  background: #f1f2f4;
  padding: 3px 7px;
  color: #747a86;
  font-size: 10px;
  letter-spacing: 0;
}

.skill-card-footer {
  align-items: center;
  display: flex;
  gap: 8px;
  justify-content: space-between;
}

.skill-enabled {
  color: #737985;
  font-size: 12px;
}

.skill-use {
  border: 1px solid #e3e5ea;
  border-radius: 8px;
  background: #fff;
  padding: 6px 16px;
  cursor: pointer;
  font-size: 13px;
  transition: all 0.2s;
}

.skill-use {
  margin-left: auto;
  border-color: #111;
  background: #111;
  color: #fff;
}

.skill-use:hover {
  background: #303035;
}

.status-box {
  display: grid;
  place-items: center;
  min-height: 320px;
  color: #737b88;
}

.status-box.error-state {
  align-content: center;
  gap: 8px;
}

.status-box.error-state > :deep(.anticon) {
  color: #c2413b;
  font-size: 24px;
}

.status-box.error-state strong {
  color: #2e3036;
}

.status-box.error-state span {
  max-width: 420px;
  color: #8a8f99;
  font-size: 13px;
  text-align: center;
}

.status-box.error-state button {
  margin-top: 6px;
  border: 1px solid #dadce2;
  border-radius: 9px;
  background: #fff;
  padding: 7px 14px;
  color: #3f434b;
  cursor: pointer;
}

/* iPad 与手机共用紧凑的广场布局：顶栏已经显示页面名，
   内容区只保留搜索、刷新和可扫读卡片。 */
@media (max-width: 1024px) {
  .skill-square {
    max-width: none;
    padding: 0;
  }

  .skill-header {
    grid-template-columns: minmax(0, 1fr) 80px;
    gap: 10px;
    margin-bottom: 16px;
  }

  .skill-header > div:first-child {
    display: none;
  }

  .skill-search {
    min-width: 0;
    min-height: 44px;
    border-style: solid;
    border-radius: 999px;
    padding: 0 16px;
  }

  .skill-search input {
    min-width: 0;
  }

  .skill-refresh {
    width: 80px;
    height: 44px;
    justify-self: stretch;
  }

  .skill-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px;
  }

  .skill-card {
    display: flex;
    min-height: 184px;
    flex-direction: column;
    padding: 16px;
  }

  .skill-card-footer {
    margin-top: auto;
  }

  .skill-use {
    min-height: 44px;
    padding: 0 16px;
  }
}

@media (max-width: 719px) {
  .skill-header {
    grid-template-columns: minmax(0, 1fr) 72px;
    gap: 8px;
    margin-bottom: 12px;
  }

  .skill-search {
    padding: 0 14px;
  }

  .skill-search input {
    font-size: 16px;
  }

  .skill-refresh {
    width: 72px;
  }

  .skill-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }

  .skill-card {
    min-height: 176px;
    border-radius: 16px;
    padding: 15px;
  }

  .skill-card-header {
    gap: 12px;
    margin-bottom: 12px;
  }

  .skill-card-info strong {
    font-size: 16px;
    line-height: 22px;
  }

  .skill-card-info p {
    min-height: 40px;
    line-height: 20px;
  }

  .skill-meta {
    margin-top: 8px;
  }

  .skill-enabled {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .skill-use {
    flex: 0 0 auto;
  }

  .status-box {
    min-height: 220px;
  }
}

@media (hover: none) {
  .skill-card:hover {
    transform: none;
    box-shadow: 0 8px 28px rgba(15, 23, 42, 0.04);
  }
}
</style>

<!-- a-modal 的内容 teleport 到 body，scoped 样式够不到 AntD 的 .ant-modal-* 包裹层；
     借 wrapClassName 用非 scoped 选择器撑开内边距，修「内容贴边」。
     AntD v5（ant-design-vue 4.x）横向内边距在 .ant-modal-content 上——先清零，
     再由 header/body 各自显式控制，避免叠加成双倍或改错层不生效。 -->
<style>
.skill-detail-modal .ant-modal-content {
  padding: 0;
  border-radius: 14px;
  overflow: hidden;
}
.skill-detail-modal .ant-modal-header {
  margin: 0;
  padding: 22px 28px 14px;
  border-bottom: none;
}
.skill-detail-modal .ant-modal-body {
  padding: 4px 28px 24px;
}
.skill-detail-modal .ant-modal-close {
  top: 18px;
  inset-inline-end: 20px;
}

@media (max-width: 719px) {
  .skill-detail-modal .ant-modal {
    top: max(8px, env(safe-area-inset-top));
    width: calc(100vw - 16px) !important;
    max-width: calc(100vw - 16px);
    margin: 0 auto;
    padding-bottom: max(8px, env(safe-area-inset-bottom));
  }

  .skill-detail-modal .ant-modal-content {
    max-height: calc(100dvh - max(24px, env(safe-area-inset-top)) - max(24px, env(safe-area-inset-bottom)));
  }

  .skill-detail-modal .ant-modal-header {
    padding: 18px 20px 12px;
  }

  .skill-detail-modal .ant-modal-body {
    max-height: calc(100dvh - 88px - env(safe-area-inset-top) - env(safe-area-inset-bottom));
    overflow-y: auto;
    padding: 4px 20px 20px;
    overscroll-behavior: contain;
  }

  .skill-detail-modal .ant-modal-close {
    top: 13px;
    inset-inline-end: 12px;
  }
}
</style>
