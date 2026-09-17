<template>
  <div class="llm-model-setting">
    <a-select
      class="model-select"
      size="small"
      :value="modelValue"
      :options="selectOptions"
      :loading="modelLoading"
      placeholder="选择模型"
      popup-class-name="wf-node-select-popup"
      show-search
      @change="handleModelChange"
    />
    <a-button size="small" class="setting-btn" @click="modalVisible = true">
      <SettingOutlined />
    </a-button>

    <a-modal
      v-model:open="modalVisible"
      title="模型参数"
      :width="440"
      :footer="null"
      destroy-on-close
      wrapClassName="model-setting-modal"
    >
      <div class="model-setting-modal-body">
        <div class="setting-form">
          <div class="setting-item">
            <label>模型</label>
            <a-select
              :value="modelValue"
              :options="selectOptions"
              :loading="modelLoading"
              popup-class-name="wf-node-select-popup"
              show-search
              style="width: 100%"
              @change="handleModelChange"
            />
          </div>
          <div class="setting-item">
            <label>温度（Temperature）</label>
            <div class="slider-row">
              <a-slider :value="temperature" :min="0" :max="2" :step="0.1" style="flex: 1" @change="setValue(NodeInputKeyEnum.aiChatTemperature, $event)" />
              <a-input-number :value="temperature" :min="0" :max="2" :step="0.1" size="small" @change="setValue(NodeInputKeyEnum.aiChatTemperature, $event)" />
            </div>
          </div>
          <div class="setting-item">
            <label>回复上限（Max Tokens）</label>
            <a-input-number
              :value="maxToken"
              :min="100"
              :max="128000"
              :step="100"
              style="width: 100%"
              @change="setValue(NodeInputKeyEnum.aiChatMaxToken, $event)"
            />
          </div>
          <div class="setting-item">
            <label>Top P</label>
            <div class="slider-row">
              <a-slider :value="topP" :min="0" :max="1" :step="0.05" style="flex: 1" @change="setValue(NodeInputKeyEnum.aiChatTopP, $event)" />
              <a-input-number :value="topP" :min="0" :max="1" :step="0.05" size="small" @change="setValue(NodeInputKeyEnum.aiChatTopP, $event)" />
            </div>
          </div>
          <div class="setting-item">
            <label>停止序列（Stop）</label>
            <a-input
              :value="stopSign"
              placeholder="多个序列用 | 分隔"
              @change="setValue(NodeInputKeyEnum.aiChatStopSign, ($event.target as HTMLInputElement).value)"
            />
          </div>
          <div v-if="hasInput(NodeInputKeyEnum.aiChatReasoningEffort)" class="setting-item">
            <label>推理强度</label>
            <a-select
              :value="getNodeInput(props.node, NodeInputKeyEnum.aiChatReasoningEffort)?.value || undefined"
              :options="reasoningEffortOptions"
              allow-clear
              placeholder="默认"
              popup-class-name="wf-node-select-popup"
              style="width: 100%"
              @change="setValue(NodeInputKeyEnum.aiChatReasoningEffort, $event ?? '')"
            />
          </div>

          <div v-if="hasAnySwitch" class="switch-group">
            <div v-if="hasInput(NodeInputKeyEnum.aiChatVision)" class="switch-item">
              <label>视觉识别</label>
              <a-switch
                size="small"
                :checked="boolInput(NodeInputKeyEnum.aiChatVision, true)"
                @change="setValue(NodeInputKeyEnum.aiChatVision, $event)"
              />
            </div>
            <div v-if="hasInput(NodeInputKeyEnum.aiChatReasoning)" class="switch-item">
              <label>输出思考过程</label>
              <a-switch
                size="small"
                :checked="boolInput(NodeInputKeyEnum.aiChatReasoning, true)"
                @change="setValue(NodeInputKeyEnum.aiChatReasoning, $event)"
              />
            </div>
            <div v-if="hasInput(NodeInputKeyEnum.aiChatIsResponseText)" class="switch-item">
              <label>返回 AI 内容（流式回复）</label>
              <a-switch
                size="small"
                :checked="boolInput(NodeInputKeyEnum.aiChatIsResponseText, true)"
                @change="setValue(NodeInputKeyEnum.aiChatIsResponseText, $event)"
              />
            </div>
          </div>
        </div>
      </div>

      <div class="model-setting-modal-footer">
        <a-button type="primary" @click="modalVisible = false">完成</a-button>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { SettingOutlined } from '@ant-design/icons-vue';
import { NodeInputKeyEnum } from '../../../core/constants';
import type { StoreNodeItemType } from '../../../core/type';
import { getNodeInput } from '../../../core/utils';
import { useEditorContext } from '../../composables/useEditorContext';

const props = defineProps<{ node: StoreNodeItemType }>();

const { modelOptions, modelLoading } = useEditorContext();

const modalVisible = ref(false);

const selectOptions = computed(() =>
  modelOptions.value.map((item) => ({ label: item.label || item.value, value: item.value }))
);

const modelValue = computed(() => getNodeInput(props.node, NodeInputKeyEnum.aiModel)?.value);
const temperature = computed(() => getNodeInput(props.node, NodeInputKeyEnum.aiChatTemperature)?.value ?? 0.7);
const maxToken = computed(() => getNodeInput(props.node, NodeInputKeyEnum.aiChatMaxToken)?.value ?? 2048);
const topP = computed(() => getNodeInput(props.node, NodeInputKeyEnum.aiChatTopP)?.value ?? 1);
const stopSign = computed(() => getNodeInput(props.node, NodeInputKeyEnum.aiChatStopSign)?.value ?? '');

const reasoningEffortOptions = [
  { label: 'low', value: 'low' },
  { label: 'medium', value: 'medium' },
  { label: 'high', value: 'high' },
];

function hasInput(key: string) {
  return !!getNodeInput(props.node, key);
}

const hasAnySwitch = computed(
  () =>
    hasInput(NodeInputKeyEnum.aiChatVision) ||
    hasInput(NodeInputKeyEnum.aiChatReasoning) ||
    hasInput(NodeInputKeyEnum.aiChatIsResponseText)
);

function boolInput(key: string, defaultValue: boolean) {
  const value = getNodeInput(props.node, key)?.value;
  return value === undefined || value === null ? defaultValue : !!value;
}

function setValue(key: string, value: any) {
  const input = getNodeInput(props.node, key);
  if (input) input.value = value;
}

function handleModelChange(value: any) {
  setValue(NodeInputKeyEnum.aiModel, value);
}
</script>

<style lang="less">
.model-setting-modal {
  .ant-modal-body {
    padding: 0;
  }
}
</style>

<style scoped lang="less">
.llm-model-setting {
  display: flex;
  gap: 6px;
  align-items: center;
}

.model-select {
  flex: 1;
  min-width: 0;
}

.setting-btn {
  flex-shrink: 0;
  color: #64748b;
}

.model-setting-modal-body {
  padding: 18px 24px 10px;
}

.setting-form {
  display: flex;
  flex-direction: column;
  gap: 16px;

  .setting-item {
    label {
      display: block;
      margin-bottom: 6px;
      font-size: 13px;
      font-weight: 500;
      color: #374151;
    }
  }

  .slider-row {
    display: flex;
    align-items: center;
    gap: 12px;
  }
}

.switch-group {
  padding: 2px 14px;
  border: 1px solid #eceef3;
  border-radius: 10px;
  background: #fafbfc;

  .switch-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 0;

    & + .switch-item {
      border-top: 1px solid #eef0f4;
    }

    label {
      margin-bottom: 0;
      font-size: 13px;
      font-weight: 500;
      color: #374151;
    }
  }
}

.model-setting-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 12px 24px 18px;
  border-top: 1px solid #edf2f7;
}
</style>
