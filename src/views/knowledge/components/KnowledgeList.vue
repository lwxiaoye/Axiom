<template>
  <main class="knowledge-page">
    <header class="page-header">
      <div>
        <span class="eyebrow">企业知识资产</span>
        <h1>知识库</h1>
        <p>集中维护可检索的企业文档，为智能体提供准确、可追溯的上下文。</p>
      </div>
      <div class="header-actions">
        <a-button size="large" @click="goCampusAssistant">校园百事通配置</a-button>
        <a-button type="primary" size="large" class="primary-action" @click="openCreate">
          <template #icon><PlusOutlined /></template>
          创建知识库
        </a-button>
      </div>
    </header>
    <section class="workspace">
      <div class="toolbar">
        <a-input-search v-model:value="keyword" class="search" allow-clear placeholder="搜索名称或描述" @search="load" />
      </div>

      <a-alert
        v-if="loadError"
        type="warning"
        show-icon
        message="知识库暂时无法加载"
        description="请检查后端服务和菜单权限后重试。"
        class="load-alert"
      >
        <template #action><a-button size="small" @click="load">重试</a-button></template>
      </a-alert>

      <a-table
        class="knowledge-table"
        row-key="id"
        table-layout="fixed"
        :columns="columns"
        :data-source="items"
        :loading="loading"
        :pagination="pagination"
        :custom-row="rowEvents"
        @change="handleTableChange"
      >
        <template #emptyText>
          <a-empty>
            <a-button type="primary" @click="openCreate">创建第一个知识库</a-button>
          </a-empty>
        </template>
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'name'">
            <div class="name-cell">
              <span class="kb-icon"><ReadOutlined /></span>
              <span>
                <strong>{{ record.name }}</strong>
                <small :title="record.description || '暂无描述'">{{ record.description || '暂无描述' }}</small>
              </span>
            </div>
          </template>
          <template v-else-if="column.key === 'assets'">
            <span class="asset-cell">
              <span class="asset-pill">{{ record.documentCount || 0 }} 文档</span>
              <span class="asset-pill secondary">{{ record.chunkCount || 0 }} 分段</span>
            </span>
          </template>
          <template v-else-if="column.key === 'status'">
            <span class="status-cell">
              <a-badge :status="record.status === 'ACTIVE' ? 'success' : 'default'" :text="record.status === 'ACTIVE' ? '可用' : '停用'" />
            </span>
          </template>
          <template v-else-if="column.key === 'action'">
            <a-dropdown>
              <a-button type="text" size="small" class="icon-action" @click.stop>
                <MoreOutlined />
              </a-button>
              <template #overlay>
                <a-menu>
                  <a-menu-item @click="emit('open', record.id)">管理</a-menu-item>
                  <a-menu-item @click="openEdit(record)">编辑</a-menu-item>
                  <a-menu-divider />
                  <a-menu-item danger @click="confirmDelete(record)">删除</a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>
          </template>
        </template>
      </a-table>
    </section>

    <a-modal
      v-model:open="modalOpen"
      :title="editingId ? '编辑知识库' : '创建知识库'"
      :confirm-loading="saving"
      ok-text="保存"
      :body-style="{ padding: '20px 24px' }"
      @ok="save"
    >
      <a-form layout="vertical">
        <a-form-item label="名称" required>
          <a-input v-model:value="form.name" :maxlength="128" placeholder="例如：产品帮助中心" />
        </a-form-item>
        <a-form-item label="描述">
          <a-textarea v-model:value="form.description" :rows="3" :maxlength="1000" placeholder="说明知识库的内容和适用范围" />
        </a-form-item>
      </a-form>
    </a-modal>
  </main>
</template>

<script setup lang="ts">
import { createVNode, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { Modal } from 'ant-design-vue';
import { ExclamationCircleOutlined, MoreOutlined, PlusOutlined, ReadOutlined } from '@ant-design/icons-vue';
import { useMessage } from '/@/hooks/web/useMessage';
import { createManagedKnowledge, deleteManagedKnowledge, getManagedKnowledgeList, updateManagedKnowledge } from '../knowledge.api';
import type { KnowledgeBase } from '../knowledge.types';

const emit = defineEmits<{ open: [id: string] }>();
const router = useRouter();
const { createMessage } = useMessage();

const loading = ref(false);
const saving = ref(false);
const loadError = ref(false);
const modalOpen = ref(false);
const editingId = ref('');
const keyword = ref('');
const items = ref<KnowledgeBase[]>([]);

const pagination = reactive({
  current: 1,
  pageSize: 12,
  total: 0,
  showSizeChanger: true,
});

const form = reactive({
  name: '',
  description: '',
});

const columns = [
  { title: '知识库', key: 'name' },
  { title: '内容', key: 'assets', width: 176 },
  { title: '状态', key: 'status', width: 92 },
  { title: '创建人', dataIndex: 'createBy_dictText', width: 112 },
  { title: '更新时间', dataIndex: 'updateTime', width: 156 },
  { title: '', key: 'action', width: 56, align: 'center' },
];

function goCampusAssistant() {
  router.push('/newapi/campus-assistant');
}

onMounted(load);

async function load() {
  loading.value = true;
  loadError.value = false;
  try {
    const page = await getManagedKnowledgeList({
      pageNo: pagination.current,
      pageSize: pagination.pageSize,
      keyword: keyword.value || undefined,
      scope: 'all',
    });
    items.value = page?.records || [];
    pagination.total = page?.total || 0;
  } catch {
    items.value = [];
    pagination.total = 0;
    loadError.value = true;
  } finally {
    loading.value = false;
  }
}

function handleTableChange(pageInfo: any) {
  pagination.current = pageInfo.current;
  pagination.pageSize = pageInfo.pageSize;
  load();
}

function rowEvents(record: KnowledgeBase) {
  return {
    class: 'clickable-row',
    onClick: () => emit('open', record.id),
  };
}

function openCreate() {
  editingId.value = '';
  Object.assign(form, { name: '', description: '' });
  modalOpen.value = true;
}

function openEdit(record: KnowledgeBase) {
  editingId.value = record.id;
  Object.assign(form, { name: record.name, description: record.description || '' });
  modalOpen.value = true;
}

async function save() {
  if (!form.name.trim()) {
    createMessage.warning('请输入知识库名称');
    return;
  }
  saving.value = true;
  try {
    const payload = { ...form, name: form.name.trim(), id: editingId.value || undefined };
    if (editingId.value) {
      await updateManagedKnowledge(payload);
      createMessage.success('知识库已更新');
    } else {
      await createManagedKnowledge(payload);
      createMessage.success('知识库已创建');
    }
    modalOpen.value = false;
    load();
  } finally {
    saving.value = false;
  }
}

function confirmDelete(record: KnowledgeBase) {
  Modal.confirm({
    title: `删除“${record.name}”？`,
    icon: createVNode(ExclamationCircleOutlined),
    content: '知识库中存在文档时需要先删除文档。',
    okType: 'danger',
    async onOk() {
      await deleteManagedKnowledge(record.id);
      createMessage.success('知识库已删除');
      load();
    },
  });
}
</script>

<style scoped lang="less">
.knowledge-page {
  min-height: 100%;
  padding: 28px 32px 44px;
  background: #f7f8fa;
  color: #17202a;
}
.page-header {
  display: flex;
  max-width: 1440px;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin: 0 auto 20px;
}
.eyebrow {
  color: #8a8f99;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
h1 {
  margin: 5px 0 0;
  color: #101828;
  font-size: 30px;
  line-height: 1.25;
}
.page-header p {
  max-width: 680px;
  margin: 8px 0 0;
  color: #667085;
  line-height: 1.65;
}
.header-actions {
  display: flex;
  flex-shrink: 0;
  gap: 12px;
  align-items: center;
}
.primary-action {
  min-width: 144px;
  height: 40px;
  box-shadow: 0 8px 18px rgba(15, 23, 42, 0.12);
}
.workspace {
  max-width: 1440px;
  margin: auto;
  overflow: hidden;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 14px 34px rgba(15, 23, 42, 0.06);
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 18px;
  border-bottom: 1px solid #edf0f3;
  background: #fbfcfe;
}
.search {
  width: 320px;
}
.load-alert {
  margin: 16px 20px 0;
}
.name-cell {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 12px;
}
.name-cell > span:last-child {
  display: flex;
  min-width: 0;
  flex-direction: column;
}
.name-cell strong,
.name-cell small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.name-cell strong {
  color: #1d2939;
}
.name-cell small {
  margin-top: 4px;
  color: #7a8694;
}
.kb-icon {
  display: grid;
  width: 38px;
  height: 38px;
  flex: 0 0 auto;
  place-items: center;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fafafa;
  color: #111827;
  font-size: 18px;
}
.asset-pill {
  display: inline-flex;
  min-width: 64px;
  align-items: center;
  justify-content: center;
  padding: 3px 8px;
  border-radius: 999px;
  background: #eef0f3;
  color: #111827;
  font-size: 12px;
}
.asset-cell,
.status-cell {
  display: inline-flex;
  align-items: center;
  white-space: nowrap;
}
.asset-cell {
  gap: 8px;
}
.asset-pill.secondary {
  margin-left: 0;
  background: #f2f4f7;
  color: #52606d;
}
.icon-action {
  width: 32px;
  height: 32px;
}
:deep(.ant-table-thead > tr > th) {
  background: #f8fafc;
  color: #475467;
  font-weight: 600;
}
:deep(.clickable-row) {
  cursor: pointer;
  transition: background-color 0.18s ease;
}
:deep(.clickable-row:hover > td) {
  background: #f8fafc !important;
}
@media (max-width: 760px) {
  .knowledge-page {
    padding: 20px 12px;
  }
  .page-header,
  .toolbar {
    align-items: stretch;
    flex-direction: column;
  }
  .search {
    width: 100%;
  }
}
</style>
