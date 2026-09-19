<template>
  <div class="system-config-card">
    <section class="config-section">
      <h4>开场白</h4>
      <VariableTextarea
        :value="chatConfig.welcomeText"
        :rows="3"
        :groups="textareaVariableGroups"
        placeholder="每次对话开始前展示给用户的引导文案，输入 / 可选择变量"
        @update:value="setWelcomeText"
      />
    </section>

    <section class="config-section">
      <div class="section-head">
        <h4>全局变量</h4>
        <a-button size="small" type="primary" ghost @click="openVariableModal()">
          <PlusOutlined />
          新增
        </a-button>
      </div>
      <p class="section-tip">对话开始前收集，工作流中可通过变量引用或 &#123;&#123;key&#125;&#125; 使用</p>
      <div v-if="variables.length" class="variable-list">
        <div v-for="(variable, index) in variables" :key="variable.id" class="variable-row">
          <span class="variable-name">{{ variable.label }}</span>
          <code class="variable-key">{{ variable.key }}</code>
          <em class="variable-type">{{ VariableInputLabelMap[variable.type] || variable.type }}</em>
          <em v-if="variable.required" class="variable-required">必填</em>
          <span class="variable-actions">
            <a-button size="small" type="text" @click="openVariableModal(index)"><EditOutlined /></a-button>
            <a-button size="small" type="text" danger @click="removeVariable(index)"><DeleteOutlined /></a-button>
          </span>
        </div>
      </div>
      <div v-else class="variable-empty">暂无全局变量</div>
    </section>

    <section class="config-section">
      <div class="section-head switch-head">
        <h4>语音播报</h4>
        <a-select
          size="small"
          style="width: 132px"
          popup-class-name="wf-node-select-popup"
          :value="ttsConfig.type"
          :options="ttsTypeOptions"
          @change="setTts({ type: $event })"
        />
      </div>
      <p class="section-tip">开启后，运行页会使用浏览器内置语音合成播报助手回复。</p>
    </section>

    <section class="config-section">
      <div class="section-head switch-head">
        <h4>自动问题引导（对话内猜你想问）</h4>
        <a-switch size="small" :checked="!!questionGuide.open" @change="setQuestionGuide({ open: $event })" />
      </div>
      <a-textarea
        v-if="questionGuide.open"
        :value="questionGuide.customPrompt"
        :rows="2"
        placeholder="自定义引导生成提示词（可选）"
        @change="setQuestionGuide({ customPrompt: ($event.target as HTMLTextAreaElement).value })"
      />
    </section>

    <section class="config-section">
      <div class="section-head">
        <h4>右侧推荐内容（人工配置）</h4>
      </div>
      <p class="section-tip">先创建场景，再添加该场景下的推荐内容；运行页按场景展示，点击后只填入输入框</p>
      <RecommendationSceneEditor :model-value="recommendationScenes" @update:model-value="setRecommendationScenes" />
    </section>

    <section class="config-section">
      <h4>文件上传</h4>
      <p class="section-tip">开启后「流程开始」节点输出「用户上传的文件」，关闭时自动移除并清理引用</p>
      <div class="config-row">
        <label>上传文件</label>
        <a-switch :checked="!!fileConfig.canSelectFile" size="small" @change="setFileConfig({ canSelectFile: $event })" />
      </div>
      <div class="config-row">
        <label>上传图片</label>
        <a-switch :checked="!!fileConfig.canSelectImg" size="small" @change="setFileConfig({ canSelectImg: $event })" />
      </div>
      <div class="config-row">
        <label>上传视频</label>
        <a-switch
          :checked="!!fileConfig.canSelectVideo"
          size="small"
          @change="setFileConfig({ canSelectVideo: $event })"
        />
      </div>
      <div class="config-row">
        <label>上传音频</label>
        <a-switch
          :checked="!!fileConfig.canSelectAudio"
          size="small"
          @change="setFileConfig({ canSelectAudio: $event })"
        />
      </div>
      <div class="config-row">
        <label>自定义扩展名</label>
        <a-switch
          :checked="!!fileConfig.canSelectCustomFileExtension"
          size="small"
          @change="setFileConfig({ canSelectCustomFileExtension: $event })"
        />
      </div>
      <a-input
        v-if="fileConfig.canSelectCustomFileExtension"
        size="small"
        :value="(fileConfig.customFileExtensionList || []).join(',')"
        placeholder="扩展名列表，逗号分隔，如 csv,xlsx"
        @change="setCustomExtensions(($event.target as HTMLInputElement).value)"
      />
      <div class="config-row">
        <label>PDF 增强解析</label>
        <a-switch
          :checked="!!fileConfig.customPdfParse"
          size="small"
          @change="setFileConfig({ customPdfParse: $event })"
        />
      </div>
      <div v-if="anyFileEnabled" class="config-row">
        <label>最大文件数</label>
        <a-input-number
          class="system-file-count"
          :value="fileConfig.maxFiles"
          :min="1"
          :max="50"
          size="small"
          @change="setFileConfig({ maxFiles: $event ?? 10 })"
        />
      </div>
    </section>

    <a-modal
      v-model:open="variableModalVisible"
      :title="editingIndex === null ? '新增全局变量' : '编辑全局变量'"
      :width="460"
      destroy-on-close
      @ok="saveVariable"
    >
      <div class="variable-form">
        <div class="form-item">
          <label>变量名称 <em>*</em></label>
          <a-input v-model:value="draft.label" placeholder="展示给用户的名称，如：姓名" />
        </div>
        <div class="form-item">
          <label>变量 key <em>*</em></label>
          <a-input v-model:value="draft.key" placeholder="工作流中引用的 key，如：user_name" />
        </div>
        <div class="form-item">
          <label>输入类型</label>
          <a-select
            v-model:value="draft.type"
            style="width: 100%"
            popup-class-name="wf-node-select-popup"
            :options="typeOptions"
          />
        </div>
        <div class="form-item">
          <label>描述</label>
          <a-input v-model:value="draft.description" placeholder="变量用途说明（可选）" />
        </div>
        <div v-if="isChoiceType(draft.type)" class="form-item">
          <label>选项（每行一个）</label>
          <a-textarea v-model:value="draftEnums" :rows="3" placeholder="选项1&#10;选项2" />
        </div>
        <div v-if="draft.type === VariableInputEnum.numberInput" class="form-item inline">
          <label>数值范围</label>
          <span class="range-inputs">
            <a-input-number v-model:value="draft.min" size="small" placeholder="最小" />
            <a-input-number v-model:value="draft.max" size="small" placeholder="最大" />
          </span>
        </div>
        <div v-if="draft.type === VariableInputEnum.input || draft.type === VariableInputEnum.textarea" class="form-item inline">
          <label>最大长度</label>
          <a-input-number v-model:value="draft.maxLength" size="small" :min="1" placeholder="不限" />
        </div>
        <div v-if="draft.type !== VariableInputEnum.switch" class="form-item">
          <label>默认值</label>
          <a-input v-model:value="draft.defaultValue" placeholder="对话开始时的初始值（可选）" />
        </div>
        <div class="form-item inline">
          <label>必填</label>
          <a-switch v-model:checked="draft.required" size="small" />
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import { message } from 'ant-design-vue';
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons-vue';
import { VariableInputEnum, VariableInputLabelMap, WorkflowIOValueTypeEnum } from '../../core/constants';
import type { AppFileSelectConfigType, RecommendationSceneConfig, VariableItemType } from '../../core/type';
import { buildTextareaVariableGroups, getNanoid, syncUserFilesOutput } from '../../core/utils';
import { flattenRecommendationScenes, preserveRecommendationSceneDrafts } from '../../shared/recommendationScenes';
import { useEditorContext } from '../composables/useEditorContext';
import RecommendationSceneEditor from '../../shared/RecommendationSceneEditor.vue';
import VariableTextarea from './VariableTextarea.vue';

/** systemConfig 节点内嵌配置（开场白/变量/TTS/问题引导/右侧推荐内容/文件） */
const { graph } = useEditorContext();

const chatConfig = computed(() => graph.value.chatConfig);

function setWelcomeText(value: string) {
  chatConfig.value.welcomeText = value;
}

if (!Array.isArray(graph.value.chatConfig.variables)) {
  graph.value.chatConfig.variables = [];
}

const variables = computed<VariableItemType[]>(() => chatConfig.value.variables || []);
const textareaVariableGroups = computed(() => buildTextareaVariableGroups(chatConfig.value));

const fileConfig = computed<AppFileSelectConfigType>(
  () => chatConfig.value.fileSelectConfig || { canSelectFile: false, canSelectImg: false, maxFiles: 10 }
);

const anyFileEnabled = computed(
  () =>
    fileConfig.value.canSelectFile ||
    fileConfig.value.canSelectImg ||
    fileConfig.value.canSelectVideo ||
    fileConfig.value.canSelectAudio ||
    fileConfig.value.canSelectCustomFileExtension
);

function setFileConfig(patch: Partial<AppFileSelectConfigType>) {
  chatConfig.value.fileSelectConfig = { ...fileConfig.value, ...patch };
  // 文件开关驱动 workflowStart.userFiles 输出的增删（蓝本 systemConfig 行为，五类开关均计入）
  syncUserFilesOutput(graph.value);
}

function setCustomExtensions(text: string) {
  setFileConfig({
    customFileExtensionList: text
      .split(/[,，]/)
      .map((item) => item.trim().replace(/^\./, ''))
      .filter(Boolean),
  });
}

/** ---- 蓝本九区块新增配置 ---- */
const ttsTypeOptions = [
  { label: '关闭', value: 'none' },
  { label: '浏览器播报', value: 'web' },
];

const ttsConfig = computed(() => (chatConfig.value.ttsConfig?.type === 'web' ? { type: 'web' as const } : { type: 'none' as const }));
function setTts(patch: { type: 'none' | 'web' }) {
  chatConfig.value.ttsConfig = { ...ttsConfig.value, ...patch };
}

const questionGuide = computed(() => chatConfig.value.questionGuide || { open: false });
function setQuestionGuide(patch: Record<string, any>) {
  chatConfig.value.questionGuide = { ...questionGuide.value, ...patch };
}

const chatInputGuide = computed(() => chatConfig.value.chatInputGuide || { open: false, textList: [] });
const recommendationScenes = computed(() =>
  preserveRecommendationSceneDrafts(chatInputGuide.value.sceneList, chatInputGuide.value.textList),
);
function setRecommendationScenes(sceneList: RecommendationSceneConfig[]) {
  const drafts = preserveRecommendationSceneDrafts(sceneList);
  const textList = flattenRecommendationScenes(drafts);
  chatConfig.value.chatInputGuide = {
    ...chatInputGuide.value,
    open: textList.length > 0,
    textList,
    sceneList: drafts,
  };
}

/** ---- 全局变量 ---- */
const typeOptions = Object.entries(VariableInputLabelMap).map(([value, label]) => ({ value, label }));

/** 蓝本 InputComponentProps：按输入类型映射变量 valueType */
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
const editingIndex = ref<number | null>(null);
const draftEnums = ref('');
const draft = reactive<VariableItemType>(emptyDraft());

function isChoiceType(type: VariableInputEnum) {
  return type === VariableInputEnum.select || type === VariableInputEnum.multipleSelect;
}

function emptyDraft(): VariableItemType {
  return {
    id: getNanoid(),
    key: '',
    label: '',
    type: VariableInputEnum.input,
    required: false,
  };
}

function openVariableModal(index?: number) {
  editingIndex.value = index ?? null;
  const source = index != null ? variables.value[index] : emptyDraft();
  Object.assign(draft, emptyDraft(), JSON.parse(JSON.stringify(source)));
  draftEnums.value = (source.enums || []).map((item) => item.value).join('\n');
  variableModalVisible.value = true;
}

function saveVariable() {
  if (!draft.label.trim() || !draft.key.trim()) {
    message.warning('请填写变量名称与 key');
    return;
  }
  const duplicated = variables.value.some(
    (item, index) => item.key === draft.key.trim() && index !== editingIndex.value
  );
  if (duplicated) {
    message.warning('变量 key 重复');
    return;
  }
  const record: VariableItemType = {
    ...JSON.parse(JSON.stringify(draft)),
    key: draft.key.trim(),
    label: draft.label.trim(),
    valueType: VARIABLE_VALUE_TYPE[draft.type] || WorkflowIOValueTypeEnum.string,
    enums: isChoiceType(draft.type)
      ? draftEnums.value
          .split('\n')
          .map((line) => line.trim())
          .filter(Boolean)
          .map((value) => ({ label: value, value }))
      : undefined,
  };
  if (editingIndex.value != null) {
    variables.value.splice(editingIndex.value, 1, record);
  } else {
    variables.value.push(record);
  }
  variableModalVisible.value = false;
}

function removeVariable(index: number) {
  variables.value.splice(index, 1);
}
</script>

<style scoped lang="less">
.system-config-card {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.config-section {
  h4 {
    margin: 0 0 8px;
    font-size: 14px;
    font-weight: 600;
    color: #354052;
  }

  .section-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 4px;

    h4 {
      margin: 0;
    }

    &.switch-head {
      margin-bottom: 8px;
    }
  }

  .section-tip {
    margin: 0 0 10px;
    font-size: 12px;
    color: #94a3b8;
  }
}

.variable-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.variable-row {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 6px 10px;

  .variable-name {
    font-size: 13px;
    color: #0f172a;
  }

  .variable-key {
    font-size: 12px;
    color: #4f46e5;
    background: #eef2ff;
    border-radius: 4px;
    padding: 0 6px;
  }

  .variable-type,
  .variable-required {
    font-size: 11px;
    font-style: normal;
    color: #64748b;
  }

  .variable-required {
    color: #d97706;
  }

  .variable-actions {
    margin-left: auto;
    display: inline-flex;
  }
}

.variable-empty {
  border: 1px dashed #e2e8f0;
  border-radius: 8px;
  text-align: center;
  color: #94a3b8;
  font-size: 12px;
  padding: 14px 0;
}

.config-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0;

  label {
    font-size: 13px;
    color: #354052;
  }
}

:deep(.system-file-count.ant-input-number-sm) {
  display: inline-flex;
  height: 24px;
  align-items: center;
}

:deep(.system-file-count.ant-input-number-sm .ant-input-number-input-wrap) {
  display: flex;
  height: 100%;
  flex: 1;
  align-items: center;
}

:deep(.system-file-count.ant-input-number-sm .ant-input-number-input) {
  height: 22px;
  padding-top: 0;
  padding-bottom: 0;
  line-height: 22px;
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
      font-size: 12px;
      color: #475569;

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
</style>
