<template>
  <section class="my-knowledge-section">
    <template v-if="!selectedKnowledgeId">
      <div class="knowledge-list-header">
        <div class="knowledge-title-block">
          <span>KNOWLEDGE</span>
          <h2>我的知识库</h2>
          <p>集中管理用于对话检索的文档与内容。</p>
        </div>
        <div class="knowledge-toolbar">
          <label class="market-search" aria-label="搜索知识库">
            <SearchOutlined />
            <input v-model="keyword" placeholder="搜索知识库名称或描述..." @keydown.enter="reload" />
          </label>
          <button class="market-refresh" type="button" @click="reload">刷新</button>
        </div>
      </div>

      <div class="category-filter" aria-label="知识库范围筛选">
        <button
          v-for="item in scopeOptions"
          :key="item.value"
          :class="['category-filter-item', { active: scope === item.value }]"
          type="button"
          @click="switchScope(item.value)"
        >
          <span>{{ item.label }}</span>
          <em>{{ item.count }}</em>
        </button>
      </div>

      <div v-if="loading" class="status-box"><LoadingOutlined /> 正在加载知识库...</div>
      <div v-else-if="loadError" class="status-box knowledge-error">
        <ReadOutlined />
        <strong>知识库暂时无法加载</strong>
        <button type="button" @click="reload">重试</button>
      </div>
      <div v-else class="app-grid knowledge-grid">
        <button v-if="scope === 'owned'" class="knowledge-create-card" type="button" title="创建知识库" @click="openCreate">
          <span class="knowledge-create-symbol"><PlusOutlined /></span>
          <span class="knowledge-create-copy">
            <strong>创建知识库</strong>
            <small>上传文档，让对话可以检索你的内容</small>
          </span>
        </button>

        <article
          v-for="item in items"
          :key="item.id"
          class="app-card knowledge-card"
          role="button"
          tabindex="0"
          @click="openDetail(item)"
          @keydown.enter="openDetail(item)"
        >
          <div class="knowledge-card-main">
                <div class="knowledge-card-top">
                  <span class="knowledge-icon"><ReadOutlined /></span>
                  <span class="knowledge-card-identity">
                    <strong :title="item.name"><span class="app-card-title">{{ displayKnowledgeName(item) }}</span></strong>
                    <small>知识库 <span class="knowledge-card-creator">· 创建人：{{ displayCreator(item) }}</span></small>
                  </span>
                </div>
            <p class="knowledge-card-description" :title="displayDescription(item)">{{ displayDescription(item) }}</p>
            <footer>
              <span :class="['knowledge-card-summary', item.status === 'ACTIVE' ? 'active' : 'disabled']">
                <i></i>{{ item.status === 'ACTIVE' ? '可用' : '停用' }}
                <em>·</em>{{ item.documentCount || 0 }} 个文档
                <em>·</em>{{ item.chunkCount || 0 }} 个分段
              </span>
              <div v-if="scope === 'owned'" class="knowledge-card-ops" @click.stop @keydown.enter.stop>
                <button type="button" title="编辑知识库" aria-label="编辑知识库" @click="openEdit(item)"><EditOutlined /></button>
                <a-popconfirm title="确定删除该知识库？" @confirm="removeKnowledge(item)">
                  <button type="button" class="danger" title="删除知识库" aria-label="删除知识库"><DeleteOutlined /></button>
                </a-popconfirm>
              </div>
            </footer>
          </div>
        </article>

        <div v-if="scope === 'shared' && !items.length" class="my-agent-empty">
          {{ scope === 'owned' ? '还没有创建知识库' : '暂无共享给我的知识库' }}
        </div>
      </div>

      <div class="app-load-more" aria-live="polite">
        <template v-if="pagination.total">共 {{ pagination.total }} 个知识库</template>
        <button v-if="pagination.current > 1" type="button" @click="changeKnowledgePage(-1)">上一页</button>
        <button v-if="pagination.current * pagination.pageSize < pagination.total" type="button" @click="changeKnowledgePage(1)">下一页</button>
      </div>
    </template>

    <template v-else>
      <div class="knowledge-detail-header">
        <button type="button" class="knowledge-back-btn" title="返回我的知识库" aria-label="返回我的知识库" @click="backToList">
          <ArrowLeftOutlined />
        </button>
        <div>
          <strong>{{ currentKnowledge?.name || '知识库' }}</strong>
          <p>{{ currentKnowledge?.description || '暂无描述' }}</p>
        </div>
        <button v-if="canUseCurrent" type="button" @click="useCurrentKnowledge"><MessageOutlined /> 在对话中使用</button>
        <button v-if="canEditCurrent" type="button" @click="settingsOpen = true"><SettingOutlined /> 设置</button>
        <button v-if="canEditCurrent" type="button" @click="uploadOpen = true"><CloudUploadOutlined /> 添加文档</button>
      </div>

      <div class="knowledge-detail-shell">
        <aside class="knowledge-detail-nav">
          <button v-for="item in visibleDetailViews" :key="item.key" :class="{ active: activeView === item.key }" type="button" @click="activeView = item.key">
            <component :is="item.icon" />
            <span>{{ item.label }}</span>
          </button>
        </aside>

        <section class="knowledge-detail-content">
          <template v-if="activeView === 'documents'">
            <div class="knowledge-section-heading">
              <div><h3>文档</h3><p>文档处理完成后即可参与检索。</p></div>
              <div class="knowledge-document-toolbar">
                <div class="market-search compact"><SearchOutlined /><input v-model="documentKeyword" placeholder="搜索文档..." @keydown.enter="loadDocuments" /></div>
                <div v-if="selectedDocumentIds.length" class="knowledge-document-bulk-actions" aria-label="批量操作">
                  <span class="selected-document-count">已选 {{ selectedDocumentIds.length }} 项</span>
                  <button type="button" class="document-table-action" @click="downloadSelectedDocuments">
                    <DownloadOutlined /> 下载原文
                  </button>
                  <a-popconfirm
                    v-if="canEditCurrent"
                    title="确定删除选中的文档？相关分段和索引将一并删除。"
                    ok-text="删除"
                    cancel-text="取消"
                    @confirm="removeSelectedDocuments"
                  >
                    <button type="button" class="document-table-action danger"><DeleteOutlined /> 删除</button>
                  </a-popconfirm>
                </div>
              </div>
            </div>

            <div v-if="documentLoading" class="knowledge-panel-state"><LoadingOutlined /> 正在加载文档...</div>
            <div v-else-if="documentError" class="knowledge-panel-state">文档列表加载失败 <button type="button" @click="loadDocuments">重试</button></div>
            <div v-else class="knowledge-list-panel">
              <div class="knowledge-list-scroll">
                <div class="knowledge-table">
                  <div class="knowledge-document-table-header" role="row">
                    <span aria-hidden="true"></span>
                    <span>文档</span>
                    <span>状态</span>
                    <span>参与检索</span>
                    <span>操作</span>
                  </div>
                  <div v-for="item in documents" :key="item.id" class="knowledge-table-row document-row">
                    <input
                      class="document-select"
                      type="checkbox"
                      :checked="selectedDocumentIds.includes(item.id)"
                      :aria-label="`选择文档 ${item.originalName}`"
                      @change="toggleDocumentSelection(item.id, $event)"
                    />
                    <button type="button" class="document-name-button" @click="openDocumentChunks(item)">
                      <FileTextOutlined />
                      <span>
                        <strong>{{ item.originalName }}</strong>
                        <em>{{ formatSize(item.fileSize) }} &middot; {{ item.fileType?.toUpperCase() || 'FILE' }}<template v-if="item.chunkCount"> &middot; {{ item.chunkCount }} 段</template></em>
                      </span>
                    </button>
                    <span class="doc-status">
                      <a-tag :color="statusColor(item.status)">{{ statusText(item.status) }}</a-tag>
                      <a-progress v-if="isProcessing(item.status)" class="doc-progress" :percent="item.progress || 0" size="small" :show-info="false" />
                      <a-tooltip v-if="item.status === 'FAILED' && item.errorMessage" :title="item.errorMessage">
                        <InfoCircleOutlined class="doc-error" />
                      </a-tooltip>
                    </span>
                    <a-switch v-if="canEditCurrent" class="document-retrieval-switch" :checked="item.enabled === 1" @change="(value) => toggleDocument(item, Boolean(value))" />
                    <span v-else class="document-enable-readonly">—</span>
                    <span class="doc-actions">
                      <button type="button" class="document-table-action" @click="downloadDocumentItem(item)">下载原文</button>
                      <button v-if="canEditCurrent && item.status === 'FAILED'" type="button" class="document-table-action doc-retry" @click="retryDocumentItem(item)">重试</button>
                      <a-popconfirm v-if="canEditCurrent" title="确定删除该文档？" @confirm="removeDocumentItem(item)">
                        <button type="button" class="document-table-action danger">删除</button>
                      </a-popconfirm>
                    </span>
                  </div>
                  <a-empty v-if="!documents.length" description="暂无文档" />
                </div>
              </div>
              <a-pagination
                v-if="documentPagination.total"
                v-model:current="documentPagination.current"
                class="knowledge-list-pagination"
                :page-size="documentPagination.pageSize"
                :total="documentPagination.total"
                show-size-changer
                :show-total="(total) => `共 ${total} 条`"
                @change="changeDocumentPage"
              />
            </div>
          </template>

          <template v-else-if="activeView === 'chunks'">
            <KnowledgeChunksPanel
              ref="chunksPanelRef"
              v-model:document-id="chunkDocumentId"
              :knowledge-id="selectedKnowledgeId"
              :can-edit="canEditCurrent"
              @edit="editChunk"
              @changed="afterChunkChanged"
            />
          </template>

          <RetrievalTester
            v-else-if="activeView === 'retrieval' && currentKnowledge && canUseCurrent"
            :knowledge-id="selectedKnowledgeId"
            :default-top-k="currentKnowledge.topK"
            :default-threshold="Number(currentKnowledge.scoreThreshold)"
          />

          <KnowledgeAnalyticsPanel
            v-else-if="activeView === 'analytics' && isActualOwner"
            :knowledge-id="selectedKnowledgeId"
          />

          <template v-else-if="activeView === 'access'">
            <div class="knowledge-section-heading">
              <div>
                <h3>授权</h3>
                <p>按用户或角色授予查看或编辑权限。</p>
              </div>
              <button v-if="canManageAccess" class="knowledge-save-button" type="button" :disabled="aclSaving" @click="saveAcl">
                {{ aclSaving ? '保存中' : '保存授权' }}
              </button>
            </div>

            <div v-if="aclLoading" class="knowledge-panel-state"><LoadingOutlined /> 正在加载授权...</div>
            <div v-else class="knowledge-acl-panel">
              <div v-if="canManageAccess" class="acl-editor-list">
                <!--
                  只读行：所有者自己那条（服务端始终保留，改了也没用），以及历史数据里的部门授权
                  （auth-api 没有部门概念，选不了也改不了，但保存时要原样带回去，否则整表替换会把它删掉）。
                  这些行以前也进了编辑器：服务端存的是小写 user，模板按大写比对不上，就落进了
                  v-else 的 JSelectDept，一挂载就打两个已下线的 sysDepart 接口——授权页打开即报 404 的根源。
                -->
                <div v-for="(item, index) in aclReadonlyItems" :key="`readonly-${index}`" class="acl-readonly-row">
                  <span>{{ subjectTypeText(item.subjectType) }}</span>
                  <strong>{{ formatSubjectId(item.subjectIds) }}</strong>
                  <em>{{ readonlyAclLabel(item) }}</em>
                </div>
                <div v-for="(item, index) in aclItems" :key="index" class="acl-editor-row">
                  <a-select v-model:value="item.subjectType" @change="() => handleAclSubjectTypeChange(item)">
                    <a-select-option value="USER">用户</a-select-option>
                    <a-select-option value="ROLE">角色</a-select-option>
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
                    v-else
                    v-model:value="item.subjectIds"
                    placeholder="请选择角色"
                    button-text="选择"
                  />
                  <a-select v-model:value="item.permission">
                    <a-select-option value="EDITOR">编辑</a-select-option>
                    <a-select-option value="VIEWER">查看</a-select-option>
                  </a-select>
                  <button class="acl-remove" type="button" title="删除" @click="aclItems.splice(index, 1)"><DeleteOutlined /></button>
                </div>
                <button class="acl-add" type="button" @click="addAcl"><PlusOutlined /> 添加授权对象</button>
              </div>

              <div v-else class="acl-readonly-list">
                <div v-for="(item, index) in allAclRows" :key="index" class="acl-readonly-row">
                  <span>{{ subjectTypeText(item.subjectType) }}</span>
                  <strong>{{ formatSubjectId(item.subjectIds) }}</strong>
                  <em>{{ permissionText(item.permission) }}</em>
                </div>
              </div>
              <a-empty v-if="!allAclRows.length" description="暂无授权对象" />
            </div>
          </template>
        </section>
      </div>

      <DocumentUploadModal v-model:open="uploadOpen" :knowledge-id="selectedKnowledgeId" @success="afterUpload" />

      <a-drawer v-model:open="settingsOpen" title="知识库设置" :width="460" root-class-name="knowledge-drawer">
        <a-form layout="vertical" class="knowledge-form">
          <a-form-item
            v-if="canManageAccess"
            label="知识库状态"
            extra="停用后不参与对话或智能体检索，文档、分段和授权会保留。"
          >
            <a-switch
              :checked="isCurrentKnowledgeActive"
              :loading="statusSaving"
              checked-children="可用"
              un-checked-children="停用"
              @change="(enabled) => changeKnowledgeStatus(Boolean(enabled))"
            />
          </a-form-item>
          <div class="knowledge-form-group">基础信息</div>
          <a-form-item label="名称"><a-input v-model:value="form.name" :maxlength="128" show-count placeholder="例如：产品帮助中心" /></a-form-item>
          <a-form-item label="描述"><a-textarea v-model:value="form.description" :rows="3" :maxlength="1000" show-count placeholder="说明知识库的内容和适用范围" /></a-form-item>
          <div class="knowledge-form-group">检索参数</div>
          <a-form-item label="检索方式" extra="向量按语义相近召回；关键词按原文用词匹配（适合编号、专有名词）；混合两路召回后按权重融合。">
            <a-select v-model:value="form.retrievalMode">
              <a-select-option v-for="option in retrievalModeOptions" :key="option.value" :value="option.value">{{ option.label }}</a-select-option>
            </a-select>
          </a-form-item>
          <a-form-item
            v-if="form.retrievalMode === 'HYBRID'"
            label="语义权重"
            :extra="`语义 ${form.semanticWeight.toFixed(2)} / 关键词 ${(1 - form.semanticWeight).toFixed(2)}；两者之和恒为 1，往右更看重语义、往左更看重关键词。`"
          >
            <a-slider v-model:value="form.semanticWeight" :min="0" :max="1" :step="0.05" />
          </a-form-item>
          <a-form-item label="返回数量" extra="每次检索返回的最相关分段数量；过大易引入噪声，一般 5–8。">
            <a-input-number v-model:value="form.topK" :min="1" :max="20" style="width: 100%" />
          </a-form-item>
          <a-form-item label="相似度阈值" extra="低于该分数的分段不参与回答；越低召回越多、越高越精准。">
            <a-slider v-model:value="form.scoreThreshold" :min="0" :max="1" :step="0.05" />
          </a-form-item>
          <a-button type="primary" block size="large" :loading="saving" @click="saveSettings">保存设置</a-button>
        </a-form>
      </a-drawer>

      <KnowledgeChunkEditorDrawer v-model:open="chunkEditorOpen" :chunk="editingChunk" :can-edit="canEditCurrent" @saved="afterChunkSaved" />
    </template>

    <a-modal
      v-model:open="modalOpen"
      :title="editingId ? '编辑知识库' : '创建知识库'"
      :confirm-loading="saving"
      :mask-closable="!saving"
      :keyboard="!saving"
      ok-text="保存"
      cancel-text="取消"
      :width="'min(560px, calc(100vw - 32px))'"
      :z-index="1200"
      centered
      wrap-class-name="knowledge-modal"
      @ok="saveBase"
    >
      <p class="knowledge-modal-hint">
        {{ editingId ? '更新名称与描述，让团队更容易识别这个知识库。' : '创建后可在知识库详情中添加文档并配置切片参数。' }}
      </p>
      <a-form layout="vertical" class="knowledge-form">
        <a-form-item label="名称" required>
          <a-input v-model:value="form.name" :maxlength="128" show-count placeholder="例如：产品帮助中心" />
        </a-form-item>
        <a-form-item label="描述">
          <a-textarea v-model:value="form.description" :rows="4" :maxlength="1000" show-count placeholder="说明知识库的内容和适用范围，便于团队识别" />
        </a-form-item>
      </a-form>
    </a-modal>
  </section>
</template>

<script setup lang="ts">
import { computed, markRaw, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import {
  ArrowLeftOutlined,
  BarChartOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  KeyOutlined,
  LoadingOutlined,
  MessageOutlined,
  PlusOutlined,
  ReadOutlined,
  SearchOutlined,
  SettingOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons-vue';
// 不引 JSelectDept：auth-api 没有部门概念，它一挂载就请求的 sysDepart 接口已随 Java 下线
import { JSelectRole, JSelectUser } from '/@/components/Form';
import { useMessage } from '/@/hooks/web/useMessage';
import { useUserStore } from '/@/store/modules/user';
import {
  createKnowledge,
  deleteDocument,
  deleteDocuments,
  deleteKnowledge,
  downloadKnowledgeDocument,
  downloadKnowledgeDocumentArchive,
  getDocumentList,
  getKnowledgeAcl,
  getKnowledgeDetail,
  getKnowledgeList,
  retryDocument,
  saveKnowledgeAcl,
  setDocumentEnabled,
  setKnowledgeEnabled,
  updateKnowledge,
} from '../../knowledge/knowledge.api';
import type { KnowledgeAcl, KnowledgeBase, KnowledgeChunk, KnowledgeDocument, KnowledgePermission } from '../../knowledge/knowledge.types';
import { canEditKnowledge, canManageKnowledge, canUseKnowledge, normalizeKnowledgePermission } from '../../knowledge/knowledgePermission';
import { isKnowledgeDocumentProcessing, knowledgeDocumentStatusText } from '../../knowledge/knowledgeStatus';
import KnowledgeChunkEditorDrawer from '../../knowledge/components/KnowledgeChunkEditorDrawer.vue';
import KnowledgeChunksPanel from '../../knowledge/components/KnowledgeChunksPanel.vue';
import DocumentUploadModal from '../../knowledge/components/DocumentUploadModal.vue';
import RetrievalTester from '../../knowledge/components/RetrievalTester.vue';
import KnowledgeAnalyticsPanel from '../../knowledge/components/KnowledgeAnalyticsPanel.vue';
import { expandAclItems, groupAclItems, type AclEditorItem } from '/@/utils/aclEditor';

type KnowledgeScope = 'owned' | 'shared';
type DetailView = 'documents' | 'chunks' | 'retrieval' | 'analytics' | 'access';
type KnowledgeUsePayload = { id: string; name: string; permission: KnowledgePermission };

const emit = defineEmits<{
  (e: 'useKnowledge', value: KnowledgeUsePayload): void;
}>();

const { createMessage } = useMessage();
const userStore = useUserStore();
const scope = ref<KnowledgeScope>('owned');
const keyword = ref('');
const loading = ref(false);
const loadError = ref(false);
const items = ref<KnowledgeBase[]>([]);
const ownedTotal = ref(0);
const sharedTotal = ref(0);
const selectedKnowledgeId = ref('');
const currentKnowledge = ref<KnowledgeBase>();
const activeView = ref<DetailView>('documents');
const uploadOpen = ref(false);
const settingsOpen = ref(false);
const modalOpen = ref(false);
const saving = ref(false);
const editingId = ref('');
const documentLoading = ref(false);
const documentError = ref(false);
const documentKeyword = ref('');
const documents = ref<KnowledgeDocument[]>([]);
const selectedDocumentIds = ref<string[]>([]);
const chunkDocumentId = ref<string>();
const chunksPanelRef = ref<{ reload: () => Promise<void>; reloadDocuments: () => Promise<void> }>();
const chunkEditorOpen = ref(false);
const editingChunk = ref<KnowledgeChunk>();
const aclLoading = ref(false);
const aclSaving = ref(false);
// 可编辑的授权行（用户 / 角色，非所有者）
const aclItems = ref<AclEditorItem<KnowledgePermission>[]>([]);
// 只读的授权行：所有者那条，以及历史数据里的部门授权（见模板注释）；保存时原样带回
const aclReadonlyItems = ref<AclEditorItem<KnowledgePermission>[]>([]);
const allAclRows = computed(() => [...aclReadonlyItems.value, ...aclItems.value]);
const pagination = reactive({ current: 1, pageSize: 24, total: 0 });
const documentPagination = reactive({ current: 1, pageSize: 20, total: 0 });
const statusSaving = ref(false);
// 检索方式取值对齐服务端 RETRIEVAL_MODES；混合模式只暴露一个「语义权重」滑块，
// 关键词权重固定为 1 − 语义权重（saveSettings 里补齐后一起发），两者永不会不和为 1。
const retrievalModeOptions = [
  { label: '向量检索', value: 'VECTOR' },
  { label: '关键词检索', value: 'KEYWORD' },
  { label: '混合检索', value: 'HYBRID' },
] as const;
const form = reactive({
  id: '',
  name: '',
  description: '',
  retrievalMode: 'VECTOR',
  semanticWeight: 0.5,
  keywordWeight: 0.5,
  topK: 5,
  scoreThreshold: 0.3,
});
let refreshTimer: ReturnType<typeof window.setInterval> | undefined;

const scopeOptions = computed(() => [
  { label: '我创建的', value: 'owned' as const, count: ownedTotal.value },
  { label: '共享给我的', value: 'shared' as const, count: sharedTotal.value },
]);

const detailViews = [
  { key: 'documents' as const, label: '文档', icon: markRaw(FileTextOutlined) },
  { key: 'chunks' as const, label: '分段', icon: markRaw(UnorderedListOutlined) },
  { key: 'retrieval' as const, label: '召回测试', icon: markRaw(SearchOutlined) },
  { key: 'analytics' as const, label: '运营统计', icon: markRaw(BarChartOutlined) },
  { key: 'access' as const, label: '授权', icon: markRaw(KeyOutlined) },
];

const currentPermission = computed<KnowledgePermission>(() => resolveKnowledgePermission(currentKnowledge.value));
const canEditCurrent = computed(() => canEditKnowledge(currentPermission.value));
const isCurrentKnowledgeActive = computed(() => currentKnowledge.value?.status === 'ACTIVE');
const canUseCurrent = computed(() => isCurrentKnowledgeActive.value && canUseKnowledge(currentKnowledge.value ? currentPermission.value : undefined));
const canManageAccess = computed(() => canManageKnowledge(currentPermission.value));
const isActualOwner = computed(() => {
  const ownerId = String(currentKnowledge.value?.ownerUserId || '');
  const currentUserId = String((userStore.getUserInfo as any)?.id || '');
  return Boolean(ownerId && currentUserId && ownerId === currentUserId);
});
const visibleDetailViews = computed(() =>
  detailViews.filter((item) => {
    if (item.key === 'retrieval') return canUseCurrent.value;
    if (item.key === 'analytics') return isActualOwner.value;
    if (item.key === 'access') return canManageAccess.value;
    return true;
  }),
);

function getRecordPermission(record?: KnowledgeBase) {
  const source: any = record || {};
  return normalizeKnowledgePermission(source.currentPermission)
    || normalizeKnowledgePermission(source.accessPermission)
    || normalizeKnowledgePermission(source.aclPermission)
    || normalizeKnowledgePermission(source.sharePermission)
    || normalizeKnowledgePermission(source.permission);
}

function resolveKnowledgePermission(record?: KnowledgeBase): KnowledgePermission {
  return getRecordPermission(record) || 'VIEWER';
}

onMounted(async () => {
  await Promise.all([reload(), refreshScopeTotals()]);
  refreshTimer = window.setInterval(refreshProcessingDocuments, 5000);
});

onBeforeUnmount(() => {
  if (refreshTimer) window.clearInterval(refreshTimer);
});

watch(activeView, (view) => {
  if (view === 'chunks') void reloadChunksPanel();
  if (view === 'analytics' && !isActualOwner.value) activeView.value = 'documents';
});

async function refreshScopeTotals() {
  try {
    const [owned, shared] = await Promise.all([
      getKnowledgeList({ pageNo: 1, pageSize: 1, scope: 'owned' }),
      getKnowledgeList({ pageNo: 1, pageSize: 1, scope: 'shared' }),
    ]);
    ownedTotal.value = owned?.total || 0;
    sharedTotal.value = shared?.total || 0;
  } catch {
    ownedTotal.value = scope.value === 'owned' ? pagination.total : ownedTotal.value;
    sharedTotal.value = scope.value === 'shared' ? pagination.total : sharedTotal.value;
  }
}

async function reload() {
  loading.value = true;
  loadError.value = false;
  try {
    const page = await getKnowledgeList({
      pageNo: pagination.current,
      pageSize: pagination.pageSize,
      keyword: keyword.value.trim() || undefined,
      scope: scope.value,
    });
    items.value = page?.records || [];
    pagination.total = page?.total || 0;
    if (scope.value === 'owned') ownedTotal.value = pagination.total;
    else sharedTotal.value = pagination.total;
  } catch {
    items.value = [];
    pagination.total = 0;
    loadError.value = true;
  } finally {
    loading.value = false;
  }
}

function switchScope(nextScope: KnowledgeScope) {
  if (scope.value === nextScope) return;
  scope.value = nextScope;
  pagination.current = 1;
  reload();
}

function changeKnowledgePage(delta: number) {
  const next = pagination.current + delta;
  const max = Math.max(1, Math.ceil(pagination.total / pagination.pageSize));
  if (next < 1 || next > max) return;
  pagination.current = next;
  reload();
}

function resetForm(record?: KnowledgeBase) {
  const semanticWeight = Number(record?.semanticWeight ?? 0.5);
  Object.assign(form, {
    id: record?.id || '',
    name: record?.name || '',
    description: record?.description || '',
    retrievalMode: record?.retrievalMode || 'VECTOR',
    semanticWeight,
    keywordWeight: 1 - semanticWeight,
    topK: record?.topK || 5,
    scoreThreshold: Number(record?.scoreThreshold ?? 0.3),
  });
}

function openCreate() {
  editingId.value = '';
  resetForm();
  modalOpen.value = true;
}

function openEdit(record: KnowledgeBase) {
  editingId.value = record.id;
  resetForm(record);
  modalOpen.value = true;
}

async function saveBase() {
  if (!form.name.trim()) {
    createMessage.warning('请输入知识库名称');
    return;
  }
  saving.value = true;
  try {
    const payload = { ...form, name: form.name.trim(), id: editingId.value || undefined };
    if (editingId.value) {
      await updateKnowledge(payload);
      createMessage.success('知识库已更新');
      modalOpen.value = false;
      await Promise.all([reload(), refreshScopeTotals()]);
      return;
    }

    await createKnowledge(payload);
    createMessage.success('知识库已创建');
    modalOpen.value = false;
    await Promise.all([reload(), refreshScopeTotals()]);
  } finally {
    saving.value = false;
  }
}

async function removeKnowledge(record: KnowledgeBase) {
  await deleteKnowledge(record.id);
  createMessage.success('知识库已删除');
  await Promise.all([reload(), refreshScopeTotals()]);
}

async function openDetail(record: KnowledgeBase) {
  selectedKnowledgeId.value = record.id;
  currentKnowledge.value = record;
  activeView.value = 'documents';
  documentPagination.current = 1;
  chunkDocumentId.value = undefined;
  selectedDocumentIds.value = [];
  await loadKnowledge();
  await Promise.all([loadDocuments(), canManageAccess.value ? loadAcl() : Promise.resolve()]);
}

function backToList() {
  selectedKnowledgeId.value = '';
  currentKnowledge.value = undefined;
  documents.value = [];
  selectedDocumentIds.value = [];
  aclItems.value = [];
  aclReadonlyItems.value = [];
}

// 请求身份闸（写法同 components/FileSelector.vue:107 的 loadSeq 注释）：详情/文档/分段/授权
// 四路都会被「切知识库」并发重入——openDetail 一次并发发四个请求，:445 的 5 秒轮询
// refreshProcessingDocuments 还在持续重开窗口。await 回来后必须复核 selectedKnowledgeId
// 是否仍是发请求时那个，否则 A 的响应会盖进 B 的界面；而 toggleDocument/removeDocumentItem
// 是拿列表行的 record.id 直调后端的——用户看着 B 的界面，删掉/停用的是 A 的文档。
async function loadKnowledge() {
  const kid = selectedKnowledgeId.value;
  if (!kid) return;
  const detail = await getKnowledgeDetail(kid);
  if (kid !== selectedKnowledgeId.value) return; // 已切走：丢弃过期响应
  currentKnowledge.value = {
    ...currentKnowledge.value,
    ...detail,
    currentPermission: detail.currentPermission || currentKnowledge.value?.currentPermission,
  };
  resetForm(currentKnowledge.value);
}

async function loadDocuments() {
  const kid = selectedKnowledgeId.value;
  if (!kid) return;
  documentLoading.value = true;
  documentError.value = false;
  try {
    const page = await getDocumentList({
      knowledgeId: kid,
      pageNo: documentPagination.current,
      pageSize: documentPagination.pageSize,
      keyword: documentKeyword.value.trim() || undefined,
    });
    if (kid !== selectedKnowledgeId.value) return;
    documents.value = page?.records || [];
    documentPagination.total = page?.total || 0;
  } catch {
    if (kid !== selectedKnowledgeId.value) return;
    documents.value = [];
    documentError.value = true;
  } finally {
    // 过期响应不许摘掉新一轮的 loading（同 FileSelector：seq === loadSeq 才关）
    if (kid === selectedKnowledgeId.value) documentLoading.value = false;
  }
}

async function reloadChunksPanel() {
  await chunksPanelRef.value?.reload?.();
}

// 服务端存的 subjectType 大小写不一（所有者那条是小写 user，页面存的是大写 USER），
// 模板按大写比对，这里先归一，否则小写行会落进错误的分支。
function normalizeAclSubjectType(value: unknown): KnowledgeAcl['subjectType'] {
  const text = String(value || '').trim().toUpperCase();
  if (text === 'ROLE') return 'ROLE';
  if (text === 'DEPT' || text === 'DEPARTMENT') return 'DEPARTMENT';
  return 'USER';
}

function isEditableAcl(item: AclEditorItem<KnowledgePermission>) {
  return (item.subjectType === 'USER' || item.subjectType === 'ROLE') && item.permission !== 'OWNER';
}

async function loadAcl() {
  const kid = selectedKnowledgeId.value;
  if (!kid || !canManageAccess.value) return;
  aclLoading.value = true;
  try {
    const rows = ((await getKnowledgeAcl(kid)) || []).map((row) => ({
      ...row,
      subjectType: normalizeAclSubjectType(row.subjectType),
      permission: String(row.permission || 'VIEWER').toUpperCase() as KnowledgePermission,
    }));
    const acl = groupAclItems(rows);
    if (kid !== selectedKnowledgeId.value) return;
    aclItems.value = acl.filter(isEditableAcl);
    aclReadonlyItems.value = acl.filter((item) => !isEditableAcl(item));
  } finally {
    if (kid === selectedKnowledgeId.value) aclLoading.value = false;
  }
}

async function refreshProcessingDocuments() {
  if (!selectedKnowledgeId.value || !documents.value.some((item) => isProcessing(item.status))) return;
  await Promise.all([loadDocuments(), loadKnowledge()]);
  if (activeView.value === 'chunks') await reloadChunksPanel();
}

async function toggleDocument(record: KnowledgeDocument, enabled: boolean) {
  if (!canEditCurrent.value) return;
  await setDocumentEnabled(record.id, enabled);
  record.enabled = enabled ? 1 : 0;
}

function editChunk(record: KnowledgeChunk) {
  editingChunk.value = record;
  chunkEditorOpen.value = true;
}

function changeDocumentPage(page: number, pageSize: number) {
  documentPagination.current = page;
  documentPagination.pageSize = pageSize;
  void loadDocuments();
}

function openDocumentChunks(document: KnowledgeDocument) {
  chunkDocumentId.value = document.id;
  activeView.value = 'chunks';
}

function toggleDocumentSelection(id: string, event: Event) {
  const checked = (event.target as HTMLInputElement).checked;
  selectedDocumentIds.value = checked
    ? [...new Set([...selectedDocumentIds.value, id])]
    : selectedDocumentIds.value.filter((selectedId) => selectedId !== id);
}

async function downloadDocumentItem(record: KnowledgeDocument) {
  await downloadKnowledgeDocument(record.id, record.originalName);
}

async function downloadSelectedDocuments() {
  if (!selectedDocumentIds.value.length) return;
  await downloadKnowledgeDocumentArchive(selectedDocumentIds.value);
}

async function removeDocumentItem(record: KnowledgeDocument) {
  if (!canEditCurrent.value) return;
  await deleteDocument(record.id);
  selectedDocumentIds.value = selectedDocumentIds.value.filter((id) => id !== record.id);
  if (chunkDocumentId.value === record.id) chunkDocumentId.value = undefined;
  await Promise.all([loadDocuments(), reloadChunksPanel(), chunksPanelRef.value?.reloadDocuments?.(), loadKnowledge(), reload()]);
}

async function removeSelectedDocuments() {
  if (!canEditCurrent.value || !selectedDocumentIds.value.length) return;
  const deletingIds = selectedDocumentIds.value;
  await deleteDocuments(selectedDocumentIds.value);
  if (chunkDocumentId.value && deletingIds.includes(chunkDocumentId.value)) chunkDocumentId.value = undefined;
  selectedDocumentIds.value = [];
  createMessage.success('已删除选中文档');
  await Promise.all([loadDocuments(), reloadChunksPanel(), chunksPanelRef.value?.reloadDocuments?.(), loadKnowledge(), reload()]);
}

async function retryDocumentItem(record: KnowledgeDocument) {
  if (!canEditCurrent.value) return;
  await retryDocument(record.id);
  createMessage.success('已重新提交处理');
  await loadDocuments();
}

async function afterUpload() {
  await Promise.all([loadDocuments(), reloadChunksPanel(), chunksPanelRef.value?.reloadDocuments?.(), loadKnowledge(), reload()]);
}

async function afterChunkSaved() {
  await Promise.all([reloadChunksPanel(), loadDocuments(), loadKnowledge(), reload()]);
}

async function afterChunkChanged() {
  await Promise.all([loadDocuments(), loadKnowledge(), reload()]);
}

async function saveSettings() {
  if (!currentKnowledge.value || !canEditCurrent.value) return;
  saving.value = true;
  try {
    // 滑块只改语义权重，关键词权重在发出前按 1 − 语义补齐，保证服务端拿到的一对和为 1
    const updated = await updateKnowledge({
      ...form,
      keywordWeight: Number((1 - form.semanticWeight).toFixed(2)),
      id: currentKnowledge.value.id,
    });
    currentKnowledge.value = {
      ...currentKnowledge.value,
      ...updated,
      currentPermission: updated.currentPermission || currentKnowledge.value.currentPermission,
    };
    resetForm(currentKnowledge.value);
    createMessage.success('设置已保存');
    settingsOpen.value = false;
    await reload();
  } finally {
    saving.value = false;
  }
}

async function changeKnowledgeStatus(enabled: boolean) {
  if (!currentKnowledge.value || !canManageAccess.value) return;
  statusSaving.value = true;
  try {
    const updated = await setKnowledgeEnabled(currentKnowledge.value.id, enabled);
    currentKnowledge.value = {
      ...currentKnowledge.value,
      ...updated,
      currentPermission: updated.currentPermission || currentKnowledge.value.currentPermission,
    };
    if (!enabled && activeView.value === 'retrieval') activeView.value = 'documents';
    createMessage.success(enabled ? '知识库已启用' : '知识库已停用');
    await reload();
  } finally {
    statusSaving.value = false;
  }
}

function useCurrentKnowledge() {
  if (!currentKnowledge.value || !canUseCurrent.value) return;
  emit('useKnowledge', {
    id: currentKnowledge.value.id,
    name: currentKnowledge.value.name,
    permission: currentPermission.value,
  });
}

function addAcl() {
  aclItems.value.push({ subjectType: 'USER', subjectIds: [], permission: 'VIEWER' });
}

function handleAclSubjectTypeChange(item: AclEditorItem<KnowledgePermission>) {
  item.subjectIds = [];
}

async function saveAcl() {
  if (!selectedKnowledgeId.value || !canManageAccess.value) return;
  aclSaving.value = true;
  try {
    // 服务端是整表替换：只读行（部门授权、所有者）也要一并带回，否则保存一次就把它们删了
    await saveKnowledgeAcl(selectedKnowledgeId.value, expandAclItems([...aclReadonlyItems.value, ...aclItems.value]));
    await loadAcl();
  } finally {
    aclSaving.value = false;
  }
}

function subjectTypeText(type: KnowledgeAcl['subjectType']) {
  const map = { USER: '用户', ROLE: '角色', DEPARTMENT: '部门' };
  return map[type] || type;
}

function readonlyAclLabel(item: AclEditorItem<KnowledgePermission>) {
  if (item.permission === 'OWNER') return '所有者';
  return `${permissionText(item.permission)} · 部门授权不可在此修改`;
}

function permissionText(permission: KnowledgeAcl['permission']) {
  const map: Record<string, string> = { OWNER: '所有者', EDITOR: '编辑', VIEWER: '查看', USER: '查看' };
  return map[permission] || permission;
}

function formatSubjectId(value: string[]) {
  return value.length ? value.join(', ') : '-';
}

function isProcessing(status: KnowledgeDocument['status']) {
  return isKnowledgeDocumentProcessing(status);
}

function statusColor(status: KnowledgeDocument['status']) {
  if (status === 'READY') return 'green';
  if (status === 'FAILED') return 'red';
  return 'default';
}

function statusText(status: KnowledgeDocument['status']) {
  return knowledgeDocumentStatusText(status);
}

function formatSize(size: number) {
  const bytes = Math.max(0, Number(size) || 0);
  if (!bytes) return '0 B';
  // B 档此前缺失：512 字节的文档会显示成 "0.5 KB"；GB 档同样补上
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function displayDescription(record: KnowledgeBase) {
  const description = String(record.description || '尚未填写详细说明').trim();
  // 单个数字或符号无法帮助用户判断知识库内容；保留原数据，仅在卡片上给出可理解的占位说明。
  // if (!description || description.length < 2 || /^[\d\W_]+$/.test(description)) return '尚未填写详细说明';
  return description;
}

function displayCreator(record: KnowledgeBase) {
  return String(record.createBy_dictText || record.createBy || '').trim() || '未知';
}

function displayKnowledgeName(record: KnowledgeBase) {
  const name = String(record.name || '').trim();
  return name || '未命名知识库';
}
</script>
