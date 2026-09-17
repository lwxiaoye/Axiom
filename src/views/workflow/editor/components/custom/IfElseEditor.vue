<template>
  <div class="ifelse-editor">
    <div v-for="(group, groupIndex) in groups" :key="groupIndex" class="ifelse-group">
      <div class="group-head">
        <span class="group-name">{{ groupLabel(groupIndex) }}</span>
        <a-radio-group
          v-if="group.list.length > 1"
          v-model:value="group.condition"
          size="small"
          button-style="solid"
        >
          <a-radio-button value="AND">AND</a-radio-button>
          <a-radio-button value="OR">OR</a-radio-button>
        </a-radio-group>
        <a-button
          v-if="groups.length > 1"
          size="small"
          type="text"
          class="group-remove"
          @click="removeGroup(groupIndex)"
        >
          <DeleteOutlined />
        </a-button>
      </div>

      <div v-for="(item, itemIndex) in group.list" :key="itemIndex" class="condition-row">
        <ReferencePicker
          class="condition-variable"
          :node="node"
          :value="item.variable"
          placeholder="选择变量"
          @update:value="(value) => (item.variable = value)"
        />
        <a-select
          v-model:value="item.condition"
          class="condition-op"
          size="small"
          popup-class-name="wf-node-select-popup"
          placeholder="条件"
          :options="conditionOptionsFor(item)"
        />
        <!-- 右值：input/reference 双方式（蓝本 valueType 语义），一元条件无右值 -->
        <template v-if="!isUnaryCondition(item.condition)">
          <a-button
            size="small"
            type="text"
            class="value-mode"
            :title="item.valueType === 'reference' ? '切换为手动输入' : '切换为引用变量'"
            @click="toggleValueMode(item)"
          >
            <SwapOutlined />
          </a-button>
          <ReferencePicker
            v-if="item.valueType === 'reference'"
            class="condition-value"
            :node="node"
            :value="isRefValue(item.value) ? item.value : undefined"
            placeholder="引用变量"
            @update:value="(value) => (item.value = value)"
          />
          <a-input-number
            v-else-if="isNumberValueCondition(item.condition)"
            class="condition-value"
            size="small"
            placeholder="对比值"
            :value="item.value === '' || item.value === undefined ? undefined : Number(item.value)"
            @change="item.value = $event === null || $event === undefined ? '' : String($event)"
          />
          <a-input
            v-else
            class="condition-value"
            size="small"
            placeholder="对比值"
            :value="typeof item.value === 'string' ? item.value : ''"
            @change="item.value = ($event.target as HTMLInputElement).value"
          />
        </template>
        <a-button size="small" type="text" class="condition-remove" @click="removeCondition(groupIndex, itemIndex)">
          <MinusCircleOutlined />
        </a-button>
      </div>

      <a-button size="small" type="text" class="add-condition" @click="addCondition(groupIndex)">
        <PlusOutlined />
        添加条件
      </a-button>
    </div>

    <a-button size="small" type="dashed" block @click="addGroup">
      <PlusOutlined />
      添加 ELSE IF 分支
    </a-button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { DeleteOutlined, MinusCircleOutlined, PlusOutlined, SwapOutlined } from '@ant-design/icons-vue';
import {
  IfElseResultEnum,
  SYSTEM_VARIABLES,
  VARIABLE_NODE_ID,
  VariableConditionEnum,
  VariableConditionLabelMap,
} from '../../../core/constants';
import type {
  FlowNodeInputItemType,
  IfElseConditionItemType,
  IfElseListItemType,
  StoreNodeItemType,
} from '../../../core/type';
import { ensureArrayValue, isReferenceValue, setInputValue } from '../../../core/utils';
import { useEditorContext } from '../../composables/useEditorContext';
import ReferencePicker from '../render/ReferencePicker.vue';

const props = defineProps<{
  node: StoreNodeItemType;
  input: FlowNodeInputItemType;
}>();

const { graph } = useEditorContext();

ensureArrayValue(props.input, [{ condition: 'AND', list: [{ valueType: 'input' }] }]);
if (!props.input.value.length) {
  setInputValue(props.input, [{ condition: 'AND', list: [{ valueType: 'input' }] }]);
}

const groups = computed<IfElseListItemType[]>(() => props.input.value);

const isRefValue = isReferenceValue;

/** 蓝本：按左值数据类型过滤适用条件 */
const CONDITIONS_BY_TYPE: Record<string, VariableConditionEnum[]> = {
  string: [
    VariableConditionEnum.equalTo,
    VariableConditionEnum.notEqual,
    VariableConditionEnum.include,
    VariableConditionEnum.notInclude,
    VariableConditionEnum.startWith,
    VariableConditionEnum.endWith,
    VariableConditionEnum.reg,
    VariableConditionEnum.isEmpty,
    VariableConditionEnum.isNotEmpty,
  ],
  number: [
    VariableConditionEnum.equalTo,
    VariableConditionEnum.notEqual,
    VariableConditionEnum.greaterThan,
    VariableConditionEnum.greaterThanOrEqualTo,
    VariableConditionEnum.lessThan,
    VariableConditionEnum.lessThanOrEqualTo,
    VariableConditionEnum.isEmpty,
    VariableConditionEnum.isNotEmpty,
  ],
  boolean: [
    // 蓝本 booleanConditionList：仅 isEmpty/isNotEmpty/equalTo
    VariableConditionEnum.isEmpty,
    VariableConditionEnum.isNotEmpty,
    VariableConditionEnum.equalTo,
  ],
  object: [VariableConditionEnum.isEmpty, VariableConditionEnum.isNotEmpty],
  array: [
    VariableConditionEnum.include,
    VariableConditionEnum.notInclude,
    VariableConditionEnum.lengthEqualTo,
    VariableConditionEnum.lengthNotEqualTo,
    VariableConditionEnum.lengthGreaterThan,
    VariableConditionEnum.lengthGreaterThanOrEqualTo,
    VariableConditionEnum.lengthLessThan,
    VariableConditionEnum.lengthLessThanOrEqualTo,
    VariableConditionEnum.isEmpty,
    VariableConditionEnum.isNotEmpty,
  ],
};

/** 左值数据类型：从被引用节点输出/全局变量声明推断，取不到时不过滤 */
function leftValueType(item: IfElseConditionItemType): string | undefined {
  if (!isReferenceValue(item.variable)) return undefined;
  const [nodeId, key] = item.variable;
  if (nodeId === VARIABLE_NODE_ID) {
    const declared =
      (graph.value.chatConfig.variables || []).find((v) => v.key === key)?.valueType ||
      SYSTEM_VARIABLES.find((v) => v.key === key)?.valueType;
    return declared as string | undefined;
  }
  const source = graph.value.nodes.find((n) => n.nodeId === nodeId);
  return source?.outputs?.find((o) => o.key === key)?.valueType as string | undefined;
}

function conditionOptionsFor(item: IfElseConditionItemType) {
  const valueType = leftValueType(item);
  const normalized = valueType?.startsWith('array') ? 'array' : valueType;
  const allowed = normalized ? CONDITIONS_BY_TYPE[normalized] : undefined;
  const entries = allowed
    ? allowed.map((value) => [value, VariableConditionLabelMap[value]] as const)
    : // 蓝本 allConditionList：未知类型回退集不含正则
      Object.entries(VariableConditionLabelMap).filter(([value]) => value !== VariableConditionEnum.reg);
  return entries.map(([value, label]) => ({ value, label }));
}

/** 蓝本 renderNumberConditionList：数值/长度类条件右值渲染数字输入框 */
const NUMBER_VALUE_CONDITIONS = new Set<string>([
  VariableConditionEnum.greaterThan,
  VariableConditionEnum.greaterThanOrEqualTo,
  VariableConditionEnum.lessThan,
  VariableConditionEnum.lessThanOrEqualTo,
  VariableConditionEnum.lengthEqualTo,
  VariableConditionEnum.lengthNotEqualTo,
  VariableConditionEnum.lengthGreaterThan,
  VariableConditionEnum.lengthGreaterThanOrEqualTo,
  VariableConditionEnum.lengthLessThan,
  VariableConditionEnum.lengthLessThanOrEqualTo,
]);

function isNumberValueCondition(condition?: string) {
  return !!condition && NUMBER_VALUE_CONDITIONS.has(condition);
}

function toggleValueMode(item: IfElseConditionItemType) {
  item.valueType = item.valueType === 'reference' ? 'input' : 'reference';
  item.value = undefined; // 引用与字面量互斥，切换时清空避免脏值
}

function groupLabel(index: number) {
  return index === 0 ? IfElseResultEnum.IF : `${IfElseResultEnum.ELSE_IF} ${index}`;
}

function isUnaryCondition(condition?: string) {
  return condition === VariableConditionEnum.isEmpty || condition === VariableConditionEnum.isNotEmpty;
}

function addGroup() {
  groups.value.push({ condition: 'AND', list: [{ valueType: 'input' }] });
}

function removeGroup(index: number) {
  groups.value.splice(index, 1);
}

function addCondition(groupIndex: number) {
  groups.value[groupIndex].list.push({ valueType: 'input' });
}

function removeCondition(groupIndex: number, itemIndex: number) {
  const group = groups.value[groupIndex];
  group.list.splice(itemIndex, 1);
  if (!group.list.length) group.list.push({ valueType: 'input' });
}
</script>

<style scoped lang="less">
.ifelse-editor {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.ifelse-group {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: #fafbfc;
}

.group-head {
  display: flex;
  align-items: center;
  gap: 8px;

  .group-name {
    font-size: 12px;
    font-weight: 600;
    color: #1e293b;
  }

  .group-remove {
    margin-left: auto;
    color: #94a3b8;

    &:hover {
      color: #dc2626;
    }
  }
}

.condition-row {
  display: flex;
  gap: 6px;
  align-items: center;
}

.condition-variable {
  flex: 1.4;
  min-width: 0;
}

.condition-op {
  flex: 1;
  min-width: 88px;
}

.condition-value {
  flex: 1;
  min-width: 0;
}

.condition-remove {
  color: #94a3b8;
  flex-shrink: 0;

  &:hover {
    color: #dc2626;
  }
}

.value-mode {
  color: #94a3b8;
  flex-shrink: 0;
  padding: 0 4px;

  &:hover {
    color: #3370ff;
  }
}

.add-condition {
  align-self: flex-start;
  color: #4f46e5;
}
</style>
