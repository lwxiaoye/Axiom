<template>
  <section class="myfiles-pane">
    <!-- 顶栏：标题 + 新建 -->
    <header class="myfiles-head">
      <h2 class="myfiles-title">我的文件</h2>
      <span class="head-spacer" />
      <!-- 操作按页区分：文件页=上传文件，文件夹页=新建文件夹 -->
      <a-button
        v-if="paneMode === 'files'"
        type="primary"
        shape="round"
        class="myfiles-new"
        :loading="uploading"
        @click="triggerUpload"
      >
        <UploadOutlined v-if="!uploading" />
        上传文件
      </a-button>
      <a-button v-else type="primary" shape="round" class="myfiles-new" @click="openCreateFolder">
        <PlusOutlined />
        新建文件夹
      </a-button>
      <input ref="fileInputRef" type="file" multiple class="myfiles-hidden-input" @change="onFileChange" />
    </header>

    <!-- 文件夹页：从工具栏「文件夹」入口进入，网盘式表格列表 -->
    <template v-if="paneMode === 'folders'">
      <div class="myfiles-crumb">
        <button type="button" class="crumb-back" @click="backToRoot">
          <PremiumChevron direction="left" :size="16" interactive /> 我的文件
        </button>
        <span class="crumb-sep">/</span>
        <span class="crumb-current">文件夹</span>
      </div>

      <div v-if="!folders.length" class="myfiles-empty">
        <FolderAddOutlined class="myfiles-empty-icon" />
        <p>还没有文件夹</p>
        <p class="myfiles-empty-hint">新建文件夹把文件归类整理；文件可以在 ⋯ 菜单里「移动到」对应文件夹。</p>
        <a-button type="primary" ghost @click="openCreateFolder">
          <PlusOutlined />
          新建文件夹
        </a-button>
      </div>

      <div v-else class="fd-table">
        <div class="fd-head">
          <span class="fd-col-name">文件名</span>
          <span class="fd-col fd-col-size">大小</span>
          <span class="fd-col fd-col-type">类型</span>
          <button type="button" class="fd-col fd-col-time fd-sort" @click="folderSortDesc = !folderSortDesc">
            创建时间
            <PremiumChevron :class="['fd-caret', { asc: !folderSortDesc }]" :direction="folderSortDesc ? 'down' : 'up'" :size="13" interactive />
          </button>
        </div>
        <div
          v-for="f in sortedFolders"
          :key="f.id"
          class="fd-row"
          title="点击进入"
          @click="enterFolder(f.id)"
        >
          <span class="fd-icon"><FolderFilled /></span>
          <span class="fd-name" :title="f.name">{{ f.name }}</span>
          <span class="fd-col fd-col-size">{{ f.fileCount ? `${f.fileCount} 项` : '-' }}</span>
          <span class="fd-col fd-col-type">文件夹</span>
          <span class="fd-col fd-col-time">{{ formatTime(f.createdAt) || '-' }}</span>
          <span class="fd-ops" @click.stop>
            <button type="button" class="op-icon" title="重命名" @click="openRenameFolder(f)">
              <EditOutlined />
            </button>
            <button type="button" class="op-icon" title="删除" @click="confirmDeleteFolder(f)">
              <DeleteOutlined />
            </button>
          </span>
        </div>
      </div>
    </template>

    <!-- 文件页（原有主视图） -->
    <template v-else>
    <!-- 工具栏：桌面左右分区；手机端只纵向滚动，筛选项过长省略。 -->
    <div class="myfiles-toolbar">
      <div class="myfiles-filter-strip">
        <a-dropdown :trigger="['click']" placement="bottomLeft" overlay-class-name="mf-filter-overlay">
          <button type="button" class="mf-filter">
            <FilterOutlined />
            <span class="mf-filter-label">{{ typeFilterLabel }}</span>
            <PremiumChevron class="mf-caret" direction="down" :size="13" interactive />
          </button>
          <template #overlay>
            <a-menu :selected-keys="[typeFilter]" @click="onTypeSelect">
              <a-menu-item v-for="t in typeTabs" :key="t.value" :class="{ 'mf-menu-empty': t.count === 0 }">
                {{ t.label }}<span class="mf-menu-count">{{ t.count }}</span>
              </a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
        <a-dropdown :trigger="['click']" placement="bottomLeft" overlay-class-name="mf-filter-overlay">
          <button type="button" :class="['mf-filter', { on: sourceFilter !== 'all' }]">
            <span class="mf-filter-label">{{ sourceLabel }}</span>
            <PremiumChevron class="mf-caret" direction="down" :size="13" interactive />
          </button>
          <template #overlay>
            <a-menu :selected-keys="[sourceFilter]" @click="onSourceSelect">
              <a-menu-item key="all">全部来源</a-menu-item>
              <a-menu-item key="generated">对话产物</a-menu-item>
              <a-menu-item key="uploaded">已上传</a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
        <button type="button" class="mf-filter mf-folders-entry" @click="openFoldersPane">
          <FolderOutlined />
          <span class="mf-filter-label">文件夹</span>
          <span class="mf-entry-count">{{ folders.length }}</span>
        </button>
        <div
          ref="viewToggleRef"
          class="myfiles-viewtoggle"
          role="switch"
          :aria-checked="viewMode === 'list'"
          :data-mode="viewMode"
          :data-dragging="viewDragging ? 'true' : undefined"
          :data-pressed="viewPressed ? 'true' : undefined"
          tabindex="0"
          title="拖动或点击切换缩略图 / 列表"
          aria-label="切换缩略图或列表"
          @pointerdown="onViewPointerDown"
          @pointermove="onViewPointerMove"
          @pointerup="onViewPointerUp"
          @pointercancel="onViewPointerUp"
          @lostpointercapture="onViewPointerUp"
          @keydown="onViewKeydown"
        >
          <span ref="viewThumbRef" class="vt-thumb" aria-hidden="true" />
          <span class="vt-icon vt-grid" :class="{ on: viewMode === 'grid' }" aria-hidden="true">
            <AppstoreOutlined />
          </span>
          <span class="vt-icon vt-list" :class="{ on: viewMode === 'list' }" aria-hidden="true">
            <UnorderedListOutlined />
          </span>
        </div>
      </div>
      <span class="tb-spacer" />
      <div class="myfiles-search">
        <SearchOutlined />
        <input v-model="keyword" placeholder="搜索文件" aria-label="搜索文件" />
        <button v-if="keyword" type="button" class="mf-clear" aria-label="清空搜索" @click="keyword = ''">
          <CloseOutlined />
        </button>
      </div>
    </div>

    <!-- 面包屑：进入文件夹后显示返回（我的文件 / 文件夹 / 当前夹） -->
    <div v-if="currentFolderId" class="myfiles-crumb">
      <button type="button" class="crumb-back" @click="backToRoot">
        <PremiumChevron direction="left" :size="16" interactive /> 我的文件
      </button>
      <span class="crumb-sep">/</span>
      <button type="button" class="crumb-back" @click="backToFolders">文件夹</button>
      <span class="crumb-sep">/</span>
      <span class="crumb-current">{{ currentFolder?.name || '文件夹' }}</span>
    </div>

    <!-- 加载：骨架屏取代「加载中…」，减少布局跳动 -->
    <div v-if="loading" class="myfiles-list myfiles-skeleton" aria-hidden="true">
      <div v-for="n in 5" :key="n" class="sk-row">
        <span class="sk-icon" />
        <div class="sk-body">
          <span class="sk-line sk-w55" />
          <span class="sk-line sk-w35" />
        </div>
      </div>
    </div>

    <div v-else-if="errorText" class="myfiles-status myfiles-error">
      <span>{{ errorText }}</span>
      <a-button size="small" @click="loadFiles">重试</a-button>
    </div>

    <div v-else-if="!files.length && !folders.length && !keyword.trim() && typeFilter === 'all' && sourceFilter === 'all'" class="myfiles-empty">
      <FolderOpenOutlined class="myfiles-empty-icon" />
      <p>还没有文件</p>
      <p class="myfiles-empty-hint">上传文件后，主对话可以随时读取它们；对话生成的文档也会出现在这里。</p>
      <a-button type="primary" ghost :loading="uploading" @click="triggerUpload">
        <UploadOutlined v-if="!uploading" />
        上传第一个文件
      </a-button>
    </div>

    <div v-else-if="!filteredFiles.length" class="myfiles-empty myfiles-empty-slim">
      <component :is="emptyState.icon" class="myfiles-empty-icon" />
      <p>{{ emptyState.title }}</p>
      <p class="myfiles-empty-hint">{{ emptyState.hint }}</p>
      <a-button size="small" @click="resetFilters">重置筛选</a-button>
    </div>

    <!-- 内容区（对标 Manus 库）：按任务/对话分组，网格=内容缩略卡片，列表=紧凑行 -->
    <div v-else :class="['myfiles-content', viewMode]">
      <section v-for="g in groupedFiles" :key="g.key" class="mf-group">
        <div class="mf-group-head">
          <span class="mf-group-title" :title="g.title">{{ g.title }}</span>
          <span v-if="g.time" class="mf-group-time">{{ formatTime(g.time) }}</span>
        </div>

        <!-- 网格：内容缩略卡片 -->
        <div v-if="viewMode === 'grid'" class="mf-grid">
          <div
            v-for="item in g.files"
            :key="item.id"
            class="mf-card"
            @click="openPreview(item)"
          >
            <div class="mf-card-head">
              <span :class="['mf-ic', `k-${fileKind(item)}`]">
                <component :is="KIND_ICON[fileKind(item)]" />
              </span>
              <span class="mf-card-name" :title="item.filename">{{ item.filename }}</span>
              <a-dropdown :trigger="['click']" placement="bottomRight" overlay-class-name="mf-file-actions-overlay">
                <button type="button" class="mf-more" title="更多操作" aria-label="更多文件操作" @click.stop>
                  <MoreOutlined />
                </button>
                <template #overlay>
                  <a-menu>
                    <a-menu-item
                      v-if="(item.source === 'generated' || item.source === 'research') && item.threadId"
                      key="locate"
                      @click="emit('open-thread', item.threadId || '')"
                    ><AimOutlined /> 在任务中定位</a-menu-item>
                    <a-menu-item key="preview" @click="openPreview(item)"><EyeOutlined /> 预览</a-menu-item>
                    <a-menu-item key="download" @click="onDownload(item)"><DownloadOutlined /> 下载</a-menu-item>
                    <a-menu-item key="versions" @click="openVersions(item)">版本历史</a-menu-item>
                    <a-sub-menu key="move" title="移动到">
                      <a-menu-item v-if="item.folderId" key="__root__" @click="onMoveFile(item, null)">移出到「未分类」</a-menu-item>
                      <a-menu-item v-for="f in folders" :key="f.id" :disabled="f.id === item.folderId" @click="onMoveFile(item, f.id)">{{ f.name }}</a-menu-item>
                      <a-menu-item v-if="!folders.length" key="__none__" disabled>还没有文件夹</a-menu-item>
                    </a-sub-menu>
                    <a-menu-item v-if="(item.source === 'generated' || item.source === 'research') && item.expiresAt" key="keep" @click="onKeep(item)">转为永久保留</a-menu-item>
                    <a-menu-divider />
                    <a-menu-item key="delete" danger @click="confirmDelete(item)">删除</a-menu-item>
                  </a-menu>
                </template>
              </a-dropdown>
            </div>
            <div class="mf-card-thumb">
              <FileThumb
                v-if="hasThumb(item)"
                :file="item"
                :kind="fileKind(item)"
                :icon="KIND_ICON[fileKind(item)]"
              />
              <div v-else class="mf-card-ph">
                <component :is="KIND_ICON[fileKind(item)]" class="mf-ph-ic" />
              </div>
              <span v-if="item.expiresAt" class="mf-card-expire">{{ expiryText(item.expiresAt) }}</span>
            </div>
          </div>
        </div>

        <!-- 列表：紧凑行 -->
        <div v-else class="mf-rows">
          <div
            v-for="item in g.files"
            :key="item.id"
            class="mf-row"
            @click="openPreview(item)"
          >
            <span :class="['mf-ic', `k-${fileKind(item)}`]">
              <component :is="KIND_ICON[fileKind(item)]" />
            </span>
            <span class="mf-row-name" :title="item.filename">{{ item.filename }}</span>
            <span v-if="item.expiresAt" class="mf-row-note">{{ expiryText(item.expiresAt) }}</span>
            <span class="mf-row-size">{{ formatSize(item.size) }}</span>
            <a-dropdown :trigger="['click']" placement="bottomRight" overlay-class-name="mf-file-actions-overlay">
              <button type="button" class="mf-more" title="更多操作" aria-label="更多文件操作" @click.stop>
                <MoreOutlined />
              </button>
              <template #overlay>
                <a-menu>
                  <a-menu-item
                    v-if="(item.source === 'generated' || item.source === 'research') && item.threadId"
                    key="locate"
                    @click="emit('open-thread', item.threadId || '')"
                  ><AimOutlined /> 在任务中定位</a-menu-item>
                  <a-menu-item key="preview" @click="openPreview(item)"><EyeOutlined /> 预览</a-menu-item>
                  <a-menu-item key="download" @click="onDownload(item)"><DownloadOutlined /> 下载</a-menu-item>
                  <a-menu-item key="versions" @click="openVersions(item)">版本历史</a-menu-item>
                  <a-sub-menu key="move" title="移动到">
                    <a-menu-item v-if="item.folderId" key="__root__" @click="onMoveFile(item, null)">移出到「未分类」</a-menu-item>
                    <a-menu-item v-for="f in folders" :key="f.id" :disabled="f.id === item.folderId" @click="onMoveFile(item, f.id)">{{ f.name }}</a-menu-item>
                    <a-menu-item v-if="!folders.length" key="__none__" disabled>还没有文件夹</a-menu-item>
                  </a-sub-menu>
                  <a-menu-item v-if="(item.source === 'generated' || item.source === 'research') && item.expiresAt" key="keep" @click="onKeep(item)">转为永久保留</a-menu-item>
                  <a-menu-divider />
                  <a-menu-item key="delete" danger @click="confirmDelete(item)">删除</a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>
          </div>
        </div>
      </section>

    </div>
    </template>

    <a-modal
      v-model:open="folderModalOpen"
      wrap-class-name="myfiles-folder-modal"
      :title="folderModalMode === 'create' ? '新建文件夹' : '重命名文件夹'"
      :confirm-loading="folderModalBusy"
      ok-text="确定"
      cancel-text="取消"
      :width="420"
      @ok="submitFolderModal"
    >
      <a-input
        v-model:value="folderModalName"
        placeholder="文件夹名（最多 50 字）"
        :maxlength="50"
        @press-enter="submitFolderModal"
      />
    </a-modal>

    <!-- 预览弹窗只剩加载/错误两个瞬时状态（2026-07-23 预览全量统一）：
         所有类型的正式预览一律在全屏文档查看器里进行 -->
    <a-modal
      v-model:open="previewOpen"
      :title="previewItem?.filename || '预览'"
      :footer="null"
      :width="520"
      wrap-class-name="myfiles-preview-modal"
      @cancel="closePreview"
    >
      <div v-if="previewError" class="myfiles-preview-state myfiles-error">{{ previewError }}</div>
      <div v-else class="myfiles-preview-state">{{ previewLoadingText }}</div>
    </a-modal>

    <!-- 全屏文档查看器（2026-07-20 与主对话统一；2026-07-23 全类型并入）：pdf/office 分页画布，
         md 文章、html 网页、文本源码走内容 props；Excel/图片/渲染兜底/无渲染器提示走内容槽 -->
    <DocPagesViewer
      v-if="docViewer"
      :key="docViewerSeq"
      :doc="docViewer.doc"
      :filename="docViewer.item.filename"
      :created-at="docViewer.item.createdAt"
      :pdf-url="docViewer.custom ? undefined : docViewer.url"
      :article-html="docViewer.html"
      :slides-pages="docViewer.slidesPages"
      :html-src="docViewer.htmlSrc"
      :code-text="docViewer.codeText"
      :meta-note="docViewer.metaNote"
      :saving="savingSlides"
      @close="closeDocViewer"
      @download="docViewer && onDownload(docViewer.item)"
      @save-pages="onViewerPagesSave"
    >
      <template v-if="docViewer.custom" #default>
        <ExcelFileViewer
          v-if="docViewer.custom.type === 'excel'"
          :src="docViewer.url"
          @error="onViewerRenderError"
        />
        <div v-else-if="docViewer.custom.type === 'docx'" class="dpv-host-office">
          <VueOfficeDocx :src="docViewer.url" @error="onViewerRenderError" />
        </div>
        <div v-else-if="docViewer.custom.type === 'pptx'" class="dpv-host-office dpv-host-pptx">
          <VueOfficePptx :src="docViewer.url" @error="onViewerRenderError" />
        </div>
        <img
          v-else-if="docViewer.custom.type === 'image'"
          class="dpv-host-image"
          :src="docViewer.url"
          :alt="docViewer.item.filename"
        />
        <div v-else class="dpv-host-binary">
          <p>该格式暂不支持在线预览</p>
          <a-button size="small" @click="docViewer && onDownload(docViewer.item)">下载文件</a-button>
        </div>
      </template>
    </DocPagesViewer>

    <!-- 版本历史（Phase B）：每个版本可下载；历史版本可恢复为当前版（生成新版本，不删历史） -->
    <a-modal
      v-model:open="versionsOpen"
      :title="versionsItem ? `版本历史 · ${versionsItem.filename}` : '版本历史'"
      :footer="null"
      :width="620"
      wrap-class-name="myfiles-versions-modal"
    >
      <div v-if="versionsLoading" class="myfiles-preview-state">加载版本历史…</div>
      <div v-else-if="versionsError" class="myfiles-preview-state myfiles-error">{{ versionsError }}</div>
      <div v-else-if="!versions.length" class="myfiles-preview-state">
        暂无版本记录——历史文件在下一次修改时会自动补录版本。
      </div>
      <ul v-else class="myfiles-versions">
        <li v-for="v in versions" :key="v.id" class="ver-row">
          <div class="ver-head">
            <strong class="ver-no">v{{ v.versionNo }}</strong>
            <span v-if="isCurrentVersion(v)" class="ver-tag ver-current">当前版本</span>
            <span v-else-if="v.status === 'draft'" class="ver-tag ver-draft">草稿 · 审查未通过</span>
            <span class="ver-meta">
              {{ verSourceLabel(v) }} · {{ formatSize(v.size) }} · {{ formatTime(v.createdAt) }}
            </span>
            <span class="ver-ops">
              <button type="button" class="op" @click="onDownloadVersion(v)">下载</button>
              <a-popconfirm
                v-if="!isCurrentVersion(v)"
                title="恢复该版本？将以此内容生成一个新版本作为当前版，后续历史全部保留。"
                ok-text="恢复"
                cancel-text="取消"
                @confirm="onRestoreVersion(v)"
              >
                <button type="button" class="op" :disabled="versionsBusy">恢复为当前版</button>
              </a-popconfirm>
            </span>
          </div>
          <div v-if="v.changeSummary" class="ver-summary">{{ v.changeSummary }}</div>
        </li>
      </ul>
    </a-modal>
  </section>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onActivated, onBeforeUnmount, ref, shallowRef } from 'vue';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import DocPagesViewer from '../components/DocPagesViewer.vue';
import ExcelFileViewer from '../components/ExcelFileViewer.vue';
import { useRoute, useRouter } from 'vue-router';
import FileThumb from '../components/FileThumb.vue';
import { destroyPdfDoc, loadPdfDocFromUrl } from '../utils/pdfDoc';
import { renderMarkdownDoc } from '../utils/mdRender';
import { formatDbNaiveTime, parseUtcNaiveTs } from '../utils/serverTime';
// 类型判定 + 图标映射与 composer 的「我的文件」选择器共用一份（2026-07-28）
import { KIND_ICON, fileKindOf, type FileKind } from '../composables/fileKind';
import { message, Modal } from 'ant-design-vue';
import {
  AimOutlined,
  AppstoreOutlined,
  CloseOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  EyeOutlined,
  FilterOutlined,
  FolderAddOutlined,
  FolderFilled,
  FolderOpenOutlined,
  FolderOutlined,
  MoreOutlined,
  SearchOutlined,
  UnorderedListOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue';
import PremiumChevron from '../components/PremiumChevron.vue';
import {
  createFolder,
  deleteFolder,
  deleteUserFile,
  downloadFileVersion,
  downloadUserFile,
  saveArtifactFile,
  compileSlidesDeck,
  fetchUserFileBlobUrl,
  fetchUserFilePreview,
  fetchUserFilePreviewPdfUrl,
  fetchUserFileText,
  getUserFileContent,
  keepUserFile,
  listFileVersions,
  listUserFiles,
  moveUserFile,
  renameFolder,
  restoreFileVersion,
  uploadUserFile,
  type UserFileItem,
  type UserFilesQuota,
  type UserFileVersion,
  type UserFolderItem,
} from '../myfiles.api';

defineOptions({ name: 'MyFilesTab' });

const emit = defineEmits<{
  (e: 'open-thread', threadId: string): void;
}>();

// 与后端 /files/upload（USER_FILES_MAX_SIZE_MB）一致的客户端护栏
const MAX_FILE_MB = 15;

const files = ref<UserFileItem[]>([]);
const quota = ref<UserFilesQuota | null>(null);
// 默认关（保持克制视图：只列文档类交付物）。打开后请求带 show_all=true，把模型取回的材料、
// 生成脚本等过程文件也列出来——它们一直都在库里占配额，只是默认不露出。
const showAll = ref(false);
const loading = ref(false);
const uploading = ref(false);
const errorText = ref('');
const fileInputRef = ref<HTMLInputElement | null>(null);

// ===== 文件夹（单层，ADR-047 §6.6）=====
const folders = ref<UserFolderItem[]>([]);
const currentFolderId = ref<string | null>(null); // null=顶层视图
const currentFolder = computed(() => folders.value.find((f) => f.id === currentFolderId.value) || null);
// 文件夹独立成页（2026-07-22）：工具栏「文件夹」按钮进入网盘式表格列表
const paneMode = ref<'files' | 'folders'>('files');
const folderSortDesc = ref(true);
const sortedFolders = computed(() =>
  [...folders.value].sort((a, b) => {
    const r = (b.createdAt || '').localeCompare(a.createdAt || '');
    return folderSortDesc.value ? r : -r;
  }),
);
// 新建 / 重命名文件夹弹窗
const folderModalOpen = ref(false);
const folderModalMode = ref<'create' | 'rename'>('create');
const folderModalName = ref('');
const folderModalTargetId = ref<string | null>(null);
const folderModalBusy = ref(false);

// ===== 检索 / 筛选 / 排序（纯前端，作用于已加载的 files） =====
type SourceFilter = 'all' | 'generated' | 'uploaded';
type TypeFilter = 'all' | FileKind;
type SortKey = 'time' | 'name' | 'size';

const keyword = ref('');
const sourceFilter = ref<SourceFilter>('all');
const typeFilter = ref<TypeFilter>('all');
const sortBy = ref<SortKey>('time');

const sourceLabel = computed(
  () => ({ all: '全部来源', generated: '对话产物', uploaded: '已上传' }[sourceFilter.value]),
);

// 类型索引（按文件类型快速分类）：标签 + 固定展示顺序（用户最常找 Word/PPT/PDF）
const TYPE_META: Record<FileKind, { label: string; order: number }> = {
  word: { label: 'Word', order: 1 },
  ppt: { label: 'PPT', order: 2 },
  pdf: { label: 'PDF', order: 3 },
  excel: { label: 'Excel', order: 4 },
  markdown: { label: 'Markdown', order: 5 },
  image: { label: '图片', order: 6 },
  text: { label: '文本', order: 7 },
  archive: { label: '压缩包', order: 8 },
  other: { label: '其他文件', order: 9 },
};

// 来源筛选先应用，类型索引的计数反映「当前来源下」各类型数量（两个维度不打架）。
// .slides.json 是幻灯片编辑器的内部编辑源，不作为文件露出（与交付卡同一拍板，2026-07-21）；
// files.value 原始数组保留它，供 slidesSiblingItem 配对出「编辑」能力
const sourceScopedFiles = computed(() =>
  files.value.filter(
    (f) =>
      !/\.(slides\.json|research\.md)$/i.test(f.filename) &&
      (sourceFilter.value === 'all'
        || f.source === sourceFilter.value
        || (sourceFilter.value === 'generated' && f.source === 'research')),
  ),
);

const typeTabs = computed(() => {
  const counts = new Map<FileKind, number>();
  for (const f of sourceScopedFiles.value) {
    const k = fileKind(f);
    counts.set(k, (counts.get(k) || 0) + 1);
  }
  const tabs: { value: TypeFilter; label: string; count: number }[] = [
    { value: 'all', label: '全部', count: sourceScopedFiles.value.length },
  ];
  // 固定展示全部类型分类（含 xlsx/其他文件，即使当前来源下为 0）：筛选项稳定可预测，
  // 用户随时知道有哪些类型可筛；点进空分类由下方「兜底空态」承接（不再依赖恰好有文件）。
  (Object.keys(TYPE_META) as FileKind[])
    .sort((a, b) => TYPE_META[a].order - TYPE_META[b].order)
    .forEach((kind) => tabs.push({ value: kind, label: TYPE_META[kind].label, count: counts.get(kind) || 0 }));
  return tabs;
});

// ===== 视图模式（列表 / 缩略卡片）+ 按任务分组（Manus 式）=====
type ViewMode = 'list' | 'grid';
const viewMode = ref<ViewMode>(
  (typeof localStorage !== 'undefined' && (localStorage.getItem('myfiles-view') as ViewMode)) || 'grid',
);
const typeFilterLabel = computed(
  () => typeTabs.value.find((t) => t.value === typeFilter.value)?.label ?? '全部',
);
function onTypeSelect({ key }: { key: string | number }) {
  typeFilter.value = key as TypeFilter;
}

// 兜底空态文案：区分「点了空分类」「搜索无结果」「来源无结果」，各给对应引导，
// 而不是一律提示「换个关键词」（类型筛选下这句会驴唇不对马嘴）。
const emptyState = computed(() => {
  if (typeFilter.value !== 'all') {
    const label = typeTabs.value.find((t) => t.value === typeFilter.value)?.label ?? '该';
    return {
      icon: FolderOpenOutlined,
      title: `暂无「${label}」文件`,
      hint: '这个分类当前还没有文件，换个分类或点下方重置筛选。',
    };
  }
  if (keyword.value.trim()) {
    return { icon: SearchOutlined, title: '没有匹配的文件', hint: '换个关键词，或切换上方的来源筛选。' };
  }
  return { icon: SearchOutlined, title: '当前来源下没有文件', hint: '切换上方的来源筛选，或点下方重置。' };
});
function setView(v: ViewMode) {
  viewMode.value = v;
  try { localStorage.setItem('myfiles-view', v); } catch { /* 隐私模式忽略 */ }
}

const viewToggleRef = ref<HTMLElement | null>(null);
const viewThumbRef = ref<HTMLElement | null>(null);
const viewDragging = ref(false);
const viewPressed = ref(false);
const VIEW_THUMB_SNAP = 'transform 0.34s cubic-bezier(0.32, 0.72, 0, 1)';
let viewDrag: {
  pointerId: number;
  startX: number;
  origin: number;
  max: number;
  x: number;
  lastX: number;
  lastT: number;
  vx: number;
} | null = null;
let viewThumbRaf = 0;
let viewSnapTimer = 0;

function viewToggleMax(): number {
  const el = viewToggleRef.value;
  if (!el) return 0;
  return Math.max(0, (el.clientWidth - 6) / 2);
}

function prefersReducedViewMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function paintViewThumb(x: number, animate: boolean) {
  const thumb = viewThumbRef.value;
  if (!thumb) return;
  thumb.style.transition = animate && !prefersReducedViewMotion() ? VIEW_THUMB_SNAP : 'none';
  thumb.style.transform = `translate3d(${x}px, 0, 0)`;
}

function paintViewProgress(x: number, max: number) {
  const el = viewToggleRef.value;
  if (!el) return;
  const progress = max > 0 ? Math.min(1, Math.max(0, x / max)) : 0;
  el.style.setProperty('--vt-progress', progress.toFixed(3));
}

function clearViewThumbInline() {
  const thumb = viewThumbRef.value;
  if (thumb) {
    thumb.style.transition = '';
    thumb.style.transform = '';
  }
  viewToggleRef.value?.style.removeProperty('--vt-progress');
}

function cancelViewThumbRaf() {
  if (!viewThumbRaf) return;
  cancelAnimationFrame(viewThumbRaf);
  viewThumbRaf = 0;
}

function rubberViewThumbX(x: number, max: number): number {
  if (x < 0) return x * 0.22;
  if (x > max) return max + (x - max) * 0.22;
  return x;
}

function finishViewThumbSnap(target: number) {
  window.clearTimeout(viewSnapTimer);
  const thumb = viewThumbRef.value;
  const settle = () => {
    thumb?.removeEventListener('transitionend', onSnapEnd);
    window.clearTimeout(viewSnapTimer);
    if (viewDrag || viewDragging.value) return;
    clearViewThumbInline();
  };
  const onSnapEnd = (event: TransitionEvent) => {
    if (event.propertyName && event.propertyName !== 'transform') return;
    settle();
  };
  if (prefersReducedViewMotion() || !thumb) {
    clearViewThumbInline();
    return;
  }
  thumb.addEventListener('transitionend', onSnapEnd);
  viewSnapTimer = window.setTimeout(settle, 420);
  paintViewThumb(target, true);
}

function onViewPointerDown(e: PointerEvent) {
  if (e.pointerType === 'mouse' && e.button !== 0) return;
  window.clearTimeout(viewSnapTimer);
  const max = viewToggleMax();
  viewDrag = {
    pointerId: e.pointerId,
    startX: e.clientX,
    origin: viewMode.value === 'list' ? max : 0,
    max,
    x: viewMode.value === 'list' ? max : 0,
    lastX: e.clientX,
    lastT: performance.now(),
    vx: 0,
  };
  viewDragging.value = false;
  viewPressed.value = true;
  (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
}

function onViewPointerMove(e: PointerEvent) {
  if (!viewDrag || e.pointerId !== viewDrag.pointerId) return;
  const now = performance.now();
  const dt = Math.max(8, now - viewDrag.lastT);
  viewDrag.vx = (e.clientX - viewDrag.lastX) / dt;
  viewDrag.lastX = e.clientX;
  viewDrag.lastT = now;
  const dx = e.clientX - viewDrag.startX;
  if (!viewDragging.value && Math.abs(dx) < 3) return;
  if (!viewDragging.value) {
    viewDragging.value = true;
    paintViewThumb(viewDrag.origin, false);
  }
  if (e.cancelable) e.preventDefault();
  viewDrag.x = rubberViewThumbX(viewDrag.origin + dx, viewDrag.max);
  const nextX = viewDrag.x;
  const max = viewDrag.max;
  cancelViewThumbRaf();
  viewThumbRaf = requestAnimationFrame(() => {
    viewThumbRaf = 0;
    paintViewThumb(nextX, false);
    paintViewProgress(nextX, max);
  });
}

function onViewPointerUp(e: PointerEvent) {
  if (!viewDrag || e.pointerId !== viewDrag.pointerId) return;
  const drag = viewDrag;
  viewDrag = null;
  viewPressed.value = false;
  cancelViewThumbRaf();
  if (performance.now() - drag.lastT > 80) drag.vx = 0;
  let next: ViewMode = viewMode.value;
  if (viewDragging.value) {
    const flicked = Math.abs(drag.vx) > 0.22;
    if (flicked) next = drag.vx > 0 ? 'list' : 'grid';
    else next = drag.x >= drag.max / 2 ? 'list' : 'grid';
  } else {
    const el = viewToggleRef.value;
    if (el) {
      const mid = el.getBoundingClientRect().left + el.clientWidth / 2;
      next = e.clientX >= mid ? 'list' : 'grid';
    }
  }
  const target = next === 'list' ? drag.max : 0;
  setView(next);
  const shouldSnap = viewDragging.value && Math.abs(drag.x - target) > 0.5;
  viewDragging.value = false;
  if (shouldSnap) {
    paintViewThumb(drag.x, false);
    requestAnimationFrame(() => {
      requestAnimationFrame(() => finishViewThumbSnap(target));
    });
  } else {
    clearViewThumbInline();
  }
}

onBeforeUnmount(() => {
  cancelViewThumbRaf();
  window.clearTimeout(viewSnapTimer);
  viewDrag = null;
});

function onViewKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    setView(viewMode.value === 'grid' ? 'list' : 'grid');
  } else if (e.key === 'ArrowRight') {
    e.preventDefault();
    setView('list');
  } else if (e.key === 'ArrowLeft') {
    e.preventDefault();
    setView('grid');
  }
}

type FileGroup = { key: string; title: string; time: string; files: UserFileItem[] };
// 文件按「哪次对话/任务生成」归组：generated 带 thread → 任务组；上传 → 上传组；其余 → 其他产物组。
// 组按组内最新时间倒序，上传/其他恒沉底；组内沿用 filteredFiles 的排序。
const groupedFiles = computed<FileGroup[]>(() => {
  const map = new Map<string, FileGroup>();
  const order: string[] = [];
  for (const f of filteredFiles.value) {
    let key: string;
    let title: string;
    // 有来源对话就按对话分组（生成/上传一视同仁，混进同一条时间线）——只要有 threadId 就成组，
    // 标题为空时兜底「未命名对话」，绝不因标题缺失把整条对话的产物塌进「其他产物」；
    // 真的没有来源对话的：上传 → 「上传的文件」，产物 → 「其他产物」
    if (f.threadId) {
      key = `task:${f.threadId}`;
      title = f.threadTitle || '未命名对话';
    } else if (f.source === 'uploaded') {
      key = '__upload__';
      title = '上传的文件';
    } else {
      key = '__other__';
      title = '其他产物';
    }
    let g = map.get(key);
    if (!g) {
      g = { key, title, time: f.createdAt || '', files: [] };
      map.set(key, g);
      order.push(key);
    }
    g.files.push(f);
    if ((f.createdAt || '') > g.time) g.time = f.createdAt || '';
  }
  // 统一按组内最新时间倒序——全部混进一条时间线，不再把上传/其他强制沉底
  return order
    .map((k) => map.get(k) as FileGroup)
    .sort((a, b) => (b.time || '').localeCompare(a.time || ''));
});

// 缩略图渲染的类型（2026-07-23 全类型覆盖）：图片直显 / pdf·word·ppt 首页转 canvas /
// md·text·html·xlsx·csv 渲染内容片段（表格/网页/源码）；仅旧格式 xls·压缩包·未知退化为图标。
// FileThumb 内部按真实扩展名决定渲染方式，这里放行到组件即可。
const THUMB_KINDS = new Set<FileKind>(['image', 'pdf', 'word', 'ppt', 'markdown', 'text', 'excel']);
function hasThumb(item: UserFileItem): boolean {
  return THUMB_KINDS.has(fileKind(item));
}

const filteredFiles = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  const list = sourceScopedFiles.value.filter((f) => {
    if (typeFilter.value !== 'all' && fileKind(f) !== typeFilter.value) return false;
    if (kw && !f.filename.toLowerCase().includes(kw)) return false;
    return true;
  });
  const by = sortBy.value;
  return [...list].sort((a, b) => {
    if (by === 'name') return a.filename.localeCompare(b.filename, 'zh-Hans-CN');
    if (by === 'size') return (b.size || 0) - (a.size || 0);
    // 默认按时间倒序（新→旧）；createdAt 是可比较的 ISO 字符串
    return (b.createdAt || '').localeCompare(a.createdAt || '');
  });
});

function onSourceSelect({ key }: { key: string | number }) {
  sourceFilter.value = key as SourceFilter;
  typeFilter.value = 'all'; // 换来源后旧类型可能已不存在，回到「全部」避免落进空列表
}

function resetFilters() {
  keyword.value = '';
  sourceFilter.value = 'all';
  typeFilter.value = 'all';
}

// 删除确认（动作收敛进 ⋯ 菜单后，用 Modal 承接原 popconfirm 的二次确认）
function confirmDelete(item: UserFileItem) {
  Modal.confirm({
    title: '确认删除该文件？',
    content: `《${item.filename}》删除后不可恢复。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => onDelete(item),
  });
}

// 列表加载请求序号（深扫收尾 2026-07-26，照 FileSelector.loadSeq 模式）：
// enterFolder(A) 后立刻 backToRoot()，A 的慢响应回来会把列表覆盖成 A 的内容
let filesLoadSeq = 0;

async function loadFiles() {
  const seq = ++filesLoadSeq;
  loading.value = true;
  errorText.value = '';
  try {
    const data = await listUserFiles(currentFolderId.value, showAll.value);
    if (seq !== filesLoadSeq) return; // 已有更新的一次加载在途/完成，本次过期
    files.value = data.files || [];
    if (data.folders) folders.value = data.folders;
    quota.value = data.quota || null;
  } catch (e) {
    if (seq !== filesLoadSeq) return;
    errorText.value = e instanceof Error ? e.message : '加载失败';
  } finally {
    if (seq === filesLoadSeq) loading.value = false;
  }
}

// ===== 文件夹操作 =====
function enterFolder(id: string) {
  currentFolderId.value = id;
  paneMode.value = 'files';
  keyword.value = '';
  loadFiles();
}

function backToRoot() {
  currentFolderId.value = null;
  paneMode.value = 'files';
  loadFiles();
}

// 进入文件夹页：从文件夹内部点入口也回到顶层列表（folders 随顶层 loadFiles 一起刷新）
function openFoldersPane() {
  const needReload = currentFolderId.value !== null;
  currentFolderId.value = null;
  paneMode.value = 'folders';
  if (needReload) loadFiles();
}

function backToFolders() {
  currentFolderId.value = null;
  paneMode.value = 'folders';
  loadFiles();
}

function openCreateFolder() {
  folderModalMode.value = 'create';
  folderModalName.value = '';
  folderModalTargetId.value = null;
  folderModalOpen.value = true;
}

function openRenameFolder(f: UserFolderItem) {
  folderModalMode.value = 'rename';
  folderModalName.value = f.name;
  folderModalTargetId.value = f.id;
  folderModalOpen.value = true;
}

async function submitFolderModal() {
  const name = folderModalName.value.trim();
  if (!name) {
    message.warning('请输入文件夹名');
    return;
  }
  folderModalBusy.value = true;
  try {
    if (folderModalMode.value === 'create') {
      await createFolder(name);
      message.success('文件夹已创建');
    } else if (folderModalTargetId.value) {
      await renameFolder(folderModalTargetId.value, name);
      message.success('已重命名');
    }
    folderModalOpen.value = false;
    await loadFiles();
  } catch (e) {
    message.error(e instanceof Error ? e.message : '操作失败');
  } finally {
    folderModalBusy.value = false;
  }
}

// 表格行上的删除是个小图标，误触成本高：先确认再删（文件夹删除不动文件，只回未分类）
function confirmDeleteFolder(f: UserFolderItem) {
  Modal.confirm({
    title: '确认删除该文件夹？',
    content: `《${f.name}》删除后，里面的文件会回到未分类，文件本身不会被删除。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => onDeleteFolder(f),
  });
}

async function onDeleteFolder(f: UserFolderItem) {
  try {
    await deleteFolder(f.id);
    message.success('文件夹已删除，里面的文件已回到未分类');
    if (currentFolderId.value === f.id) currentFolderId.value = null;
    await loadFiles();
  } catch (e) {
    message.error(e instanceof Error ? e.message : '删除失败');
  }
}

async function onMoveFile(item: UserFileItem, folderId: string | null) {
  try {
    await moveUserFile(item.id, folderId);
    message.success(folderId ? '已移入文件夹' : '已移出到未分类');
    await loadFiles();
  } catch (e) {
    message.error(e instanceof Error ? e.message : '移动失败');
  }
}

function triggerUpload() {
  fileInputRef.value?.click();
}

async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const picked = Array.from(input.files || []);
  input.value = '';
  if (!picked.length) return;
  uploading.value = true;
  try {
    for (const file of picked) {
      if (file.size > MAX_FILE_MB * 1024 * 1024) {
        message.warning(`《${file.name}》超过 ${MAX_FILE_MB}MB，已跳过`);
        continue;
      }
      try {
        // 在文件夹视图内上传：直接归入当前夹（顶层视图 currentFolderId=null 落未分类，原行为）
        await uploadUserFile(file, currentFolderId.value);
        message.success(currentFolderId.value ? `《${file.name}》已存入当前文件夹` : `《${file.name}》已存入`);
      } catch (e) {
        message.error(e instanceof Error ? e.message : `《${file.name}》上传失败`);
      }
    }
  } finally {
    uploading.value = false;
  }
  await loadFiles();
}

async function onDownload(item: UserFileItem) {
  try {
    await downloadUserFile(item);
  } catch (e) {
    message.error(e instanceof Error ? e.message : '下载失败');
  }
}

async function onKeep(item: UserFileItem) {
  try {
    // PPT 的 .slides.json 是隐藏的编辑源。只保留交付 PPT 会让它七天后被清理，
    // 用户再打开就只能预览不能编辑；两者必须作为一个用户可见文件一起保留。
    const sibling = slidesSiblingItem(item);
    await Promise.all([
      keepUserFile(item.id),
      ...(sibling ? [keepUserFile(sibling.id)] : []),
    ]);
    message.success(sibling ? `《${item.filename}》及其编辑内容已永久保留` : `《${item.filename}》已永久保留`);
    await loadFiles();
  } catch (e) {
    message.error(e instanceof Error ? e.message : '操作失败');
  }
}

async function onDelete(item: UserFileItem) {
  try {
    await deleteUserFile(item.id);
    message.success('已删除');
    await loadFiles();
  } catch (e) {
    message.error(e instanceof Error ? e.message : '删除失败');
  }
}

// ===== 预览 =====
// 按格式分发渲染器：
// - doc/docx/ppt/pptx（版式文档）→ 后端沙箱 LibreOffice 转 PDF 高保真预览（字体沙箱内
//   嵌入渲染，不再出 tofu 乱码符号；首次转换约几秒~30s、按 file_id 缓存后秒开），
//   失败回落 @vue-office 纯前端渲染兜底；
// - pdf → 浏览器原生 iframe；xlsx → @vue-office 交互式表格（翻 sheet 比 PDF 分页好用）；
// - 图片/文本沿用原逻辑；其余二进制不再显示 zip 乱码，引导下载。
type PreviewKind = 'image' | 'pdf' | 'office-pdf' | 'markdown' | 'xlsx' | 'html' | 'text' | 'binary';

const VueOfficeDocx = defineAsyncComponent(async () => {
  await import('@vue-office/docx/lib/v3/index.css');
  return (await import('@vue-office/docx/lib/v3/vue-office-docx.mjs')).default;
});
const VueOfficePptx = defineAsyncComponent(
  async () => (await import('@vue-office/pptx/lib/v3/vue-office-pptx.mjs')).default,
);

// 预览弹窗只剩加载/错误两个瞬时状态（2026-07-23 预览全量统一）：内容状态全部搬进 docViewer
const previewOpen = ref(false);
const previewLoadingText = ref('加载中…');
const previewError = ref('');
const previewItem = ref<UserFileItem | null>(null);

function previewKindOf(item: UserFileItem): PreviewKind {
  const name = item.filename.toLowerCase();
  if ((item.mime || '').startsWith('image/')) return 'image';
  // 网页文件：全屏查看器网页模式（沙箱渲染，顶栏可切源码）——不再当纯文本显示源码
  if (name.endsWith('.html') || name.endsWith('.htm')) return 'html';
  if (name.endsWith('.pdf')) return 'pdf';
  // markdown 走展览区文章模式（2026-07-20）：渲染排版而不是源码文本
  if (/\.(md|markdown)$/.test(name)) return 'markdown';
  // 版式文档走服务端 LibreOffice→PDF 高保真预览（旧格式 doc/ppt 同样能转）
  if (/\.(docx?|pptx?)$/.test(name)) return 'office-pdf';
  if (name.endsWith('.xlsx') || name.endsWith('.xlsm')) return 'xlsx';
  // 旧版 xls 与压缩包等无渲染器，明确引导下载
  if (/\.(xls|zip|rar|7z|gz|tar|exe|bin|dll|so|dmg)$/.test(name)) return 'binary';
  return 'text';
}

// 预览请求令牌：快速 A→B 切换时，A 的慢响应（尤其 office 转 PDF 十几秒）不得覆盖 B 的视图
let previewToken = 0;

async function openPreview(item: UserFileItem) {
  const token = ++previewToken;
  previewItem.value = item;
  previewOpen.value = true;
  previewLoadingText.value = '加载中…';
  previewError.value = '';
  const kind = previewKindOf(item);
  const stale = () => token !== previewToken; // 已切到其它文件/重开：本次结果一律丢弃
  try {
    if (kind === 'office-pdf') {
      // 带 slides.json 编辑源的 pptx（2026-07-21「进入即编辑」）：查看器保持原界面,
      // 主画布直接是可编辑活页——免 LibreOffice 转换等待
      const sib = slidesSiblingItem(item);
      if (sib) {
        const arr = JSON.parse(await fetchUserFileText(sib.id));
        if (stale()) return;
        if (Array.isArray(arr) && arr.length) {
          setDocViewer({
            item,
            slidesPages: arr.map((x: unknown) => String(x)),
            slidesFile: sib,
          });
          previewOpen.value = false;
          return;
        }
      }
      previewLoadingText.value = '正在生成高保真预览，首次转换可能需要十几秒…';
      try {
        const url = await fetchUserFilePreviewPdfUrl(item.id);
        if (stale()) { URL.revokeObjectURL(url); return; } // 丢弃的 blob 必须释放
        // 与主对话统一（2026-07-20 用户拍板）：pdf/office 走全屏文档查看器（缩略图轨+大画布），
        // 不再塞进弹窗 iframe
        if (!(await openDocViewer(item, url, stale))) return;
        previewOpen.value = false;
        return;
      } catch (convertError) {
        if (stale()) return;
        // 转换失败（沙箱忙/超时）：docx/pptx 回落纯前端渲染兜底（同样装进全屏查看器壳），
        // 旧格式无兜底则如实报错
        const name = item.filename.toLowerCase();
        if (name.endsWith('.docx') || name.endsWith('.pptx')) {
          const url = await fetchUserFileBlobUrl(item.id);
          if (stale()) { URL.revokeObjectURL(url); return; }
          setDocViewer({ item, url, custom: { type: name.endsWith('.docx') ? 'docx' : 'pptx' } });
          previewOpen.value = false;
        } else {
          throw convertError;
        }
        return;
      }
    } else if (kind === 'pdf') {
      const url = await fetchUserFileBlobUrl(item.id, 'application/pdf');
      if (stale()) { URL.revokeObjectURL(url); return; }
      if (!(await openDocViewer(item, url, stale))) return;
      previewOpen.value = false;
      return;
    } else if (kind === 'markdown') {
      // 走 /preview 的文本分支（后端不再对 md 回 400「不支持转换」，直接回原文）；
      // 渲染仍在前端 markdown-it 完成
      const preview = await fetchUserFilePreview(item.id);
      if (stale()) return;
      if (preview.kind !== 'text') {
        URL.revokeObjectURL(preview.url);
        throw new Error('后端返回了非文本预览，无法按 Markdown 渲染');
      }
      openArticleViewer(item, renderMarkdownDoc(preview.text));
      previewOpen.value = false;
      return;
    } else if (kind === 'html') {
      // 拿完整源码（download 端点，不受 content 端点 50k 截断）；与主对话产物统一
      // （2026-07-23）：走全屏查看器网页模式（沙箱渲染 + 顶栏切源码），不再用弹窗
      const text = await fetchUserFileText(item.id);
      if (stale()) return;
      setDocViewer({ item, htmlSrc: text });
      previewOpen.value = false;
      return;
    } else if (kind === 'image' || kind === 'xlsx') {
      // Excel/图片走查看器内容槽（vue-office Excel / 原图），blob 存 docViewer.url 供关闭时释放
      const url = await fetchUserFileBlobUrl(item.id);
      if (stale()) { URL.revokeObjectURL(url); return; } // 不进 docViewer 就没人释放，就地回收
      setDocViewer({ item, url, custom: { type: kind === 'xlsx' ? 'excel' : 'image' } });
      previewOpen.value = false;
    } else if (kind === 'binary') {
      setDocViewer({ item, custom: { type: 'binary' } });
      previewOpen.value = false;
    } else if (kind === 'text') {
      // 'text' 是兜底档（txt/json/csv 也含 .py/.sql 等未列举扩展名）：
      // ① 先走 /preview 文本分支——txt/md/json/csv 这类不需要转换，后端直接回原文（上限 1MB）；
      // ② 后端按扩展名判为「不支持在线预览」（400）时回落 /content：它会嗅探二进制并解析
      //    pdf/docx 之类，未知代码文件也能当文本读；两条路都失败才把后端原因抛给弹窗。
      let codeText: string | null = null;
      let truncated = false;
      let previewReason = '';
      try {
        const preview = await fetchUserFilePreview(item.id);
        if (stale()) {
          if (preview.kind === 'pdf') URL.revokeObjectURL(preview.url);
          return;
        }
        if (preview.kind === 'text') {
          codeText = preview.text;
          truncated = preview.truncated;
        } else {
          URL.revokeObjectURL(preview.url);
        }
      } catch (e) {
        if (stale()) return;
        previewReason = e instanceof Error ? e.message : '';
      }
      if (codeText === null) {
        let data: Awaited<ReturnType<typeof getUserFileContent>>;
        try {
          data = await getUserFileContent(item.id);
        } catch (e) {
          // 两条路都失败：优先展示 /preview 给的「哪个格式为什么不行」，而不是笼统的请求失败
          throw new Error(previewReason || (e instanceof Error ? e.message : '加载失败'));
        }
        if (stale()) return;
        // 后端对无法解码的未知二进制回 kind=binary（不再回乱码文本）
        if (data.kind === 'binary') {
          setDocViewer({ item, custom: { type: 'binary' } });
          previewOpen.value = false;
          return;
        }
        codeText = data.text;
        truncated = data.truncated;
      }
      setDocViewer({
        item,
        codeText: codeText || '（未解析出文本内容）',
        metaNote: truncated ? '内容过长，仅展示前一部分；完整内容请下载查看' : undefined,
      });
      previewOpen.value = false;
    }
  } catch (e) {
    if (stale()) return;
    // 弹窗里原样展示后端 detail（如「.xyz 格式暂不支持在线预览，请下载后本地查看」），不得静默
    previewError.value = e instanceof Error ? e.message : '加载失败';
  }
}

/** @vue-office 渲染失败（文件损坏/超大/不支持的内嵌对象）：给出明确出路而不是空白 */
function onViewerRenderError() {
  closeDocViewer();
  message.error('该文件在线渲染失败，请下载后本地查看');
}

function closePreview() {
  previewToken++; // 作废在途预览请求：关掉后迟到的响应不得改已关闭的弹窗状态
  previewOpen.value = false;
}

// ===== 全屏文档查看器（2026-07-20 与主对话统一；2026-07-23 全类型并入） =====
// custom＝内容槽形态（宿主自带渲染器）：excel/image 用 url（blob，关闭时统一释放）、
// docx/pptx 是 LibreOffice 转换失败时的纯前端渲染兜底、binary 是「下载引导」占位
const docViewer = shallowRef<{
  doc?: PDFDocumentProxy;
  html?: string;
  htmlSrc?: string;
  codeText?: string;
  metaNote?: string;
  custom?: { type: 'excel' | 'image' | 'docx' | 'pptx' | 'binary' };
  item: UserFileItem;
  url?: string;
  slidesPages?: string[];
  /** 打开时锁定的编辑源（.slides.json）。保存时优先用它，避免 files 列表刷新后
   *  slidesSiblingItem 找不到而静默 no-op（点了保存、脏标记还在、✕ 仍弹放弃确认）。 */
  slidesFile?: UserFileItem;
} | null>(null);
// 同 tick 连开两个文件时 v-if 不翻转、组件实例被复用，会吃到陈旧的 setup 期常量
// （pageCount/isWeb/metaLine 等）——用递增 key 强制重挂
const docViewerSeq = ref(0);

/** 装配查看器的唯一入口：先释放上一份，再换 key 重挂 */
function setDocViewer(v: NonNullable<typeof docViewer.value>) {
  if (docViewer.value) closeDocViewer();
  docViewerSeq.value += 1;
  docViewer.value = v;
}

// ===== 幻灯片手改（2026-07-21 与主对话同一套 Manus 式编辑）=====
// 2026-07-30：保存改成直接调编译端点后，这里不再需要 centerCtx——此前它只用来往主对话
// 输入框塞重编译指令并跳到对话页。router 仍在（onActivated 要清 URL 上的 ?all=1）。
const router = useRouter();
// 时间线跳过来时带的 `?all=1`（见 onActivated）需要读地址栏
const route = useRoute();
/** pptx 是否带同名 .slides.json 编辑源（同列表配对） */
function slidesSiblingItem(item: UserFileItem): UserFileItem | null {
  if (!/\.pptx?$/i.test(item.filename)) return null;
  const base = item.filename.replace(/\.pptx?$/i, '');
  return files.value.find((f) => f.filename === `${base}.slides.json`) || null;
}

// 保存在途：重复点「保存修改」不重复落库、不重复发重编译消息。
// 用 ref 而非裸变量——同一面旗要传给 DocPagesViewer 的 :saving，落库期间锁住 ✕/Esc
// （手改内容只活在查看器的 editedPages 里，关掉即销毁）。
const savingSlides = ref(false);

/** 保存：改后的整页 HTML 写回 slides.json（原地新版本），再编译覆盖 pptx。
 *  done(ok) 回执给 DocPagesViewer：成功必须清脏，否则「保存过了还弹放弃确认」。 */
async function onViewerPagesSave(pagesHtml: string[], done?: (ok: boolean) => void) {
  const deck = docViewer.value?.item;
  // 优先用打开时锁定的编辑源；列表刷新丢 companion 时仍能保存
  const sib = docViewer.value?.slidesFile || (deck ? slidesSiblingItem(deck) : null);
  if (savingSlides.value) {
    done?.(false);
    return;
  }
  if (!deck || !sib) {
    message.error('找不到对应的编辑源（.slides.json），无法保存手改');
    done?.(false);
    return;
  }
  const ed = { deckFile: deck, slidesFile: sib };
  // P0（2026-07-26 深扫）：**先落库成功再关查看器**。pagesHtml 是用户手改内容的唯一持有者，
  // 提前 closeDocViewer 会让保存失败＝编辑静默蒸发。
  // 现在：失败＝可见报错 + 查看器保持打开（editedPages 还在，可直接重试）。
  // 按**文件名**指路（2026-07-27 统一文件系统）：同名歧义由服务端 _find_row 取最新那条消解。
  // 2026-07-30 用户拍板：「保存修改就直接保存到我的文件中」。此前这里落库编辑源后会把一段
  // 重编译指令写进主对话输入框、发出去、再把用户**踢到对话页**——点的是「保存」，结果换了
  // 个页面、对话里多了一段技术指令、还要等模型跑一轮。现在改成直接调编译接口，人留在原地。
  savingSlides.value = true;
  // 两段式（2026-08-06 修「保存很久 + 保存完还弹放弃」）：
  // ① 编辑源落库（秒级）→ 立刻清脏 + 关查看器，用户不用干等编译
  // ② 后台沙箱重编译 PPTX（几十秒~两分钟）；失败只警告，不回滚脏状态
  // 此前两段串在同一 try 且成功才关：compile 404/慢都会让「保存修改」像坏了。
  try {
    await saveArtifactFile(
      ed.slidesFile.filename, JSON.stringify(pagesHtml), ed.slidesFile.threadId || undefined,
    );
  } catch (e) {
    message.error(`${e instanceof Error ? e.message : '保存失败'}；修改仍在编辑器里，可直接重试`);
    done?.(false);
    savingSlides.value = false;
    return;
  }
  done?.(true);
  savingSlides.value = false;
  closeDocViewer();
  message.loading({ content: `编辑已保存，正在生成 ${ed.deckFile.filename}…`, key: 'slides-compile', duration: 0 });
  try {
    await compileSlidesDeck(
      ed.slidesFile.filename, ed.deckFile.filename, ed.slidesFile.threadId || undefined,
    );
    message.success({ content: `已保存到我的文件：${ed.deckFile.filename}`, key: 'slides-compile' });
  } catch (e) {
    message.warning({
      content: `编辑已保存，但生成 PPT 失败：${e instanceof Error ? e.message : '未知错误'}。重新打开后再点「保存修改」可重试生成`,
      key: 'slides-compile',
      duration: 6,
    });
  }
  await loadFiles(); // 覆盖后缩略图/大小/版本号都变了，就地刷新
}

/** 装 PDF 到全屏查看器。stale()＝本次请求已被后续预览作废：
 *  pdf 解析是慢步骤（大文件几秒），此处**必须在任何副作用之前**判定——否则先点的 A
 *  转完后会把用户正在看的 B 顶掉（setDocViewer 内部还会 revoke 掉 B 的 blob）。
 *  丢弃时要连同本次的 doc/blob 一起释放，返回 false 让调用方直接收手。 */
async function openDocViewer(
  item: UserFileItem,
  url: string,
  stale?: () => boolean,
): Promise<boolean> {
  let doc: PDFDocumentProxy;
  try {
    doc = await loadPdfDocFromUrl(url);
  } catch (e) {
    URL.revokeObjectURL(url); // 解析失败（含回落到 vue-office 的分支）也不能漏 blob
    throw e;
  }
  if (stale?.()) {
    // 先 revoke 再销毁：blob 释放不能被销毁路径的异常挡住（挡住还会一路逃进
    // 调用方的 office 转换 catch，把「预览被作废」误报成「转换失败」）
    URL.revokeObjectURL(url);
    destroyPdfDoc(doc);
    return false;
  }
  setDocViewer({ doc, item, url });
  return true;
}

function closeDocViewer() {
  const cur = docViewer.value;
  docViewer.value = null;
  if (cur) {
    if (cur.url) URL.revokeObjectURL(cur.url);
    destroyPdfDoc(cur.doc);
  }
}

/** markdown 文章模式（2026-07-20）：无分页，渲染好的 HTML 直接进展览区 */
function openArticleViewer(item: UserFileItem, html: string) {
  setDocViewer({ html, item });
}

// ===== 文件类型：图标 + 着色（低饱和 tint，仅作用于 40px 图标底片，不破坏黑白主调） =====
// 判据与图标映射见 composables/fileKind.ts —— composer 的文件选择器用的是同一份
function fileKind(item: UserFileItem): FileKind {
  return fileKindOf(item.filename, item.mime);
}

function formatSize(bytes: number): string {
  if (!bytes || bytes < 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 展示创建/更新时间（2026-07-30 修：此前整整晚 8 小时，显示成未来时间）。
 *
 * `createdAt` 是 MySQL `func.now()` 写的 **+08:00 本地墙上时间**，而同一行里的
 * `expiresAt` 是 Python `datetime.now()` 写的 **UTC** —— 两个时钟差 8 小时，序列化出来
 * 却是长得一模一样的无时区裸串。**所以这两个字段必须用两个不同的解析器**，函数名里
 * 带着来源正是为了让人没法再混用（口径表与判据见 utils/serverTime.ts）。
 */
function formatTime(value: string | null): string {
  return formatDbNaiveTime(value);
}

function expiryText(expiresAt: string): string {
  const remainMs = parseUtcNaiveTs(expiresAt) - Date.now();
  if (Number.isNaN(remainMs)) return '';
  if (remainMs <= 0) return '已过期';
  const days = Math.ceil(remainMs / (24 * 3600 * 1000));
  return days <= 1 ? '今天过期' : `${days} 天后过期`;
}

// ===== 版本历史（Phase B）=====
const versionsOpen = ref(false);
const versionsLoading = ref(false);
const versionsBusy = ref(false);
const versionsError = ref('');
const versionsItem = ref<UserFileItem | null>(null);
const versions = ref<UserFileVersion[]>([]);
// 当前版本 = version_no 最大的 active 版本（draft 永不是当前版）
const currentVersionNo = computed(
  () => versions.value.filter((v) => v.status === 'active').reduce((m, v) => Math.max(m, v.versionNo), 0),
);

function isCurrentVersion(v: UserFileVersion): boolean {
  return v.status === 'active' && v.versionNo === currentVersionNo.value;
}

function verSourceLabel(v: UserFileVersion): string {
  const src = { uploaded: '上传', generated: 'AI 生成', restored: '恢复', research: '研究报告' }[v.source] || v.source;
  return v.createdBy === 'agent' ? `${src}（助手）` : src;
}

async function loadVersions(fileId: string) {
  versionsLoading.value = true;
  versionsError.value = '';
  try {
    const data = await listFileVersions(fileId);
    versions.value = data.versions || [];
  } catch (e) {
    versionsError.value = e instanceof Error ? e.message : '加载版本历史失败';
  } finally {
    versionsLoading.value = false;
  }
}

function openVersions(item: UserFileItem) {
  versionsItem.value = item;
  versions.value = [];
  versionsOpen.value = true;
  void loadVersions(item.id);
}

async function onDownloadVersion(v: UserFileVersion) {
  if (!versionsItem.value) return;
  try {
    await downloadFileVersion(versionsItem.value.id, v);
  } catch (e) {
    message.error(e instanceof Error ? e.message : '下载失败');
  }
}

async function onRestoreVersion(v: UserFileVersion) {
  if (!versionsItem.value || versionsBusy.value) return;
  versionsBusy.value = true;
  try {
    await restoreFileVersion(versionsItem.value.id, v.id);
    message.success(`已恢复 v${v.versionNo} 为当前版本（生成新版本，历史保留）`);
    await loadVersions(versionsItem.value.id);
    await loadFiles();
  } catch (e) {
    message.error(e instanceof Error ? e.message : '恢复失败');
  } finally {
    versionsBusy.value = false;
  }
}

// 页面在 center.vue 的 <keep-alive> 下：onMounted 只首次触发，切走再回来不会重跑。
// 用 onActivated——每次切到「我的文件」都刷新，对话里刚生成的文件立刻可见（keep-alive 首次挂载也会触发）。
onActivated(() => {
  // 时间线上「已下载 xxx.pdf」那颗按钮带 `?all=1` 跳过来（deliverable.myFilesRouteFor）：
  // 那个文件是 source=material / .py 之类，默认清单必然滤掉它——不接这个参数就是跳进一片
  // 找不到它的列表。参数**用完即弃**：留在地址栏里的话，用户手动关掉开关、切走再切回来
  // 会被它一次次强行掰回全量视图。
  if (route.query.all === '1') {
    showAll.value = true;
    const query = { ...route.query };
    delete query.all;
    // 放到下一个 tick：activated 发生在本次导航的收尾里，就地再发一次导航会被判为取消。
    // 失败也只是参数留在地址栏（下次进来又被当成一次显式请求），吞掉即可，不该冒红。
    nextTick(() => router.replace({ path: route.path, query }).catch(() => {}));
  }
  loadFiles();
});
</script>

<style scoped lang="less">
/* 文件夹（ADR-047 §6.6，黑白灰）*/
.myfiles-crumb {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 0 12px;
  font-size: 14px;
}
.crumb-back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: none;
  color: #111827;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 6px;
}
.crumb-back:hover {
  background: #f3f4f6;
}
.crumb-sep {
  color: #9ca3af;
}
.crumb-current {
  color: #6b7280;
}
/* 文件夹分区（移到文件列表下方）：与列表拉开留白，安静的分组标题 */
.myfiles-folders-section {
  margin-top: 28px;
}
.mfs-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;

  .mfs-title {
    font-size: 13px;
    font-weight: 600;
    color: #6b7280;
    letter-spacing: 0.02em;
  }
  .mfs-count {
    min-width: 18px;
    height: 18px;
    padding: 0 6px;
    border-radius: 9px;
    background: #f3f4f6;
    color: #9095a0;
    font-size: 12px;
    line-height: 18px;
    text-align: center;
  }
  .mfs-add {
    margin-left: auto;
    display: inline-flex;
    align-items: center;
    gap: 5px;
    border: 0;
    background: transparent;
    color: #6b7280;
    font-size: 12.5px;
    cursor: pointer;
    padding: 4px 8px;
    border-radius: 7px;
    transition: background 0.15s ease, color 0.15s ease;

    &:hover {
      background: #f3f4f6;
      color: #1a1a1a;
    }
  }
}
.myfiles-folders {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 10px;
}
.folder-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  cursor: pointer;
  background: #fff;
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
}
.folder-card:hover {
  border-color: #d1d5db;
  background: #fafafa;
}
.folder-card.drop-over {
  border-color: #111827;
  background: #f3f4f6;
  box-shadow: 0 0 0 2px rgba(17, 24, 39, 0.12);
}
.fc-icon {
  flex: none;
  font-size: 20px;
  color: #6b7280;
}
.fc-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  color: #111827;
}
.fc-count {
  flex: none;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  border-radius: 10px;
  background: #f3f4f6;
  color: #6b7280;
  font-size: 12px;
  line-height: 20px;
  text-align: center;
}
.fc-more {
  flex: none;
  border: none;
  background: none;
  color: #9ca3af;
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  padding: 2px 6px;
  border-radius: 6px;
}
.fc-more:hover {
  background: #eef0f2;
  color: #374151;
}
.myfiles-pane {
  height: 100%;
  overflow-x: hidden;
  overflow-y: auto;
  width: 100%;
  /* 滚动容器占满全宽，滚动条贴视口右边缘；内容用左右自适应内边距居中在 980px */
  padding: 32px max(24px, calc((100% - 980px) / 2)) 60px;
}

.myfiles-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.myfiles-title {
  font-size: 20px;
  font-weight: 600;
  color: #1a1a1a;
  margin: 0 0 4px;
}

.myfiles-sub {
  font-size: 13px;
  color: #8c8c8c;
  margin: 0;
  max-width: 640px;
}

.myfiles-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.myfiles-hidden-input {
  display: none;
}

/* 容量条 */
.myfiles-quota {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 18px;

  .mq-track {
    flex: 1;
    height: 6px;
    border-radius: 999px;
    background: #f0f0f2;
    overflow: hidden;
  }

  .mq-fill {
    height: 100%;
    border-radius: 999px;
    background: #1a1a1a;
    transition: width 0.3s ease;

    &.warn {
      background: #d4380d;
    }
  }

  .mq-text {
    flex-shrink: 0;
    font-size: 12px;
    color: #9095a0;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    white-space: nowrap;

    .dot {
      width: 3px;
      height: 3px;
      border-radius: 50%;
      background: #d9d9d9;
    }
  }
}

/* 工具栏 */
.myfiles-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.myfiles-search {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 200px;
  height: 38px;
  padding: 0 12px;
  border: 1px solid #ececec;
  border-radius: 10px;
  background: #fff;
  color: #9095a0;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;

  &:focus-within {
    border-color: #bcbcc4;
    box-shadow: none;
  }

  input {
    flex: 1;
    min-width: 0;
    border: 0;
    outline: none;
    background: transparent;
    font-size: 14px;
    line-height: 20px;
    color: #1a1a1a;

    &::placeholder {
      color: #b0b4bd;
    }
  }

  .mf-clear {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 0;
    background: transparent;
    padding: 2px;
    color: #b0b4bd;
    cursor: pointer;
    font-size: 12px;
    border-radius: 4px;

    &:hover {
      color: #6b7280;
      background: #f0f0f2;
    }
  }
}


.myfiles-tool {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 38px;
  padding: 0 12px;
  border: 1px solid #ececec;
  border-radius: 10px;
  background: #fff;
  color: #4b5563;
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;

  .ms-caret {
    font-size: 10px;
    color: #b0b4bd;
  }

  &:hover {
    border-color: #d9d9de;
    background: #fafafa;
    color: #1a1a1a;
  }
}

/* 类型索引条（signature）：安静的滚动 pill 行，选中提墨；类型微色点辅助扫读，不喧宾夺主 */
.myfiles-typebar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 16px;
  padding-bottom: 2px;
  overflow-x: auto;
  scrollbar-width: none;

  &::-webkit-scrollbar {
    display: none;
  }

  .ty-item {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    flex: none;
    height: 30px;
    padding: 0 12px;
    border: 1px solid transparent;
    border-radius: 999px;
    background: transparent;
    color: #6b7280;
    font-size: 13px;
    white-space: nowrap;
    cursor: pointer;
    transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;

    .ty-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      flex: none;

      &.k-word { background: #3b5ba5; }
      &.k-ppt { background: #c07a25; }
      &.k-pdf { background: #c0554f; }
      &.k-excel { background: #2f8a5b; }
      &.k-markdown { background: #5b6472; }
      &.k-image { background: #6b52b8; }
      &.k-text { background: #5b6472; }
      &.k-archive { background: #8a7a55; }
      &.k-other { background: #9ca3af; }
    }

    em {
      font-style: normal;
      font-size: 11px;
      color: #a6abb5;
    }

    &:hover {
      background: #f4f4f6;
      color: #1a1a1a;
    }

    &.active {
      background: #1a1a1a;
      border-color: #1a1a1a;
      color: #fff;

      em { color: rgba(255, 255, 255, 0.6); }
      .ty-dot { box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.25); }
    }
  }
}

.myfiles-status {
  padding: 48px 0;
  text-align: center;
  color: #8c8c8c;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
}

.myfiles-error {
  color: #cf1322;
}

.myfiles-empty {
  padding: 72px 0;
  text-align: center;
  color: #595959;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;

  &.myfiles-empty-slim {
    padding: 56px 0;
  }

  .myfiles-empty-icon {
    font-size: 40px;
    color: #d9d9d9;
    margin-bottom: 2px;
  }

  p {
    margin: 0;
    font-size: 16px;
    font-weight: 500;
    line-height: 24px;
  }

  .myfiles-empty-hint {
    font-size: 14px;
    font-weight: 400;
    line-height: 22px;
    color: #858a93;
    max-width: 440px;
    text-wrap: balance;
  }

  :deep(.ant-btn) {
    margin-top: 6px;
    font-size: 14px;
  }
}

.myfiles-list {
  border: 1px solid #ececec;
  border-radius: 12px;
  overflow: hidden;
  background: #fff;
}

.myfiles-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 15px 18px;
  transition: background-color 0.15s ease;

  & + .myfiles-row {
    border-top: 1px solid #f4f4f4;
  }

  &:hover {
    background: #fafafa;

    .myfiles-ops {
      opacity: 1;
    }
  }
}

.myfiles-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  flex-shrink: 0;
  border-radius: 10px;
  background: #f3f3f3;
  color: #4b5563;
  font-size: 19px;

  /* 低饱和类型着色：仅图标底片，帮助扫读，不做大面积高饱和 */
  &.k-word { background: #eef2fc; color: #3b5ba5; }
  &.k-excel { background: #ebf6ef; color: #2f8a5b; }
  &.k-pdf { background: #fceeee; color: #c0554f; }
  &.k-ppt { background: #fdf1e7; color: #c07a25; }
  &.k-image { background: #f1eefb; color: #6b52b8; }
  &.k-markdown,
  &.k-text { background: #eef1f4; color: #5b6472; }
  &.k-archive { background: #f2f0ec; color: #8a7a55; }
  &.k-other { background: #f3f3f3; color: #4b5563; }
}

.myfiles-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.myfiles-name {
  align-self: flex-start;
  max-width: 100%;
  border: 0;
  background: transparent;
  padding: 0;
  font-size: 14.5px;
  font-weight: 500;
  color: #1a1a1a;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.myfiles-name:hover {
  text-decoration: underline;
  text-underline-offset: 3px;
}

/* 来源对话去胶囊化：安静的灰字链接行（描边圆框读作按钮、抢视线），hover 提墨 + 箭头浮现 */
.myfiles-thread {
  align-self: flex-start;
  max-width: 100%;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-top: 3px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #9ca3af;
  font-size: 12px;
  cursor: pointer;
  transition: color 0.15s ease;

  .mt-icon {
    font-size: 12px;
    flex-shrink: 0;
  }

  .mt-title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 320px;
  }

  .mt-arrow {
    font-size: 10px;
    opacity: 0;
    transform: translateX(-2px);
    transition: opacity 0.15s ease, transform 0.15s ease;
    flex-shrink: 0;
  }

  &:hover {
    color: #1a1a1a;

    .mt-title {
      text-decoration: underline;
      text-underline-offset: 3px;
      text-decoration-color: #c3c6cd;
    }

    .mt-arrow {
      opacity: 0.7;
      transform: translateX(0);
    }
  }
}

.myfiles-preview-state {
  padding: 32px 0;
  text-align: center;
  color: #8c8c8c;
}

/* ===== 全屏查看器内容槽（2026-07-23 预览全量统一）：槽内容编译在本组件作用域，样式写在这里 ===== */

/* Office 兜底渲染容器（docx/pptx）：占满壳、内部滚动 */
.dpv-host-office {
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: #fff;
}

/* pptx 渲染器画布底色偏灰，给幻灯片留呼吸边距 */
.dpv-host-pptx {
  padding: 12px;
  background: #f5f5f6;
}

/* 图片：灰舞台居中原图，等比收缩 */
.dpv-host-image {
  margin: auto;
  padding: 26px;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

/* 无渲染器的二进制：居中引导下载 */
.dpv-host-binary {
  margin: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;

  p {
    margin: 0;
    color: #8c8c8c;
  }
}

.myfiles-meta {
  font-size: 12px;
  color: #9095a0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;

  .dot {
    width: 3px;
    height: 3px;
    border-radius: 50%;
    background: #d9d9d9;
    display: inline-block;
  }
}

/* 来源标注去胶囊化：不再用描边气泡，但保留一处克制的可扫读信号——
   对话产物一抹低饱和靛（示意「AI 产物」），已上传中性灰，与大小/时间区分但不抢视线 */
.src-tag {
  font-size: 12px;
  font-weight: 500;
  color: #8c8c8c;

  &.generated {
    color: #5f6d96;
  }
}

/* 过期提示分级：常态灰不吵；≤3 天转橙、≤24h 转红（此时才值得打扰） */
.expire {
  color: #8c8c8c;

  &.warn {
    color: #ad6800;
  }

  &.danger {
    color: #d4380d;
    font-weight: 500;
  }
}

/* 时间 / 大小：右对齐安静列（ChatGPT 式），窄屏隐藏时间列避免拥挤 */
.myfiles-col {
  flex: none;
  font-size: 12.5px;
  color: #9095a0;
  text-align: right;
  white-space: nowrap;
  transition: opacity 0.15s ease;
}
.myfiles-col-time {
  width: 96px;
}
.myfiles-col-size {
  width: 68px;
}
/* hover 时列让位给操作图标（图标绝对够用，避免两者挤在一起） */
.myfiles-row:hover .myfiles-col {
  opacity: 0;
  pointer-events: none;
}

.myfiles-ops {
  position: absolute;
  right: 14px;
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.15s ease;

  .op-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    border: 0;
    background: transparent;
    border-radius: 8px;
    font-size: 15px;
    color: #8b9099;
    cursor: pointer;
    transition: background 0.15s ease, color 0.15s ease;

    &:hover {
      background: #eef0f2;
      color: #1a1a1a;
    }
  }
}

/* 骨架屏 */
.myfiles-skeleton {
  .sk-row {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 15px 18px;

    & + .sk-row {
      border-top: 1px solid #f4f4f4;
    }
  }

  .sk-icon {
    width: 40px;
    height: 40px;
    border-radius: 10px;
    flex-shrink: 0;
  }

  .sk-body {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .sk-line {
    height: 12px;
    border-radius: 6px;

    &.sk-w55 { width: 55%; }
    &.sk-w35 { width: 35%; }
  }

  .sk-icon,
  .sk-line {
    background: linear-gradient(90deg, #f0f0f2 25%, #f7f7f8 37%, #f0f0f2 63%);
    background-size: 400% 100%;
    animation: mf-shimmer 1.3s ease-in-out infinite;
  }
}

@keyframes mf-shimmer {
  0% { background-position: 100% 0; }
  100% { background-position: 0 0; }
}

@media (prefers-reduced-motion: reduce) {
  .myfiles-skeleton .sk-icon,
  .myfiles-skeleton .sk-line {
    animation: none;
  }
}

@media (max-width: 980px) {
  .myfiles-pane {
    padding: 20px 16px;
  }

  .myfiles-head {
    flex-direction: column;
  }

  .myfiles-toolbar {
    gap: 10px;
  }

  .myfiles-search {
    flex-basis: 100%;
    order: -1;
  }

  .myfiles-ops {
    opacity: 1;
  }

  .myfiles-thread .mt-title {
    max-width: 180px;
  }
}

/* 手机窄屏：容量文案换行、筛选平铺、操作条整行下沉，避免把文件信息挤成竖条 */
@media (max-width: 640px) {
  .myfiles-quota {
    flex-wrap: wrap;

    .mq-track {
      flex-basis: 100%;
    }

    .mq-text {
      white-space: normal;
    }
  }

  /* 窄屏：隐藏时间列腾地方；操作图标常显（触屏无 hover），不再靠悬停 */
  .myfiles-col-time {
    display: none;
  }

  .myfiles-ops {
    position: static;
    opacity: 1;
  }

  .myfiles-row:hover .myfiles-col {
    opacity: 1;
    pointer-events: auto;
  }
}

/* ============ 我的文件（全量对标 Manus 库，2026-07-20 重构）============ */
/* 顶栏：标题 + 新建 */
.myfiles-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 18px;
}
.head-spacer { flex: 1; }
.myfiles-title {
  font-size: 22px;
  font-weight: 650;
  line-height: 30px;
  letter-spacing: -0.01em;
  color: #0d0d0d;
  margin: 0;
}
.myfiles-new {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  font-size: 14px;
  line-height: 20px;
  .nb-caret { font-size: 10px; opacity: 0.7; }
}

/* 工具栏：类型/来源筛选（左）· 搜索 + 视图切换（右） */
.myfiles-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.myfiles-filter-strip {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
}
.tb-spacer { flex: 1; }
.mf-filter {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 7px;
  height: 36px;
  padding: 0 14px;
  border: 1px solid #e8e8ec;
  border-radius: 10px;
  background: #fff;
  color: #35353f;
  font-size: 14px;
  line-height: 20px;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s ease, border-color 0.15s ease;

  .mf-caret { flex: none; font-size: 10px; color: #9a9aa5; }
  .anticon { flex: none; }
  &:hover { background: #fafafb; border-color: #dcdce2; }
  &.on { border-color: #0d0d0d; color: #0d0d0d; }
}
.mf-filter-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mf-menu-count {
  margin-left: 10px;
  color: #b0b0ba;
  font-size: 12px;
}
.myfiles-search {
  width: 240px;
  flex: none;
  height: 36px;
}
.myfiles-viewtoggle {
  --vt-progress: 0;
  position: relative;
  display: grid;
  width: 76px;
  height: 36px;
  flex: none;
  grid-template-columns: 1fr 1fr;
  align-items: stretch;
  padding: 3px;
  border-radius: 10px;
  background: #f0f0f2;
  cursor: pointer;
  touch-action: none;
  user-select: none;
  transition: background 0.2s ease;

  &:focus-visible {
    outline: 2px solid #7786d9;
    outline-offset: 2px;
  }
}
.myfiles-viewtoggle[data-pressed] {
  background: #e7e7ec;
}
.vt-thumb {
  position: absolute;
  top: 3px;
  bottom: 3px;
  left: 3px;
  width: calc(50% - 3px);
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
  transform: translate3d(0, 0, 0);
  transition: transform 0.34s cubic-bezier(0.32, 0.72, 0, 1), box-shadow 0.2s ease;
  pointer-events: none;
}
.myfiles-viewtoggle[data-mode='list'] .vt-thumb {
  transform: translate3d(100%, 0, 0);
}
.myfiles-viewtoggle[data-dragging] .vt-thumb,
.myfiles-viewtoggle[data-pressed] .vt-thumb {
  will-change: transform;
  box-shadow: 0 2px 8px rgba(15, 18, 28, 0.14);
}
.myfiles-viewtoggle[data-dragging] .vt-thumb {
  transition: none;
}
.vt-icon {
  position: relative;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #8e8ea0;
  font-size: 15px;
  pointer-events: none;
  transition: color 0.28s cubic-bezier(0.32, 0.72, 0, 1);

  &.on { color: #0d0d0d; }
}
.myfiles-viewtoggle[data-dragging] .vt-icon {
  transition: none;
}
.myfiles-viewtoggle[data-dragging] .vt-grid {
  color: color-mix(in srgb, #0d0d0d calc((1 - var(--vt-progress)) * 100%), #8e8ea0);
}
.myfiles-viewtoggle[data-dragging] .vt-list {
  color: color-mix(in srgb, #0d0d0d calc(var(--vt-progress) * 100%), #8e8ea0);
}
@media (prefers-reduced-motion: reduce) {
  .myfiles-viewtoggle,
  .vt-thumb,
  .vt-icon {
    transition: none;
  }
}

/* 内容区：按任务分组 */
.myfiles-content { margin-top: 4px; }
.mf-group { margin-top: 24px; &:first-child { margin-top: 8px; } }
.mf-group-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.mf-group-title {
  font-size: 14.5px;
  font-weight: 600;
  color: #0d0d0d;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mf-group-time { margin-left: auto; font-size: 12.5px; color: #b0b0ba; flex: none; }

/* 类型着色图标底片（沿用现有 k-* 低饱和 tint） */
.mf-ic {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  flex: none;
  border-radius: 8px;
  background: #f3f3f3;
  color: #4b5563;
  font-size: 16px;

  &.k-word { background: #eef2fc; color: #3b5ba5; }
  &.k-excel { background: #ebf6ef; color: #2f8a5b; }
  &.k-pdf { background: #fceeee; color: #c0554f; }
  &.k-ppt { background: #fdf1e7; color: #c07a25; }
  &.k-image { background: #f1eefb; color: #6b52b8; }
  &.k-markdown,
  &.k-text { background: #eef1f4; color: #5b6472; }
  &.k-archive { background: #f2f0ec; color: #8a7a55; }
  &.k-other { background: #eef1f4; color: #5b6472; }
}

/* 更多 ⋯ 按钮（卡片头 / 行尾 / 文件夹） */
.mf-more {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  background: transparent;
  border-radius: 7px;
  color: #9a9aa5;
  font-size: 16px;
  cursor: pointer;
  flex: none;
  transition: background 0.15s ease, color 0.15s ease;

  &:hover { background: #eef0f2; color: #0d0d0d; }
}

/* 网格：内容缩略卡片 */
.mf-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.mf-card {
  border: 1px solid #ececef;
  border-radius: 14px;
  overflow: hidden;
  background: #fff;
  cursor: pointer;
  transition: box-shadow 0.16s ease, border-color 0.16s ease;

  &:hover { border-color: #dcdce2; box-shadow: 0 6px 22px rgba(0, 0, 0, 0.07); }
}
.mf-card-head {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 12px 12px 12px 13px;

  .mf-card-name {
    flex: 1;
    min-width: 0;
    font-size: 13.5px;
    color: #0d0d0d;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .mf-more { opacity: 0; }
}
.mf-card:hover .mf-card-head .mf-more { opacity: 1; }
.mf-card-thumb {
  position: relative;
  height: 168px;
  border-top: 1px solid #f2f2f4;
  background: #fafafb;
  overflow: hidden;

  /* 底部渐隐（对齐 Manus，长内容不生硬截断） */
  &::after {
    content: '';
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: 40px;
    background: linear-gradient(to bottom, rgba(255, 255, 255, 0), #fff);
    pointer-events: none;
  }
}
.mf-card-ph {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #cdced6;
  font-size: 40px;
}
.mf-card-expire {
  position: absolute;
  left: 10px;
  bottom: 8px;
  z-index: 1;
  font-size: 11px;
  color: #9095a0;
  background: rgba(255, 255, 255, 0.85);
  padding: 1px 7px;
  border-radius: 6px;
}

/* 列表：紧凑行 */
.mf-rows {
  border: 1px solid #efeff2;
  border-radius: 12px;
  overflow: hidden;
}
.mf-row {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 11px 12px;
  cursor: pointer;
  transition: background 0.15s ease;

  & + .mf-row { border-top: 1px solid #f4f4f6; }
  &:hover { background: #f9f9fa; }

  .mf-row-name {
    flex: 1;
    min-width: 0;
    font-size: 14px;
    color: #0d0d0d;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .mf-row-note { flex: none; font-size: 12px; color: #b8b8c0; }
  .mf-row-size { flex: none; width: 74px; text-align: right; font-size: 12.5px; color: #9a9aa5; }
  .mf-more { opacity: 0; }
  &:hover .mf-more { opacity: 1; }
}

/* 工具栏「文件夹」入口：与筛选按钮同款，右侧小计数 */
.mf-folders-entry {
  .mf-entry-count {
    min-width: 18px;
    height: 18px;
    padding: 0 6px;
    border-radius: 9px;
    background: #f3f4f6;
    color: #9095a0;
    font-size: 11px;
    line-height: 18px;
    text-align: center;
  }
}

/* 文件夹页：网盘式表格（文件名 · 大小 · 类型 · 创建时间，悬停出行内操作） */
.fd-table {
  border: 1px solid #ececef;
  border-radius: 12px;
  background: #fff;
  overflow: hidden;
}
.fd-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px solid #f2f2f4;
  color: #9a9aa5;
  font-size: 12.5px;

  .fd-col-name {
    flex: 1;
    min-width: 0;
  }
}
.fd-sort {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 0;
  background: transparent;
  color: inherit;
  font-size: inherit;
  padding: 0;
  cursor: pointer;
  transition: color 0.15s ease;

  &:hover { color: #0d0d0d; }

  .fd-caret {
    &.asc { color: #3e434c; }
  }
}
.fd-col {
  flex: none;
  text-align: left;
  white-space: nowrap;
  transition: opacity 0.15s ease;
}
.fd-col-size { width: 96px; }
.fd-col-type { width: 96px; }
.fd-col-time { width: 150px; }
.fd-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 16px;
  cursor: pointer;
  transition: background 0.15s ease;

  & + .fd-row { border-top: 1px solid #f4f4f6; }

  .fd-col {
    color: #9a9aa5;
    font-size: 12.5px;
  }

  /* hover：时间列让位给操作图标（同文件行的既有模式） */
  &:hover {
    background: #fafafb;

    .fd-col-time { opacity: 0; }
    .fd-ops { opacity: 1; }
  }
}
.fd-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  flex: none;
  border-radius: 8px;
  background: #fdf4e3;
  color: #d9a648;
  font-size: 16px;
}
.fd-name {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  color: #0d0d0d;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fd-ops {
  position: absolute;
  right: 12px;
  display: flex;
  align-items: center;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.15s ease;

  .op-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    border: 0;
    background: transparent;
    border-radius: 8px;
    font-size: 15px;
    color: #8b9099;
    cursor: pointer;
    transition: background 0.15s ease, color 0.15s ease;

    &:hover { background: #eef0f2; color: #0d0d0d; }
  }
}

@media (max-width: 720px) {
  .myfiles-search { width: 160px; }
  .mf-grid { grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); }
}

/* 窄屏：隐藏类型/时间列腾地方；操作图标常显（触屏无 hover） */
@media (max-width: 640px) {
  .fd-col-type,
  .fd-col-time { display: none; }

  .fd-ops {
    position: static;
    opacity: 1;
  }
}

/* 手机 / iPad 文件工作区：顶栏不重复标题，筛选可滑动，触控操作不依赖 hover。 */
@media (max-width: 1024px) {
  .myfiles-pane {
    padding: 22px clamp(16px, 3vw, 28px) calc(44px + env(safe-area-inset-bottom));
    overflow-x: hidden;
    overscroll-behavior-x: none;
    touch-action: pan-y;
  }

  .myfiles-head {
    min-height: 44px;
    justify-content: flex-end;
    margin-bottom: 14px;
  }

  .myfiles-title,
  .head-spacer {
    display: none;
  }

  .myfiles-new {
    min-height: 44px;
    padding-right: 18px;
    padding-left: 18px;
    touch-action: manipulation;
  }

  .myfiles-toolbar {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    align-items: center;
    gap: 10px;
    margin-bottom: 14px;
  }

  .myfiles-filter-strip {
    grid-column: 1;
    grid-row: 2;
    gap: 8px;
    overflow: hidden;
    min-width: 0;
    padding: 1px;
    touch-action: pan-y;
  }

  .tb-spacer {
    display: none;
  }

  .myfiles-search {
    grid-column: 1;
    grid-row: 1;
    width: 100%;
    min-width: 0;
    height: 44px;
    flex: none;
  }

  .mf-filter {
    height: 44px;
    flex: 1 1 0;
    min-width: 0;
    padding: 0 12px;
    touch-action: manipulation;
  }

  .myfiles-viewtoggle {
    width: 80px;
    height: 44px;
    flex: none;
  }

  .mf-grid {
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 14px;
  }

  .mf-card-head .mf-more,
  .mf-row .mf-more,
  .mf-card:hover .mf-card-head .mf-more,
  .mf-row:hover .mf-more {
    width: 44px;
    height: 44px;
    opacity: 1;
  }

  .mf-row {
    min-height: 56px;
  }

  .myfiles-crumb {
    min-width: 0;
    min-height: 44px;
    overflow: hidden;
    white-space: nowrap;
  }

  .crumb-current {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .crumb-back {
    min-height: 44px;
    touch-action: manipulation;
  }

  .fd-row .fd-ops,
  .fd-row:hover .fd-ops {
    position: static;
    opacity: 1;
  }

  .fd-ops .op-icon {
    width: 44px;
    height: 44px;
    touch-action: manipulation;
  }
}

@media (max-width: 719px) {
  .myfiles-pane {
    padding: 14px 12px calc(32px + env(safe-area-inset-bottom));
  }

  .myfiles-head {
    margin-bottom: 12px;
  }

  .myfiles-new {
    width: 100%;
    justify-content: center;
  }

  .myfiles-toolbar {
    grid-template-columns: minmax(0, 1fr);
  }

  .myfiles-search {
    grid-column: 1;
  }

  .myfiles-filter-strip {
    margin-right: 0;
    margin-left: 0;
    padding-right: 0;
    padding-left: 0;
  }

  .mf-group {
    margin-top: 18px;

    &:first-child {
      margin-top: 6px;
    }
  }

  .mf-group-head {
    min-height: 28px;
    margin-bottom: 7px;
  }

  .mf-group-title {
    font-size: 14px;
  }

  .mf-group-time {
    display: none;
  }

  .mf-rows {
    border-radius: 14px;
  }

  .mf-row {
    display: grid;
    min-height: 66px;
    grid-template-columns: 36px minmax(0, 1fr) 44px;
    grid-template-rows: auto auto;
    gap: 2px 10px;
    padding: 8px 6px 8px 10px;

    .mf-ic {
      grid-column: 1;
      grid-row: 1 / span 2;
      align-self: center;
    }

    .mf-row-name {
      grid-column: 2;
      grid-row: 1;
      align-self: end;
      overflow: hidden;
      font-size: 14px;
      line-height: 19px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .mf-row-note {
      grid-column: 2;
      grid-row: 2;
      overflow: hidden;
      align-self: start;
      font-size: 11px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .mf-row-size {
      display: none;
    }

    .mf-more {
      grid-column: 3;
      grid-row: 1 / span 2;
      align-self: center;
    }
  }

  .myfiles-empty,
  .myfiles-status {
    min-height: 46vh;
    box-sizing: border-box;
    padding: 44px 12px;
  }

  .myfiles-empty .myfiles-empty-hint {
    max-width: 290px;
    line-height: 1.65;
  }

  .fd-table {
    border-radius: 14px;
  }

  .fd-head {
    min-height: 42px;
    padding: 8px 10px;
  }

  .fd-head .fd-col,
  .fd-row .fd-col {
    display: none;
  }

  .fd-row {
    display: grid;
    min-height: 64px;
    grid-template-columns: 36px minmax(0, 1fr) 88px;
    gap: 8px;
    padding: 7px 6px 7px 10px;
  }

  .fd-icon {
    grid-column: 1;
  }

  .fd-name {
    grid-column: 2;
  }

  .fd-ops {
    grid-column: 3;
    gap: 0;
  }
}
</style>

<style lang="less">
/* 类型 / 来源筛选下拉（teleport 到 body，scoped 够不到）：
   全局主题把选中项背景改成了黑底白字，太重；这里覆盖成克制的浅灰高亮 + 深色文字。 */
.mf-filter-overlay {
  .ant-menu-item-selected,
  .ant-dropdown-menu-item-selected {
    color: #17171c !important;
    font-weight: 500;
    background: #f2f2f4 !important;
  }

  .ant-menu-item-selected::after {
    display: none; /* 去掉右侧竖条高亮 */
  }

  .ant-menu-item:hover,
  .ant-dropdown-menu-item:hover {
    background: #f7f7f8;
  }

  /* 计数为 0 的分类淡化——仍可点选（点进去落到兜底空态），只是视觉上后退。
     放非 scoped 块：overlay teleport 到 body，且需盖过 Ant 默认 item 文字色。 */
  .mf-menu-empty:not(.ant-menu-item-selected):not(.ant-dropdown-menu-item-selected) {
    color: #9a9aa5;

    .mf-menu-count { color: #cfcfd6; }
  }
}

/* 非 scoped：modal teleport 到 body，scoped 样式够不到。
   全局主题把 .ant-modal-body padding 清零（components/Modal/src/index.less），这里恢复内边距。 */
.myfiles-preview-modal {
  .ant-modal-header {
    padding: 20px 24px 8px;
  }

  .ant-modal-body {
    padding: 12px 24px 24px;
  }
}

/* 版本历史弹窗（Phase B）：恢复被全局主题清零的内边距 + 版本列表样式 */
.myfiles-versions-modal {
  .ant-modal-header {
    padding: 20px 24px 8px;
  }

  .ant-modal-body {
    padding: 8px 24px 20px;
  }

  .myfiles-versions {
    max-height: 52vh;
    margin: 0;
    padding: 0;
    overflow-y: auto;
    list-style: none;
  }

  .ver-row {
    padding: 12px 2px;
    border-bottom: 1px solid #f0f1f3;

    &:last-child {
      border-bottom: none;
    }
  }

  .ver-head {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }

  .ver-no {
    color: #202228;
    font-size: 13px;
  }

  .ver-tag {
    padding: 1px 8px;
    border-radius: 999px;
    font-size: 11px;
    white-space: nowrap;
  }

  .ver-current {
    background: #eef6f0;
    color: #3e8e58;
  }

  .ver-draft {
    background: #fbf3f3;
    color: #c04a4a;
  }

  .ver-meta {
    overflow: hidden;
    color: #8a8f99;
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .ver-ops {
    display: flex;
    flex-shrink: 0;
    gap: 6px;
    margin-left: auto;

    .op {
      padding: 2px 10px;
      border: 1px solid #e3e5e8;
      border-radius: 6px;
      background: #fff;
      color: #4a4e57;
      font-size: 12px;
      cursor: pointer;

      &:hover {
        border-color: #c9cdd3;
        color: #202228;
      }

      &:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
    }
  }

  .ver-summary {
    margin-top: 4px;
    padding-left: 2px;
    color: #6a6f78;
    font-size: 12px;
  }
}

/* 新建/重命名文件夹弹窗：同样恢复被全局主题清零的内边距，避免内容贴边 */
.myfiles-folder-modal {
  .ant-modal-header {
    padding: 20px 24px 12px;
  }
  .ant-modal-body {
    padding: 8px 24px 20px;
  }
  .ant-modal-footer {
    padding: 12px 24px 20px;
    margin-top: 0;
  }
  .ant-input {
    border-radius: 8px;
  }
}

/* Teleport 到 body 的筛选、操作菜单和弹窗也要跟随手机宽度。 */
@media (max-width: 719px) {
  .mf-filter-overlay,
  .mf-file-actions-overlay {
    max-width: calc(100vw - 24px);

    .ant-dropdown-menu,
    .ant-menu {
      max-width: calc(100vw - 24px);
      max-height: min(70dvh, 520px);
      overflow-y: auto;
      overscroll-behavior: contain;
      border-radius: 13px;
    }

    .ant-dropdown-menu-item,
    .ant-dropdown-menu-submenu-title,
    .ant-menu-item,
    .ant-menu-submenu-title {
      min-height: 44px;
      display: flex;
      align-items: center;
    }
  }

  .myfiles-preview-modal,
  .myfiles-versions-modal,
  .myfiles-folder-modal {
    .ant-modal {
      top: 12px;
      width: calc(100vw - 16px) !important;
      max-width: none;
      margin: 0 auto;
      padding-bottom: calc(12px + env(safe-area-inset-bottom));
    }

    .ant-modal-content {
      border-radius: 18px;
    }

    .ant-modal-header {
      padding: 18px 18px 10px;
    }

    .ant-modal-body {
      padding-right: 18px;
      padding-left: 18px;
    }
  }

  .myfiles-folder-modal {
    .ant-modal-body {
      padding-top: 8px;
      padding-bottom: 14px;
    }

    .ant-modal-footer {
      padding: 10px 18px calc(16px + env(safe-area-inset-bottom));
    }

    .ant-input {
      min-height: 44px;
      font-size: 16px;
    }
  }

  .myfiles-versions-modal {
    .ant-modal-body {
      padding: 6px 16px calc(16px + env(safe-area-inset-bottom));
    }

    .myfiles-versions {
      max-height: calc(100dvh - 150px - env(safe-area-inset-bottom));
    }

    .ver-head {
      flex-wrap: wrap;
    }

    .ver-meta {
      order: 3;
      flex-basis: 100%;
    }

    .ver-ops {
      order: 4;
      width: 100%;
      margin-left: 0;

      .op {
        min-height: 40px;
        padding-right: 14px;
        padding-left: 14px;
      }
    }
  }
}
</style>
