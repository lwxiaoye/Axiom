<template>
  <div class="code-editor" @mousedown.stop @click.stop>
    <div class="code-head">
      <span class="code-label">代码 <em>*</em></span>
      <a-select
        size="small"
        class="code-lang"
        popup-class-name="wf-node-select-popup"
        :value="codeType"
        :options="langOptions"
        @change="switchLanguage"
      />
    </div>
    <VariableTextarea
      class="code-textarea nodrag"
      :value="codeValue"
      rows="10"
      :placeholder="defaultTemplate"
      :groups="textVariableGroups"
      @update:value="setInputValue(input, $event)"
    />
    <p class="code-tip">{{ codeType === 'py' ? 'def main(data1, data2): 返回对象作为输出' : 'function main({data1, data2}) 返回对象作为输出，支持 async' }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { NodeInputKeyEnum } from '../../../core/constants';
import { JS_CODE_TEMPLATE, PY_CODE_TEMPLATE } from '../../../core/templates';
import type { FlowNodeInputItemType, StoreNodeItemType } from '../../../core/type';
import { buildTextVariableGroups, getNodeInput, setInputValue } from '../../../core/utils';
import { useEditorContext } from '../../composables/useEditorContext';
import VariableTextarea from '../VariableTextarea.vue';

const props = defineProps<{
  node: StoreNodeItemType;
  input: FlowNodeInputItemType;
}>();
const { graph } = useEditorContext();

/** 蓝本 SandboxCodeTypeEnum：js / py 双语言 */
const langOptions = [
  { label: 'JavaScript', value: 'js' },
  { label: 'Python', value: 'py' },
];

const TEMPLATE_BY_LANG: Record<string, string> = { js: JS_CODE_TEMPLATE, py: PY_CODE_TEMPLATE };

const codeType = computed(() => String(getNodeInput(props.node, NodeInputKeyEnum.codeType)?.value || 'js'));
const codeValue = computed(() => String(props.input.value ?? ''));
const defaultTemplate = computed(() => TEMPLATE_BY_LANG[codeType.value] || JS_CODE_TEMPLATE);
const textVariableGroups = computed(() =>
  buildTextVariableGroups(props.node.nodeId, graph.value.nodes, graph.value.edges, graph.value.chatConfig)
);

/** 蓝本编辑器行为：切换语言时，未改动的默认模板代码跟随替换 */
function switchLanguage(lang: any) {
  const typeInput = getNodeInput(props.node, NodeInputKeyEnum.codeType);
  if (typeInput) typeInput.value = lang;
  const current = codeValue.value.trim();
  const isDefault = !current || Object.values(TEMPLATE_BY_LANG).some((tpl) => tpl.trim() === current);
  if (isDefault) setInputValue(props.input, TEMPLATE_BY_LANG[String(lang)] || '');
}
</script>

<style scoped lang="less">
.code-editor {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.code-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.code-label {
  font-size: 12px;
  font-weight: 600;
  color: #475569;

  em {
    color: #dc2626;
    font-style: normal;
  }
}

.code-lang {
  width: 120px;
}

.code-textarea {
  :deep(.variable-textarea) {
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
    font-size: 12px;
    line-height: 1.6;
    background: #f8fafc;

    &:focus {
      border-color: #6f5dd7;
      background: #fff;
    }
  }
}

.code-tip {
  margin: 0;
  font-size: 11px;
  color: #94a3b8;
}
</style>
