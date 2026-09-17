<template>
  <section class="presentation-studio">
    <div class="studio-layout">
      <aside class="studio-catalog" aria-label="皮肤包目录">
        <div class="catalog-head">
          <strong>外观目录</strong>
        </div>
        <a-button class="catalog-import" type="primary" :loading="importing" @click="openImport">
          <PlusOutlined />
          导入皮肤包
        </a-button>
        <input
          ref="importInput"
          class="package-file-input"
          type="file"
          accept=".axiomskin,.zip"
          @change="handleImport"
        />
        <label class="catalog-search">
          <SearchOutlined aria-hidden="true" />
          <input v-model="catalogKeyword" type="search" placeholder="搜索名称或标识" aria-label="搜索皮肤包" />
        </label>

        <div class="catalog-list">
          <button
            v-for="option in visibleCatalogOptions"
            :key="option.key"
            type="button"
            :class="['catalog-card', { selected: selectedKey === option.key }]"
            :aria-pressed="selectedKey === option.key"
            @click="selectedKey = option.key"
          >
            <span class="catalog-preview" aria-hidden="true">
              <img v-if="previewFor(option.key)" :src="previewFor(option.key)" alt="" />
              <span v-else class="default-preview"></span>
            </span>
            <span class="catalog-copy">
              <strong>{{ option.name }}</strong>
            </span>
          </button>
          <div v-if="visibleCatalogOptions.length === 0" class="catalog-empty">
            <InboxOutlined />
            <span>没有找到匹配的皮肤包</span>
          </div>
        </div>
      </aside>

      <div class="studio-workbench">
        <div class="studio-toolbar">
          <div class="selected-preset">
            <strong>{{ selectedOption?.name || '未选择外观' }}</strong>
            <span
              v-if="selectedServerRecord?.materialReady === false"
              class="material-state is-error"
              :title="selectedServerRecord.materialProblem || '素材异常'"
            >
              素材异常
            </span>
          </div>

          <div class="toolbar-controls">
            <div class="package-actions" role="group" aria-label="皮肤包操作">
              <a-button :disabled="!selectedPortableSkin" @click="openDetail">
                <EyeOutlined />
                详情
              </a-button>
              <a-button :disabled="!selectedPortableSkin" @click="openMetadataEditor">
                <EditOutlined />
                编辑
              </a-button>
              <a-button :loading="exporting" :disabled="!selectedPortableSkin" @click="exportSelected">
                <DownloadOutlined />
                导出
              </a-button>
              <a-tooltip :title="portableSkinDeleteHint">
                <span class="delete-control">
                  <a-popconfirm
                    :title="deleteConfirmTitle"
                    ok-text="删除"
                    cancel-text="取消"
                    :disabled="!canDeletePortableSkin"
                    @confirm="deleteSelected"
                  >
                    <a-button danger :loading="deleting" :disabled="!canDeletePortableSkin">
                      <DeleteOutlined />
                      删除
                    </a-button>
                  </a-popconfirm>
                </span>
              </a-tooltip>
            </div>
            <div class="viewport-switch" role="group" aria-label="预览宽度">
              <button
                v-for="option in viewportOptions"
                :key="option.key"
                type="button"
                :class="{ active: viewport === option.key }"
                :aria-pressed="viewport === option.key"
                @click="viewport = option.key"
              >
                {{ option.label }}
              </button>
            </div>
          </div>
        </div>

        <div class="preview-scroll">
          <div
            :class="[
              'studio-run-shell',
              `is-${viewport}`,
              {
                'show-left': compactPanel === 'left',
                'show-right': compactPanel === 'right',
              },
            ]"
            :data-presentation-preset="runtime.key"
            :style="runtime.styleVars"
          >
            <button
              v-if="viewport !== 'desktop' && compactPanel !== 'none'"
              type="button"
              class="studio-compact-backdrop"
              aria-label="关闭预览面板"
              @click="compactPanel = 'none'"
            ></button>
            <header v-if="viewport !== 'desktop'" class="studio-compact-bar">
              <button
                type="button"
                class="studio-compact-icon-button"
                aria-label="打开会话列表"
                title="会话"
                :aria-pressed="compactPanel === 'left'"
                @click="toggleCompactPanel('left')"
              >
                <HistoryOutlined />
              </button>
              <strong>2026学校迎新助手</strong>
              <button
                type="button"
                :aria-pressed="compactPanel === 'right'"
                @click="toggleCompactPanel('right')"
              >
                <BulbOutlined />
                <span>场景</span>
              </button>
            </header>
            <aside class="studio-left-rail">
              <span v-if="runtime.sidebarDecoration" class="studio-rail-decoration" aria-hidden="true">
                <component
                  :is="runtime.sidebarDecoration"
                  v-bind="runtime.componentProps || {}"
                  region="sidebar"
                />
              </span>
              <div class="studio-rail-content">
                <div class="studio-brand"><i>迎</i><strong>2026学校迎新助手</strong><span>◫</span></div>
                <button class="studio-new-session" type="button">＋&nbsp; 新对话</button>
                <div class="studio-search">⌕&nbsp; 搜索会话</div>
                <small class="studio-group-label">今天</small>
                <div class="studio-session is-active">新生现场报到流程</div>
                <div class="studio-session">宿舍入住与校园卡</div>
              </div>
            </aside>

            <main class="studio-main-stage">
              <component
                :is="runtime.backdrop"
                v-if="runtime.backdrop"
                v-bind="runtime.componentProps || {}"
                :empty-state="true"
              />
              <div class="studio-welcome">
                <small>2026学校迎新助手</small>
                <h3>{{ runtime.welcomeTitle }}</h3>
                <p>报到、缴费、资助、专业与校区问题，都可以在这里获得清楚的指引。</p>
              </div>
              <div class="studio-composer">
                <component
                  :is="runtime.composerDecoration"
                  v-if="runtime.composerDecoration"
                  v-bind="runtime.componentProps || {}"
                />
                <span>{{ runtime.composerPlaceholder }}</span>
                <div class="studio-composer-actions"><i>＋</i><b>↑</b></div>
              </div>
            </main>

            <aside class="studio-right-rail">
              <span v-if="runtime.inspirationDecoration" class="studio-rail-decoration" aria-hidden="true">
                <component
                  :is="runtime.inspirationDecoration"
                  v-bind="runtime.componentProps || {}"
                  region="inspiration"
                />
              </span>
              <div class="studio-rail-content">
                <div class="studio-right-head"><strong>场景与推荐</strong><small>按场景快速找到问题</small></div>
                <small class="studio-right-label">使用场景</small>
                <div class="studio-select">报到入学 <span>⌄</span></div>
                <small class="studio-right-label">问题推荐</small>
                <div v-for="task in previewTasks" :key="task" class="studio-task"><i>◇</i><span>{{ task }}</span><b>›</b></div>
              </div>
            </aside>
          </div>
        </div>

      </div>
    </div>

    <a-modal
      v-model:open="detailOpen"
      title="皮肤包详情"
      :width="520"
      :footer="null"
      :centered="true"
      wrap-class-name="subagent-skin-modal"
    >
      <a-spin :spinning="detailLoading">
        <dl v-if="skinDetail" class="skin-modal-fields">
          <div>
            <dt>显示名称</dt>
            <dd>{{ skinDetail.name }}</dd>
          </div>
          <div>
            <dt>皮肤包标识</dt>
            <dd><code>{{ skinDetail.key }}</code></dd>
          </div>
          <div>
            <dt>选择标识</dt>
            <dd><code>{{ skinDetail.assignmentKey }}</code></dd>
          </div>
          <div>
            <dt>版本</dt>
            <dd>v{{ skinDetail.version }}</dd>
          </div>
          <div>
            <dt>来源</dt>
            <dd>{{ sourceLabel(skinDetail.sourceType) }}</dd>
          </div>
          <div>
            <dt>引用记录</dt>
            <dd>{{ skinDetail.referenceCount || 0 }} 条当前或历史引用</dd>
          </div>
          <div class="is-block">
            <dt>管理备注</dt>
            <dd>{{ skinDetail.description || '暂无备注' }}</dd>
          </div>
        </dl>
      </a-spin>
    </a-modal>

    <a-modal
      v-model:open="metadataEditorOpen"
      title="编辑子智能体皮肤信息"
      :width="480"
      :centered="true"
      :mask-closable="false"
      ok-text="保存"
      cancel-text="取消"
      :confirm-loading="updating"
      wrap-class-name="subagent-skin-modal"
      @ok="saveMetadata"
    >
      <p class="skin-modal-note">
        这里只修改当前系统的显示名称和备注。图片或布局变更需升级包版本后重新导入。
      </p>
      <dl v-if="selectedPortableSkin" class="skin-modal-meta">
        <div>
          <dt>标识</dt>
          <dd><code>{{ selectedPortableSkin.key }}</code></dd>
        </div>
        <div>
          <dt>版本</dt>
          <dd>v{{ selectedPortableSkin.version }}</dd>
        </div>
      </dl>
      <a-form layout="vertical" class="skin-modal-form">
        <a-form-item label="显示名称" required>
          <a-input
            v-model:value="metadataName"
            :maxlength="128"
            show-count
            placeholder="会出现在外观目录中"
          />
        </a-form-item>
        <a-form-item label="管理备注">
          <a-textarea
            v-model:value="metadataDescription"
            :maxlength="512"
            :rows="4"
            show-count
            placeholder="说明这套皮肤的用途或适用场景"
          />
        </a-form-item>
      </a-form>
    </a-modal>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import {
  BulbOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  EyeOutlined,
  HistoryOutlined,
  InboxOutlined,
  PlusOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue';
import { getRunPresentationPreview } from '../../agent/run/presentation/preview';
import { resolveRunPresentation } from '../../agent/run/presentation/registry';
import {
  hydratePortableRunSkin,
  releaseHydratedPortableRunSkin,
  type HydratedPortableRunSkin,
} from '../../agent/run/presentation/portable';
import type { PortableRunSkinRecord } from '../core/type';
import {
  deleteSubAgentSkin,
  exportSubAgentSkin,
  getSubAgentSkin,
  importSubAgentSkin,
  queryAdminPresentationPresets,
  updateSubAgentSkinMetadata,
  type PresentationPresetRecord,
} from '../api/presentation.api';

type Viewport = 'desktop' | 'tablet' | 'mobile';

const selectedKey = ref('');
const viewport = ref<Viewport>('desktop');
const compactPanel = ref<'none' | 'left' | 'right'>('none');
const serverRecords = ref<PresentationPresetRecord[]>([]);
const hydratedPortableSkins = ref(new Map<string, HydratedPortableRunSkin>());
const catalogKeyword = ref('');
const importing = ref(false);
const exporting = ref(false);
const updating = ref(false);
const deleting = ref(false);
const importInput = ref<HTMLInputElement>();
const detailOpen = ref(false);
const detailLoading = ref(false);
const skinDetail = ref<PortableRunSkinRecord | null>(null);
const metadataEditorOpen = ref(false);
const metadataName = ref('');
const metadataDescription = ref('');

const viewportOptions: Array<{ key: Viewport; label: string }> = [
  { key: 'desktop', label: '桌面三栏' },
  { key: 'tablet', label: '平板' },
  { key: 'mobile', label: '手机' },
];

const previewTasks = ['2026级新生什么时候报到？', '报到需要携带哪些材料？', '如何办理保留入学资格？'];
const catalogOptions = computed(() => serverRecords.value.filter((item) => (
  item.key === 'default' || Boolean(item.portableSkin)
)));
const visibleCatalogOptions = computed(() => {
  const keyword = catalogKeyword.value.trim().toLocaleLowerCase();
  const matches = (item: PresentationPresetRecord) => !keyword || [
    item.name,
    item.key,
    item.packageKey,
    item.description,
  ].some((value) => String(value || '').toLocaleLowerCase().includes(keyword));
  return catalogOptions.value.filter(matches);
});
const selectedOption = computed(() => catalogOptions.value.find((item) => item.key === selectedKey.value));
const selectedServerRecord = computed(() => selectedOption.value);
const selectedPortableSkin = computed(() => selectedServerRecord.value?.portableSkin || null);
const canDeletePortableSkin = computed(() => Boolean(selectedPortableSkin.value));
const portableSkinDeleteHint = computed(() => {
  if (!selectedPortableSkin.value) return '标准外观是系统兜底，不能删除';
  return '删除后，正在使用该皮肤的智能体会回退为系统默认外观';
});
const deleteConfirmTitle = computed(() => {
  if (!selectedPortableSkin.value) return '确定删除这个皮肤包？';
  return `确定删除“${selectedPortableSkin.value.name}” v${selectedPortableSkin.value.version}？正在使用它的智能体将回退为系统默认外观。`;
});
const runtime = computed(() => {
  const portableSkin = hydratedPortableSkins.value.get(selectedKey.value);
  return resolveRunPresentation(
    {
      schemaVersion: 1,
      preset: selectedKey.value,
      ...(portableSkin ? { portableSkin } : {}),
    },
    viewport.value,
  );
});
function sourceLabel(sourceType: string) {
  if (sourceType === 'builtin-package') return '随系统提供';
  if (sourceType === 'imported') return '导入安装';
  return sourceType || '未知来源';
}

function toggleCompactPanel(panel: 'left' | 'right') {
  compactPanel.value = compactPanel.value === panel ? 'none' : panel;
}

watch(viewport, () => {
  compactPanel.value = 'none';
});

function previewFor(key: string) {
  const hydrated = hydratedPortableSkins.value.get(key);
  if (hydrated) {
    const preview = hydrated.manifest?.assets.find((asset) => asset.key === 'preview');
    if (preview && hydrated.assetUrls[preview.key]) return hydrated.assetUrls[preview.key];
  }
  return getRunPresentationPreview(key);
}

function releasePreviews() {
  for (const skin of hydratedPortableSkins.value.values()) releaseHydratedPortableRunSkin(skin);
  hydratedPortableSkins.value = new Map();
}

async function loadCatalog() {
  releasePreviews();
  try {
    const response = await queryAdminPresentationPresets();
    serverRecords.value = Array.isArray(response?.records) ? response.records : [];
    const hydrated = new Map<string, HydratedPortableRunSkin>();
    await Promise.all(serverRecords.value.map(async (record) => {
      if (!record.portableSkin) return;
      try {
        hydrated.set(record.key, await hydratePortableRunSkin(record.portableSkin));
      } catch {
        // The record remains visible with its material state; preview safely falls back.
      }
    }));
    hydratedPortableSkins.value = hydrated;
    const manageableRecords = serverRecords.value.filter((record) => (
      record.key === 'default' || Boolean(record.portableSkin)
    ));
    if (!manageableRecords.some((record) => record.key === selectedKey.value)) {
      selectedKey.value = manageableRecords.find((record) => record.portableSkin)?.key
        || manageableRecords.find((record) => record.key === 'default')?.key
        || 'default';
    }
  } catch {
    serverRecords.value = [];
    selectedKey.value = 'default';
  }
}

function openImport() {
  importInput.value?.click();
}

async function handleImport(event: Event) {
  const target = event.target as HTMLInputElement;
  const file = target.files?.[0];
  target.value = '';
  if (!file) return;
  importing.value = true;
  try {
    const result = await importSubAgentSkin(file);
    message.success(result.installed ? '皮肤包已导入，可以直接给子智能体试穿' : '相同皮肤包已经存在');
    await loadCatalog();
    const installed = serverRecords.value.find((item) => item.portableSkin?.id === result.skin.id);
    if (installed) selectedKey.value = installed.key;
  } catch (error: any) {
    message.error(error?.message || '皮肤包导入失败');
  } finally {
    importing.value = false;
  }
}

async function exportSelected() {
  if (!selectedServerRecord.value?.portableSkin) return;
  exporting.value = true;
  try {
    await exportSubAgentSkin(selectedServerRecord.value);
  } catch (error: any) {
    message.error(error?.message || '皮肤包导出失败');
  } finally {
    exporting.value = false;
  }
}

async function openDetail() {
  if (!selectedPortableSkin.value) return;
  detailOpen.value = true;
  detailLoading.value = true;
  skinDetail.value = null;
  try {
    skinDetail.value = await getSubAgentSkin(selectedPortableSkin.value.id);
  } catch (error: any) {
    detailOpen.value = false;
    message.error(error?.message || '读取皮肤详情失败');
  } finally {
    detailLoading.value = false;
  }
}

async function openMetadataEditor() {
  if (!selectedPortableSkin.value) return;
  try {
    const detail = await getSubAgentSkin(selectedPortableSkin.value.id);
    metadataName.value = detail.name;
    metadataDescription.value = detail.description || '';
    metadataEditorOpen.value = true;
  } catch (error: any) {
    message.error(error?.message || '读取皮肤详情失败');
  }
}

async function saveMetadata() {
  if (!selectedPortableSkin.value) return;
  const name = metadataName.value.trim();
  if (!name) {
    message.warning('请填写皮肤显示名称');
    return;
  }
  updating.value = true;
  try {
    const selection = selectedKey.value;
    await updateSubAgentSkinMetadata(selectedPortableSkin.value.id, {
      name,
      description: metadataDescription.value.trim(),
    });
    await loadCatalog();
    if (serverRecords.value.some((item) => item.key === selection)) selectedKey.value = selection;
    metadataEditorOpen.value = false;
    message.success('皮肤管理信息已更新');
  } catch (error: any) {
    message.error(error?.message || '更新皮肤信息失败');
  } finally {
    updating.value = false;
  }
}

async function deleteSelected() {
  if (!selectedPortableSkin.value || !canDeletePortableSkin.value) return;
  deleting.value = true;
  try {
    const result = await deleteSubAgentSkin(selectedPortableSkin.value.id);
    selectedKey.value = 'default';
    await loadCatalog();
    message.success(
      result.fallbackAgentCount > 0
        ? `皮肤包已删除，${result.fallbackAgentCount} 个智能体已回退为系统默认外观`
        : '皮肤包已从可用目录删除',
    );
  } catch (error: any) {
    message.error(error?.message || '删除皮肤包失败');
  } finally {
    deleting.value = false;
  }
}

onMounted(loadCatalog);

onBeforeUnmount(releasePreviews);
</script>

<style scoped lang="less">
.presentation-studio {
  --studio-ink: #18334d;
  --studio-muted: #6e8295;
  --studio-line: #dce7f0;
  display: grid;
  gap: 0;
  padding: 4px 2px 18px;
  color: var(--studio-ink);
}

.studio-layout { display: grid; grid-template-columns: 246px minmax(0, 1fr); gap: 16px; min-width: 0; }
.studio-catalog { display: flex; min-width: 0; flex-direction: column; gap: 11px; padding: 2px 16px 0 2px; border-right: 1px solid #e7edf2; }
.catalog-head { padding: 2px 2px 0; }
.catalog-head strong { color: #29465f; font-size: 15px; }
.catalog-import { width: 100%; height: 36px; border-radius: 8px; box-shadow: none; }
.package-file-input { display: none; }
.catalog-search { display: flex; height: 34px; box-sizing: border-box; align-items: center; gap: 7px; padding: 0 10px; border: 1px solid #dfe7ed; border-radius: 8px; background: #fff; color: #8ba0b1; }
.catalog-search:focus-within { border-color: #8bb8dc; box-shadow: 0 0 0 2px rgba(61, 126, 181, 0.08); }
.catalog-search input { width: 100%; min-width: 0; border: 0; outline: 0; background: transparent; color: #29465f; font-size: 12px; }
.catalog-search input::placeholder { color: #9aabb8; }
.catalog-search input::-webkit-search-cancel-button { opacity: 0.55; }
.catalog-list { display: grid; min-height: 0; max-height: 560px; gap: 7px; overflow: auto; padding: 0 2px 4px; }
.catalog-empty { display: grid; min-height: 112px; place-items: center; align-content: center; gap: 8px; border: 1px dashed #d9e3ea; border-radius: 10px; color: #8a9ca9; font-size: 12px; }
.catalog-empty :deep(.anticon) { font-size: 20px; }
.catalog-card { display: grid; width: 100%; min-height: 58px; grid-template-columns: 82px minmax(0, 1fr); align-items: center; gap: 10px; padding: 7px; border: 1px solid transparent; border-radius: 9px; outline: 0; background: transparent; color: #203b53; text-align: left; cursor: pointer; transition: border-color 0.16s ease, background 0.16s ease; }
.catalog-card:hover { border-color: #e1e9ef; background: #f7fafc; }
.catalog-card:focus-visible { outline: 2px solid #2878d2; outline-offset: 2px; }
.catalog-card.selected { border-color: #b8d3e9; background: #edf6fd; }
.catalog-preview { position: relative; display: block; aspect-ratio: 3.1 / 1; overflow: hidden; border: 1px solid #e6ebef; border-radius: 6px; background: #f2f6f9; }
.catalog-preview img { display: block; width: 100%; height: 100%; object-fit: cover; }
.default-preview { position: absolute; inset: 0; background: linear-gradient(#d9dde3, #d9dde3) 50% 28% / 26% 4px no-repeat, linear-gradient(#fff, #fff) 50% 74% / 68% 18px no-repeat, #f6f7f8; }
.catalog-copy { min-width: 0; }
.catalog-copy strong { display: block; overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }

.studio-workbench { min-width: 0; overflow: hidden; border: 1px solid #e4e9ee; border-radius: 10px; background: #f5f8fa; }
.studio-toolbar { display: flex; min-height: 58px; align-items: center; justify-content: space-between; gap: 18px; padding: 10px 14px; border-bottom: 1px solid var(--studio-line); background: #fff; }
.selected-preset { display: flex; min-width: 0; align-items: center; gap: 8px; }
.selected-preset > strong { overflow: hidden; color: #23445f; font-size: 15px; text-overflow: ellipsis; white-space: nowrap; }
.material-state { color: #8a99a6; font-size: 12px; }
.material-state::before { display: inline-block; width: 6px; height: 6px; margin-right: 5px; border-radius: 50%; background: currentColor; content: ''; vertical-align: 1px; }
.material-state.is-error { color: #c34e4e; }
.toolbar-controls { display: flex; min-width: 0; align-items: center; justify-content: flex-end; gap: 12px; }
.package-actions { display: flex; flex: 0 0 auto; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
.delete-control { display: inline-flex; }
.viewport-switch { display: flex; padding: 3px; border-radius: 8px; background: #eef3f7; }
.viewport-switch button { min-width: 76px; height: 32px; padding: 0 10px; border: 0; border-radius: 6px; background: transparent; color: #728598; font-size: 12px; cursor: pointer; }
.viewport-switch button.active { background: #fff; color: #285c87; box-shadow: 0 2px 8px rgba(40, 80, 116, 0.08); }
.viewport-switch button:focus-visible { outline: 2px solid #2878d2; outline-offset: 1px; }
.preview-scroll { display: grid; min-height: 574px; overflow: auto; place-items: start center; padding: 14px; }

.studio-run-shell {
  position: relative;
  display: grid;
  width: 1060px;
  height: 560px;
  grid-template-columns: 190px minmax(470px, 1fr) 220px;
  overflow: hidden;
  border: 1px solid rgba(64, 103, 138, 0.17);
  border-radius: 12px;
  background: var(--run-page-bg, #fff);
  box-shadow: 0 18px 42px rgba(31, 66, 99, 0.12);
  color: var(--run-text, #1d1d20);
  font-family: Inter, 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
.studio-run-shell.is-tablet { width: 760px; grid-template-columns: 1fr; grid-template-rows: 60px minmax(0, 1fr); }
.studio-run-shell.is-mobile { width: 390px; height: 720px; grid-template-columns: 1fr; grid-template-rows: 56px minmax(0, 1fr); }
.studio-compact-bar,
.studio-compact-backdrop { display: none; }
.studio-left-rail, .studio-right-rail, .studio-main-stage { position: relative; min-width: 0; overflow: hidden; }
.studio-left-rail { border-right: 1px solid var(--run-left-rail-border, #ededef); background: var(--run-left-rail-bg, #fafafa); }
.studio-right-rail { border-left: 1px solid var(--run-right-rail-border, #ededef); background: var(--run-right-rail-bg, #f8f8f9); }
.studio-main-stage { display: flex; align-items: center; flex-direction: column; background: var(--run-main-bg, #fff); }
.studio-rail-decoration { position: absolute; z-index: 0; inset: 0; pointer-events: none; }
.studio-rail-content { position: relative; z-index: 1; display: flex; min-width: 0; height: 100%; box-sizing: border-box; flex-direction: column; }
.studio-left-rail .studio-rail-content { gap: 9px; padding: 16px 10px; }
.studio-brand { display: grid; grid-template-columns: 24px minmax(0, 1fr) auto; align-items: center; gap: 7px; margin-bottom: 4px; color: var(--run-left-item-title, #252b32); font-size: 11px; }
.studio-brand i { display: grid; width: 24px; height: 24px; place-items: center; border-radius: 7px; background: #fff; color: #3573a7; font-size: 10px; font-style: normal; box-shadow: 0 2px 7px rgba(39, 83, 121, 0.09); }
.studio-brand strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.studio-brand > span { color: var(--run-left-muted, #92959c); }
.studio-new-session { height: 36px; border: 0; border-radius: 9px; background: var(--run-left-action-bg, #f0f0f2); color: var(--run-left-item-title, #111); font-size: 11px; }
.studio-search { height: 31px; box-sizing: border-box; padding: 7px 9px; border: 1px solid var(--run-left-control-border, #e5e6e9); border-radius: 8px; background: var(--run-left-control-bg, #fff); color: var(--run-left-muted, #92959c); font-size: 10px; }
.studio-group-label { margin-top: 5px; padding: 0 5px; color: var(--run-left-muted, #9aa0aa); font-size: 9px; }
.studio-session { overflow: hidden; padding: 8px 7px; border-radius: 7px; color: var(--run-left-item-color, #4b5059); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.studio-session.is-active { border-left: 2px solid var(--run-left-item-accent, #111827); background: var(--run-left-item-active-bg, #f1f2f4); color: var(--run-left-item-title, #111827); }

.studio-welcome { position: relative; z-index: 1; width: min(76%, 530px); margin-top: 168px; text-align: center; }
.studio-welcome small { color: var(--run-welcome-kicker-color, #747780); font-size: 10px; font-weight: 650; }
.studio-welcome h3 { margin: 9px 0 0; color: var(--run-welcome-title-color, #1d1d20); font-size: 25px; font-weight: 720; letter-spacing: -0.04em; }
.studio-welcome p { margin: 14px auto 0; color: var(--run-welcome-body-color, #5e6169); font-size: 11px; line-height: 1.7; }
.studio-composer { position: relative; z-index: 2; display: flex; width: min(82%, 580px); height: 90px; box-sizing: border-box; justify-content: space-between; flex-direction: column; margin-top: var(--run-composer-empty-gap, 34px); padding: 15px 12px 10px; border: 1px solid var(--run-composer-border, #e8e8e8); border-radius: var(--run-composer-radius, 20px); background: var(--run-composer-bg, #fff); box-shadow: var(--run-composer-shadow, 0 10px 28px rgba(0, 0, 0, 0.06)); }
.studio-composer > span { color: #9aa8b3; font-size: 11px; }
.studio-composer-actions { display: flex; align-items: center; justify-content: space-between; }
.studio-composer-actions i, .studio-composer-actions b { display: grid; width: 26px; height: 26px; place-items: center; border-radius: 50%; font-size: 13px; font-style: normal; }
.studio-composer-actions i { border: 1px solid #dce4ea; background: #fff; color: #586b7c; }
.studio-composer-actions b { background: var(--run-accent, #202124); color: #fff; font-weight: 500; }

.studio-right-rail .studio-rail-content { padding: 21px 14px; }
.studio-right-head { display: grid; gap: 3px; padding-bottom: 14px; border-bottom: 1px solid var(--run-right-divider, #e7e8eb); }
.studio-right-head strong { color: var(--run-right-heading, #191a1e); font-size: 14px; }
.studio-right-head small { color: var(--run-right-muted, #858992); font-size: 9px; }
.studio-right-label { margin: 16px 0 7px; color: var(--run-right-muted, #5f636c); font-size: 9px; font-weight: 650; }
.studio-select { display: flex; min-height: 34px; align-items: center; justify-content: space-between; padding: 0 9px; border: 1px solid var(--run-right-control-border, #dfe1e5); border-radius: 8px; background: var(--run-right-control-bg, #fff); color: var(--run-right-heading, #292b31); font-size: 10px; }
.studio-task { display: grid; min-height: 43px; box-sizing: border-box; grid-template-columns: 21px minmax(0, 1fr) 8px; align-items: center; gap: 6px; margin-bottom: 7px; padding: 7px; border: 1px solid var(--run-right-card-border, #e3e5e8); border-radius: 9px; background: var(--run-right-card-bg, #fff); color: var(--run-right-card-text, #373a41); font-size: 9.5px; line-height: 1.4; }
.studio-task i { display: grid; width: 21px; height: 21px; place-items: center; border: 1px solid var(--run-right-mark-border, #e8e9ec); border-radius: 6px; background: var(--run-right-mark-bg, #f4f4f6); color: var(--run-right-mark-color, #686c75); font-style: normal; }
.studio-task b { color: var(--run-right-muted, #a2a5ac); font-size: 13px; }
.studio-run-shell.is-tablet .studio-compact-bar,
.studio-run-shell.is-mobile .studio-compact-bar {
  position: relative;
  z-index: 4;
  display: flex;
  grid-row: 1;
  align-items: center;
  justify-content: space-between;
  padding: 0 14px;
  border-bottom: 1px solid var(--run-left-rail-border, #ededef);
  background: color-mix(in srgb, var(--run-left-rail-bg, #fafafa) 92%, transparent);
  backdrop-filter: blur(18px);
}
.studio-compact-bar strong { overflow: hidden; color: var(--run-left-item-title, #252b32); font-size: 12px; font-weight: 680; text-overflow: ellipsis; white-space: nowrap; }
.studio-compact-bar button {
  display: inline-flex;
  min-width: 52px;
  height: 44px;
  align-items: center;
  flex-direction: column;
  justify-content: center;
  gap: 1px;
  padding: 0 7px;
  border: 1px solid var(--run-left-control-border, #e5e6e9);
  border-radius: 12px;
  background: var(--run-left-control-bg, #fff);
  color: var(--run-left-item-title, #252b32);
  font-size: 9.5px;
  font-weight: 600;
  line-height: 11px;
  cursor: pointer;
}
.studio-compact-bar button :deep(.anticon) { font-size: 14px; }
.studio-compact-bar button.studio-compact-icon-button { width: 44px; min-width: 44px; padding: 0; }
.studio-compact-bar button[aria-pressed='true'] { border-color: var(--run-accent, #202124); color: var(--run-accent, #202124); }
.studio-run-shell.is-tablet .studio-main-stage,
.studio-run-shell.is-mobile .studio-main-stage { grid-row: 2; min-height: 0; }
.studio-run-shell.is-tablet .studio-left-rail,
.studio-run-shell.is-mobile .studio-left-rail,
.studio-run-shell.is-tablet .studio-right-rail,
.studio-run-shell.is-mobile .studio-right-rail {
  position: absolute;
  z-index: 7;
  display: block;
  box-sizing: border-box;
  transition: transform 0.22s cubic-bezier(0.22, 1, 0.36, 1);
}
.studio-run-shell.is-tablet .studio-left-rail,
.studio-run-shell.is-mobile .studio-left-rail {
  top: 0;
  bottom: 0;
  left: 0;
  width: min(86%, 340px);
  border-right: 1px solid var(--run-left-rail-border, #ededef);
  box-shadow: 14px 0 34px rgba(15, 23, 42, 0.16);
  transform: translateX(-105%);
}
.studio-run-shell.is-tablet .studio-right-rail,
.studio-run-shell.is-mobile .studio-right-rail {
  right: 0;
  bottom: 0;
  left: 0;
  width: 100%;
  height: min(72%, 430px);
  border-top: 1px solid var(--run-right-rail-border, #ededef);
  border-left: 0;
  border-radius: 18px 18px 0 0;
  box-shadow: 0 -14px 34px rgba(15, 23, 42, 0.16);
  transform: translateY(105%);
}
.studio-run-shell.is-tablet.show-left .studio-left-rail,
.studio-run-shell.is-mobile.show-left .studio-left-rail { transform: translateX(0); }
.studio-run-shell.is-tablet.show-right .studio-right-rail,
.studio-run-shell.is-mobile.show-right .studio-right-rail { transform: translateY(0); }
.studio-run-shell.is-tablet .studio-compact-backdrop,
.studio-run-shell.is-mobile .studio-compact-backdrop {
  position: absolute;
  z-index: 6;
  inset: 0;
  display: block;
  padding: 0;
  border: 0;
  background: rgba(15, 23, 42, 0.26);
  cursor: pointer;
}
.studio-run-shell.is-tablet .studio-welcome { width: min(78%, 580px); margin-top: 76px; }
.studio-run-shell.is-tablet .studio-welcome h3 { font-size: 27px; line-height: 1.24; }
.studio-run-shell.is-tablet .studio-welcome p { max-width: 540px; font-size: 12px; }
.studio-run-shell.is-tablet .studio-composer {
  width: min(calc(100% - 48px), 680px);
  height: 104px;
  margin-top: clamp(64px, var(--run-composer-empty-gap-mobile, 70px), 76px);
}
.studio-run-shell.is-mobile .studio-compact-bar { padding: 0 10px; }
.studio-run-shell.is-mobile .studio-compact-bar button { min-width: 50px; height: 44px; }
.studio-run-shell.is-mobile .studio-compact-bar button.studio-compact-icon-button { width: 44px; min-width: 44px; }
.studio-run-shell.is-mobile .studio-welcome { width: calc(100% - 36px); margin-top: 52px; }
.studio-run-shell.is-mobile .studio-welcome small { font-size: 10.5px; }
.studio-run-shell.is-mobile .studio-welcome h3 { margin-top: 8px; font-size: 25px; line-height: 1.24; }
.studio-run-shell.is-mobile .studio-welcome p { max-width: 326px; margin-top: 12px; font-size: 12px; line-height: 1.65; }
.studio-run-shell.is-mobile .studio-composer {
  width: calc(100% - 32px);
  min-height: 112px;
  margin-top: clamp(52px, var(--run-composer-empty-gap-mobile, 58px), 62px);
  padding: 16px 12px 9px;
}
.studio-run-shell.is-mobile .studio-composer > span { font-size: 12px; }
.studio-run-shell.is-mobile .studio-composer-actions i,
.studio-run-shell.is-mobile .studio-composer-actions b { width: 36px; height: 36px; font-size: 15px; }

@media (max-width: 980px) {
  .studio-layout { grid-template-columns: 1fr; }
  .studio-catalog { padding: 0 0 14px; border-right: 0; border-bottom: 1px solid #edf0f3; }
  .catalog-list { grid-template-columns: repeat(2, minmax(0, 1fr)); max-height: 260px; }
  .studio-toolbar { align-items: stretch; flex-direction: column; }
  .toolbar-controls { width: 100%; justify-content: space-between; }
}

@media (max-width: 640px) {
  .catalog-list { grid-template-columns: 1fr; max-height: 320px; }
  .studio-toolbar,
  .toolbar-controls { align-items: stretch; flex-direction: column; }
  .studio-toolbar { padding: 10px 12px; }
  .package-actions { display: grid; width: 100%; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .package-actions :deep(.ant-btn) { width: 100%; }
  .viewport-switch { width: 100%; }
  .viewport-switch button { min-width: 0; flex: 1; }
  .preview-scroll { padding: 10px; }
}

@media (prefers-reduced-motion: reduce) {
  .catalog-card { transition: none; }
}
</style>

<style lang="less">
/* teleport 到 body，必须非 scoped。全局主题把 .ant-modal-body padding 清零。 */
.subagent-skin-modal .ant-modal-content {
  overflow: hidden;
  border-radius: 14px;
  box-shadow: 0 18px 48px rgba(31, 66, 99, 0.16);
}
.subagent-skin-modal .ant-modal-header {
  margin: 0;
  padding: 18px 22px 12px;
  border-bottom: 1px solid #edf1f5;
}
.subagent-skin-modal .ant-modal-title {
  color: #16324f;
  font-size: 16px;
  font-weight: 680;
  line-height: 1.4;
}
.subagent-skin-modal .ant-modal-close {
  top: 14px;
  color: #7b8c9b;
}
.subagent-skin-modal .ant-modal-body {
  padding: 16px 22px 8px;
}
.subagent-skin-modal .ant-modal-footer {
  margin: 0;
  padding: 12px 22px 18px;
  border-top: 1px solid #edf1f5;
}
.subagent-skin-modal .ant-modal-footer .ant-btn {
  height: 34px;
  padding: 0 14px;
  border-radius: 8px;
}
.subagent-skin-modal .ant-modal-footer .ant-btn-default {
  border-color: #dfe7ed;
  color: #3a5368;
}
.subagent-skin-modal .ant-modal-footer .ant-btn-primary {
  background: #18334d;
  border-color: #18334d;
}
.subagent-skin-modal .skin-modal-note {
  margin: 0 0 14px;
  padding: 10px 12px;
  border: 1px solid #e3edf5;
  border-radius: 8px;
  background: #f5f9fc;
  color: #4d6478;
  font-size: 12.5px;
  line-height: 1.65;
}
.subagent-skin-modal .skin-modal-meta,
.subagent-skin-modal .skin-modal-fields {
  display: grid;
  gap: 10px 18px;
  margin: 0 0 16px;
  padding: 12px 14px;
  border: 1px solid #eef2f5;
  border-radius: 10px;
  background: #fafcfd;
}
.subagent-skin-modal .skin-modal-meta {
  grid-template-columns: 1fr 1fr;
  margin-bottom: 16px;
}
.subagent-skin-modal .skin-modal-fields {
  grid-template-columns: 1fr 1fr;
}
.subagent-skin-modal .skin-modal-fields .is-block {
  grid-column: 1 / -1;
}
.subagent-skin-modal dt {
  color: #8a9ca9;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
}
.subagent-skin-modal dd {
  margin: 4px 0 0;
  color: #29465f;
  font-size: 13px;
  line-height: 1.5;
  word-break: break-all;
}
.subagent-skin-modal code {
  color: #3a607f;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}
.subagent-skin-modal .skin-modal-form .ant-form-item {
  margin-bottom: 16px;
}
.subagent-skin-modal .skin-modal-form .ant-form-item:last-child {
  margin-bottom: 4px;
}
.subagent-skin-modal .skin-modal-form .ant-form-item-label > label {
  color: #29465f;
  font-weight: 600;
}
.subagent-skin-modal .skin-modal-form .ant-input,
.subagent-skin-modal .skin-modal-form textarea.ant-input {
  border-color: #dfe7ed;
  border-radius: 8px;
}
.subagent-skin-modal .skin-modal-form .ant-input:hover,
.subagent-skin-modal .skin-modal-form textarea.ant-input:hover {
  border-color: #c5d4e0;
}
.subagent-skin-modal .skin-modal-form .ant-input:focus,
.subagent-skin-modal .skin-modal-form textarea.ant-input:focus,
.subagent-skin-modal .skin-modal-form .ant-input-focused {
  border-color: #8bb8dc;
  box-shadow: 0 0 0 2px rgba(61, 126, 181, 0.08);
}

@media (max-width: 560px) {
  .subagent-skin-modal .skin-modal-meta,
  .subagent-skin-modal .skin-modal-fields {
    grid-template-columns: 1fr;
  }
}
</style>
