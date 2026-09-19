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
      <div class="skill-header-actions">
        <button class="skill-upload" type="button" @click="openUpload">上传技能</button>
        <button class="skill-refresh" type="button" @click="handleRefresh">刷新</button>
      </div>
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
    <template v-else>
      <!-- 两个区上下分段而不是 tab：技能总量不大，一屏放得下；用户一进来就同时看到
           「自己的」和「平台的」，不用先猜该点哪个 tab。管理员看到的结构完全一样——
           他看不到别人的个人技能，只是自己的卡片上多出「分发」、平台卡片上多出「撤回」（2026-09-19）。 -->
      <section v-for="group in groups" :key="group.key" class="skill-section" :data-section="group.key">
        <div class="skill-section-head">
          <h3>{{ group.title }}</h3>
          <em>{{ group.all.length }}</em>
          <span>{{ group.hint }}</span>
        </div>

        <div v-if="!group.all.length && group.key === 'personal'" class="skill-empty">
          <strong>还没有上传技能</strong>
          <span>点右上角「上传技能」：传一个含 skill.json 与 SKILL.md 的 zip 包，或直接在线写一份说明。</span>
        </div>
        <div v-else-if="!group.all.length" class="skill-empty is-quiet">暂无平台技能</div>
        <div v-else-if="!group.filtered.length" class="skill-empty is-quiet">没有匹配的技能</div>
        <div v-else class="skill-grid">
          <div
            v-for="skill in group.filtered"
            :key="skill.id"
            :class="['skill-card', { 'is-disabled': skill.enabled === false }]"
            role="button"
            tabindex="0"
            title="查看 Skill 详情"
            :data-skill-id="skill.id"
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
                  <span v-if="skill.version">{{ versionLabel(skill.version) }}</span>
                  <!-- 平台技能标来源：内置 / 管理员分发。个人区不标，分区标题已经说明了 -->
                  <span v-if="skill.source === 'system'" class="skill-origin">{{ originLabel(skill) }}</span>
                  <span v-if="isPackageSkill(skill)">zip · {{ skill.fileCount }} 个文件</span>
                  <span v-if="skill.enabled === false" class="skill-disabled">不可用</span>
                </div>
              </div>
            </div>
            <div class="skill-card-footer">
              <!-- 操作只看后端给的权限位：canEdit 直接露「编辑」，删除 / 分发 / 撤回收进「···」 -->
              <div v-if="skill.canEdit || moreActions(skill).length" class="skill-card-ops" @click.stop @keyup.enter.stop>
                <button v-if="skill.canEdit" class="skill-op" type="button" @click="openEdit(skill)">编辑</button>
                <a-dropdown v-if="moreActions(skill).length" :trigger="['click']" placement="bottomLeft">
                  <button class="skill-op skill-op-more" type="button" title="更多操作" aria-label="更多操作">
                    <EllipsisOutlined />
                  </button>
                  <template #overlay>
                    <a-menu @click="({ key }) => runAction(skill, String(key))">
                      <a-menu-item
                        v-for="action in moreActions(skill)"
                        :key="action.key"
                        :class="action.danger ? 'wb-menu-danger' : undefined"
                      >
                        {{ action.label }}
                      </a-menu-item>
                    </a-menu>
                  </template>
                </a-dropdown>
              </div>
              <span v-else class="skill-enabled">点击查看详情</span>
              <button v-if="skill.enabled !== false" class="skill-use" type="button" @click.stop="useSkill(skill)">
                在对话中使用
              </button>
            </div>
          </div>
        </div>
      </section>
    </template>

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
          <span v-if="detailSkill.version">{{ versionLabel(detailSkill.version) }}</span>
          <span v-if="detailSkill.author">{{ detailSkill.author }}</span>
          <span>{{ originLabel(detailSkill) }}</span>
          <span v-if="isPackageSkill(detailSkill)">zip 包 · {{ detailSkill.fileCount }} 个文件</span>
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
          <button v-if="detailSkill.enabled !== false" class="skill-use" type="button" @click="useFromDetail">在对话中使用</button>
        </div>
      </div>
    </a-modal>

    <!-- 上传技能：一个弹窗两种方式。zip 走 /skill/import，直接写走 /skill/add。 -->
    <a-modal
      v-model:open="uploadOpen"
      title="上传技能"
      :footer="null"
      :width="560"
      :mask-closable="!submitting"
      wrap-class-name="skill-detail-modal skill-form-modal"
    >
      <div class="skill-mode-switch" role="tablist" aria-label="上传方式">
        <button
          type="button"
          role="tab"
          :aria-selected="uploadMode === 'zip'"
          :class="['category-filter-item', { active: uploadMode === 'zip' }]"
          @click="uploadMode = 'zip'"
        >
          <span>上传 zip 包</span>
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="uploadMode === 'write'"
          :class="['category-filter-item', { active: uploadMode === 'write' }]"
          @click="uploadMode = 'write'"
        >
          <span>直接编写</span>
        </button>
      </div>

      <div v-if="uploadMode === 'zip'" class="wb-modal-form">
        <a-upload-dragger
          accept=".zip,application/zip"
          :max-count="1"
          :before-upload="stageZip"
          :show-upload-list="false"
          :disabled="submitting"
        >
          <p class="ant-upload-drag-icon"><InboxOutlined /></p>
          <p class="ant-upload-text">{{ zipFile ? zipFile.name : '点击或拖拽 zip 技能包到此处' }}</p>
          <p class="ant-upload-hint">
            {{ zipFile ? `${formatBytes(zipFile.size)} · 再次点击可更换` : '包内需含 skill.json 与 SKILL.md，可带 scripts/；压缩后不超过 5MB' }}
          </p>
        </a-upload-dragger>
      </div>

      <div v-else class="wb-modal-form">
        <div class="wb-field">
          <label>名称 <em>*</em></label>
          <a-input v-model:value="uploadForm.name" size="large" placeholder="例如：周报整理" :maxlength="64" show-count />
        </div>
        <div class="wb-field">
          <label>描述</label>
          <p>一句话说明什么时候该用它，@Skill 选择器里就显示这句。</p>
          <a-input v-model:value="uploadForm.description" size="large" placeholder="例如：把零散的工作记录整理成周报" :maxlength="200" />
        </div>
        <div class="wb-field">
          <label>SKILL.md 内容 <em>*</em></label>
          <p>写清楚这个技能怎么一步步做、输出成什么样；模型回答时会照着执行。</p>
          <a-textarea
            v-model:value="uploadForm.content"
            :rows="10"
            :maxlength="SKILL_CONTENT_MAX"
            placeholder="# 周报整理&#10;&#10;## 什么时候用&#10;用户给出一周的工作记录，要整理成周报时。&#10;&#10;## 步骤&#10;1. 按项目归类……"
          />
        </div>
      </div>

      <p v-if="formError" class="skill-form-error" role="alert">{{ formError }}</p>

      <div class="skill-form-actions">
        <button class="skill-ghost" type="button" :disabled="submitting" @click="uploadOpen = false">取消</button>
        <button
          class="skill-use"
          type="button"
          :disabled="submitting || (uploadMode === 'zip' && !zipFile)"
          @click="submitUpload"
        >
          <LoadingOutlined v-if="submitting" />
          {{ uploadMode === 'zip' ? '上传' : '保存' }}
        </button>
      </div>
    </a-modal>

    <!-- 编辑技能：内容型可改 SKILL.md 正文；zip 型只改名称/描述（正文在包里，要改就重传） -->
    <a-modal
      v-model:open="editOpen"
      title="编辑技能"
      :footer="null"
      :width="560"
      :mask-closable="!submitting"
      wrap-class-name="skill-detail-modal skill-form-modal"
    >
      <div class="wb-modal-form">
        <div class="wb-field">
          <label>名称 <em>*</em></label>
          <a-input v-model:value="editForm.name" size="large" :maxlength="64" show-count />
        </div>
        <div class="wb-field">
          <label>描述</label>
          <a-input v-model:value="editForm.description" size="large" :maxlength="200" />
        </div>
        <div v-if="editingSkill && isPackageSkill(editingSkill)" class="wb-note">
          <InfoCircleOutlined />
          <p>这个技能由 zip 包导入（{{ editingSkill.fileCount }} 个文件），这里只能改名称和描述；要改正文请删除后重新上传 zip。</p>
        </div>
        <div v-else class="wb-field">
          <label>SKILL.md 内容 <em>*</em></label>
          <div v-if="editContentLoading" class="skill-detail-status"><LoadingOutlined /> 正在读取当前内容...</div>
          <a-textarea v-else v-model:value="editForm.content" :rows="10" :maxlength="SKILL_CONTENT_MAX" />
        </div>
      </div>

      <p v-if="formError" class="skill-form-error" role="alert">{{ formError }}</p>

      <div class="skill-form-actions">
        <button class="skill-ghost" type="button" :disabled="submitting" @click="editOpen = false">取消</button>
        <button class="skill-use" type="button" :disabled="submitting || editContentLoading" @click="submitEdit">
          <LoadingOutlined v-if="submitting" />
          保存
        </button>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive, onMounted } from 'vue';
import { Modal, message } from 'ant-design-vue';
import {
  ExclamationCircleOutlined,
  SearchOutlined,
  LoadingOutlined,
  InboxOutlined,
  InfoCircleOutlined,
  EllipsisOutlined,
} from '@ant-design/icons-vue';
// 图标 + 品牌色与 composer + 菜单的「使用技能」面板共用一份（2026-07-28）
import { skillVisualOf, type SkillVisual } from '../composables/skillVisual';
import { MarkdownViewer } from '/@/components/Markdown';
import {
  getSkills,
  getSkillReadme,
  importSkillZip,
  createSkill,
  updateSkill,
  deleteSkill,
  distributeSkill,
  revokeSkillDistribution,
  isPackageSkill,
  type SkillItem,
} from '../agentApi';

const emit = defineEmits<{
  (e: 'useSkill', skill: SkillItem): void;
}>();

/** SKILL.md 正文上限：与后端 CONTENT_LIMIT 同量级，防止一次贴进几 MB 文本 */
const SKILL_CONTENT_MAX = 60000;

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

// ── 分区：我的技能 / 平台技能 ─────────────────────────────────────────────
// 一次 scope=all 拉回来按 source 切开，而不是两个请求：列表小、且刷新/上传后只需重拉一次。
// 自己的技能不可用也列出来（标「不可用」，好删掉）；平台技能不可用的对用户没有任何操作可做，直接不显示。
const personalSkills = computed(() => skills.value.filter((s) => s.source !== 'system'));
const systemSkills = computed(() => skills.value.filter((s) => s.source === 'system' && s.enabled !== false));

function matchKeyword(list: SkillItem[]): SkillItem[] {
  const keyword = searchKeyword.value.trim().toLowerCase();
  if (!keyword) return list;
  return list.filter(
    (skill) =>
      skill.name.toLowerCase().includes(keyword) ||
      (skill.description || '').toLowerCase().includes(keyword)
  );
}

/** 模板里两个分区共用一套卡片，按这个数组循环；顺序固定：先自己的，再平台的 */
const groups = computed(() => [
  {
    key: 'personal' as const,
    title: '我的技能',
    hint: '只有你自己能看到',
    all: personalSkills.value,
    filtered: matchKeyword(personalSkills.value),
  },
  {
    key: 'system' as const,
    title: '平台技能',
    hint: '内置与管理员分发，所有人可用',
    all: systemSkills.value,
    filtered: matchKeyword(systemSkills.value),
  },
]);

async function loadSkills() {
  loading.value = true;
  errorMessage.value = '';
  try {
    // 广场要把自己上传但暂不可用的也列出来（才能删掉），所以 includeDisabled
    skills.value = await getSkills({ scope: 'all', includeDisabled: true });
  } catch (error) {
    console.error('Failed to load skills:', error);
    skills.value = [];
    errorMessage.value = error instanceof Error ? error.message : '请检查管理员端 Skill 接口';
  } finally {
    loading.value = false;
  }
}

/** 上传 / 编辑 / 删除 / 分发之后静默重拉：不清空当前列表、不闪 loading */
async function reloadQuietly() {
  try {
    skills.value = await getSkills({ scope: 'all', includeDisabled: true });
  } catch (error) {
    console.error('Failed to reload skills:', error);
  }
}

function useSkill(skill: SkillItem) {
  emit('useSkill', skill);
}

/** 卡片图标 + 品牌色，判据与配色见 composables/skillVisual.ts（Skill 广场与对话框内共用） */
function skillVisual(skill: SkillItem): SkillVisual {
  return skillVisualOf(skill.name, skill.skillId);
}

/** 平台技能上的来源小标签：内置 / 管理员分发；个人技能显示「我的」 */
/** 版本名后端可能已带 v（v1/v2），也可能是裸语义版本（3.0.7）：统一成 v 开头，不出现 vv2 */
function versionLabel(version: string): string {
  const v = String(version || '').trim();
  return /^v/i.test(v) ? v : `v${v}`;
}

function originLabel(skill: SkillItem): string {
  if (skill.source === 'system') return skill.builtin ? '内置' : '管理员分发';
  return '我的';
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

// ── 上传 ─────────────────────────────────────────────────────────────────
const uploadOpen = ref(false);
const uploadMode = ref<'zip' | 'write'>('zip');
const zipFile = ref<File | null>(null);
const uploadForm = reactive({ name: '', description: '', content: '' });
const submitting = ref(false);
const formError = ref('');

function openUpload() {
  formError.value = '';
  zipFile.value = null;
  uploadForm.name = '';
  uploadForm.description = '';
  uploadForm.content = '';
  uploadOpen.value = true;
}

/** a-upload 的 beforeUpload：只暂存文件，真正上传等用户点「上传」；返回 false 阻止组件自己发请求 */
function stageZip(file: File) {
  formError.value = '';
  if (!/\.zip$/i.test(file.name)) {
    formError.value = '只支持 .zip 技能包';
    return false;
  }
  if (file.size > 5 * 1024 * 1024) {
    formError.value = '技能包压缩后不能超过 5MB';
    return false;
  }
  zipFile.value = file;
  return false;
}

async function submitUpload() {
  formError.value = '';
  if (uploadMode.value === 'zip') {
    if (!zipFile.value) return;
    submitting.value = true;
    try {
      const created = await importSkillZip(zipFile.value);
      uploadOpen.value = false;
      message.success(`已上传「${created.name}」`);
      await reloadQuietly();
    } catch (error) {
      formError.value = errorText(error, '上传失败，请稍后再试');
    } finally {
      submitting.value = false;
    }
    return;
  }

  const name = uploadForm.name.trim();
  if (!name) {
    formError.value = '请填写技能名称';
    return;
  }
  if (!uploadForm.content.trim()) {
    formError.value = '请填写 SKILL.md 内容';
    return;
  }
  submitting.value = true;
  try {
    const created = await createSkill({ name, description: uploadForm.description, content: uploadForm.content });
    uploadOpen.value = false;
    message.success(`已保存「${created.name}」`);
    await reloadQuietly();
  } catch (error) {
    formError.value = errorText(error, '保存失败，请稍后再试');
  } finally {
    submitting.value = false;
  }
}

// ── 编辑 ─────────────────────────────────────────────────────────────────
const editOpen = ref(false);
const editingSkill = ref<SkillItem | null>(null);
const editForm = reactive({ name: '', description: '', content: '' });
const editContentLoading = ref(false);
let editReqToken = 0;

async function openEdit(skill: SkillItem) {
  formError.value = '';
  editingSkill.value = skill;
  editForm.name = skill.name;
  editForm.description = skill.description || '';
  editForm.content = '';
  editOpen.value = true;
  if (isPackageSkill(skill)) return;
  // 内容型：把当前 SKILL.md 原文（含 frontmatter）读回来放进多行框，保存时整份回传
  const token = ++editReqToken;
  editContentLoading.value = true;
  try {
    const md = await getSkillReadme(skill.recordId || skill.id);
    if (token !== editReqToken) return;
    editForm.content = md;
  } catch (error) {
    if (token !== editReqToken) return;
    formError.value = errorText(error, '当前内容读取失败，可直接重新填写');
  } finally {
    if (token === editReqToken) editContentLoading.value = false;
  }
}

async function submitEdit() {
  const skill = editingSkill.value;
  if (!skill) return;
  formError.value = '';
  const name = editForm.name.trim();
  if (!name) {
    formError.value = '请填写技能名称';
    return;
  }
  const packaged = isPackageSkill(skill);
  if (!packaged && !editForm.content.trim()) {
    formError.value = '请填写 SKILL.md 内容';
    return;
  }
  submitting.value = true;
  try {
    await updateSkill({
      skillId: skill.skillId || skill.id,
      name,
      description: editForm.description,
      content: packaged ? undefined : editForm.content,
    });
    editOpen.value = false;
    message.success('已保存');
    await reloadQuietly();
  } catch (error) {
    formError.value = errorText(error, '保存失败，请稍后再试');
  } finally {
    submitting.value = false;
  }
}

// ── 删除 / 分发 / 撤回 ────────────────────────────────────────────────────
function confirmDelete(skill: SkillItem) {
  Modal.confirm({
    title: '删除技能',
    content: `确定删除「${skill.name}」？删除后不可恢复，正在对话里用它的轮次不受影响。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await deleteSkill(skill.skillId || skill.id);
        message.success('已删除');
        await reloadQuietly();
      } catch (error) {
        message.error(errorText(error, '删除失败'));
        throw error; // 让 confirm 保持打开，用户看得到失败原因
      }
    },
  });
}

function confirmDistribute(skill: SkillItem) {
  Modal.confirm({
    title: '分发到全平台',
    content: `分发后「${skill.name}」会出现在所有用户的「平台技能」里，随时可以撤回。`,
    okText: '分发',
    cancelText: '取消',
    async onOk() {
      try {
        await distributeSkill(skill.skillId || skill.id);
        message.success('已分发到全平台');
        await reloadQuietly();
      } catch (error) {
        message.error(errorText(error, '分发失败'));
        throw error;
      }
    },
  });
}

function confirmRevoke(skill: SkillItem) {
  Modal.confirm({
    title: '撤回分发',
    content: `撤回后其他用户将不再看到「${skill.name}」，它会回到你的「我的技能」。`,
    okText: '撤回',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await revokeSkillDistribution(skill.skillId || skill.id);
        message.success('已撤回分发');
        await reloadQuietly();
      } catch (error) {
        message.error(errorText(error, '撤回失败'));
        throw error;
      }
    },
  });
}

/** 「···」菜单里放哪些项：完全由后端给的权限位决定，前端不判管理员 */
function moreActions(skill: SkillItem): Array<{ key: string; label: string; danger?: boolean; run: () => void }> {
  const items: Array<{ key: string; label: string; danger?: boolean; run: () => void }> = [];
  if (skill.canDistribute) items.push({ key: 'distribute', label: '分发到全平台', run: () => confirmDistribute(skill) });
  if (skill.canRevoke) items.push({ key: 'revoke', label: '撤回分发', run: () => confirmRevoke(skill) });
  if (skill.canDelete) items.push({ key: 'delete', label: '删除', danger: true, run: () => confirmDelete(skill) });
  return items;
}

function runAction(skill: SkillItem, key: string) {
  moreActions(skill).find((action) => action.key === key)?.run();
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

.skill-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-self: end;
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
}

/* 「上传技能」是这页唯一的主动作，用与卡片「在对话中使用」同一个黑底按钮，不另起颜色 */
.skill-upload {
  height: 42px;
  padding: 0 18px;
  border: 1px solid var(--ink);
  border-radius: 10px;
  background: var(--ink);
  color: var(--surface);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.2s;
}

.skill-upload:hover {
  background: #303035;
}

/* ── 分区：我的技能 / 平台技能 ── */
.skill-section + .skill-section {
  margin-top: 30px;
}

.skill-section-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 12px;
}

.skill-section-head h3 {
  margin: 0;
  color: var(--ink);
  font-size: 16px;
  font-weight: 650;
}

.skill-section-head em {
  color: var(--muted);
  font-size: 13px;
  font-style: normal;
  font-variant-numeric: tabular-nums;
}

.skill-section-head span {
  color: var(--faint);
  font-size: 12px;
  letter-spacing: 0;
}

/* 空态：虚线框，文案直接告诉用户下一步在哪 */
.skill-empty {
  display: grid;
  gap: 6px;
  justify-items: center;
  min-height: 132px;
  align-content: center;
  border: 1px dashed var(--line);
  border-radius: 14px;
  padding: 24px;
  color: var(--muted);
  font-size: 13px;
  text-align: center;
}

.skill-empty strong {
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
}

.skill-empty.is-quiet {
  min-height: 88px;
  color: var(--faint);
}

/* ── 卡片上的操作：编辑直接露出来，删除 / 分发 / 撤回收进「···」 ── */
.skill-card-ops {
  display: flex;
  align-items: center;
  gap: 6px;
}

.skill-op {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 30px;
  padding: 0 10px;
  border: 1px solid #e3e5ea;
  border-radius: 8px;
  background: #fff;
  color: var(--ink);
  font-size: 12px;
  cursor: pointer;
  transition: border-color 0.18s;
}

.skill-op:hover {
  border-color: var(--ink);
}

.skill-op-more {
  width: 30px;
  padding: 0;
}

.skill-meta .skill-disabled {
  color: var(--warning);
}

.skill-card.is-disabled .skill-icon {
  filter: grayscale(1);
  opacity: 0.6;
}

/* ── 上传 / 编辑弹窗 ── */
.skill-mode-switch {
  display: flex;
  gap: 8px;
  margin-bottom: 18px;
}

.skill-form-error {
  margin: 12px 0 0;
  color: var(--danger);
  font-size: 13px;
  line-height: 1.6;
}

.skill-form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid #eef0f4;
}

.skill-ghost {
  border: 1px solid #e3e5ea;
  border-radius: 8px;
  background: #fff;
  padding: 6px 16px;
  color: var(--ink);
  cursor: pointer;
  font-size: 13px;
}

.skill-use:disabled,
.skill-ghost:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.skill-form-modal .ant-upload-drag {
  border-radius: 12px;
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
    grid-template-columns: minmax(0, 1fr) auto;
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
  }

  .skill-upload {
    height: 44px;
    padding: 0 14px;
  }

  .skill-section-head span {
    display: none;
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
    grid-template-columns: minmax(0, 1fr) auto;
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
    width: 64px;
  }

  .skill-upload {
    padding: 0 12px;
    font-size: 13px;
  }

  .skill-card-ops {
    min-width: 0;
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
