<template>
  <main class="campus-page">
    <header class="page-header">
      <div>
        <span class="eyebrow">校园百事通</span>
        <h1>配置知识库、模型与主对话皮肤</h1>
        <p>这里选择的皮肤只用于校园百事通主对话；子智能体运行页继续使用独立的外观配置。保存发布后对新打开或刷新的对话生效。</p>
      </div>
      <div class="header-actions">
        <a-tag v-if="config?.current_release" color="success">已发布</a-tag>
        <a-tag v-else>未发布</a-tag>
        <a-button size="large" @click="goKnowledge">知识库内容管理</a-button>
        <a-button type="primary" size="large" class="primary-action" :loading="saving" @click="save">
          保存
        </a-button>
      </div>
    </header>

    <section class="skin-workshop" aria-labelledby="main-chat-skin-title">
      <div class="skin-workshop-head">
        <div>
          <span class="section-kicker">主对话试衣间</span>
          <h2 id="main-chat-skin-title">选择已安装皮肤</h2>
          <p>一份皮肤包同时携带桌面、平板和手机布局。导入后素材保存在当前系统，不依赖开发机路径。</p>
        </div>
        <div class="skin-actions">
          <a-button :loading="importingSkin" @click="openSkinFilePicker">导入皮肤包</a-button>
          <a-button :disabled="!selectedSkin" :loading="exportingSkin" @click="exportSelectedSkin">导出当前皮肤</a-button>
          <a-button :disabled="!selectedSkin" @click="openSkinEditor">编辑信息</a-button>
          <a-popconfirm
            title="确定删除这个皮肤版本和它的全部素材吗？"
            ok-text="删除"
            cancel-text="取消"
            :disabled="!canDeleteSelectedSkin"
            @confirm="deleteSelectedSkin"
          >
            <a-button
              danger
              :disabled="!canDeleteSelectedSkin"
              :loading="deletingSkin"
              :title="selectedSkinDeleteHint"
            >删除</a-button>
          </a-popconfirm>
          <input
            ref="skinFileInput"
            class="skin-file-input"
            type="file"
            accept=".axiomskin,.zip,application/zip"
            @change="onSkinFileChange"
          />
        </div>
      </div>

      <a-alert
        v-if="skinErrorText"
        type="warning"
        show-icon
        :message="skinErrorText"
        class="skin-alert"
      />

      <div class="skin-workshop-layout">
        <aside class="skin-catalog" aria-label="已安装主对话皮肤">
          <button
            type="button"
            :class="['skin-card', { selected: !selectedMainChatSkinId }]"
            :aria-pressed="!selectedMainChatSkinId"
            @click="selectedMainChatSkinId = ''"
          >
            <span class="skin-card-preview standard-preview" aria-hidden="true"><i></i><b></b></span>
            <span><strong>标准外观</strong><small>系统默认 · 三端</small></span>
          </button>
          <button
            v-for="skin in installedSkins"
            :key="skin.id"
            type="button"
            :class="['skin-card', { selected: selectedMainChatSkinId === skin.id }]"
            :aria-pressed="selectedMainChatSkinId === skin.id"
            @click="selectedMainChatSkinId = skin.id"
          >
            <span class="skin-card-preview" aria-hidden="true">
              <img v-if="skinPreviewUrl(skin)" :src="skinPreviewUrl(skin)" alt="" />
              <i v-else></i>
            </span>
            <span>
              <strong>{{ skin.name }}</strong>
              <small>
                {{ skin.version }} · {{ skin.sourceType.startsWith('builtin') ? '内置' : '已导入' }}
                <template v-if="skin.referenceCount">· 使用中</template>
              </small>
            </span>
          </button>
          <div v-if="skinsLoading" class="skin-loading">正在读取皮肤库…</div>
        </aside>

        <div class="skin-preview-workbench">
          <div class="skin-preview-toolbar">
            <div>
              <strong>{{ selectedSkin?.name || '标准外观' }}</strong>
              <span v-if="selectedSkin">{{ selectedSkin.key }} · {{ selectedSkin.version }}</span>
              <span v-else>main_chat · default</span>
            </div>
            <div class="device-switch" role="group" aria-label="预览设备">
              <button
                v-for="device in previewDevices"
                :key="device.key"
                type="button"
                :class="{ active: previewDevice === device.key }"
                :aria-pressed="previewDevice === device.key"
                @click="previewDevice = device.key"
              >
                {{ device.label }}
              </button>
            </div>
          </div>
          <div class="skin-preview-canvas">
            <MainChatSkinPreview
              v-if="selectedSkin"
              :skin="selectedSkin"
              :device="previewDevice"
            />
            <div v-else :class="['standard-stage', `is-${previewDevice}`]">
              <small>校园百事通</small>
              <h3>你好，有什么校园事务想了解？</h3>
              <p>校园政策、办事流程和常见问题，都可以在这里查询。</p>
              <div class="standard-composer"><span>请输入校园政策、办事流程或常见问题…</span><b>↑</b></div>
            </div>
          </div>
          <p class="package-safety-note">仅接受声明式主对话皮肤包：图片、颜色和三端布局会随包导入；JS、Vue、HTML、CSS、SVG 和外部链接会被拒绝。</p>
        </div>
      </div>
    </section>

    <section class="workspace">
      <div class="toolbar">
        <div class="model-field">
          <span class="model-label">对话模型</span>
          <a-select
            v-model:value="selectedModelId"
            class="model-select"
            show-search
            placeholder="选择校园百事通使用的模型"
            :options="modelSelectOptions"
            option-filter-prop="label"
            popup-class-name="campus-model-dropdown"
          />
        </div>
        <div class="official-domain-field">
          <span class="model-label">学校官网域名</span>
          <a-select
            v-model:value="officialDomainHosts"
            mode="tags"
            class="official-domain-select"
            placeholder="例如 example.edu.cn"
            :token-separators="[',', '，', ' ']"
            :open="false"
          />
          <small>只填域名，不含 https:// 或路径；官网正文与配图都会按此白名单过滤。</small>
        </div>
        <a-input-search v-model:value="keyword" class="search" allow-clear placeholder="搜索知识库名称" />
      </div>
      <a-alert
        v-if="errorText"
        type="error"
        show-icon
        :message="errorText"
        class="load-alert"
      />
      <a-table
        row-key="id"
        class="knowledge-table"
        :loading="loading"
        :pagination="false"
        :data-source="filteredKnowledge"
        :row-class-name="rowClassName"
        :row-selection="{ selectedRowKeys: selectedKnowledgeIds, onChange: onSelectKnowledge }"
        :columns="kbColumns"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'name'">
            <strong class="kb-name">{{ record.name }}</strong>
          </template>
          <template v-else-if="column.key === 'status'">
            <span class="kb-status">{{ record.status === 'ACTIVE' ? '可用' : (record.status || '—') }}</span>
          </template>
        </template>
      </a-table>
    </section>

    <a-modal
      v-model:open="skinEditorOpen"
      title="编辑皮肤管理信息"
      ok-text="保存"
      cancel-text="取消"
      :confirm-loading="updatingSkin"
      @ok="saveSkinMetadata"
    >
      <a-alert
        type="info"
        show-icon
        message="这里只修改当前系统的显示名称和备注。要改图片或布局，请升级包版本后重新导入。"
        class="skin-editor-note"
      />
      <a-form layout="vertical" class="skin-editor-form">
        <a-form-item label="显示名称" required>
          <a-input v-model:value="skinEditorName" :maxlength="128" show-count placeholder="例如：校园蓝图" />
        </a-form-item>
        <a-form-item label="管理备注">
          <a-textarea
            v-model:value="skinEditorDescription"
            :maxlength="512"
            :rows="4"
            show-count
            placeholder="说明这套皮肤的用途或适用场景"
          />
        </a-form-item>
      </a-form>
    </a-modal>
  </main>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { getKnowledgeList } from '/@/views/knowledge/knowledge.api';
import MainChatSkinPreview from '../../peopleCenter/mainChatSkin/MainChatSkinPreview.vue';
import {
  deleteMainChatSkin,
  exportMainChatSkin,
  hydrateMainChatSkin,
  importMainChatSkin,
  listInstalledMainChatSkins,
  releaseHydratedMainChatSkin,
  updateMainChatSkinMetadata,
} from '../../peopleCenter/mainChatSkin/api';
import type { HydratedMainChatSkin, MainChatSkinDevice } from '../../peopleCenter/mainChatSkin/types';
import { ensureCampusDraft, getCampusConfig, listCampusModels, publishCampusDraft, saveCampusDraft } from './campusAssistant.api';
import type { CampusConfig, CampusModelOption, KnowledgeBinding } from './campusAssistant.types';

defineOptions({ name: 'CampusAssistantConfigPage' });

const router = useRouter();
const loading = ref(false);
const saving = ref(false);
const errorText = ref('');
const keyword = ref('');
const config = ref<CampusConfig | null>(null);
const knowledgeOptions = ref<any[]>([]);
const knowledgeBindings = ref<KnowledgeBinding[]>([]);
const selectedModelId = ref('');
const officialDomainHosts = ref<string[]>([]);
const gatewayModels = ref<CampusModelOption[]>([]);
const skinsLoading = ref(false);
const importingSkin = ref(false);
const exportingSkin = ref(false);
const deletingSkin = ref(false);
const updatingSkin = ref(false);
const skinErrorText = ref('');
const installedSkins = ref<HydratedMainChatSkin[]>([]);
const selectedMainChatSkinId = ref('');
const previewDevice = ref<MainChatSkinDevice>('desktop');
const skinFileInput = ref<HTMLInputElement | null>(null);
const skinEditorOpen = ref(false);
const skinEditorName = ref('');
const skinEditorDescription = ref('');
const previewDevices: Array<{ key: MainChatSkinDevice; label: string }> = [
  { key: 'desktop', label: '电脑' },
  { key: 'tablet', label: '平板' },
  { key: 'mobile', label: '手机' },
];

const selectedSkin = computed(() => (
  installedSkins.value.find((skin) => skin.id === selectedMainChatSkinId.value) || null
));
const canDeleteSelectedSkin = computed(() => Boolean(
  selectedSkin.value
  && selectedSkin.value.deletable !== false
  && !selectedSkin.value.sourceType.startsWith('builtin')
  && !(selectedSkin.value.referenceCount || 0)
));
const selectedSkinDeleteHint = computed(() => {
  if (!selectedSkin.value) return '请先选择皮肤';
  if (selectedSkin.value.sourceType.startsWith('builtin')) return '内置皮肤不能删除';
  if (selectedSkin.value.referenceCount) return '该版本仍被草稿或发布版本引用';
  return '删除这个皮肤版本';
});

const modelSelectOptions = computed(() => {
  const byId = new Map<string, { value: string; label: string }>();
  for (const item of [...(config.value?.available_models || []), ...gatewayModels.value]) {
    const id = String(item.id || '').trim();
    if (!id) continue;
    byId.set(id, { value: id, label: String(item.name || id) });
  }
  if (selectedModelId.value && !byId.has(selectedModelId.value)) {
    byId.set(selectedModelId.value, { value: selectedModelId.value, label: selectedModelId.value });
  }
  return [...byId.values()];
});

const kbColumns = [
  { title: '知识库', key: 'name', dataIndex: 'name' },
  { title: '片段数', dataIndex: 'chunkCount', width: 120 },
  { title: '状态', key: 'status', dataIndex: 'status', width: 120 },
];

const selectedKnowledgeIds = computed(() => knowledgeBindings.value.map((item) => item.knowledge_id));

const filteredKnowledge = computed(() => {
  const q = keyword.value.trim().toLowerCase();
  if (!q) return knowledgeOptions.value;
  return knowledgeOptions.value.filter((item) => String(item.name || '').toLowerCase().includes(q));
});

function applyConfig(next: CampusConfig) {
  config.value = next;
  const draft = next.draft || next.current_release;
  knowledgeBindings.value = [...(draft?.knowledge_bindings || [])];
  selectedModelId.value = String(draft?.model_id || next.current_release?.model_id || selectedModelId.value || '');
  officialDomainHosts.value = (draft?.official_domains || [])
    .map((item) => String(item.host || ''))
    .filter(Boolean);
  selectedMainChatSkinId.value = String(draft?.main_chat_skin_id || '');
}

function releaseSkinCatalog() {
  for (const skin of installedSkins.value) releaseHydratedMainChatSkin(skin);
}

async function loadSkinCatalog() {
  skinsLoading.value = true;
  skinErrorText.value = '';
  try {
    const payload = await listInstalledMainChatSkins();
    const nextSkins = await Promise.all(payload.records.map((skin) => hydrateMainChatSkin(skin)));
    releaseSkinCatalog();
    installedSkins.value = nextSkins;
  } catch (error) {
    skinErrorText.value = error instanceof Error ? error.message : '读取主对话皮肤库失败';
  } finally {
    skinsLoading.value = false;
  }
}

async function reload() {
  loading.value = true;
  errorText.value = '';
  try {
    const [cfg, kb, models] = await Promise.all([
      getCampusConfig(),
      getKnowledgeList({ pageNo: 1, pageSize: 200 }),
      listCampusModels().catch(() => []),
    ]);
    applyConfig(cfg);
    knowledgeOptions.value = kb?.records || [];
    const modelList = Array.isArray(models) ? models : [];
    gatewayModels.value = modelList
      .map((item) => ({
        id: String(item?.id || '').trim(),
        name: String(item?.name || item?.id || '').trim(),
        is_default: Boolean(item?.is_default),
      }))
      .filter((item) => item.id);
    await loadSkinCatalog();
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '加载校园百事通配置失败';
  } finally {
    loading.value = false;
  }
}

function skinPreviewUrl(skin: HydratedMainChatSkin) {
  return skin.assetUrls.preview || skin.assetUrls.background || '';
}

function openSkinFilePicker() {
  skinFileInput.value?.click();
}

async function onSkinFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  if (!/\.(axiomskin|zip)$/i.test(file.name)) {
    skinErrorText.value = '请选择 .axiomskin 皮肤包';
    return;
  }
  importingSkin.value = true;
  skinErrorText.value = '';
  try {
    const result = await importMainChatSkin(file);
    await loadSkinCatalog();
    selectedMainChatSkinId.value = result.skin.id;
    message.success(result.installed ? '皮肤包已安装，保存后可发布使用' : '该皮肤已安装，已为你选中');
  } catch (error) {
    skinErrorText.value = error instanceof Error ? error.message : '导入皮肤包失败';
  } finally {
    importingSkin.value = false;
  }
}

async function exportSelectedSkin() {
  if (!selectedSkin.value) return;
  exportingSkin.value = true;
  skinErrorText.value = '';
  try {
    await exportMainChatSkin(selectedSkin.value);
    message.success('皮肤包已导出');
  } catch (error) {
    skinErrorText.value = error instanceof Error ? error.message : '导出皮肤包失败';
  } finally {
    exportingSkin.value = false;
  }
}

function openSkinEditor() {
  if (!selectedSkin.value) return;
  skinEditorName.value = selectedSkin.value.name;
  skinEditorDescription.value = selectedSkin.value.description || '';
  skinEditorOpen.value = true;
}

async function saveSkinMetadata() {
  if (!selectedSkin.value) return;
  const name = skinEditorName.value.trim();
  if (!name) {
    message.warning('请填写皮肤显示名称');
    return;
  }
  updatingSkin.value = true;
  skinErrorText.value = '';
  try {
    const skinId = selectedSkin.value.id;
    await updateMainChatSkinMetadata(skinId, {
      name,
      description: skinEditorDescription.value.trim(),
    });
    await loadSkinCatalog();
    selectedMainChatSkinId.value = skinId;
    skinEditorOpen.value = false;
    message.success('皮肤管理信息已更新');
  } catch (error) {
    skinErrorText.value = error instanceof Error ? error.message : '更新皮肤信息失败';
  } finally {
    updatingSkin.value = false;
  }
}

async function deleteSelectedSkin() {
  if (!selectedSkin.value || !canDeleteSelectedSkin.value) return;
  deletingSkin.value = true;
  skinErrorText.value = '';
  try {
    await deleteMainChatSkin(selectedSkin.value.id);
    selectedMainChatSkinId.value = '';
    await loadSkinCatalog();
    message.success('皮肤包和持久素材已删除');
  } catch (error) {
    skinErrorText.value = error instanceof Error ? error.message : '删除皮肤包失败';
  } finally {
    deletingSkin.value = false;
  }
}

function onSelectKnowledge(keys: Array<string | number>) {
  const selected = new Set(keys.map((key) => String(key)));
  const existing = new Map(knowledgeBindings.value.map((item) => [item.knowledge_id, item]));
  knowledgeBindings.value = knowledgeOptions.value
    .filter((item) => selected.has(String(item.id)))
    .map((item) => existing.get(String(item.id)) || {
      knowledge_id: String(item.id),
      knowledge_name_snapshot: String(item.name || ''),
      enabled: true,
    });
}

async function save() {
  if (!selectedModelId.value.trim()) {
    message.warning('请选择校园百事通使用的模型');
    return;
  }
  if (!knowledgeBindings.value.length) {
    message.warning('请至少选择一个知识库');
    return;
  }
  const officialDomains = [...new Set(officialDomainHosts.value.map((item) => item.trim()).filter(Boolean))];
  if (!officialDomains.length) {
    message.warning('请至少填写一个学校官网域名，官网检索和配图不会放行第三方来源');
    return;
  }
  saving.value = true;
  errorText.value = '';
  try {
    const ensured = await ensureCampusDraft();
    config.value = ensured;
    const saved = await saveCampusDraft({
      expected_revision: ensured.revision,
      model_id: selectedModelId.value.trim(),
      main_chat_skin_id: selectedMainChatSkinId.value || null,
      official_domains: officialDomains.map((host) => ({ host, include_subdomains: true })),
      knowledge_bindings: knowledgeBindings.value,
    });
    applyConfig(await publishCampusDraft({
      expected_revision: saved.revision,
      confirm_warnings: true,
    }));
    message.success('已保存发布，新打开的校园百事通对话会使用所选皮肤、知识库和模型');
    await reload();
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : '保存失败';
  } finally {
    saving.value = false;
  }
}

function rowClassName(record: { id?: string | number }) {
  return selectedKnowledgeIds.value.includes(String(record.id)) ? 'is-selected' : '';
}

function goKnowledge() {
  router.push('/knowledge/base');
}

onMounted(reload);
onBeforeUnmount(releaseSkinCatalog);
</script>

<style scoped>
.campus-page {
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
  min-width: 96px;
  height: 40px;
}
.skin-workshop {
  max-width: 1440px;
  box-sizing: border-box;
  margin: 0 auto 20px;
  padding: 22px;
  border: 1px solid #dfe5eb;
  border-radius: 14px;
  background:
    radial-gradient(circle at 86% 8%, rgba(198, 223, 246, 0.42), transparent 25%),
    linear-gradient(140deg, #fff 0%, #fbfdff 60%, #f3f8fc 100%);
  box-shadow: 0 14px 34px rgba(40, 70, 98, 0.06);
}
.skin-workshop-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}
.section-kicker {
  color: #55738e;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.12em;
}
.skin-workshop h2 {
  margin: 4px 0 0;
  color: #13293d;
  font-size: 21px;
  letter-spacing: -0.02em;
}
.skin-workshop-head p {
  max-width: 720px;
  margin: 7px 0 0;
  color: #64788a;
  font-size: 13px;
  line-height: 1.65;
}
.skin-actions {
  display: flex;
  flex-shrink: 0;
  gap: 10px;
}
.skin-file-input {
  display: none;
}
.skin-alert {
  margin-top: 16px;
}
.skin-editor-note {
  margin-bottom: 18px;
}
.skin-editor-form :deep(.ant-form-item:last-child) {
  margin-bottom: 0;
}
.skin-workshop-layout {
  display: grid;
  grid-template-columns: minmax(210px, 260px) minmax(0, 1fr);
  gap: 20px;
  margin-top: 20px;
}
.skin-catalog {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 9px;
}
.skin-card {
  display: grid;
  width: 100%;
  min-height: 66px;
  grid-template-columns: 74px minmax(0, 1fr);
  align-items: center;
  gap: 11px;
  padding: 7px;
  border: 1px solid transparent;
  border-radius: 11px;
  outline: none;
  background: rgba(255, 255, 255, 0.72);
  color: #203346;
  cursor: pointer;
  text-align: left;
  transition: border-color 0.18s ease, background 0.18s ease, transform 0.18s ease;
}
.skin-card:hover {
  border-color: #cbdbe8;
  background: #fff;
  transform: translateY(-1px);
}
.skin-card:focus-visible {
  box-shadow: 0 0 0 3px rgba(69, 125, 174, 0.18);
}
.skin-card.selected {
  border-color: #7faed2;
  background: #edf6fd;
  box-shadow: inset 3px 0 #427eae;
}
.skin-card > span:last-child {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}
.skin-card strong,
.skin-card small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.skin-card strong {
  font-size: 13px;
  font-weight: 700;
}
.skin-card small {
  color: #738698;
  font-size: 11px;
}
.skin-card-preview {
  position: relative;
  display: block;
  width: 74px;
  height: 48px;
  overflow: hidden;
  border: 1px solid rgba(80, 113, 142, 0.14);
  border-radius: 7px;
  background: linear-gradient(180deg, #eff7ff, #f8fbfd);
}
.skin-card-preview img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.skin-card-preview i {
  position: absolute;
  right: 8px;
  bottom: 7px;
  left: 8px;
  height: 11px;
  border: 1px solid #d7e1e9;
  border-radius: 5px;
  background: #fff;
}
.standard-preview b {
  position: absolute;
  top: 10px;
  left: 21px;
  width: 32px;
  height: 3px;
  border-radius: 3px;
  background: #334155;
}
.skin-loading {
  padding: 12px;
  color: #718096;
  font-size: 12px;
  text-align: center;
}
.skin-preview-workbench {
  min-width: 0;
  padding: 14px;
  border: 1px solid rgba(111, 145, 174, 0.18);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.82);
}
.skin-preview-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 0 2px 12px;
}
.skin-preview-toolbar > div:first-child {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}
.skin-preview-toolbar strong {
  color: #1f3548;
  font-size: 13px;
}
.skin-preview-toolbar span {
  overflow: hidden;
  color: #7b8c9b;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.device-switch {
  display: inline-flex;
  flex-shrink: 0;
  gap: 3px;
  padding: 3px;
  border-radius: 8px;
  background: #eef3f7;
}
.device-switch button {
  min-width: 48px;
  height: 29px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #657688;
  cursor: pointer;
  font-size: 12px;
}
.device-switch button.active {
  background: #fff;
  color: #18364f;
  box-shadow: 0 2px 7px rgba(42, 68, 91, 0.11);
}
.skin-preview-canvas {
  display: flex;
  min-height: 360px;
  align-items: center;
  justify-content: center;
  overflow: auto;
  padding: 18px;
  border-radius: 10px;
  background:
    linear-gradient(45deg, rgba(69, 101, 129, 0.035) 25%, transparent 25%),
    linear-gradient(-45deg, rgba(69, 101, 129, 0.035) 25%, transparent 25%),
    #eef3f7;
  background-position: 0 0, 10px 10px;
  background-size: 20px 20px;
}
.standard-stage {
  display: flex;
  width: min(100%, 920px);
  aspect-ratio: 16 / 8.7;
  box-sizing: border-box;
  align-items: center;
  flex-direction: column;
  justify-content: center;
  padding: 8%;
  border: 1px solid #e2e7ec;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 18px 40px rgba(31, 66, 99, 0.08);
  text-align: center;
}
.standard-stage.is-tablet { width: min(100%, 660px); aspect-ratio: 4 / 3; }
.standard-stage.is-mobile { width: min(100%, 330px); aspect-ratio: 9 / 16; }
.standard-stage small { color: #8a909a; font-size: 11px; font-weight: 700; }
.standard-stage h3 { margin: 8px 0 0; color: #111827; font-size: clamp(18px, 2.3vw, 28px); }
.standard-stage p { margin: 9px 0 0; color: #89919c; font-size: 11px; }
.standard-composer {
  display: flex;
  width: min(92%, 660px);
  min-height: 70px;
  align-items: flex-start;
  justify-content: space-between;
  margin-top: 12%;
  padding: 14px;
  border: 1px solid #e4e7eb;
  border-radius: 20px;
  color: #9ca3af;
  font-size: 11px;
  text-align: left;
  box-shadow: 0 12px 30px rgba(17, 24, 39, 0.06);
}
.standard-composer b {
  display: grid;
  width: 27px;
  height: 27px;
  align-self: flex-end;
  place-items: center;
  border-radius: 50%;
  background: #e8ebef;
  color: #fff;
}
.standard-stage.is-mobile .standard-composer {
  width: 100%;
  min-height: 88px;
  margin-top: 24%;
}
.package-safety-note {
  margin: 11px 2px 0;
  color: #778797;
  font-size: 11px;
  line-height: 1.55;
}
.workspace {
  max-width: 1440px;
  margin: auto;
  overflow: hidden;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 16px;
  align-items: center;
  padding: 16px 16px 0;
}
.model-field {
  display: flex;
  min-width: 280px;
  flex: 1 1 280px;
  gap: 10px;
  align-items: center;
}
.official-domain-field {
  display: flex;
  min-width: 360px;
  flex: 2 1 520px;
  gap: 10px;
  align-items: center;
}
.official-domain-field small {
  max-width: 320px;
  color: #8a94a3;
  font-size: 11px;
  line-height: 1.45;
}
.official-domain-select {
  min-width: 220px;
  flex: 1 1 280px;
}
.official-domain-select :deep(.ant-select-selector) {
  background: #fff !important;
  border-color: #e5e7eb !important;
}
.model-label {
  flex-shrink: 0;
  color: #667085;
  font-size: 13px;
  font-weight: 600;
}
.model-select {
  flex: 1 1 auto;
  max-width: 360px;
}
.model-select :deep(.ant-select-selector) {
  background: #fff !important;
  color: #1d2939 !important;
  border-color: #e5e7eb !important;
}
.model-select :deep(.ant-select-selection-item),
.model-select :deep(.ant-select-selection-placeholder) {
  color: #1d2939 !important;
}
.search {
  max-width: 360px;
}
.load-alert {
  margin: 12px 16px 0;
}
.knowledge-table {
  padding: 8px 8px 16px;
}
.kb-name {
  color: #1d2939;
  font-weight: 600;
}
.kb-status {
  color: #667085;
}
.knowledge-table :deep(.ant-table-thead > tr > th) {
  background: #fbfcfe;
  color: #667085;
  font-weight: 600;
  border-bottom: 1px solid #edf0f3;
}
.knowledge-table :deep(.ant-table-tbody > tr > td) {
  color: #1d2939;
  background: #fff;
  border-bottom: 1px solid #f2f4f7;
}
.knowledge-table :deep(.ant-table-tbody > tr:hover > td) {
  background: #f8fafc;
}
.knowledge-table :deep(.ant-table-tbody > tr.ant-table-row-selected > td),
.knowledge-table :deep(.ant-table-tbody > tr.is-selected > td) {
  background: #f4f7fb !important;
  color: #1d2939 !important;
}
.knowledge-table :deep(.ant-table-tbody > tr.ant-table-row-selected:hover > td),
.knowledge-table :deep(.ant-table-tbody > tr.is-selected:hover > td) {
  background: #eef2f7 !important;
}
.knowledge-table :deep(.ant-checkbox-inner) {
  background: #fff;
  border-color: #c5cbd3;
}
.knowledge-table :deep(.ant-checkbox-checked .ant-checkbox-inner) {
  background: #111827;
  border-color: #111827;
}
@media (max-width: 980px) {
  .campus-page { padding: 20px; }
  .page-header { align-items: flex-start; flex-direction: column; }
  .header-actions { width: 100%; flex-wrap: wrap; }
  .skin-workshop-head { flex-direction: column; }
  .skin-workshop-layout { grid-template-columns: 1fr; }
  .skin-catalog { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
}
@media (max-width: 620px) {
  .campus-page { padding: 14px; }
  .skin-workshop { padding: 15px; }
  .skin-actions { width: 100%; }
  .skin-actions :deep(.ant-btn) { flex: 1; }
  .skin-preview-toolbar { align-items: flex-start; flex-direction: column; }
  .skin-preview-canvas { min-height: 300px; padding: 10px; }
  .toolbar { align-items: stretch; flex-direction: column; }
  .model-field, .official-domain-field { min-width: 0; align-items: flex-start; flex-direction: column; }
  .official-domain-field small { max-width: none; }
  .model-select, .official-domain-select, .search { width: 100%; max-width: none; }
}
@media (prefers-reduced-motion: reduce) {
  .skin-card { transition: none; }
}
</style>

<style>
/* 覆盖全局表格选中态（深色主色会把整行涂黑）。 */
.campus-page .ant-table-tbody > tr.ant-table-row-selected > td,
.campus-page .ant-table-tbody > tr.ant-table-row-selected > .ant-table-cell,
.campus-page .ant-table-tbody > tr.is-selected > td,
.campus-page .ant-table-tbody > tr.is-selected > .ant-table-cell {
  background: #f4f7fb !important;
  color: #1d2939 !important;
}
.campus-page .ant-table-tbody > tr.ant-table-row-selected:hover > td,
.campus-page .ant-table-tbody > tr.ant-table-row-selected:hover > .ant-table-cell {
  background: #eef2f7 !important;
}
.campus-model-dropdown .ant-select-item {
  color: #1d2939;
}
.campus-model-dropdown .ant-select-item-option-selected {
  background: #f4f7fb;
  color: #1d2939;
}
.campus-model-dropdown .ant-select-item-option-active {
  background: #eef2f7;
}
</style>
