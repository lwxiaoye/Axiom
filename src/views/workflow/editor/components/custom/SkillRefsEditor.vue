<template>
  <div class="skill-editor">
    <div v-if="selectedSkills.length" class="selected-list">
      <span v-for="skill in selectedSkills" :key="skillKey(skill)" class="selected-item">
        <span class="selected-name">{{ skill.name }}</span>
        <span class="selected-source">{{ skillSourceLabel(skill.source) }}</span>
        <button type="button" class="selected-remove" @click="removeSkill(skill.skillId, skill.source)">
          <CloseOutlined />
        </button>
      </span>
    </div>
    <div v-else class="selected-empty">未选择 Skill</div>

    <a-button size="small" block class="open-picker-btn" @click="openPicker">
      <PlusOutlined />
      选择 Skill
    </a-button>

    <a-modal
      v-model:open="pickerOpen"
      width="640px"
      centered
      :footer="null"
      :destroy-on-close="true"
      wrap-class-name="tool-skill-modal-wrap"
      @cancel="cancelPicker"
    >
      <template #title>
        <div class="picker-title">
          <strong>关联 Skill</strong>
          <span>选择后会保存到当前 AI 对话节点的 inputs.skills</span>
        </div>
      </template>

      <div class="picker-shell">
        <div class="picker-tabs">
          <button
            v-for="tab in tabs"
            :key="tab.value"
            type="button"
            :class="{ active: activeTab === tab.value }"
            @click="changeTab(tab.value)"
          >
            {{ tab.label }}
          </button>
        </div>

        <div class="picker-toolbar">
          <a-input v-model:value="keyword" allow-clear placeholder="搜索 Skill">
            <template #prefix>
              <SearchOutlined />
            </template>
          </a-input>
          <a-button type="text" :loading="loading" @click="loadSkills(activeTab, true)">
            <ReloadOutlined />
          </a-button>
        </div>

        <a-spin :spinning="loading">
          <div class="picker-list">
            <button
              v-for="item in filteredOptions"
              :key="optionKey(item)"
              type="button"
              :class="['picker-row', { selected: isTempSelected(item) }]"
              @click="toggleTempSkill(item)"
            >
              <a-checkbox :checked="isTempSelected(item)" @click.stop @change="toggleTempSkill(item)" />
              <span class="item-avatar skill">{{ avatarText(item.name) }}</span>
              <span class="picker-info">
                <strong>{{ item.name }}</strong>
                <em>{{ item.description || '暂无描述' }}</em>
              </span>
              <span class="type-badge skill">{{ skillSourceLabel(item.source) }}</span>
            </button>
            <div v-if="!filteredOptions.length && !loading" class="picker-empty">
              {{ errorText || '暂无可选 Skill' }}
            </div>
          </div>
        </a-spin>

        <div class="picker-footer">
          <span>已选择 {{ tempSkills.length }} 项</span>
          <div>
            <a-button @click="cancelPicker">取消</a-button>
            <a-button type="primary" @click="confirmPicker">确认选择</a-button>
          </div>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { CloseOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue';
import { getAgentSkillList, getSkillMarketList, type AgentSkill, type SkillMarketSkill } from '../../../api/skill.api';
import type { AgentSkillRef, AgentSkillSource } from '../../../agent/form';
import type { FlowNodeInputItemType } from '../../../core/type';
import { setInputValue } from '../../../core/utils';

const props = defineProps<{ input: FlowNodeInputItemType }>();

type SkillTab = 'mine' | 'system';
type SkillOption = {
  id: string;
  name: string;
  description?: string;
  source: AgentSkillSource;
};

const tabs: { label: string; value: SkillTab }[] = [
  { label: '我的技能', value: 'mine' },
  { label: '系统技能', value: 'system' },
];

const pickerOpen = ref(false);
const activeTab = ref<SkillTab>('mine');
const keyword = ref('');
const loading = ref(false);
const errorText = ref('');
const personalSkills = ref<AgentSkill[]>([]);
const systemSkills = ref<SkillMarketSkill[]>([]);
const loadedTabs = ref<Set<SkillTab>>(new Set());
const tempSkills = ref<AgentSkillRef[]>([]);

if (!Array.isArray(props.input.value)) {
  setInputValue(props.input, []);
}

const selectedSkills = computed<AgentSkillRef[]>(() => (Array.isArray(props.input.value) ? props.input.value : []));

const options = computed<SkillOption[]>(() => {
  if (activeTab.value === 'mine') {
    return personalSkills.value
      .filter((item) => item.type === 'skill' && item.creationStatus !== 'creating' && item.creationStatus !== 'failed')
      .map((item) => ({
        id: item.id,
        name: item.name,
        description: item.description,
        source: 'mine' as AgentSkillSource,
      }));
  }
  return systemSkills.value.map((item) => ({
    id: item.skillId || item.id,
    name: item.name,
    description: item.description,
    source: 'system' as AgentSkillSource,
  }));
});

const filteredOptions = computed(() => {
  const query = keyword.value.trim().toLowerCase();
  if (!query) return options.value;
  return options.value.filter((item) =>
    [item.name, item.description].some((value) => String(value || '').toLowerCase().includes(query))
  );
});

function normalizeSkillSource(source?: AgentSkillSource) {
  return source === 'system' ? 'system' : 'mine';
}

function skillSourceLabel(source?: AgentSkillSource) {
  return normalizeSkillSource(source) === 'system' ? '系统技能' : '我的技能';
}

function skillKey(skill: AgentSkillRef) {
  return `${normalizeSkillSource(skill.source)}:${skill.skillId}`;
}

function optionKey(item: SkillOption) {
  return `${item.source}:${item.id}`;
}

function avatarText(name: string) {
  return (name || 'SK').slice(0, 2);
}

function commit(value: AgentSkillRef[]) {
  setInputValue(props.input, value);
}

function openPicker() {
  pickerOpen.value = true;
  activeTab.value = 'mine';
  keyword.value = '';
  tempSkills.value = selectedSkills.value.map((item) => ({ ...item }));
  loadSkills(activeTab.value);
}

function cancelPicker() {
  pickerOpen.value = false;
}

function confirmPicker() {
  commit(tempSkills.value.map((item) => ({ ...item })));
  pickerOpen.value = false;
}

function isTempSelected(item: SkillOption) {
  return tempSkills.value.some(
    (skill) => skill.skillId === item.id && normalizeSkillSource(skill.source) === item.source
  );
}

function toggleTempSkill(item: SkillOption) {
  if (isTempSelected(item)) {
    const source = normalizeSkillSource(item.source);
    tempSkills.value = tempSkills.value.filter(
      (skill) => !(skill.skillId === item.id && normalizeSkillSource(skill.source) === source)
    );
    return;
  }
  tempSkills.value = [
    ...tempSkills.value,
    { skillId: item.id, name: item.name, description: item.description, source: item.source },
  ];
}

function removeSkill(id: string, source?: AgentSkillSource) {
  const normalized = normalizeSkillSource(source);
  commit(selectedSkills.value.filter((skill) => !(skill.skillId === id && normalizeSkillSource(skill.source) === normalized)));
}

function changeTab(tab: SkillTab) {
  activeTab.value = tab;
  keyword.value = '';
  loadSkills(tab);
}

async function loadSkills(tab: SkillTab, force = false) {
  if (!force && loadedTabs.value.has(tab)) return;
  loading.value = true;
  errorText.value = '';
  try {
    if (tab === 'mine') {
      personalSkills.value = await getAgentSkillList({ source: 'personal' });
    } else {
      systemSkills.value = await getSkillMarketList();
    }
    loadedTabs.value = new Set([...loadedTabs.value, tab]);
  } catch {
    errorText.value = 'Skill 加载失败';
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped lang="less">
.skill-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.selected-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.selected-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  max-width: 100%;
  height: 24px;
  padding: 0 6px 0 8px;
  border: 1px solid #dbe3ef;
  border-radius: 6px;
  background: #f8fafc;
  color: #334155;
  font-size: 12px;
}

.selected-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.selected-source {
  flex-shrink: 0;
  color: #64748b;
  font-size: 11px;
}

.selected-remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;

  &:hover {
    color: #dc2626;
    background: #fee2e2;
  }
}

.selected-empty {
  height: 24px;
  color: #94a3b8;
  font-size: 12px;
  line-height: 24px;
}

.open-picker-btn {
  justify-content: center;
}

.picker-title {
  display: flex;
  flex-direction: column;
  gap: 4px;

  strong {
    color: #172033;
    font-size: 16px;
  }

  span {
    color: #6b7280;
    font-size: 12px;
    font-weight: 400;
  }
}

.picker-shell {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 4px 4px 6px;
}

.picker-tabs {
  display: inline-flex;
  width: fit-content;
  padding: 3px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;

  button {
    height: 30px;
    padding: 0 14px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: #64748b;
    font-size: 13px;
    cursor: pointer;

    &.active {
      background: #fff;
      color: #1e293b;
      box-shadow: 0 1px 3px rgba(15, 23, 42, 0.1);
    }
  }
}

.picker-toolbar {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  align-items: center;
}

.picker-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 260px;
  max-height: 360px;
  overflow: auto;
  padding: 2px 4px 16px;
}

.picker-row {
  display: grid;
  grid-template-columns: auto 34px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  color: #1f2937;
  text-align: left;
  cursor: pointer;

  &:hover {
    border-color: #c7d2fe;
    background: #f8fafc;
  }

  &.selected {
    border-color: #818cf8;
    background: #eef2ff;
  }
}

.item-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 8px;
  background: #eef2ff;
  color: #4f46e5;
  font-size: 12px;
  font-weight: 700;
}

.picker-info {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;

  strong {
    overflow: hidden;
    color: #172033;
    font-size: 14px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  em {
    display: -webkit-box;
    overflow: hidden;
    color: #6b7280;
    font-size: 12px;
    font-style: normal;
    line-height: 1.4;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
  }
}

.type-badge {
  padding: 2px 8px;
  border-radius: 999px;
  background: #eef2ff;
  color: #4f46e5;
  font-size: 12px;
  white-space: nowrap;
}

.picker-empty {
  padding: 48px 0;
  color: #94a3b8;
  font-size: 13px;
  text-align: center;
}

.picker-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 4px 2px;
  border-top: 1px solid #e5e7eb;
  color: #64748b;
  font-size: 13px;

  > div {
    display: inline-flex;
    gap: 12px;
  }

  :deep(.ant-btn) {
    min-width: 76px;
    border-radius: 8px;
  }
}
</style>
