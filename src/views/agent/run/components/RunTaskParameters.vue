<template>
  <section v-if="variables.length" class="run-task-parameters">
    <button type="button" class="params-head" :aria-expanded="String(!collapsed)" @click="toggle">
      <span>任务参数</span>
      <small>{{ collapsed ? summary : '运行时按工作流配置传入' }}</small>
    </button>
    <div v-if="!collapsed" class="params-grid">
      <label v-for="variable in variables" :key="variable.id || variable.key" class="param-field">
        <span>{{ variable.label || variable.key }}<i v-if="variable.required">*</i></span>
        <a-switch v-if="variable.type === VariableInputEnum.switch" :checked="!!modelValue[variable.key]" :disabled="disabled" size="small" @change="set(variable.key, $event)" />
        <a-input-number v-else-if="variable.type === VariableInputEnum.numberInput" :value="modelValue[variable.key]" :min="variable.min" :max="variable.max" :disabled="disabled" style="width:100%" @change="set(variable.key, $event)" />
        <a-select v-else-if="variable.type === VariableInputEnum.select" :value="modelValue[variable.key]" :options="options(variable)" :disabled="disabled" @change="set(variable.key, $event)" />
        <a-select v-else-if="variable.type === VariableInputEnum.multipleSelect" :value="modelValue[variable.key]" mode="multiple" :options="options(variable)" :disabled="disabled" @change="set(variable.key, $event)" />
        <a-date-picker v-else-if="variable.type === VariableInputEnum.timePointSelect" :value="modelValue[variable.key]" :disabled="disabled" show-time value-format="YYYY-MM-DD HH:mm:ss" style="width:100%" @change="set(variable.key, $event)" />
        <a-range-picker v-else-if="variable.type === VariableInputEnum.timeRangeSelect" :value="modelValue[variable.key]" :disabled="disabled" show-time value-format="YYYY-MM-DD HH:mm:ss" style="width:100%" @change="set(variable.key, $event)" />
        <a-input-password v-else-if="variable.type === VariableInputEnum.password" :value="modelValue[variable.key]" :maxlength="variable.maxLength" :disabled="disabled" @update:value="set(variable.key, $event)" />
        <a-textarea v-else-if="variable.type === VariableInputEnum.textarea" :value="modelValue[variable.key]" :rows="2" :maxlength="variable.maxLength" :disabled="disabled" @update:value="set(variable.key, $event)" />
        <a-input v-else :value="modelValue[variable.key]" :maxlength="variable.maxLength" :disabled="disabled" @update:value="set(variable.key, $event)" />
        <small v-if="variable.description">{{ variable.description }}</small>
      </label>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { VariableItemType } from '../../../workflow/core/type';
import { VariableInputEnum } from '../../../workflow/core/constants';

const props = defineProps<{ variables: VariableItemType[]; modelValue: Record<string, any>; disabled?: boolean }>();
const emit = defineEmits<{ (e: 'update:modelValue', value: Record<string, any>): void }>();
const collapsed = ref(false);

const allRequiredFilled = computed(() => props.variables.every((variable) => {
  if (!variable.required) return true;
  const value = props.modelValue[variable.key];
  return !(value === undefined || value === null || value === '' || (Array.isArray(value) && !value.length));
}));
const summary = computed(() => {
  if (!allRequiredFilled.value) return '请补齐必填参数';
  const filled = props.variables.filter((variable) => {
    const value = props.modelValue[variable.key];
    return value !== undefined && value !== null && value !== '' && (!Array.isArray(value) || value.length);
  });
  return filled.length ? `已填写 ${filled.length}/${props.variables.length} 项 · 点击展开` : '点击填写参数';
});

watch(allRequiredFilled, (ready) => {
  collapsed.value = ready;
}, { immediate: true });

function toggle() {
  if (allRequiredFilled.value) collapsed.value = !collapsed.value;
}
function set(key: string, value: any) { emit('update:modelValue', { ...props.modelValue, [key]: value }); }
function options(variable: VariableItemType) { return (variable.enums || []).map((item) => ({ label: item.label || item.value, value: item.value })); }
</script>

<style lang="less" scoped>
.run-task-parameters { padding: 12px; border: 1px solid #e4e5e8; border-radius: 14px; background: #fafafa; }
.params-head { display: flex; width: 100%; align-items: baseline; justify-content: space-between; gap: 8px; margin: 0 0 10px; padding: 0; border: 0; background: transparent; color: #36383e; font-size: 13px; font-weight: 650; text-align: left; cursor: pointer; }
.params-head small { color: #91939a; font-size: 11px; font-weight: 400; text-align: right; }
.params-head:focus-visible { outline: 2px solid #7d89a8; outline-offset: 4px; border-radius: 5px; }
.params-head[aria-expanded='false'] { margin-bottom: 0; }
.params-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
.param-field { display: grid; gap: 5px; min-width: 0; color: #565960; font-size: 12px; }
.param-field > span i { margin-left: 2px; color: #df3b43; font-style: normal; }
.param-field > small { color: #96989f; font-size: 11px; }
.param-field :deep(.ant-input), .param-field :deep(.ant-input-number), .param-field :deep(.ant-select-selector) { border-radius: 8px; }
</style>
