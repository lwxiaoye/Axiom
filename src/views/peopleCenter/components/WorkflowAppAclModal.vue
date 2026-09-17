<template>
  <a-modal
    v-model:open="open"
    :width="720"
    :title="modalTitle"
    :footer="null"
    :destroyOnClose="true"
    :maskClosable="false"
    wrapClassName="workflow-app-acl-modal"
    @cancel="close"
  >
    <div class="workflow-app-acl-body">
      <div class="acl-heading">
        <div>
          <strong>分享给其他人</strong>
          <p>可按用户、角色或部门授予查看/编辑权限。删除仍仅限拥有者或管理员。</p>
        </div>
      </div>

      <a-spin :spinning="loading">
        <div class="acl-list">
          <div v-for="(item, index) in aclItems" :key="index" class="acl-row">
            <a-select v-model:value="item.subjectType" class="acl-type" @change="() => (item.subjectIds = [])">
              <a-select-option value="USER">用户</a-select-option>
              <a-select-option value="ROLE">角色</a-select-option>
              <a-select-option value="DEPARTMENT">部门</a-select-option>
            </a-select>
            <JSelectUser
              v-if="item.subjectType === 'USER'"
              v-model:value="item.subjectIds"
              row-key="id"
              label-key="realname"
              placeholder="请选择用户"
              button-text="选择"
            />
            <JSelectRole
              v-else-if="item.subjectType === 'ROLE'"
              v-model:value="item.subjectIds"
              placeholder="请选择角色"
              button-text="选择"
            />
            <JSelectDept v-else v-model:value="item.subjectIds" placeholder="请选择部门" button-text="选择" />
            <a-select v-model:value="item.permission" class="acl-permission">
              <a-select-option value="VIEWER">查看</a-select-option>
              <a-select-option value="EDITOR">编辑</a-select-option>
            </a-select>
            <button class="acl-remove" type="button" title="删除" @click="aclItems.splice(index, 1)">
              <DeleteOutlined />
            </button>
          </div>
          <a-empty v-if="!aclItems.length && !loading" description="暂无授权对象" />
          <button class="acl-add" type="button" @click="addAcl"><PlusOutlined /> 添加授权对象</button>
        </div>
      </a-spin>
    </div>

    <div class="workflow-app-acl-footer">
      <a-button @click="close">取消</a-button>
      <a-button type="primary" :loading="saving" @click="save">保存授权</a-button>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue';
import { JSelectDept, JSelectRole, JSelectUser } from '/@/components/Form';
import {
  queryWorkflowAppAcl,
  saveWorkflowAppAcl,
  type AiWorkflowApp,
  type WorkflowAclItem,
} from '../../workflow/api/workflow.api';
import { expandAclItems, groupAclItems, type AclEditorItem } from '/@/utils/aclEditor';

const emit = defineEmits<{
  (e: 'success'): void;
}>();

const open = ref(false);
const loading = ref(false);
const saving = ref(false);
const record = ref<Partial<AiWorkflowApp> | null>(null);
const aclItems = ref<AclEditorItem<WorkflowAclItem['permission']>[]>([]);

const modalTitle = computed(() => {
  const name = record.value?.name || (record.value as Recordable | null)?.appName;
  return name ? '授权设置: ' + name : '授权设置';
});

async function init(item: Partial<AiWorkflowApp>) {
  record.value = item;
  aclItems.value = [];
  open.value = true;
  if (!item?.id) return;
  loading.value = true;
  try {
    aclItems.value = groupAclItems(await queryWorkflowAppAcl(String(item.id)).catch(() => []));
  } finally {
    loading.value = false;
  }
}

function addAcl() {
  aclItems.value.push({ subjectType: 'USER', subjectIds: [], permission: 'VIEWER' });
}

async function save() {
  if (!record.value?.id) return;
  saving.value = true;
  try {
    await saveWorkflowAppAcl(String(record.value.id), expandAclItems(aclItems.value));
    emit('success');
    close();
  } finally {
    saving.value = false;
  }
}

function close() {
  open.value = false;
}

defineExpose({ init });
</script>

<style lang="less">
.workflow-app-acl-modal {
  .ant-modal-body {
    padding: 0;
  }
}
</style>

<style scoped lang="less">
.workflow-app-acl-body {
  padding: 18px 24px 10px;
}

.acl-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 12px;
  padding: 12px 14px;
  border: 1px solid #eceef3;
  border-radius: 10px;
  background: #fafbfc;

  strong {
    color: #111827;
    font-size: 13px;
  }

  p {
    margin: 6px 0 0;
    color: #64748b;
    font-size: 12px;
    line-height: 1.7;
  }
}

.acl-list {
  display: grid;
  gap: 10px;
}

.acl-row {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr) 96px 34px;
  gap: 8px;
  align-items: center;
}

.acl-remove,
.acl-add {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #e3e5ea;
  border-radius: 8px;
  background: #fff;
  color: #475569;
  cursor: pointer;
  transition: border-color 0.18s ease, color 0.18s ease;

  &:hover {
    border-color: #111827;
    color: #111827;
  }
}

.acl-remove {
  width: 34px;
  height: 32px;
}

.acl-add {
  justify-self: start;
  gap: 6px;
  height: 32px;
  padding: 0 10px;
}

.workflow-app-acl-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 12px 24px 18px;
  border-top: 1px solid #edf2f7;
}

@media (max-width: 720px) {
  .acl-heading,
  .acl-row {
    grid-template-columns: 1fr;
  }

  .acl-heading {
    display: grid;
  }
}
</style>
