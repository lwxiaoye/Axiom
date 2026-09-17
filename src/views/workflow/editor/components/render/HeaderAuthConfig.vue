<template>
  <div class="header-auth" @mousedown.stop @click.stop>
    <span class="auth-label">鉴权</span>
    <a-select
      v-model:value="authType"
      size="small"
      class="auth-type"
      popup-class-name="wf-node-select-popup"
      :options="typeOptions"
      @change="commit"
    />
    <template v-if="authType !== 'none'">
      <a-input
        v-if="authType === 'custom'"
        v-model:value="customKey"
        size="small"
        class="auth-key"
        placeholder="Header 名"
        @change="commit"
      />
      <a-input-password
        v-model:value="secretValue"
        size="small"
        class="auth-value"
        :placeholder="authType === 'basic' ? 'username:password' : '密钥值'"
        @change="commit"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { NodeInputKeyEnum } from '../../../core/constants';
import type { StoreNodeItemType } from '../../../core/type';
import { getNodeInput } from '../../../core/utils';

const props = defineProps<{ node: StoreNodeItemType }>();

const typeOptions = [
  { label: '无', value: 'none' },
  { label: 'Bearer', value: 'bearer' },
  { label: 'Basic', value: 'basic' },
  { label: '自定义', value: 'custom' },
];

const stored = (getNodeInput(props.node, NodeInputKeyEnum.headerSecret)?.value || {}) as Record<string, any>;
const authType = ref<string>(stored.type || 'none');
const customKey = ref<string>(stored.key || '');
const secretValue = ref<string>(stored.value || '');

/** 蓝本 system_header_secret：{type, key?, value}，hidden 输入持久化 */
function commit() {
  const input = getNodeInput(props.node, NodeInputKeyEnum.headerSecret);
  if (!input) return;
  input.value =
    authType.value === 'none'
      ? undefined
      : {
          type: authType.value,
          ...(authType.value === 'custom' ? { key: customKey.value } : {}),
          value: secretValue.value,
        };
}
</script>

<style scoped lang="less">
.header-auth {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 4px;
}

.auth-label {
  font-size: 12px;
  color: #64748b;
  flex-shrink: 0;
}

.auth-type {
  width: 92px;
  flex-shrink: 0;
}

.auth-key {
  width: 30%;
}

.auth-value {
  flex: 1;
  min-width: 0;
}
</style>
