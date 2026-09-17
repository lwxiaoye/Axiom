<template>
  <main class="detail-page">
    <header class="detail-header">
      <a-button type="text" class="back" @click="emit('back')"><ArrowLeftOutlined /></a-button>
      <span class="kb-icon"><ReadOutlined /></span>
      <div class="title">
        <div>
          <h1>{{ knowledge?.name || '知识库' }}</h1>
          <a-badge :status="knowledge?.status === 'ACTIVE' ? 'success' : 'default'" :text="knowledge?.status === 'ACTIVE' ? '可用' : '停用'" />
        </div>
        <p>{{ knowledge?.description || '暂无描述' }}</p>
      </div>
      <a-button v-if="canEdit" @click="settingsOpen = true"><SettingOutlined /> 设置</a-button>
      <a-button v-if="canEdit" type="primary" @click="uploadOpen = true"><PlusOutlined /> 添加文档</a-button>
    </header>

    <div class="detail-shell">
      <aside>
        <button v-for="item in navItems" :key="item.key" :class="{ active: activeView === item.key }" @click="activeView = item.key">
          <component :is="item.icon" />
          {{ item.label }}
          <em v-if="item.key === 'documents'">{{ documentPagination.total }}</em>
        </button>
        <div class="summary">
          <span>Embedding</span>
          <strong>{{ knowledge?.embeddingModel || '未配置' }}</strong>
          <span>分段数量</span>
          <strong>{{ knowledge?.chunkCount || 0 }}</strong>
        </div>
      </aside>

      <section class="content">
        <template v-if="activeView === 'documents'">
          <div class="section-heading">
            <div><h2>文档</h2><p>文档处理完成后即可参与检索。</p></div>
            <div class="document-toolbar">
              <a-input-search v-model:value="documentKeyword" class="section-search" allow-clear placeholder="搜索文档" @search="loadDocuments" />
              <span v-if="selectedDocumentIds.length" class="selected-document-count">已选 {{ selectedDocumentIds.length }} 项</span>
              <a-button size="small" :disabled="!selectedDocumentIds.length" @click="downloadSelectedDocuments">批量下载原文</a-button>
              <a-popconfirm
                v-if="canEdit"
                title="确定删除选中的文档？相关分段和索引将一并删除。"
                ok-text="删除"
                cancel-text="取消"
                @confirm="removeSelectedDocuments"
              >
                <a-button danger size="small" :disabled="!selectedDocumentIds.length">批量删除</a-button>
              </a-popconfirm>
            </div>
          </div>
          <a-alert v-if="documentError" type="warning" show-icon message="文档列表加载失败" class="section-alert">
            <template #action><a-button size="small" @click="loadDocuments">重试</a-button></template>
          </a-alert>
          <a-table
            row-key="id"
            :columns="documentColumns"
            :data-source="documents"
            :loading="documentLoading"
            :pagination="documentPagination"
            :row-selection="documentRowSelection"
            :scroll="{ y: 'calc(100vh - 440px)' }"
            @change="handleDocumentPage"
          >
              <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'name'">
                <button type="button" class="document-name document-name-button" @click="openDocumentChunks(record)"><FileTextOutlined /><span><strong>{{ record.originalName }}</strong><small>{{ formatSize(record.fileSize) }} · {{ record.fileType.toUpperCase() }}</small></span></button>
              </template>
              <template v-else-if="column.key === 'status'">
                <div class="status-cell">
                  <a-tag :color="statusColor(record.status)">{{ statusText(record.status) }}</a-tag>
                  <a-progress v-if="isProcessing(record.status)" :percent="record.progress" size="small" :show-info="false" />
                  <a-tooltip v-if="shouldShowKnowledgeDocumentError(record.status, record.errorMessage)" :title="record.errorMessage"><InfoCircleOutlined class="error-icon" /></a-tooltip>
                </div>
              </template>
              <template v-else-if="column.key === 'enabled'">
                <a-switch v-if="canEdit" :checked="record.enabled === 1" @change="(value) => toggleDocument(record, Boolean(value))" />
              </template>
              <template v-else-if="column.key === 'action'">
                <a-space>
                  <a-button type="link" size="small" @click="downloadDocument(record)">下载原文</a-button>
                  <template v-if="canEdit">
                    <a-button v-if="record.status === 'FAILED'" type="link" size="small" @click="retry(record)">重试</a-button>
                    <a-popconfirm title="确定删除该文档？" @confirm="removeDocument(record)">
                      <a-button type="link" danger size="small">删除</a-button>
                    </a-popconfirm>
                  </template>
                </a-space>
              </template>
            </template>
          </a-table>
        </template>

        <template v-else-if="activeView === 'chunks'">
          <KnowledgeChunksPanel
            ref="chunksPanelRef"
            v-model:document-id="chunkDocumentId"
            :knowledge-id="knowledgeId"
            :can-edit="canEdit"
            :management="true"
            title-tag="h2"
            description="编辑分段内容后会立即重新生成向量。"
            @edit="editChunk"
            @changed="afterChunkChanged"
          />
        </template>

        <RetrievalTester
          v-else-if="activeView === 'retrieval' && knowledge && canView"
          :knowledge-id="knowledgeId"
          :default-top-k="knowledge.topK"
          :default-threshold="Number(knowledge.scoreThreshold)"
          :management="true"
        />

        <KnowledgeAnalyticsPanel
          v-else-if="activeView === 'analytics'"
          scope="admin"
          :knowledge-id="knowledgeId"
        />

        <template v-else-if="activeView === 'access' && canManageAccess">
          <div class="section-heading"><div><h2>访问权限</h2><p>按用户、角色或部门授予使用权限。</p></div><a-button type="primary" :loading="aclSaving" @click="saveAcl">保存权限</a-button></div>
          <div class="acl-list">
            <div v-for="(item, index) in aclItems" :key="index" class="acl-row">
              <a-select v-model:value="item.subjectType" style="width: 130px" @change="() => handleAclSubjectTypeChange(item)">
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
              <JSelectDept
                v-else
                v-model:value="item.subjectIds"
                placeholder="请选择部门"
                button-text="选择"
              />
              <a-select v-model:value="item.permission" style="width: 130px">
                <a-select-option value="EDITOR">编辑</a-select-option>
                <a-select-option value="VIEWER">查看</a-select-option>
              </a-select>
              <a-button type="text" danger @click="aclItems.splice(index, 1)"><DeleteOutlined /></a-button>
            </div>
            <a-button type="dashed" block @click="addAcl"><PlusOutlined /> 添加授权对象</a-button>
          </div>
        </template>
      </section>
    </div>

    <DocumentUploadModal v-if="canEdit" v-model:open="uploadOpen" :knowledge-id="knowledgeId" :management="true" @success="afterUpload" />

    <a-drawer v-if="canEdit" v-model:open="settingsOpen" title="知识库设置" width="440px">
      <a-form v-if="knowledge" layout="vertical">
        <a-form-item
          v-if="canManageAccess"
          label="知识库状态"
          extra="停用后不参与对话或智能体检索，文档、分段和授权会保留。"
        >
          <a-switch
            :checked="isKnowledgeActive"
            :loading="statusSaving"
            checked-children="可用"
            un-checked-children="停用"
            @change="(enabled) => changeKnowledgeStatus(Boolean(enabled))"
          />
        </a-form-item>
        <a-form-item label="名称"><a-input v-model:value="settingsForm.name" /></a-form-item>
        <a-form-item label="描述"><a-textarea v-model:value="settingsForm.description" :rows="3" /></a-form-item>
        <a-form-item label="返回数量"><a-input-number v-model:value="settingsForm.topK" :min="1" :max="20" /></a-form-item>
        <a-form-item label="相似度阈值">
          <div class="threshold-control">
            <a-slider v-model:value="settingsForm.scoreThreshold" :min="0" :max="1" :step="0.05" />
            <output>{{ Number(settingsForm.scoreThreshold || 0).toFixed(2) }}</output>
          </div>
        </a-form-item>
        <a-button type="primary" block :loading="settingsSaving" @click="saveSettings">保存设置</a-button>
      </a-form>
    </a-drawer>

    <KnowledgeChunkEditorDrawer v-model:open="chunkEditorOpen" :chunk="editingChunk" :can-edit="canEdit" :management="true" width="600px" :rows="14" @saved="afterChunkSaved" />
  </main>
</template>

<script setup lang="ts">
  import { computed, markRaw, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
  import { useRoute } from 'vue-router';
  import {
    ArrowLeftOutlined, BarChartOutlined, DeleteOutlined, FileTextOutlined, InfoCircleOutlined,
    KeyOutlined, PlusOutlined, ReadOutlined, SearchOutlined, SettingOutlined, UnorderedListOutlined,
  } from '@ant-design/icons-vue';
  import { JSelectDept, JSelectRole, JSelectUser } from '/@/components/Form';
  import { useMessage } from '/@/hooks/web/useMessage';
  import {
    deleteManagedDocument, deleteManagedDocuments, downloadManagedKnowledgeDocument, downloadManagedKnowledgeDocumentArchive,
    getManagedDocumentList, getManagedKnowledgeAcl, getManagedKnowledgeDetail,
    retryManagedDocument, saveManagedKnowledgeAcl, setManagedDocumentEnabled, setManagedKnowledgeEnabled, updateManagedKnowledge,
  } from '../knowledge.api';
  import type { KnowledgeBase, KnowledgeChunk, KnowledgeDocument, KnowledgePermission } from '../knowledge.types';
  import { canEditKnowledge, canManageKnowledge, canUseKnowledge, normalizeKnowledgePermission } from '../knowledgePermission';
  import { isKnowledgeDocumentProcessing, knowledgeDocumentStatusText, shouldShowKnowledgeDocumentError } from '../knowledgeStatus';
  import { expandAclItems, groupAclItems, type AclEditorItem } from '/@/utils/aclEditor';
  import KnowledgeChunkEditorDrawer from './KnowledgeChunkEditorDrawer.vue';
  import KnowledgeChunksPanel from './KnowledgeChunksPanel.vue';
  import DocumentUploadModal from './DocumentUploadModal.vue';
  import RetrievalTester from './RetrievalTester.vue';
  import KnowledgeAnalyticsPanel from './KnowledgeAnalyticsPanel.vue';

  const props = defineProps<{ knowledgeId: string }>();
  const emit = defineEmits<{ back: []; deleted: [] }>();
  const route = useRoute();
  const { createMessage } = useMessage();
  const knowledge = ref<KnowledgeBase>();
  const activeView = ref('documents');
  const uploadOpen = ref(false);
  const settingsOpen = ref(false);
  const settingsSaving = ref(false);
  const statusSaving = ref(false);
  const documentLoading = ref(false);
  const documentError = ref(false);
  const documentKeyword = ref('');
  const documents = ref<KnowledgeDocument[]>([]);
  const selectedDocumentIds = ref<string[]>([]);
  const chunkDocumentId = ref<string>();
  const chunksPanelRef = ref<{ reload: () => Promise<void>; reloadDocuments: () => Promise<void> }>();
  const chunkEditorOpen = ref(false);
  const editingChunk = ref<KnowledgeChunk>();
  const aclItems = ref<AclEditorItem<KnowledgePermission>[]>([]);
  const aclSaving = ref(false);
  const settingsForm = reactive<any>({});
  const documentPagination = reactive({ current: 1, pageSize: 20, total: 0, showSizeChanger: true });
  let refreshTimer: ReturnType<typeof setInterval> | undefined;

  const currentPermission = computed<KnowledgePermission>(() => normalizeKnowledgePermission(knowledge.value?.currentPermission) || 'VIEWER');
  const canEdit = computed(() => canEditKnowledge(currentPermission.value));
  const canManageAccess = computed(() => canManageKnowledge(currentPermission.value));
  const isKnowledgeActive = computed(() => knowledge.value?.status === 'ACTIVE');
  const canView = computed(() => isKnowledgeActive.value && canUseKnowledge(knowledge.value ? currentPermission.value : undefined));
  const navItems = computed(() => [
    { key: 'documents', label: '文档', icon: markRaw(FileTextOutlined) },
    { key: 'chunks', label: '分段', icon: markRaw(UnorderedListOutlined) },
    ...(canView.value ? [{ key: 'retrieval', label: '召回测试', icon: markRaw(SearchOutlined) }] : []),
    { key: 'analytics', label: '运营统计', icon: markRaw(BarChartOutlined) },
    ...(canManageAccess.value ? [{ key: 'access', label: '访问权限', icon: markRaw(KeyOutlined) }] : []),
  ]);
  const documentColumns = [
    { title: '文档', key: 'name' },
    { title: '分段', dataIndex: 'chunkCount', width: 90 },
    { title: '处理状态', key: 'status', width: 190 },
    { title: '参与检索', key: 'enabled', width: 100 },
    { title: '更新时间', dataIndex: 'updateTime', width: 170 },
    { title: '', key: 'action', width: 180 },
  ];
  const documentRowSelection = computed(() => ({
    selectedRowKeys: selectedDocumentIds.value,
    onChange: (keys: Array<string | number>) => { selectedDocumentIds.value = keys.map(String); },
  }));
  onMounted(async () => {
    if (route.query.tab === 'analytics') activeView.value = 'analytics';
    await loadKnowledge();
    await Promise.all([loadDocuments(), canManageAccess.value ? loadAcl() : Promise.resolve()]);
    refreshTimer = setInterval(refreshProcessingDocuments, 5000);
  });
  onBeforeUnmount(() => {
    if (refreshTimer) clearInterval(refreshTimer);
  });

  async function loadKnowledge() {
    knowledge.value = await getManagedKnowledgeDetail(props.knowledgeId);
    Object.assign(settingsForm, {
      id: knowledge.value.id,
      name: knowledge.value.name,
      description: knowledge.value.description || '',
      retrievalMode: knowledge.value.retrievalMode,
      topK: knowledge.value.topK,
      scoreThreshold: Number(knowledge.value.scoreThreshold),
    });
  }

  async function loadDocuments() {
    documentLoading.value = true;
    documentError.value = false;
    try {
      const page = await getManagedDocumentList({ knowledgeId: props.knowledgeId, pageNo: documentPagination.current, pageSize: documentPagination.pageSize, keyword: documentKeyword.value || undefined });
      documents.value = page?.records || [];
      documentPagination.total = page?.total || 0;
    } catch {
      documents.value = [];
      documentError.value = true;
    } finally {
      documentLoading.value = false;
    }
  }

  async function refreshProcessingDocuments() {
    if (documents.value.some((item) => isProcessing(item.status))) {
      await Promise.all([loadDocuments(), reloadChunksPanel(), loadKnowledge()]);
    }
  }

  async function reloadChunksPanel() {
    await chunksPanelRef.value?.reload?.();
  }

  async function loadAcl() {
    if (!canManageAccess.value) return;
    aclItems.value = groupAclItems((await getManagedKnowledgeAcl(props.knowledgeId)) || []);
  }

  function handleDocumentPage(page: any) { documentPagination.current = page.current; documentPagination.pageSize = page.pageSize; loadDocuments(); }
  function openDocumentChunks(document: KnowledgeDocument) {
    chunkDocumentId.value = document.id;
    activeView.value = 'chunks';
  }
  async function afterUpload() {
    await Promise.all([loadDocuments(), reloadChunksPanel(), chunksPanelRef.value?.reloadDocuments?.(), loadKnowledge()]);
  }
  async function retry(record: KnowledgeDocument) { if (!canEdit.value) return; await retryManagedDocument(record.id); createMessage.success('已重新提交处理'); loadDocuments(); }
  async function toggleDocument(record: KnowledgeDocument, enabled: boolean) { if (!canEdit.value) return; await setManagedDocumentEnabled(record.id, enabled); record.enabled = enabled ? 1 : 0; }
  async function downloadDocument(record: KnowledgeDocument) {
    await downloadManagedKnowledgeDocument(record.id, record.originalName);
  }
  async function downloadSelectedDocuments() {
    if (!selectedDocumentIds.value.length) return;
    await downloadManagedKnowledgeDocumentArchive(selectedDocumentIds.value);
  }
  async function removeDocument(record: KnowledgeDocument) {
    if (!canEdit.value) return;
    await deleteManagedDocument(record.id);
    selectedDocumentIds.value = selectedDocumentIds.value.filter((id) => id !== record.id);
    if (chunkDocumentId.value === record.id) chunkDocumentId.value = undefined;
    await Promise.all([loadDocuments(), reloadChunksPanel(), chunksPanelRef.value?.reloadDocuments?.(), loadKnowledge()]);
  }
  async function removeSelectedDocuments() {
    if (!canEdit.value || !selectedDocumentIds.value.length) return;
    const deletingIds = selectedDocumentIds.value;
    await deleteManagedDocuments(deletingIds);
    if (chunkDocumentId.value && deletingIds.includes(chunkDocumentId.value)) chunkDocumentId.value = undefined;
    selectedDocumentIds.value = [];
    createMessage.success('已删除选中文档');
    await Promise.all([loadDocuments(), reloadChunksPanel(), chunksPanelRef.value?.reloadDocuments?.(), loadKnowledge()]);
  }
  function editChunk(record: KnowledgeChunk) {
    if (!canEdit.value) return;
    editingChunk.value = record;
    chunkEditorOpen.value = true;
  }

  async function afterChunkSaved() {
    await Promise.all([reloadChunksPanel(), loadDocuments(), loadKnowledge()]);
  }

  async function afterChunkChanged() {
    await Promise.all([loadDocuments(), loadKnowledge()]);
  }

  async function saveSettings() {
    if (!canEdit.value) return;
    settingsSaving.value = true;
    try {
      const updated = await updateManagedKnowledge(settingsForm);
      knowledge.value = {
        ...knowledge.value,
        ...updated,
        currentPermission: updated.currentPermission || knowledge.value?.currentPermission,
      };
      createMessage.success('设置已保存');
      settingsOpen.value = false;
    } finally {
      settingsSaving.value = false;
    }
  }

  async function changeKnowledgeStatus(enabled: boolean) {
    if (!knowledge.value || !canManageAccess.value) return;
    statusSaving.value = true;
    try {
      const updated = await setManagedKnowledgeEnabled(knowledge.value.id, enabled);
      knowledge.value = {
        ...knowledge.value,
        ...updated,
        currentPermission: updated.currentPermission || knowledge.value.currentPermission,
      };
      if (!enabled && activeView.value === 'retrieval') activeView.value = 'documents';
      createMessage.success(enabled ? '知识库已启用' : '知识库已停用');
    } finally {
      statusSaving.value = false;
    }
  }

  function addAcl() { aclItems.value.push({ subjectType: 'USER', subjectIds: [], permission: 'VIEWER' }); }
  function handleAclSubjectTypeChange(item: AclEditorItem<KnowledgePermission>) { item.subjectIds = []; }
  async function saveAcl() {
    if (!canManageAccess.value) return;
    aclSaving.value = true;
    try {
      const items = expandAclItems(aclItems.value);
      await saveManagedKnowledgeAcl(props.knowledgeId, items);
      loadAcl();
    } finally {
      aclSaving.value = false;
    }
  }

  function isProcessing(status: KnowledgeDocument['status']) { return isKnowledgeDocumentProcessing(status); }
  function statusColor(status: string) { return status === 'READY' ? 'green' : status === 'FAILED' ? 'red' : 'default'; }
  function statusText(status: KnowledgeDocument['status']) { return knowledgeDocumentStatusText(status); }
  function formatSize(size: number) { if (!size) return '0 B'; if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`; return `${(size / 1024 / 1024).toFixed(1)} MB`; }
</script>

<style scoped lang="less">
  .detail-page { min-height: 100%; background: #f7f8fa; color: #17202a; }
  .detail-header { display: grid; grid-template-columns: 38px 44px minmax(0, 1fr) auto auto; min-height: 86px; align-items: center; gap: 12px; padding: 14px 28px; border-bottom: 1px solid #e5e7eb; background: rgba(255,255,255,.96); box-shadow: 0 10px 28px rgba(15, 23, 42, .05); }
  .back { width: 36px; height: 36px; }
  .kb-icon { display: grid; width: 42px; height: 42px; place-items: center; border: 1px solid #e5e7eb; border-radius: 8px; background: #fafafa; color: #111827; font-size: 20px; }
  .title > div { display: flex; align-items: center; gap: 12px; }
  .title h1 { margin: 0; color: #101828; font-size: 19px; }
  .title p { max-width: 760px; margin: 4px 0 0; color: #667085; font-size: 13px; line-height: 1.6; }
.detail-shell { display: grid; width: 100%; max-width: none; grid-template-columns: 220px minmax(0, 1fr); min-height: calc(100vh - 146px); margin: 0; border-right: 1px solid #e5e7eb; }
  aside { padding: 22px 14px; border-right: 1px solid #e5e7eb; background: rgba(255,255,255,.92); }
  aside > button { display: flex; width: 100%; min-height: 42px; align-items: center; gap: 10px; margin-bottom: 6px; padding: 10px 12px; border: 0; border-radius: 8px; background: transparent; color: #52606d; cursor: pointer; transition: background-color .18s ease, color .18s ease; }
  aside > button:hover { background: #f4f6f8; color: #111827; }
  aside > button.active { background: #f0f1f3; color: #111827; font-weight: 600; }
  aside em { margin-left: auto; color: #98a2b3; font-size: 12px; font-style: normal; }
  .summary { display: grid; gap: 4px; margin: 28px 8px 0; padding: 18px 12px 0; border-top: 1px solid #eaecf0; }
  .summary span { margin-top: 8px; color: #98a2b3; font-size: 11px; }
  .summary strong { overflow: hidden; color: #475467; font-size: 12px; text-overflow: ellipsis; }
  .content { min-width: 0; padding: 28px 34px 48px; }
  .section-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
  .section-heading h2 { margin: 0; color: #101828; font-size: 22px; }
  .section-heading p { margin: 6px 0 0; color: #667085; font-size: 14px; line-height: 1.6; }
  .threshold-control { display: flex; align-items: center; gap: 12px; }
  .threshold-control :deep(.ant-slider) { min-width: 0; flex: 1; margin: 0; }
  .threshold-control output { min-width: 42px; color: #344054; font-variant-numeric: tabular-nums; text-align: right; }
  .section-search { width: 280px; }
  .document-toolbar { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: wrap; }
  .selected-document-count { color: #667085; font-size: 12px; }
  .section-alert { margin-bottom: 16px; }
  :deep(.ant-table-wrapper) { overflow: hidden; border: 1px solid #e5e7eb; border-radius: 8px; background: #fff; box-shadow: 0 10px 24px rgba(15, 23, 42, .04); }
  :deep(.ant-table-thead > tr > th) { background: #f8fafc; color: #475467; font-weight: 600; }
  .document-name { display: flex; min-width: 0; align-items: center; gap: 11px; }
  .document-name-button { width: 100%; padding: 0; border: 0; background: transparent; cursor: pointer; text-align: left; }
  .document-name-button:hover strong { color: #2563eb; }
  .document-name > span:last-child { display: flex; min-width: 0; flex-direction: column; }
  .document-name strong { overflow: hidden; color: #1d2939; text-overflow: ellipsis; white-space: nowrap; }
  .document-name small { margin-top: 4px; color: #98a2b3; }
  .status-cell { display: flex; align-items: center; gap: 8px; }
  .status-cell :deep(.ant-progress) { width: 62px; margin: 0; }
  .error-icon { color: #d92d20; }
  .acl-list { max-width: 860px; padding: 18px; border: 1px solid #e5e7eb; border-radius: 8px; background: #fff; box-shadow: 0 10px 24px rgba(15, 23, 42, .04); }
  .acl-row { display: grid; grid-template-columns: 130px minmax(180px, 1fr) 130px 36px; gap: 10px; margin-bottom: 10px; }
  @media (max-width: 900px) {
    .document-toolbar { justify-content: flex-start; }
    .detail-header { grid-template-columns: 38px 44px 1fr; }
    .detail-header > .ant-btn:not(.back) { display: none; }
    .detail-shell { grid-template-columns: 1fr; }
    aside { display: flex; overflow-x: auto; border-right: 0; border-bottom: 1px solid #e5e7eb; }
    aside > button { width: auto; flex: 0 0 auto; }
    .summary { display: none; }
    .content { padding: 22px 14px; }
    .section-heading { align-items: stretch; flex-direction: column; }
    .section-search { width: 100%; }
  }
</style>
