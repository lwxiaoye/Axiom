<template>
  <div class="workbench-panel">
    <div class="market-heading">
      <div>
        <span>AGENT SKILLS</span>
        <h2>技能</h2>
      </div>
      <div class="market-search">
        <SearchOutlined />
        <input v-model="keyword" placeholder="搜索技能..." />
      </div>
      <button class="market-refresh" type="button" @click="loadSkills()">刷新</button>
    </div>

    <p class="wb-panel-tip">
      技能是提示词型 Skill ,与Skill广场的技能相互独立。
    </p>

    <div v-if="loading" class="status-box"><LoadingOutlined /> 正在加载技能...</div>

    <div v-else-if="!serviceReady" class="wb-unready">
      <ToolOutlined />
      <strong>技能服务暂不可用</strong>
      <p>工作流运行时（agent-api）未连接或未部署，恢复后点击刷新即可。</p>
      <a-button size="small" @click="loadSkills()">重试</a-button>
    </div>

    <div v-else class="wb-grid">
      <button class="wb-create-card" type="button" @click="createVisible = true">
        <span class="wb-create-plus"><PlusOutlined /></span>
        <strong>创建技能</strong>
        <small>按描述异步生成技能包</small>
      </button>
      <button class="wb-create-card" type="button" @click="importVisible = true">
        <span class="wb-create-plus"><InboxOutlined /></span>
        <strong>导入技能包</strong>
        <small>上传 zip，含技能元数据</small>
      </button>

      <div
        v-for="skill in filteredSkills"
        :key="skill.id"
        class="wb-card"
        role="button"
        tabindex="0"
        title="查看版本"
        @click="showVersions(skill)"
        @keydown.enter="showVersions(skill)"
      >
        <div class="wb-card-head">
          <span class="wb-tile skill">
            <span>{{ (skill.name || 'SK').slice(0, 2) }}</span>
          </span>
          <div class="wb-card-ident">
            <strong class="wb-card-title">{{ skill.name }}</strong>
          </div>
        </div>

        <p class="wb-card-desc">{{ skill.description || '暂无描述' }}</p>

        <div v-if="(skill.category || []).length" class="wb-tags" style="margin-bottom: 8px">
          <em v-for="cat in skill.category || []" :key="cat">{{ AgentSkillCategoryLabelMap[cat] || cat }}</em>
        </div>

        <div class="wb-card-foot skill-card-foot">
          <div class="wb-card-ops" @click.stop @keydown.enter.stop>
            <button class="wb-op" type="button" @click="showVersions(skill)"><HistoryOutlined /> 版本</button>
            <button v-if="skill.source !== 'system'" class="wb-op" type="button" @click="openEdit(skill)">
              <EditOutlined /> 编辑
            </button>
            <a-dropdown v-if="skill.source !== 'system'" :trigger="['click']" placement="bottomRight">
              <button class="wb-op icon-only" type="button" title="更多操作" aria-label="更多操作">
                <EllipsisOutlined />
              </button>
              <template #overlay>
                <a-menu @click="({ key }) => key === 'delete' && confirmDelete(skill)">
                  <a-menu-item key="delete" class="wb-menu-danger"><DeleteOutlined /> 删除</a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>
          </div>
        </div>
      </div>

      <div v-if="skills.length && !filteredSkills.length" class="wb-empty">没有匹配的技能，换个关键词试试</div>
    </div>

    <!-- 创建技能（描述生成） -->
    <a-modal
      v-model:open="createVisible"
      title="创建技能"
      :width="540"
      :confirm-loading="creating"
      ok-text="创建"
      cancel-text="取消"
      wrap-class-name="wb-modal"
      @ok="submitCreate"
    >
      <div class="wb-modal-form">
        <div class="wb-field">
          <label>技能名称 <em>*</em></label>
          <a-input v-model:value="createForm.name" size="large" placeholder="例如：课表查询" maxlength="64" show-count />
        </div>
        <div class="wb-field">
          <label>技能描述</label>
          <p>写清楚用途、输入输出与边界，后台将按描述生成技能包说明书，描述越具体效果越好。</p>
          <a-textarea
            v-model:value="createForm.description"
            :rows="5"
            :maxlength="500"
            show-count
            placeholder="例如：按学号或班级查询本周课表，输出星期几、节次、课程与教室；信息不足时提示需要补充什么。"
          />
        </div>
        <div class="wb-field">
          <label>分类 <i>可多选</i></label>
          <a-select
            v-model:value="createForm.category"
            mode="multiple"
            size="large"
            style="width: 100%"
            placeholder="选择分类，便于筛选与检索"
            :options="categoryOptions"
          />
        </div>
        <div class="wb-note">
          <InfoCircleOutlined />
          <p>创建后后台会生成技能包说明书，完成后自动刷新列表。</p>
        </div>
      </div>
    </a-modal>

    <!-- 编辑技能 -->
    <a-modal
      v-model:open="editVisible"
      title="编辑技能"
      :width="540"
      :confirm-loading="editing"
      ok-text="保存"
      cancel-text="取消"
      wrap-class-name="wb-modal"
      @ok="submitEdit"
    >
      <div class="wb-modal-form">
        <div class="wb-field">
          <label>技能名称 <em>*</em></label>
          <a-input v-model:value="editForm.name" size="large" placeholder="请输入技能名称" maxlength="64" show-count />
        </div>
        <div class="wb-field">
          <label>技能描述</label>
          <a-textarea
            v-model:value="editForm.description"
            :rows="5"
            :maxlength="500"
            show-count
            placeholder="描述该技能的用途、输入输出与边界"
          />
        </div>
        <div class="wb-field">
          <label>分类 <i>可多选</i></label>
          <a-select
            v-model:value="editForm.category"
            mode="multiple"
            size="large"
            style="width: 100%"
            placeholder="选择分类，便于筛选与检索"
            :options="categoryOptions"
          />
        </div>
      </div>
    </a-modal>

    <!-- 导入技能包 -->
    <a-modal v-model:open="importVisible" title="导入技能包" :width="540" :footer="null" wrap-class-name="wb-modal">
      <div class="wb-modal-form">
        <a-upload-dragger
          accept=".zip"
          :max-count="1"
          :before-upload="handleImport"
          :show-upload-list="false"
          :disabled="importing"
        >
          <p class="ant-upload-drag-icon"><InboxOutlined /></p>
          <p class="ant-upload-text">点击或拖拽 zip 技能包到此处</p>
          <p class="ant-upload-hint">最大 5MB，导入成功后立即可用</p>
        </a-upload-dragger>
        <div v-if="importing" class="import-loading"><LoadingOutlined /> 正在上传与校验...</div>
        <div class="wb-note">
          <InfoCircleOutlined />
          <p>包内需含 <code>skill.json</code>（name / description / category）或 <code>SKILL.md</code> 说明书，二者至少其一。</p>
        </div>
      </div>
    </a-modal>

    <!-- 技能详情抽屉：说明书 + 版本 -->
    <a-drawer v-model:open="versionsVisible" :title="activeSkill?.name || '技能详情'" :width="480">
      <a-spin :spinning="versionsLoading">
        <div class="skill-detail">
          <section>
            <h4>说明书（当前版本）</h4>
            <pre v-if="skillContent" class="skill-content">{{ skillContent }}</pre>
            <p v-else class="skill-content-empty">
              {{ activeSkill?.creationStatus === 'creating' ? '生成中，稍后刷新查看' : '暂无内容' }}
            </p>
          </section>
          <section>
            <h4>版本记录</h4>
            <div v-if="versions.length" class="version-list">
              <div v-for="version in versions" :key="version.id" class="version-row">
                <strong>{{ version.versionName || version.id }}</strong>
                <span v-if="version.importSource">导入自 {{ version.importSource.originalFilename }}</span>
                <em>{{ version.createdAt || '' }}</em>
              </div>
            </div>
            <a-empty v-else :image-style="{ height: '48px' }" description="暂无版本记录" />
          </section>
        </div>
      </a-spin>
    </a-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import { Modal, message } from 'ant-design-vue';
import {
  DeleteOutlined,
  EditOutlined,
  EllipsisOutlined,
  HistoryOutlined,
  InboxOutlined,
  InfoCircleOutlined,
  LoadingOutlined,
  PlusOutlined,
  SearchOutlined,
  ToolOutlined,
} from '@ant-design/icons-vue';
import {
  AgentSkillCategoryLabelMap,
  createAgentSkill,
  deleteAgentSkill,
  getAgentSkillContent,
  getAgentSkillList,
  getAgentSkillVersions,
  importAgentSkillZip,
  updateAgentSkill,
  type AgentSkill,
  type AgentSkillCategory,
  type AgentSkillVersion,
} from '../../workflow/api/skill.api';

const emit = defineEmits<{
  (e: 'count', value: number): void;
}>();

const keyword = ref('');
const loading = ref(false);
const serviceReady = ref(true);
const skills = ref<AgentSkill[]>([]);

type SkillForm = { name: string; description: string; category: AgentSkillCategory[] };

const createVisible = ref(false);
const creating = ref(false);
const createForm = reactive<SkillForm>({
  name: '',
  description: '',
  category: [],
});

const editVisible = ref(false);
const editing = ref(false);
const editingSkill = ref<AgentSkill | null>(null);
const editForm = reactive<SkillForm>({
  name: '',
  description: '',
  category: [],
});

const importVisible = ref(false);
const importing = ref(false);

const versionsVisible = ref(false);
const versionsLoading = ref(false);
const versions = ref<AgentSkillVersion[]>([]);
const skillContent = ref('');
const activeSkill = ref<AgentSkill | null>(null);

const categoryOptions = Object.entries(AgentSkillCategoryLabelMap).map(([value, label]) => ({ value, label }));

const filteredSkills = computed(() => {
  const key = keyword.value.trim().toLowerCase();
  if (!key) return skills.value;
  return skills.value.filter(
    (skill) =>
      skill.name.toLowerCase().includes(key) || String(skill.description || '').toLowerCase().includes(key)
  );
});

let pollTimer: ReturnType<typeof setTimeout> | null = null;

async function loadSkills(silent = false) {
  if (!silent) loading.value = true;
  try {
    const list = await getAgentSkillList({ source: 'personal' });
    skills.value = Array.isArray(list) ? list.filter((item) => item.type !== 'folder') : [];
    serviceReady.value = true;
    emit('count', skills.value.length);
  } catch {
    skills.value = [];
    serviceReady.value = false;
  } finally {
    if (!silent) loading.value = false;
    schedulePoll();
  }
}

/** 有「生成中」的技能时每 5s 静默刷新，直到全部落定 */
function schedulePoll() {
  if (pollTimer) clearTimeout(pollTimer);
  if (!skills.value.some((skill) => skill.creationStatus === 'creating')) return;
  pollTimer = setTimeout(() => loadSkills(true), 5000);
}

onBeforeUnmount(() => {
  if (pollTimer) clearTimeout(pollTimer);
});

async function submitCreate() {
  if (!createForm.name.trim()) {
    message.warning('请输入技能名称');
    return;
  }
  creating.value = true;
  try {
    await createAgentSkill({
      name: createForm.name.trim(),
      description: createForm.description.trim() || undefined,
      category: createForm.category,
    });
    message.success('已提交创建，生成完成后会自动刷新');
    createVisible.value = false;
    createForm.name = '';
    createForm.description = '';
    createForm.category = [];
    await loadSkills();
  } catch {
    message.error('创建失败，请确认技能服务已部署');
  } finally {
    creating.value = false;
  }
}

function openEdit(skill: AgentSkill) {
  if (skill.source === 'system') return;
  editingSkill.value = skill;
  editForm.name = skill.name || '';
  editForm.description = skill.description || '';
  editForm.category = [...(skill.category || [])];
  editVisible.value = true;
}

async function submitEdit() {
  const skill = editingSkill.value;
  if (!skill) return;
  if (!editForm.name.trim()) {
    message.warning('请输入技能名称');
    return;
  }
  editing.value = true;
  try {
    await updateAgentSkill({
      id: skill.id,
      name: editForm.name.trim(),
      description: editForm.description.trim(),
      category: editForm.category,
    });
    message.success('技能已更新');
    editVisible.value = false;
    editingSkill.value = null;
    await loadSkills();
  } catch {
    message.error('编辑失败');
  } finally {
    editing.value = false;
  }
}

async function handleImport(file: File) {
  importing.value = true;
  try {
    await importAgentSkillZip(file);
    message.success('技能包导入成功');
    importVisible.value = false;
    await loadSkills();
  } catch {
    message.error('导入失败，请检查技能包结构');
  } finally {
    importing.value = false;
  }
  return false;
}

function confirmDelete(skill: AgentSkill) {
  Modal.confirm({
    title: '删除技能',
    content: `确定删除「${skill.name}」？删除后不可恢复。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => removeSkill(skill),
  });
}

async function removeSkill(skill: AgentSkill) {
  try {
    await deleteAgentSkill(skill.id);
    message.success('已删除');
    await loadSkills();
  } catch {
    message.error('删除失败');
  }
}

async function showVersions(skill: AgentSkill) {
  activeSkill.value = skill;
  versionsVisible.value = true;
  versionsLoading.value = true;
  skillContent.value = '';
  try {
    const [versionList, content] = await Promise.all([
      getAgentSkillVersions(skill.id).catch(() => []),
      getAgentSkillContent(skill.id).catch(() => null),
    ]);
    versions.value = versionList || [];
    skillContent.value = content?.content || '';
  } finally {
    versionsLoading.value = false;
  }
}

onMounted(loadSkills);
</script>

<style scoped lang="less">
.import-loading {
  margin-top: 12px;
  text-align: center;
  color: #64748b;
  font-size: 12px;
}

.wb-note code {
  padding: 1px 6px;
  border-radius: 4px;
  background: #eef1f5;
  color: #334155;
  font-size: 11px;
}

.skill-card-foot {
  justify-content: flex-end;
}

.skill-detail {
  display: flex;
  flex-direction: column;
  gap: 20px;

  h4 {
    margin: 0 0 8px;
    color: #111827;
    font-size: 13px;
  }
}

.skill-content {
  max-height: 340px;
  margin: 0;
  padding: 12px;
  overflow: auto;
  border: 1px solid #eceef3;
  border-radius: 10px;
  background: #fafbfc;
  color: #334155;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.skill-content-empty {
  margin: 0;
  padding: 18px 0;
  border: 1px dashed #e2e8f0;
  border-radius: 10px;
  text-align: center;
  color: #98a0ad;
  font-size: 12px;
}

.version-list {
  display: flex;
  flex-direction: column;
  gap: 8px;

  .version-row {
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 8px 12px;
    display: flex;
    flex-direction: column;
    gap: 2px;

    strong {
      font-size: 13px;
      color: #0f172a;
    }

    span,
    em {
      font-size: 11px;
      font-style: normal;
      color: #94a3b8;
    }
  }
}
</style>
