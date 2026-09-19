<!-- eslint-disable vue/no-v-html -->
<template>
  <section class="agent-v2-page">
    <header class="editor-header">
      <button type="button" class="back-btn" title="返回" @click="router.back()">
        <ArrowLeftOutlined />
      </button>
      <div class="app-meta">
        <strong>{{ workflowApp?.name || '对话 Agent' }}</strong>
        <span class="app-sub">
          {{ workflowApp?.description || '模型自主调用工具、Skill 与知识库' }}
        </span>
      </div>
      <span v-if="hasUnsavedChanges" class="unsaved-dot" title="有未保存的修改"></span>
      <div class="header-actions">
        <a-button size="small" :loading="previewing" @click="handlePreview">
          <EyeOutlined />
          运行预览
        </a-button>
        <a-button size="small" :loading="saving" @click="handleSaveDraft">
          <SaveOutlined />
          保存草稿
        </a-button>
      </div>
    </header>

    <a-spin :spinning="loading" wrapper-class-name="agent-v2-spin">
      <main class="agent-workspace">
        <section class="config-panel">
          <div class="panel-title">
            <RobotOutlined class="title-icon violet" />
            <strong>AI 配置</strong>
          </div>

          <div class="config-field model-field">
            <label>AI 模型</label>
            <div class="model-control">
              <a-select
                v-model:value="form.model"
                :options="modelOptions"
                :loading="modelLoading"
                placeholder="选择对话模型"
                show-search
                class="full"
              />
              <a-popover placement="bottomRight" trigger="click">
                <template #content>
                  <div class="model-popover">
                    <label>
                      <span>温度</span>
                      <a-slider v-model:value="modelTemperature" :min="0" :max="2" :step="0.1" />
                    </label>
                    <label>
                      <span>上下文轮数</span>
                      <a-input-number v-model:value="form.maxHistories" :min="0" :max="30" class="full" />
                    </label>
                  </div>
                </template>
                <button type="button" class="icon-btn soft" title="模型参数">
                  <SettingOutlined />
                </button>
              </a-popover>
            </div>
          </div>

          <div class="config-field">
            <label>提示词</label>
            <VariableTextarea
              :value="form.systemPrompt"
              :rows="7"
              :groups="welcomeVariableGroups"
              class="prompt-variable-textarea"
              placeholder="请输入提示词，输入 / 可选择变量"
              @update:value="(value) => (form.systemPrompt = value)"
            />
            <a-button size="small" class="prompt-debug-button" :disabled="!workflowApp?.id" @click="promptDebugOpen = true">
              提示词调试
            </a-button>
          </div>

          <div class="config-section">
            <div class="section-head">
              <div>
                <MessageOutlined class="section-icon welcome" />
                <strong>全局变量</strong>
                <a-tooltip title="运行前由用户填写，可在提示词、开场白和工具参数中通过 {{key}} 引用">
                  <QuestionCircleOutlined class="hint-icon" />
                </a-tooltip>
              </div>
              <button type="button" class="choose-btn" @click="openVariableModal()">
                <PlusOutlined />
                新增
              </button>
            </div>
            <div v-if="form.variables.length" class="variable-list">
              <div v-for="(variable, index) in form.variables" :key="variable.id" class="variable-row">
                <span class="variable-main">
                  <strong>{{ variable.label }}</strong>
                  <code>{{ variable.key }}</code>
                </span>
                <em>{{ VariableInputLabelMap[variable.type] || variable.type }}</em>
                <b v-if="variable.required">必填</b>
                <span class="variable-actions">
                  <button type="button" class="remove-btn" title="编辑变量" @click="openVariableModal(index)">
                    <EditOutlined />
                  </button>
                  <button type="button" class="remove-btn" title="删除变量" @click="removeVariable(index)">
                    <DeleteOutlined />
                  </button>
                </span>
              </div>
            </div>
            <button v-else type="button" class="empty-box compact" @click="openVariableModal()">
              <MessageOutlined />
              尚未添加全局变量
            </button>
          </div>

          <div class="config-section">
            <div class="section-head">
              <div>
                <LinkOutlined class="section-icon skill" />
                <strong>关联 Skill</strong>
              </div>
              <button type="button" class="choose-btn" @click="openPicker('skill')">
                <PlusOutlined />
                选择
              </button>
            </div>
            <div v-if="form.skills.length" class="selected-list">
              <div
                v-for="skill in form.skills"
                :key="skillKey(skill)"
                class="selected-row"
              >
                <span class="item-avatar skill">{{ (skill.name || 'SK').slice(0, 2) }}</span>
                <span>
                  <strong>{{ skill.name }}</strong>
                  <em>{{ skillSourceLabel(skill.source) }} · {{ skill.description || '暂无描述' }}</em>
                </span>
                <button type="button" class="remove-btn" title="移除 Skill" @click="removeSkill(skill.skillId, skill.source)">
                  <DeleteOutlined />
                </button>
              </div>
            </div>
            <button v-else type="button" class="empty-box" @click="openPicker('skill')">
              <FolderOpenOutlined />
              尚未选择 Skill
            </button>
          </div>

          <div class="config-section">
            <div class="section-head">
              <div>
                <ToolOutlined class="section-icon tool" />
                <strong>工具</strong>
                <a-tooltip title="工具可由模型按需自主调用">
                  <QuestionCircleOutlined class="hint-icon" />
                </a-tooltip>
              </div>
              <button type="button" class="choose-btn" @click="openPicker('tool')">
                <PlusOutlined />
                选择
              </button>
            </div>
            <div v-if="form.selectedTools.length" class="selected-list">
              <div
                v-for="tool in form.selectedTools"
                :key="tool.id"
                class="selected-row"
              >
                <span :class="['item-avatar', iconClass(tool.kind)]">{{ avatarText(tool.name, tool.kind) }}</span>
                <span>
                  <strong>{{ tool.name }}</strong>
                  <em>{{ toolKindLabel(tool.kind) }}</em>
                </span>
                <button type="button" class="remove-btn" title="移除工具" @click="removeTool(tool.id)">
                  <DeleteOutlined />
                </button>
              </div>
            </div>
            <button v-else type="button" class="empty-box" @click="openPicker('tool')">
              <FolderOpenOutlined />
              尚未选择工具
            </button>
          </div>

          <div class="config-section knowledge-section">
            <div class="section-head">
              <div>
                <DatabaseOutlined class="section-icon knowledge" />
                <strong>知识库</strong>
              </div>
              <div class="section-actions">
                <a-button v-if="form.datasets.length" type="text" size="small" @click="showKnowledgeParams = !showKnowledgeParams">
                  参数
                </a-button>
                <button type="button" class="choose-btn" @click="openPicker('knowledge')">
                  <PlusOutlined />
                  选择
                </button>
              </div>
            </div>
            <div v-if="selectedDatasetIds.length" class="knowledge-param-strip">
              <span>搜索方式 <b>{{ searchModeLabel }}</b></span>
              <span>引用上限 <b>3000</b></span>
              <span>最低相关度 <b>{{ form.similarity }}</b></span>
              <span>问题优化 <b>{{ form.model || '-' }}</b></span>
            </div>
            <div v-if="form.datasets.length" class="selected-list">
              <div v-for="dataset in form.datasets" :key="dataset.datasetId" class="selected-row">
                <span class="item-avatar knowledge">KB</span>
                <span>
                  <strong>{{ dataset.name || dataset.datasetId }}</strong>
                  <em>知识库</em>
                </span>
                <button type="button" class="remove-btn" title="移除知识库" @click="removeDataset(dataset.datasetId)">
                  <DeleteOutlined />
                </button>
              </div>
            </div>
            <button v-else type="button" class="empty-box" @click="openPicker('knowledge')">
              <FolderOpenOutlined />
              尚未选择知识库
            </button>
            <div v-if="showKnowledgeParams && selectedDatasetIds.length" class="knowledge-controls">
              <label>
                <span>最低相关度（{{ form.similarity }}）</span>
                <a-slider v-model:value="form.similarity" :min="0" :max="1" :step="0.05" />
              </label>
            </div>
          </div>

          <div class="config-section upload-section">
            <div class="section-head">
              <div>
                <FileTextOutlined class="section-icon file" />
                <strong>文件上传</strong>
                <a-tooltip title="调试预览支持附件入口，运行时能力由后端配置决定">
                  <QuestionCircleOutlined class="hint-icon" />
                </a-tooltip>
              </div>
              <a-switch v-model:checked="form.extractFiles" size="small" />
            </div>
          </div>

          <div class="config-section">
            <div class="section-head">
              <div>
                <MessageOutlined class="section-icon welcome" />
                <strong>开场白</strong>
              </div>
            </div>
            <VariableTextarea
              :value="form.welcomeText"
              :rows="3"
              :groups="welcomeVariableGroups"
              placeholder="你好！我是你的智能体助手，你可以问我任何问题。输入 / 可选择变量"
              @update:value="(value) => (form.welcomeText = value)"
            />
            <div class="quick-guide">
              <div class="quick-guide-head">
                <span>右侧推荐内容</span>
              </div>
              <p class="quick-guide-desc">先创建场景，再添加该场景下的推荐内容；运行页按场景展示，点击后只填入输入框。</p>
              <RecommendationSceneEditor v-model="form.recommendationScenes" />
            </div>
          </div>
        </section>

        <section class="preview-panel">
          <div class="panel-title">
            <MessageOutlined class="title-icon blue" />
            <strong>调试预览</strong>
            <a-button v-if="messages.length" class="clear-btn" type="text" size="small" @click="clearDebug">清空</a-button>
          </div>

          <div ref="messageListRef" class="preview-stage">
            <div v-if="displayWelcomeText" class="bubble assistant welcome">{{ displayWelcomeText }}</div>
            <template v-for="(item, index) in messages" :key="index">
              <div :class="['bubble', item.role, { failed: item.failed, 'has-charts': item.chartOutputs?.length }]">
                <!-- eslint-disable-next-line vue/no-v-html --><!-- assistant 内容经 xss(md.render()) 过滤后展示 -->
                <div
                  v-if="item.role === 'assistant' && item.content"
                  class="markdown-body"
                  v-html="renderMarkdown(item.content)"
                ></div>
                <p v-else-if="item.content || item.failed">{{ item.content || '（无输出）' }}</p>
                <div v-if="item.chartOutputs?.length" class="message-chart-list">
                  <EChartsOutputPreview
                    v-for="(chartOutput, chartIndex) in item.chartOutputs"
                    :key="chartIndex"
                    :output="chartOutput"
                  />
                </div>
                <span v-if="item.meta" class="bubble-meta">{{ item.meta }}</span>
              </div>
            </template>
            <div v-if="!messages.length && previewQuickQuestions.length" class="quick-suggestions">
              <button
                v-for="question in previewQuickQuestions"
                :key="question"
                type="button"
                class="quick-bubble"
                @click="useQuickQuestion(question)"
              >
                {{ question }}
              </button>
            </div>
            <div v-if="debugRunning" class="bubble assistant pending"><LoadingOutlined /> 正在思考...</div>
          </div>

          <div class="debug-composer">
            <div v-if="form.variables.length" class="debug-vars-panel">
              <div v-for="variable in form.variables" :key="variable.id || variable.key" class="debug-var-field">
                <label>
                  {{ variable.label || variable.key }}
                  <i v-if="variable.required">*</i>
                </label>
                <a-switch
                  v-if="variable.type === VariableInputEnum.switch"
                  :checked="!!debugVariableValues[variable.key]"
                  :disabled="debugRunning"
                  size="small"
                  @change="debugVariableValues[variable.key] = $event"
                />
                <a-input-number
                  v-else-if="variable.type === VariableInputEnum.numberInput"
                  v-model:value="debugVariableValues[variable.key]"
                  :min="variable.min"
                  :max="variable.max"
                  :disabled="debugRunning"
                  style="width: 100%"
                />
                <a-select
                  v-else-if="variable.type === VariableInputEnum.select"
                  v-model:value="debugVariableValues[variable.key]"
                  :options="variableOptions(variable)"
                  :disabled="debugRunning"
                  style="width: 100%"
                />
                <a-select
                  v-else-if="variable.type === VariableInputEnum.multipleSelect"
                  v-model:value="debugVariableValues[variable.key]"
                  mode="multiple"
                  :options="variableOptions(variable)"
                  :disabled="debugRunning"
                  style="width: 100%"
                />
                <a-input-password
                  v-else-if="variable.type === VariableInputEnum.password"
                  v-model:value="debugVariableValues[variable.key]"
                  :maxlength="variable.maxLength"
                  :disabled="debugRunning"
                />
                <a-date-picker
                  v-else-if="variable.type === VariableInputEnum.timePointSelect"
                  v-model:value="debugVariableValues[variable.key]"
                  show-time
                  value-format="YYYY-MM-DD HH:mm:ss"
                  :disabled="debugRunning"
                  style="width: 100%"
                />
                <a-range-picker
                  v-else-if="variable.type === VariableInputEnum.timeRangeSelect"
                  v-model:value="debugVariableValues[variable.key]"
                  show-time
                  value-format="YYYY-MM-DD HH:mm:ss"
                  :disabled="debugRunning"
                  style="width: 100%"
                />
                <a-textarea
                  v-else-if="variable.type === VariableInputEnum.textarea"
                  v-model:value="debugVariableValues[variable.key]"
                  :rows="2"
                  :maxlength="variable.maxLength"
                  :disabled="debugRunning"
                />
                <a-input
                  v-else
                  v-model:value="debugVariableValues[variable.key]"
                  :maxlength="variable.maxLength"
                  :disabled="debugRunning"
                />
              </div>
            </div>
            <a-textarea
              v-model:value="debugInput"
              :rows="2"
              :disabled="debugRunning"
              placeholder="发送消息"
              @keydown.enter="onDebugEnter"
            />
            <button
              v-if="form.extractFiles"
              type="button"
              class="attach-btn"
              title="上传附件"
              :disabled="debugUploading || debugRunning"
              @click="openDebugFilePicker"
            >
              <LoadingOutlined v-if="debugUploading" />
              <PaperClipOutlined v-else />
            </button>
            <input
              ref="debugFileInputRef"
              class="debug-file-input"
              type="file"
              multiple
              @change="handleDebugFileChange"
            />
            <button
              type="button"
              class="send-btn"
              :disabled="debugRunning || debugUploading || !debugInput.trim()"
              title="发送"
              @click="runDebug"
            >
              <LoadingOutlined v-if="debugRunning" />
              <SendOutlined v-else />
            </button>
            <div v-if="debugFiles.length" class="debug-file-list">
              <span v-for="file in debugFiles" :key="file.url" class="debug-file-chip" :title="file.url">
                <FileTextOutlined />
                {{ file.name }}
                <button type="button" title="移除附件" @click="removeDebugFile(file.url)">×</button>
              </span>
            </div>
          </div>
        </section>
      </main>
    </a-spin>

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
          <strong>{{ pickerTitle }}</strong>
          <span>{{ pickerSubtitle }}</span>
        </div>
      </template>
      <div class="picker-shell">
        <div v-if="pickerResource === 'tool' || pickerResource === 'skill'" class="picker-tabs">
          <button
            v-for="tab in currentPickerTabs"
            :key="tab.key"
            type="button"
            :class="{ active: isPickerTabActive(tab.key) }"
            @click="changePickerTab(tab.key)"
          >
            {{ tab.label }}
          </button>
        </div>
        <div class="picker-toolbar">
          <a-input
            v-model:value="pickerKeyword"
            allow-clear
            :placeholder="pickerSearchPlaceholder"
          >
            <template #prefix><SearchOutlined /></template>
          </a-input>
        </div>

        <div class="picker-list">
          <button
            v-for="item in filteredPickerItems"
            :key="pickerItemKey(item)"
            type="button"
            :class="['picker-row', { selected: isTempSelected(item) }]"
            @click="togglePickerItem(item)"
          >
            <a-checkbox :checked="isTempSelected(item)" @click.stop @change="togglePickerItem(item)" />
            <span :class="['item-avatar', iconClass(item.kind)]">{{ avatarText(item.name, item.kind) }}</span>
            <span class="picker-info">
              <strong>{{ item.name }}</strong>
              <em>{{ item.description || item.typeLabel }}</em>
            </span>
            <span :class="['type-badge', iconClass(item.kind)]">{{ item.badge }}</span>
          </button>
          <div v-if="!filteredPickerItems.length" class="picker-empty">暂无可选择项</div>
        </div>

        <div class="picker-footer">
          <span>已选择 {{ tempSelectionCount }} 项</span>
          <div>
            <a-button @click="cancelPicker">取消</a-button>
            <a-button type="primary" @click="confirmPicker">确认选择</a-button>
          </div>
        </div>
      </div>
    </a-modal>

    <a-modal
      v-model:open="variableModalVisible"
      :title="editingVariableIndex === null ? '新增全局变量' : '编辑全局变量'"
      :width="460"
      destroy-on-close
      @ok="saveVariable"
    >
      <div class="variable-form">
        <div class="form-item">
          <label>变量名称 <em>*</em></label>
          <a-input v-model:value="variableDraft.label" placeholder="展示给用户的名称，如：姓名" />
        </div>
        <div class="form-item">
          <label>变量 key <em>*</em></label>
          <a-input v-model:value="variableDraft.key" placeholder="引用 key，如：user_name" />
        </div>
        <div class="form-item">
          <label>输入类型</label>
          <a-select v-model:value="variableDraft.type" style="width: 100%" :options="variableTypeOptions" />
        </div>
        <div class="form-item">
          <label>描述</label>
          <a-input v-model:value="variableDraft.description" placeholder="变量用途说明（可选）" />
        </div>
        <div v-if="isChoiceVariable(variableDraft.type)" class="form-item">
          <label>选项（每行一个）</label>
          <a-textarea v-model:value="variableDraftEnums" :rows="3" placeholder="选项1&#10;选项2" />
        </div>
        <div v-if="variableDraft.type === VariableInputEnum.numberInput" class="form-item inline">
          <label>数值范围</label>
          <span class="range-inputs">
            <a-input-number v-model:value="variableDraft.min" size="small" placeholder="最小" />
            <a-input-number v-model:value="variableDraft.max" size="small" placeholder="最大" />
          </span>
        </div>
        <div v-if="variableDraft.type === VariableInputEnum.input || variableDraft.type === VariableInputEnum.textarea" class="form-item inline">
          <label>最大长度</label>
          <a-input-number v-model:value="variableDraft.maxLength" size="small" :min="1" placeholder="不限" />
        </div>
        <div v-if="variableDraft.type !== VariableInputEnum.switch" class="form-item">
          <label>默认值</label>
          <a-input v-model:value="variableDraft.defaultValue" placeholder="运行前的初始值（可选）" />
        </div>
        <div class="form-item inline">
          <label>必填</label>
          <a-switch v-model:checked="variableDraft.required" size="small" />
        </div>
      </div>
    </a-modal>

    <PromptDebugDrawer
      v-model:open="promptDebugOpen"
      :app-id="workflowApp?.id || ''"
      :prompt="form.systemPrompt"
      :model="form.model"
      :variables="form.variables"
      :temperature="form.temperature"
      :max-token="form.maxToken"
      :top-p="form.topP"
      :stop-sign="form.stopSign"
      :response-format="form.responseFormat"
      :json-schema="form.jsonSchema"
      @apply="(prompt) => (form.systemPrompt = prompt)"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router';
import { Modal, message } from 'ant-design-vue';
import MarkdownIt from 'markdown-it';
import xss, { getDefaultWhiteList } from 'xss';
import { useUserStore } from '/@/store/modules/user';
import { uploadImg } from '/@/api/sys/upload';
import { getFileAccessHttpUrl } from '/@/utils/common/compUtils';
import { stopProtocolLinkAtCjkPunctuation } from '../../peopleCenter/utils/markdownLinkify';
import {
  ArrowLeftOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  LinkOutlined,
  LoadingOutlined,
  MessageOutlined,
  PaperClipOutlined,
  PlusOutlined,
  QuestionCircleOutlined,
  RobotOutlined,
  SaveOutlined,
  SearchOutlined,
  SendOutlined,
  SettingOutlined,
  ToolOutlined,
} from '@ant-design/icons-vue';
import {
  queryBuiltinWorkflowTools,
  queryWorkflowApp,
  queryWorkflowAppById,
  queryWorkflowAppPage,
  queryWorkflowDefinition,
  queryWorkflowModelOptions,
  saveWorkflowDefinition,
  type AiWorkflowApp,
  type WorkflowRunResponse,
} from '../api/workflow.api';
import { runWorkflowDefinitionStream } from '../api/workflowStream';
import { createDebugMessage, type DebugMessage } from './debugMessage';
import VariableTextarea from '../editor/components/VariableTextarea.vue';
import PromptDebugDrawer from '../components/PromptDebugDrawer.vue';
import EChartsOutputPreview from '../editor/components/EChartsOutputPreview.vue';
import RecommendationSceneEditor from '../shared/RecommendationSceneEditor.vue';
import { VariableInputEnum, VariableInputLabelMap, WorkflowIOValueTypeEnum } from '../core/constants';
import type { VariableItemType } from '../core/type';
import { buildTextareaVariableOptions, getNanoid } from '../core/utils';
import { getAgentSkillList, getSkillMarketList } from '../api/skill.api';
import { loadWorkflowSelectableKnowledgeOptions } from '../utils/knowledgeSelection';
import { parsePersistedGraph, serializeGraph } from '../core/compiler';
import { TOOL_KINDS, getAiAppKind, getAiAppKindLabelByKind } from '../shared/agentApp';
import { extractChartOutputs } from '../shared/chartOutput';
import type { WorkflowChartOutput } from '../shared/chartOutput';
import { buildMessageHistories, buildWorkflowRuntimeVariables, interpolateWorkflowText } from '../shared/runtimeVariables';
import { flattenRecommendationScenes } from '../shared/recommendationScenes';
import { getAiAppDraftPreviewRoute } from '../shared/runtimeRoute';
import {
  agentFormToGraph,
  createDefaultAgentForm,
  graphToAgentForm,
  legacyConfigToAgentForm,
  type AgentSkillSource,
  type AgentFormType,
  type AgentSkillRef,
  type AgentToolRef,
} from './form';

defineOptions({ name: 'WorkflowAgentConfigPage' });

const route = useRoute();
const router = useRouter();
const userStore = useUserStore();

const workflowAppId = computed(() => String(route.query.workflowAppId || ''));
const appInfoId = computed(() => String(route.query.appInfoId || route.query.appId || ''));

const loading = ref(false);
const saving = ref(false);
const workflowApp = ref<AiWorkflowApp | null>(null);
const promptDebugOpen = ref(false);

const form = reactive<AgentFormType>(createDefaultAgentForm());
const savedSnapshot = ref('');
const debugVariableValues = reactive<Record<string, any>>({});

const modelOptions = ref<{ label: string; value: string }[]>([]);
const modelLoading = ref(false);
const knowledgeOptions = ref<{ id: string; name: string }[]>([]);
const knowledgeLoading = ref(false);
const toolOptions = ref<{ id: string; name: string; kind: string; description?: string }[]>([]);
type SkillOption = { id: string; name: string; description?: string; source: AgentSkillSource };
const mySkillOptions = ref<SkillOption[]>([]);
const systemSkillOptions = ref<SkillOption[]>([]);
const showKnowledgeParams = ref(false);

const hasUnsavedChanges = computed(() => serializeGraph(agentFormToGraph(form)) !== savedSnapshot.value);

const modelTemperature = computed({
  get: () => form.temperature ?? 1,
  set: (value: number) => {
    form.temperature = value;
  },
});

const searchModeLabel = computed(() => {
  const value = form.searchMode || 'embedding';
  return value === 'fullTextRecall' ? '全文检索' : value === 'mixedRecall' ? '混合检索' : '语义检索';
});

/** 知识库选择与表单 datasets 的桥接 */
const selectedDatasetIds = computed({
  get: () => form.datasets.map((item) => item.datasetId),
  set: (ids: string[]) => {
    form.datasets = ids.map((id) => ({
      datasetId: id,
      name: knowledgeOptions.value.find((item) => item.id === id)?.name,
    }));
  },
});

const variableTypeOptions = Object.entries(VariableInputLabelMap).map(([value, label]) => ({ value, label }));
const VARIABLE_VALUE_TYPE: Record<string, WorkflowIOValueTypeEnum> = {
  [VariableInputEnum.input]: WorkflowIOValueTypeEnum.string,
  [VariableInputEnum.textarea]: WorkflowIOValueTypeEnum.string,
  [VariableInputEnum.numberInput]: WorkflowIOValueTypeEnum.number,
  [VariableInputEnum.select]: WorkflowIOValueTypeEnum.string,
  [VariableInputEnum.multipleSelect]: WorkflowIOValueTypeEnum.arrayString,
  [VariableInputEnum.switch]: WorkflowIOValueTypeEnum.boolean,
  [VariableInputEnum.password]: WorkflowIOValueTypeEnum.string,
  [VariableInputEnum.timePointSelect]: WorkflowIOValueTypeEnum.string,
  [VariableInputEnum.timeRangeSelect]: WorkflowIOValueTypeEnum.arrayString,
};

const variableModalVisible = ref(false);
const editingVariableIndex = ref<number | null>(null);
const variableDraftEnums = ref('');
const variableDraft = reactive<VariableItemType>(emptyVariableDraft());

type PickerResource = 'skill' | 'tool' | 'knowledge';
type PickerTab = 'mine' | 'system';
type PickerItem = {
  id: string;
  name: string;
  kind: string;
  description?: string;
  typeLabel: string;
  badge: string;
  skillSource?: AgentSkillSource;
};

const toolPickerTabs: { key: PickerTab; label: string }[] = [
  { key: 'mine', label: '我的工具' },
  { key: 'system', label: '系统工具' },
];
const skillPickerTabs: { key: PickerTab; label: string }[] = [
  { key: 'mine', label: '我的技能' },
  { key: 'system', label: '系统技能' },
];
const pickerOpen = ref(false);
const pickerResource = ref<PickerResource>('skill');
const activeToolTab = ref<PickerTab>('mine');
const activeSkillTab = ref<PickerTab>('mine');
const pickerKeyword = ref('');
const tempTools = ref<AgentToolRef[]>([]);
const tempSkills = ref<AgentSkillRef[]>([]);
const tempDatasetIds = ref<string[]>([]);

const pickerTitle = computed(() => {
  if (pickerResource.value === 'skill') return '选择 Skill';
  if (pickerResource.value === 'knowledge') return '选择知识库';
  return '选择工具';
});

const pickerSubtitle = computed(() => {
  if (pickerResource.value === 'skill') return '选择可挂载到当前 Agent 的 Skill 能力';
  if (pickerResource.value === 'knowledge') return '选择当前 Agent 可检索的知识库';
  return '系统工具与我的工具可同时挂载';
});

const pickerSearchPlaceholder = computed(() => {
  if (pickerResource.value === 'skill') return '搜索 Skill 名称';
  if (pickerResource.value === 'knowledge') return '搜索知识库名称';
  return '搜索工具名称';
});

const currentPickerTabs = computed(() => (pickerResource.value === 'skill' ? skillPickerTabs : toolPickerTabs));

const pickerItems = computed<PickerItem[]>(() => {
  if (pickerResource.value === 'skill') {
    const options = activeSkillTab.value === 'system' ? systemSkillOptions.value : mySkillOptions.value;
    return options.map((item) => ({
      id: item.id,
      name: item.name,
      kind: 'skill',
      description: item.description,
      typeLabel: 'Skill',
      badge: skillSourceLabel(item.source),
      skillSource: item.source,
    }));
  }
  if (pickerResource.value === 'knowledge') {
    return knowledgeOptions.value.map((item) => ({
      id: item.id,
      name: item.name,
      kind: 'knowledge',
      typeLabel: '知识库',
      badge: '知识库',
    }));
  }
  const systemOnly = activeToolTab.value === 'system';
  return toolOptions.value
    .filter((item) => (systemOnly ? item.kind === 'system' : item.kind !== 'system'))
    .map((item) => ({
      id: item.id,
      name: item.name,
      kind: item.kind,
      description: item.description,
      typeLabel: toolKindLabel(item.kind),
      badge: badgeLabel(item.kind),
    }));
});

const filteredPickerItems = computed(() => {
  const keyword = pickerKeyword.value.trim().toLowerCase();
  return pickerItems.value.filter((item) => {
    const matchKeyword =
      !keyword ||
      item.name.toLowerCase().includes(keyword) ||
      (item.description || '').toLowerCase().includes(keyword);
    return matchKeyword;
  });
});

const tempSelectionCount = computed(() => {
  if (pickerResource.value === 'skill') return tempSkills.value.length;
  if (pickerResource.value === 'knowledge') return tempDatasetIds.value.length;
  return tempTools.value.length;
});

const previewQuickQuestions = computed(() => {
  return flattenRecommendationScenes(form.recommendationScenes).slice(0, 5);
});

/** 调试预览的当前时间，格式与后端 workflow_engine.cTime 一致：YYYY-MM-DD HH:MM:SS */
function formatNow() {
  const pad = (n: number) => String(n).padStart(2, '0');
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

const previewRuntimeVariables = computed(() =>
  buildWorkflowRuntimeVariables({
    userInfo: userStore.getUserInfo,
    histories: buildMessageHistories(messages.value),
    extra: {
      ...(collectVariableValues(false) || {}),
      cTime: formatNow(),
      appId: workflowAppId.value || appInfoId.value || '',
      // 本轮回复 ID：后端每轮 uuid4，预览给一个稳定占位便于渲染
      responseChatItemId: 'preview-reply',
    },
  })
);
const displayWelcomeText = computed(() => interpolateWorkflowText(form.welcomeText, previewRuntimeVariables.value));

/** 开场白/提示词可引用：自定义全局变量 + 常用系统变量 */
const welcomeVariables = computed(() => {
  const customKeys = new Set((form.variables || []).map((item) => item.key).filter(Boolean));
  return buildTextareaVariableOptions({ variables: form.variables }).filter(
    (item) => customKeys.has(item.value) || ['username', 'realname', 'cTime'].includes(item.value)
  );
});
const welcomeVariableGroups = computed(() => [
  {
    label: '全局变量',
    options: welcomeVariables.value.map((item) => ({ ...item, detail: item.value })),
  },
]);

function toolKindLabel(kind: string) {
  return kind === 'system' ? '系统工具' : getAiAppKindLabelByKind(kind as any);
}

function badgeLabel(kind: string) {
  if (kind === 'system') return '接口';
  if (kind === 'workflowTool') return '自定义';
  if (kind === 'httpToolSet') return '接口';
  if (kind === 'mcpToolSet') return 'MCP';
  return '工具';
}

function iconClass(kind: string) {
  if (kind === 'skill') return 'skill';
  if (kind === 'knowledge') return 'knowledge';
  if (kind === 'system') return 'system';
  if (kind === 'workflowTool') return 'custom';
  if (kind === 'httpToolSet') return 'http';
  if (kind === 'mcpToolSet') return 'mcp';
  return 'tool';
}

function avatarText(name: string, kind: string) {
  if (kind === 'httpToolSet' || name.toLowerCase().includes('http')) return 'HTTP';
  if (kind === 'system') return 'DB';
  if (kind === 'knowledge') return 'KB';
  return (name || 'T').slice(0, 2);
}

function normalizeSkillSource(source?: AgentSkillSource) {
  return source === 'system' ? 'system' : 'mine';
}

function skillSourceLabel(source?: AgentSkillSource) {
  return normalizeSkillSource(source) === 'system' ? '系统技能' : '我的技能';
}

function skillKey(skill: AgentSkillRef) {
  return `${normalizeSkillSource(skill.source)}:${skill.skillId}`;
}

function pickerItemKey(item: PickerItem) {
  return item.skillSource ? `${item.skillSource}:${item.id}` : item.id;
}

function openPicker(resource: PickerResource) {
  pickerResource.value = resource;
  activeToolTab.value = 'mine';
  activeSkillTab.value = 'mine';
  pickerKeyword.value = '';
  tempTools.value = form.selectedTools.map((item) => ({ ...item }));
  tempSkills.value = form.skills.map((item) => ({ ...item }));
  tempDatasetIds.value = form.datasets.map((item) => item.datasetId);
  pickerOpen.value = true;
}

function isPickerTabActive(tab: PickerTab) {
  return pickerResource.value === 'skill' ? activeSkillTab.value === tab : activeToolTab.value === tab;
}

function changePickerTab(tab: PickerTab) {
  if (pickerResource.value === 'skill') {
    activeSkillTab.value = tab;
  } else {
    activeToolTab.value = tab;
  }
  pickerKeyword.value = '';
}

function cancelPicker() {
  pickerOpen.value = false;
}

function confirmPicker() {
  if (pickerResource.value === 'skill') {
    form.skills = tempSkills.value.map((item) => ({ ...item }));
  } else if (pickerResource.value === 'knowledge') {
    selectedDatasetIds.value = tempDatasetIds.value;
  } else {
    form.selectedTools = tempTools.value.map((item) => ({ ...item }));
  }
  pickerOpen.value = false;
}

function isTempSelected(item: PickerItem) {
  if (pickerResource.value === 'skill') {
    const source = normalizeSkillSource(item.skillSource);
    return tempSkills.value.some((skill) => skill.skillId === item.id && normalizeSkillSource(skill.source) === source);
  }
  if (pickerResource.value === 'knowledge') {
    return tempDatasetIds.value.includes(item.id);
  }
  return tempTools.value.some((tool) => tool.id === item.id);
}

function togglePickerItem(item: PickerItem) {
  if (pickerResource.value === 'skill') {
    const source = normalizeSkillSource(item.skillSource);
    tempSkills.value = isTempSelected(item)
      ? tempSkills.value.filter(
          (skill) => !(skill.skillId === item.id && normalizeSkillSource(skill.source) === source)
        )
      : [...tempSkills.value, { skillId: item.id, name: item.name, description: item.description, source }];
    return;
  }
  if (pickerResource.value === 'knowledge') {
    tempDatasetIds.value = isTempSelected(item)
      ? tempDatasetIds.value.filter((id) => id !== item.id)
      : [...tempDatasetIds.value, item.id];
    return;
  }
  tempTools.value = isTempSelected(item)
    ? tempTools.value.filter((tool) => tool.id !== item.id)
    : [...tempTools.value, { id: item.id, name: item.name, kind: item.kind, description: item.description }];
}

function removeSkill(id: string, source?: AgentSkillSource) {
  const normalized = normalizeSkillSource(source);
  form.skills = form.skills.filter((item) => !(item.skillId === id && normalizeSkillSource(item.source) === normalized));
}

function removeTool(id: string) {
  form.selectedTools = form.selectedTools.filter((item) => item.id !== id);
}

function removeDataset(id: string) {
  form.datasets = form.datasets.filter((item) => item.datasetId !== id);
}

function emptyVariableDraft(): VariableItemType {
  return {
    id: getNanoid(),
    key: '',
    label: '',
    type: VariableInputEnum.input,
    required: false,
  };
}

function isChoiceVariable(type: VariableInputEnum) {
  return type === VariableInputEnum.select || type === VariableInputEnum.multipleSelect;
}

function openVariableModal(index?: number) {
  editingVariableIndex.value = index ?? null;
  const source = index != null ? form.variables[index] : emptyVariableDraft();
  Object.assign(variableDraft, emptyVariableDraft(), JSON.parse(JSON.stringify(source)));
  variableDraftEnums.value = (source.enums || []).map((item) => item.value).join('\n');
  variableModalVisible.value = true;
}

function saveVariable() {
  const key = variableDraft.key.trim();
  const label = variableDraft.label.trim();
  if (!key || !label) {
    message.warning('请填写变量名称与 key');
    return;
  }
  const duplicated = form.variables.some((item, index) => item.key === key && index !== editingVariableIndex.value);
  if (duplicated) {
    message.warning('变量 key 重复');
    return;
  }
  const record: VariableItemType = {
    ...JSON.parse(JSON.stringify(variableDraft)),
    key,
    label,
    valueType: VARIABLE_VALUE_TYPE[variableDraft.type] || WorkflowIOValueTypeEnum.string,
    enums: isChoiceVariable(variableDraft.type)
      ? variableDraftEnums.value
          .split('\n')
          .map((line) => line.trim())
          .filter(Boolean)
          .map((value) => ({ label: value, value }))
      : undefined,
  };
  if (editingVariableIndex.value != null) {
    form.variables.splice(editingVariableIndex.value, 1, record);
  } else {
    form.variables.push(record);
  }
  debugVariableValues[record.key] = initialVariableValue(record);
  variableModalVisible.value = false;
  syncDebugVariableValues();
}

function removeVariable(index: number) {
  form.variables.splice(index, 1);
  syncDebugVariableValues();
}

function variableOptions(variable: { enums?: { label?: string; value: string }[] }) {
  return (variable.enums || []).map((item) => ({ label: item.label || item.value, value: item.value }));
}

function initialVariableValue(variable: { type?: string; defaultValue?: any }) {
  if (variable.defaultValue !== undefined) return variable.defaultValue;
  if (variable.type === VariableInputEnum.switch) return false;
  if (variable.type === VariableInputEnum.multipleSelect || variable.type === VariableInputEnum.timeRangeSelect) return [];
  return undefined;
}

function syncDebugVariableValues() {
  const keys = new Set((form.variables || []).map((item) => item.key));
  Object.keys(debugVariableValues).forEach((key) => {
    if (!keys.has(key)) delete debugVariableValues[key];
  });
  (form.variables || []).forEach((variable) => {
    if (!Object.prototype.hasOwnProperty.call(debugVariableValues, variable.key)) {
      debugVariableValues[variable.key] = initialVariableValue(variable);
    }
  });
}

function isMissingVariableValue(value: any) {
  if (Array.isArray(value)) return value.length === 0;
  return value === undefined || value === null || (typeof value === 'string' && !value.trim());
}

function collectVariableValues(validateRequired = true) {
  if (validateRequired) {
    const missing = form.variables.find((variable) => variable.required && isMissingVariableValue(debugVariableValues[variable.key]));
    if (missing) {
      message.warning(`请填写全局变量：${missing.label || missing.key}`);
      return null;
    }
  }
  return Object.fromEntries(
    (form.variables || [])
      .map((variable) => [variable.key, debugVariableValues[variable.key]] as const)
      .filter(([, value]) => value !== undefined)
  );
}

function useQuickQuestion(question: string) {
  debugInput.value = question;
}

// ---------- 加载 ----------

async function loadApp() {
  loading.value = true;
  try {
    workflowApp.value = workflowAppId.value
      ? await queryWorkflowAppById(workflowAppId.value)
      : await queryWorkflowApp({ appInfoId: appInfoId.value, aiAppType: 'chatAgent' });
    const definition = await queryWorkflowDefinition({
      appInfoId: workflowApp.value?.appInfoId || appInfoId.value || undefined,
      appId: workflowApp.value?.id,
    });
    const { graph } = parsePersistedGraph(definition?.draftJson);
    const parsedForm =
      graphToAgentForm(graph) ||
      legacyConfigToAgentForm(workflowApp.value?.configJson) ||
      createDefaultAgentForm();
    Object.assign(form, parsedForm);
    syncDebugVariableValues();
    savedSnapshot.value = graphToAgentForm(graph)
      ? serializeGraph(agentFormToGraph(parsedForm))
      : '';
  } catch (error) {
    console.error('load chat agent failed', error);
    message.error('对话 Agent 加载失败');
  } finally {
    loading.value = false;
  }
}

async function loadOptions() {
  modelLoading.value = true;
  queryWorkflowModelOptions()
    .then((options) => {
      modelOptions.value = (Array.isArray(options) ? options : []).map((item) => ({
        label: item.label || item.value,
        value: item.value,
      }));
    })
    .catch(() => (modelOptions.value = []))
    .finally(() => (modelLoading.value = false));

  knowledgeLoading.value = true;
  (async () => {
    knowledgeOptions.value = await loadWorkflowSelectableKnowledgeOptions(100);
  })()
    .catch(() => (knowledgeOptions.value = []))
    .finally(() => (knowledgeLoading.value = false));

  Promise.all([
    queryBuiltinWorkflowTools().catch(() => []),
    queryWorkflowAppPage({ pageNo: 1, pageSize: 100, scope: 'all' }).catch(() => ({ records: [] })),
  ]).then(([builtin, page]) => {
    const systemTools = (Array.isArray(builtin) ? builtin : []).map((item) => ({
      id: String(item.id),
      name: String(item.name || ''),
      kind: 'system',
      description: item.description,
    }));
    const myTools = ((page as any)?.records || [])
      .filter((item: any) => TOOL_KINDS.includes(getAiAppKind(item)))
      .map((item: any) => ({
        id: String(item.id),
        name: String(item.name || ''),
        kind: getAiAppKind(item) as string,
        description: item.description,
      }));
    toolOptions.value = [...systemTools, ...myTools];
  });

  getAgentSkillList({ source: 'personal' })
    .then((list) => {
      mySkillOptions.value = (Array.isArray(list) ? list : [])
        .filter(
          (item: any) =>
            item.type !== 'folder' &&
            String(item.source || '').toLowerCase() === 'personal' &&
            item.creationStatus !== 'creating' &&
            item.creationStatus !== 'failed'
        )
        .map((item: any) => ({
          id: String(item.id),
          name: String(item.name),
          description: item.description,
          source: 'mine' as AgentSkillSource,
        }));
    })
    .catch(() => (mySkillOptions.value = []));

  getSkillMarketList()
    .then((list) => {
      systemSkillOptions.value = (Array.isArray(list) ? list : []).map((item) => ({
        id: String(item.id),
        name: String(item.name),
        description: item.description,
        source: 'system' as AgentSkillSource,
      }));
    })
    .catch(() => (systemSkillOptions.value = []));
}

// ---------- 保存 / 发布 ----------

function validateForm() {
  if (!form.model) {
    message.warning('请先选择对话模型');
    return false;
  }
  return true;
}

async function saveDraft(silent: unknown = false): Promise<boolean> {
  const notify = silent !== true;
  if (!validateForm()) return false;
  saving.value = true;
  try {
    const json = serializeGraph(agentFormToGraph(form));
    await saveWorkflowDefinition({
      appId: workflowApp.value?.id,
      appInfoId: workflowApp.value?.appInfoId || appInfoId.value || undefined,
      workflowJson: json,
    });
    savedSnapshot.value = json;
    if (notify) message.success({ content: '草稿已保存', key: 'agent-draft-save' });
    return true;
  } catch (error) {
    console.error('save agent draft failed', error);
    if (notify) message.error({ content: '保存失败', key: 'agent-draft-save' });
    return false;
  } finally {
    saving.value = false;
  }
}

async function handleSaveDraft() {
  await saveDraft(false);
}

// 预览：保存当前草稿后，新窗口打开运行页用最新草稿运行（行为同发布审核预览）
const previewing = ref(false);
async function handlePreview() {
  if (!workflowApp.value?.id) {
    message.warning('请先创建应用');
    return;
  }
  previewing.value = true;
  try {
    const ok = await saveDraft(true);
    if (!ok) {
      message.error('草稿保存失败，无法预览');
      return;
    }
    const target = getAiAppDraftPreviewRoute(workflowApp.value.id);
    window.open(router.resolve(target).href, '_blank');
  } finally {
    previewing.value = false;
  }
}

// ---------- 调试对话 ----------

type DebugFile = { name: string; url: string };

const messages = ref<(DebugMessage & { chartOutputs?: WorkflowChartOutput[] })[]>([]);
const debugInput = ref('');
const debugRunning = ref(false);
const debugUploading = ref(false);
const debugFiles = ref<DebugFile[]>([]);
const debugFileInputRef = ref<HTMLInputElement>();
const messageListRef = ref<HTMLElement>();
const md = new MarkdownIt({ html: false, linkify: true, breaks: true });
stopProtocolLinkAtCjkPunctuation(md);
const markdownWhiteList = {
  ...getDefaultWhiteList(),
  a: [...getDefaultWhiteList().a, 'rel'],
};

function renderMarkdown(text: string) {
  return xss(md.render(text || ''), { whiteList: markdownWhiteList });
}

function onDebugEnter(event: KeyboardEvent) {
  if (event.shiftKey) return;
  event.preventDefault();
  runDebug();
}

function scrollDebugToBottom() {
  nextTick(() => {
    messageListRef.value?.scrollTo({ top: messageListRef.value.scrollHeight, behavior: 'smooth' });
  });
}

function openDebugFilePicker() {
  if (debugUploading.value || debugRunning.value) return;
  debugFileInputRef.value?.click();
}

function normalizeUploadUrl(response: any): string {
  const result = response?.result ?? response?.data?.result;
  if (typeof result === 'string') return getFileAccessHttpUrl(result.trim());
  const data = result || response?.data || response || {};
  const candidates = [data.url, data.fileUrl, data.path, response?.message, response?.data?.message]
    .map((item) => String(item || '').trim())
    .filter(Boolean);
  const rawUrl = candidates.find((item) => /^https?:\/\//.test(item) || item.startsWith('/') || item.includes('.')) || '';
  return rawUrl ? getFileAccessHttpUrl(rawUrl) : '';
}

async function handleDebugFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = '';
  if (!files.length) return;
  debugUploading.value = true;
  try {
    for (const file of files) {
      const response = await uploadImg({ file }, () => {});
      const url = normalizeUploadUrl(response);
      if (!url) throw new Error(`${file.name} 上传后未返回文件地址`);
      if (!debugFiles.value.some((item) => item.url === url)) {
        debugFiles.value.push({ name: file.name, url });
      }
    }
    message.success('附件已上传');
  } catch (error: any) {
    message.error(error?.message || '附件上传失败');
  } finally {
    debugUploading.value = false;
  }
}

function removeDebugFile(url: string) {
  debugFiles.value = debugFiles.value.filter((file) => file.url !== url);
}

watch(
  () => form.extractFiles,
  (enabled) => {
    if (!enabled) debugFiles.value = [];
  },
);

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function appendDebugTypewriter(item: DebugMessage, text: string) {
  const chunk = String(text || '');
  if (!chunk) return;
  for (let i = 0; i < chunk.length; i += 2) {
    item.content += chunk.slice(i, i + 2);
    scrollDebugToBottom();
    await sleep(16);
  }
}

async function reconcileDebugFinalMessage(item: DebugMessage, finalText: string) {
  const target = String(finalText || '');
  if (!target) return;
  if (!item.content) {
    await appendDebugTypewriter(item, target);
    return;
  }
  if (target.startsWith(item.content)) {
    await appendDebugTypewriter(item, target.slice(item.content.length));
    return;
  }
  if (item.content.trim() === target.trim()) {
    item.content = target;
    return;
  }
  item.content = '';
  await appendDebugTypewriter(item, target);
}

async function runDebug() {
  const input = debugInput.value.trim();
  if (!input || debugRunning.value) return;
  if (!validateForm()) return;
  const runtimeVariables = collectVariableValues();
  if (!runtimeVariables) return;
  const histories = buildMessageHistories(messages.value);
  const variables = buildWorkflowRuntimeVariables({
    userInfo: userStore.getUserInfo,
    histories,
    extra: {
      ...runtimeVariables,
      ...(form.extractFiles && debugFiles.value.length ? { userFiles: debugFiles.value.map((file) => file.url) } : {}),
    },
  });
  const sentFileCount = form.extractFiles ? debugFiles.value.length : 0;
  messages.value.push({ role: 'user', content: input, meta: sentFileCount ? `附件 ${sentFileCount} 个` : undefined });
  debugInput.value = '';
  debugFiles.value = [];
  debugRunning.value = true;
  const assistantMessage = createDebugMessage();
  messages.value.push(assistantMessage);
  scrollDebugToBottom();
  let finalResult: WorkflowRunResponse | null = null;
  let typewriterTask = Promise.resolve();
  try {
    await runWorkflowDefinitionStream(
      '/agent-api/workflow/definition/debugStream',
      {
        appId: workflowApp.value?.id,
        appInfoId: workflowApp.value?.appInfoId || appInfoId.value || undefined,
        input,
        workflowJson: serializeGraph(agentFormToGraph(form)),
        variables,
      },
      {
        token: userStore.getToken || '',
        onDelta: (chunk) => {
          typewriterTask = typewriterTask.then(() => appendDebugTypewriter(assistantMessage, chunk));
        },
        onResult: (result) => {
          finalResult = result;
        },
      },
    );
    await typewriterTask;
    const result = finalResult;
    const failed = result ? result.status !== 'success' : true;
    const finalText = (result?.output || '').trim() || result?.errorMessage || '';
    await reconcileDebugFinalMessage(assistantMessage, finalText);
    assistantMessage.chartOutputs = extractChartOutputs(result);
    assistantMessage.failed = failed;
    assistantMessage.meta = failed ? result?.errorMessage || '执行失败' : `${result?.durationMs ?? 0}ms`;
  } catch (error: any) {
    assistantMessage.content = assistantMessage.content || error?.response?.data?.detail || error?.message || '调试请求失败';
    assistantMessage.failed = true;
  } finally {
    debugRunning.value = false;
    scrollDebugToBottom();
  }
}

function clearDebug() {
  messages.value = [];
}

function handleBeforeUnload(event: BeforeUnloadEvent) {
  if (hasUnsavedChanges.value) {
    event.preventDefault();
    event.returnValue = '';
  }
}

onBeforeRouteLeave((_to, _from, next) => {
  if (!hasUnsavedChanges.value) return next();
  Modal.confirm({
    title: '有未保存的修改',
    content: '离开前是否保存草稿？',
    okText: '保存并离开',
    cancelText: '直接离开',
    onOk: async () => {
      await saveDraft();
      next();
    },
    onCancel: () => next(),
  });
});

onMounted(() => {
  loadApp();
  loadOptions();
  window.addEventListener('beforeunload', handleBeforeUnload);
});

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload);
});
</script>

<style scoped lang="less">
.agent-v2-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: calc(100vh - 88px);
  overflow: hidden;
  background: #f7f9fc;
  color: #101828;
}

.editor-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  background: #ffffff;
  border-bottom: 1px solid #e2e8f0;

  .back-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    background: #ffffff;
    color: #334155;
    cursor: pointer;

    &:hover {
      border-color: #94a3b8;
      color: #0f172a;
    }
  }

  .app-meta {
    display: flex;
    flex-direction: column;
    min-width: 0;

    strong {
      font-size: 14px;
      color: #0f172a;
      line-height: 1.3;
    }

    .app-sub {
      max-width: 460px;
      color: #64748b;
      font-size: 11px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .unsaved-dot {
    width: 8px;
    height: 8px;
    border-radius: 999px;
    background: #f59e0b;
  }

  .header-actions {
    margin-left: auto;
    display: flex;
    gap: 8px;

    :deep(.ant-btn) {
      border-radius: 0;
    }
  }
}

.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 1px solid #e5e8ef;
  border-radius: 8px;
  background: #fff;
  color: #344054;
  cursor: pointer;
  transition: all 0.18s ease;

  &:hover {
    border-color: #3b82f6;
    color: #2563eb;
  }

  &.soft {
    flex: 0 0 auto;
    width: 34px;
    height: 34px;
    border-color: transparent;
    background: #f6f8fb;
  }
}

.prompt-debug-button {
  margin-top: 8px;
  color: #2563eb;
  border-color: #bfdbfe;
  background: #eff6ff;
}

.agent-v2-spin {
  flex: 1;
  min-height: 0;

  :deep(.ant-spin-container) {
    height: 100%;
  }
}

.agent-workspace {
  display: grid;
  grid-template-columns: minmax(380px, 42%) minmax(0, 1fr);
  gap: 10px;
  height: 100%;
  min-height: 0;
  padding: 10px;
  overflow: hidden;
}

.config-panel,
.preview-panel {
  min-width: 0;
  border: 1px solid #e8edf5;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.03);
}

.config-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px 18px;
  overflow: auto;
}

.panel-title {
  display: flex;
  align-items: center;
  gap: 9px;
  min-height: 28px;

  strong {
    font-size: 15px;
  }

  .clear-btn {
    margin-left: auto;
  }
}

.title-icon,
.section-icon {
  font-size: 18px;

  &.violet,
  &.skill {
    color: #8b5cf6;
  }

  &.blue,
  &.tool,
  &.welcome {
    color: #3b82f6;
  }

  &.knowledge {
    color: #7561f5;
  }

  &.file {
    color: #14b8a6;
  }
}

.config-field {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  align-items: start;
  gap: 16px;

  > label {
    padding-top: 6px;
    color: #1f2937;
    font-size: 13px;
    font-weight: 600;
  }

  :deep(.ant-input),
  :deep(.ant-input-affix-wrapper),
  :deep(.ant-select-selector),
  :deep(.ant-input-number) {
    border-color: #dde4ef;
    border-radius: 8px;
    box-shadow: none !important;
  }

  :deep(textarea.ant-input) {
    min-height: 128px;
    resize: none;
  }

  :deep(.variable-textarea) {
    min-height: 128px;
    border-color: #dde4ef;
    border-radius: 8px;
    resize: none;

    &:focus {
      border-color: #3b82f6;
      box-shadow: none;
    }
  }

  // 提示词位于面板顶部，变量选择面板改为向下展开，避免被裁切
  :deep(.prompt-variable-textarea .variable-picker) {
    top: calc(100% + 6px);
    bottom: auto;
  }
}

.model-control {
  display: flex;
  align-items: center;
  gap: 10px;
}

.model-popover {
  width: 260px;

  label {
    display: block;
    margin-bottom: 12px;
    color: #667085;
    font-size: 12px;
  }
}

.config-section {
  padding-top: 14px;
  border-top: 1px solid #eef2f7;

  :deep(.variable-textarea) {
    border-color: #dde4ef;
    border-radius: 8px;
    resize: none;

    &:focus {
      border-color: #3b82f6;
      box-shadow: none;
    }
  }
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;

  > div {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    color: #111827;
    font-size: 14px;
    font-weight: 600;
  }
}

.section-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.hint-icon {
  color: #98a2b3;
  font-size: 14px;
}

.choose-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 12px;
  border: 1px solid #e3e8f0;
  border-radius: 7px;
  background: #fff;
  color: #1d4ed8;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.18s ease;

  &:hover {
    border-color: #bfdbfe;
    background: #eff6ff;
  }
}

.empty-box {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  height: 52px;
  border: 1px dashed #cfd8e6;
  border-radius: 8px;
  background: #fff;
  color: #98a2b3;
  cursor: pointer;
  transition: all 0.18s ease;

  &:hover {
    border-color: #93c5fd;
    color: #2563eb;
  }

  &.compact {
    height: 42px;
    margin-top: 8px;
  }
}

.selected-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.selected-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid #e7edf5;
  border-radius: 8px;
  background: #fbfdff;
  text-align: left;
  transition: border-color 0.18s ease, background 0.18s ease;

  &:hover {
    border-color: #d8e2f0;
    background: #f8fbff;
  }

  > span:nth-child(2) {
    display: flex;
    flex-direction: column;
    min-width: 0;
    flex: 1;
  }

  strong,
  em {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  strong {
    color: #111827;
    font-size: 13px;
  }

  em {
    color: #98a2b3;
    font-size: 12px;
    font-style: normal;
  }
}

.variable-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.variable-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid #e7edf5;
  border-radius: 8px;
  background: #fbfdff;

  .variable-main {
    display: flex;
    min-width: 0;
    flex: 1;
    align-items: center;
    gap: 8px;
  }

  strong {
    min-width: 0;
    color: #111827;
    font-size: 13px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  code {
    flex: 0 0 auto;
    padding: 1px 6px;
    color: #4f46e5;
    font-size: 12px;
    border-radius: 4px;
    background: #eef2ff;
  }

  em,
  b {
    flex: 0 0 auto;
    color: #64748b;
    font-size: 11px;
    font-style: normal;
    font-weight: 400;
  }

  b {
    color: #d97706;
  }
}

.variable-actions {
  display: inline-flex;
  flex: 0 0 auto;
  gap: 2px;
}

.remove-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex: 0 0 auto;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #98a2b3;
  cursor: pointer;
  transition: all 0.18s ease;

  &:hover {
    background: #fff1f2;
    color: #e11d48;
  }
}

.item-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  flex: 0 0 auto;
  border-radius: 9px;
  color: #fff;
  font-size: 11px;
  font-weight: 700;

  &.http,
  &.tool {
    background: linear-gradient(135deg, #ff65a5, #f43f5e);
  }

  &.system {
    background: linear-gradient(135deg, #60a5fa, #2563eb);
  }

  &.skill,
  &.custom {
    background: linear-gradient(135deg, #a78bfa, #6d28d9);
  }

  &.mcp {
    background: linear-gradient(135deg, #22c55e, #059669);
  }

  &.knowledge {
    background: linear-gradient(135deg, #7561f5, #3b82f6);
  }
}

.knowledge-param-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
  margin-bottom: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #eef5ff;

  span {
    display: flex;
    flex-direction: column;
    gap: 3px;
    color: #475467;
    font-size: 12px;
  }

  b {
    color: #1d4ed8;
    font-weight: 500;
  }
}

.knowledge-controls {
  margin-top: 10px;
  color: #667085;
  font-size: 12px;
}

.upload-section {
  padding-bottom: 2px;
}

.quick-guide {
  margin-top: 12px;
}

.quick-guide-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;

  span {
    color: #475467;
    font-size: 13px;
    font-weight: 600;
  }
}

.quick-guide-desc {
  margin: -2px 0 10px;
  color: #98a2b3;
  font-size: 12px;
  line-height: 1.5;
}

.debug-vars-panel {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 10px;
  width: 100%;
  margin-bottom: 10px;
  padding: 10px;
  border: 1px solid #e7edf5;
  border-radius: 8px;
  background: #fbfdff;
}

.debug-var-field {
  display: grid;
  gap: 5px;
  min-width: 0;

  label {
    color: #475467;
    font-size: 12px;

    i {
      margin-left: 2px;
      color: #dc2626;
      font-style: normal;
    }
  }
}

.variable-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 8px 24px 0;

  .form-item {
    label {
      display: block;
      margin-bottom: 6px;
      color: #475569;
      font-size: 12px;

      em {
        color: #dc2626;
        font-style: normal;
      }
    }

    &.inline {
      display: flex;
      align-items: center;
      justify-content: space-between;

      label {
        margin-bottom: 0;
      }
    }

    .range-inputs {
      display: inline-flex;
      gap: 8px;
    }
  }
}

.preview-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 16px 20px 20px;
  overflow: hidden;
}

.preview-stage {
  position: relative;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
  padding: 72px 8% 24px;
  overflow: auto;
  background:
    radial-gradient(circle at 50% 22%, rgba(226, 232, 240, 0.7), transparent 23%),
    linear-gradient(180deg, #fff 0%, #fbfcff 100%);
}

.assistant-card {
  display: inline-flex;
  align-items: flex-start;
  gap: 9px;
  width: fit-content;
  max-width: 360px;
  padding: 12px 16px;
  border: 1px solid #edf1f7;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.86);
  color: #a5adba;
  box-shadow: 0 6px 18px rgba(16, 24, 40, 0.05);

  svg {
    margin-top: 2px;
    color: #9aa4b2;
  }

  span {
    display: flex;
    flex-direction: column;
    gap: 5px;
    font-size: 12px;
  }

  strong,
  em {
    font-style: normal;
    font-weight: 500;
  }
}

.quick-suggestions {
  display: flex;
  flex-direction: column;
  align-self: flex-start;
  justify-content: flex-start;
  gap: 8px;
  max-width: 78%;
  margin-top: 0;
}

.quick-bubble {
  width: fit-content;
  max-width: 360px;
  padding: 5px 10px;
  border: 1px dashed #cbdcf8;
  border-radius: 8px;
  background: #f8fbff;
  color: #5b76a6;
  font-size: 12px;
  line-height: 1.45;
  text-align: left;
  cursor: pointer;
  box-shadow: none;
  transition: all 0.18s ease;

  &:hover {
    border-color: #bfdbfe;
    background: #eef6ff;
    color: #1d4ed8;
  }
}

.bubble {
  max-width: 72%;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.7;

  p {
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .markdown-body {
    color: inherit;
    font-size: inherit;
    line-height: inherit;
    word-break: break-word;

    :deep(p) {
      margin: 0 0 8px;

      &:last-child {
        margin-bottom: 0;
      }
    }

    :deep(ul),
    :deep(ol) {
      margin: 6px 0 8px;
      padding-left: 18px;
    }

    :deep(li + li) {
      margin-top: 3px;
    }

    :deep(pre) {
      margin: 8px 0;
      padding: 10px 12px;
      overflow: auto;
      border-radius: 8px;
      background: #f6f8fa;
      color: #1f2937;
      font-size: 12px;
      line-height: 1.6;
    }

    :deep(code) {
      padding: 1px 4px;
      border-radius: 4px;
      background: #f1f5f9;
      color: #334155;
      font-size: 12px;
    }

    :deep(pre code) {
      padding: 0;
      background: transparent;
    }

    :deep(blockquote) {
      margin: 8px 0;
      padding-left: 10px;
      border-left: 3px solid #d8e2f0;
      color: #667085;
    }

    :deep(a) {
      color: #2563eb;
    }
  }

  .bubble-meta {
    display: block;
    margin-top: 4px;
    color: #98a2b3;
    font-size: 11px;
  }

  .message-chart-list {
    display: grid;
    gap: 10px;
    margin-top: 10px;
  }

  &.user {
    align-self: flex-end;
    background: #eaf3ff;
    color: #667085;
  }

  &.assistant {
    align-self: flex-start;
    border: 1px solid #edf1f7;
    background: #fff;
    color: #475467;
    box-shadow: 0 6px 18px rgba(16, 24, 40, 0.04);
  }

  &.has-charts {
    width: min(560px, 92%);
    max-width: 92%;
  }

  &.welcome {
    border-style: dashed;
  }

  &.pending {
    color: #98a2b3;
  }

  &.failed {
    border-color: #fecaca;
    background: #fff5f5;

    .bubble-meta {
      color: #d92d20;
    }
  }
}

.debug-composer {
  position: relative;
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 10px;
  padding: 14px 16px;
  border: 1px solid #e1e7f0;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 10px 28px rgba(16, 24, 40, 0.07);

  :deep(textarea.ant-input) {
    flex: 1;
    min-width: 0;
    padding-right: 92px;
    border: 0;
    box-shadow: none !important;
    resize: none;
  }
}

.attach-btn,
.send-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border: 0;
  border-radius: 10px;
  cursor: pointer;
}

.attach-btn {
  color: #667085;
  background: transparent;

  &:disabled {
    color: #b7c0cd;
    cursor: not-allowed;
  }
}

.send-btn {
  color: #3b82f6;
  background: #eff6ff;

  &:disabled {
    color: #b7c0cd;
    cursor: not-allowed;
  }
}

.debug-file-input {
  display: none;
}

.debug-file-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  width: 100%;
  padding-left: 2px;
}

.debug-file-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  padding: 4px 8px;
  border: 1px solid #dbe3ef;
  border-radius: 8px;
  background: #f8fafc;
  color: #475467;
  font-size: 12px;
  line-height: 1.3;

  button {
    border: 0;
    background: transparent;
    color: #98a2b3;
    cursor: pointer;
    font-size: 14px;
    line-height: 1;
  }
}

.full {
  width: 100%;
}

.picker-title {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 2px 0 0 2px;

  strong {
    color: #111827;
    font-size: 16px;
  }

  span {
    color: #98a2b3;
    font-size: 12px;
    font-weight: 400;
  }
}

.picker-shell {
  margin: 2px -24px -20px;
  padding-top: 6px;
}

.picker-tabs {
  display: flex;
  gap: 28px;
  padding: 0 24px;
  border-bottom: 1px solid #edf1f7;

  button {
    position: relative;
    height: 44px;
    border: 0;
    background: transparent;
    color: #475467;
    font-size: 14px;
    cursor: pointer;

    &.active {
      color: #2563eb;
      font-weight: 600;

      &::after {
        position: absolute;
        right: 0;
        bottom: -1px;
        left: 0;
        height: 2px;
        border-radius: 999px;
        background: #2563eb;
        content: '';
      }
    }
  }
}

.picker-toolbar {
  padding: 16px 28px 12px;

  :deep(.ant-input-affix-wrapper),
  :deep(.ant-select-selector) {
    border-color: #dde4ef;
    border-radius: 8px !important;
    box-shadow: none !important;
  }
}

.picker-list {
  max-height: 360px;
  padding: 0 28px 14px;
  overflow: auto;
}

.picker-row {
  display: grid;
  grid-template-columns: 22px 42px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid transparent;
  border-radius: 9px;
  background: #fff;
  text-align: left;
  cursor: pointer;

  &:hover,
  &.selected {
    border-color: #e5edfb;
    background: #f8fbff;
  }
}

.picker-info {
  display: flex;
  flex-direction: column;
  min-width: 0;

  strong {
    color: #101828;
    font-size: 14px;
  }

  em {
    color: #667085;
    font-size: 12px;
    font-style: normal;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.type-badge {
  padding: 3px 8px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.3;

  &.skill,
  &.custom {
    color: #2563eb;
    background: #eaf1ff;
  }

  &.system,
  &.http,
  &.tool {
    color: #059669;
    background: #dcfce7;
  }

  &.mcp {
    color: #7c3aed;
    background: #f3e8ff;
  }

  &.knowledge {
    color: #1d4ed8;
    background: #eaf1ff;
  }
}

.picker-empty {
  padding: 34px 0;
  color: #98a2b3;
  text-align: center;
}

.picker-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 28px 26px;
  border-top: 1px solid #edf1f7;

  > span {
    color: #475467;
    font-size: 13px;
  }

  > div {
    display: flex;
    gap: 10px;
  }

  :deep(.ant-btn) {
    border-radius: 8px;
  }
}

:global(.tool-skill-modal-wrap .ant-modal-content) {
  overflow: hidden;
  border-radius: 8px;
  box-shadow: 0 18px 48px rgba(16, 24, 40, 0.18);
}

:global(.tool-skill-modal-wrap .ant-modal-body) {
  padding: 14px 24px 0;
}

@media (max-width: 1120px) {
  .agent-workspace {
    grid-template-columns: 1fr;
  }

  .preview-stage {
    min-height: 360px;
    padding: 48px 5% 24px;
  }
}

@media (max-width: 720px) {
  .agent-workspace {
    padding: 8px;
  }

  .config-field {
    grid-template-columns: 1fr;
    gap: 8px;
  }

  .knowledge-param-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .picker-row {
    grid-template-columns: 22px 38px minmax(0, 1fr);

    .type-badge {
      display: none;
    }
  }
}
</style>
