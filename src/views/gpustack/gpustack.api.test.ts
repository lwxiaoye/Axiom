import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('GPUStack ModelScope API calls', () => {
  const apiSource = readFileSync(resolve(process.cwd(), 'src/views/gpustack/gpustack.api.ts'), 'utf8');
  const drawerSource = readFileSync(resolve(process.cwd(), 'src/views/gpustack/components/BrowseModelsDrawer.vue'), 'utf8');
  const librarySource = readFileSync(resolve(process.cwd(), 'src/views/gpustack/library/index.vue'), 'utf8');
  const readmeSource = readFileSync(resolve(process.cwd(), 'src/views/gpustack/components/ModelReadme.vue'), 'utf8');
  const hubSource = readFileSync(resolve(process.cwd(), 'src/views/gpustack/utils/hub.ts'), 'utf8');

  it('requests ModelScope detail through the Java backend and does not call the raw readme endpoint', () => {
    expect(apiSource).toContain("modelScopeDetail: '/gpustack/library/modelscope/detail'");
    expect(apiSource).toContain('buildModelScopeDetailUrl(normalizedRepo)');
    expect(apiSource).toContain('https://modelscope.cn/api/v1/models/${path}');
    expect(apiSource).toContain('LibraryApi.modelScopeDetail, params: { url: proxyUrl } }, QUIET_ERROR');
    expect(apiSource).not.toContain('/repo?Revision=master&FilePath=README.md');
  });

  it('renders ReadMeContent returned by the Java backend detail endpoint', () => {
    expect(drawerSource).toContain('modelScopeModelDetail,');
    expect(drawerSource).toContain('loadModelScopeDetail(m, myId)');
    expect(drawerSource).toContain('const res = await modelScopeModelDetail(repo)');
    expect(drawerSource).toContain('data.ReadMeContent');
    expect(readmeSource).toContain("content: { type: String, default: '' }");
    expect(readmeSource).toContain('const directContent = props.content?.trim()');
    expect(readmeSource).not.toContain('fetchHubReadme');
    expect(hubSource).not.toContain('fetchHubReadme');
    expect(hubSource).not.toContain('/repo?Revision=master&FilePath=README.md');
    expect(drawerSource).not.toContain('fetchHubReadme');
  });

  it('caps ModelScope paging and renders previous/next controls', () => {
    const toolbarIndex = drawerSource.indexOf('<div class="bm-toolbar">');
    const paginationIndex = drawerSource.indexOf('<div class="pagination-wrap" v-if="total > pageSize">');
    const listIndex = drawerSource.indexOf('<a-spin :spinning="loading" size="small">');

    expect(drawerSource).toContain('const MODELSCOPE_MAX_PAGE = 1000;');
    expect(drawerSource).toContain('const modelScopePaginationTotal = computed');
    expect(drawerSource).toContain('const modelScopeTotalPages = computed');
    expect(drawerSource).toContain('Math.ceil(total.value / pageSize.value)');
    expect(drawerSource).toContain('const modelScopeBrowsablePages = computed');
    expect(drawerSource).toContain('第 {{ page }} / {{ modelScopeTotalPages }} 页');
    expect(toolbarIndex).toBeGreaterThan(-1);
    expect(paginationIndex).toBeGreaterThan(toolbarIndex);
    expect(paginationIndex).toBeLessThan(listIndex);
    expect(drawerSource).toContain('class="pagination-icon-btn"');
    expect(drawerSource).toContain('icon="ant-design:left-outlined"');
    expect(drawerSource).toContain('icon="ant-design:right-outlined"');
    expect(drawerSource).toContain('@click="handlePreviousPage"');
    expect(drawerSource).toContain('@click="handleNextPage"');
    expect(drawerSource).toContain(':disabled="page <= 1"');
    expect(drawerSource).toContain(':disabled="page >= modelScopeBrowsablePages"');
    expect(drawerSource).toContain('function handlePreviousPage()');
    expect(drawerSource).toContain('function handleNextPage()');
    expect(drawerSource).not.toContain('            上一页');
    expect(drawerSource).not.toContain('            下一页');
    expect(drawerSource).not.toContain('<a-pagination');
    expect(drawerSource).not.toContain('isModelScopePageLimited');
    expect(drawerSource).not.toContain('ModelScope 不支持深分页');
  });

  it('renders sanitized HTML in the model detail panel', () => {
    expect(readmeSource).toContain("import DOMPurify from 'dompurify';");
    expect(readmeSource).toContain('html: true');
    expect(readmeSource).toContain('DOMPurify.sanitize(md.render(directContent))');
    expect(readmeSource).toContain('DOMPurify.sanitize(md.render(`${description}${link}`))');
  });

  it('loads /gpustack/library models by scrolling instead of visible pagination controls', () => {
    expect(librarySource).toContain('const LIBRARY_PAGE_SIZE = 24;');
    expect(librarySource).toContain('class="library-scroll"');
    expect(librarySource).toContain('@scroll.passive="handleLibraryScroll"');
    expect(librarySource).toContain('const hasMore = computed');
    expect(librarySource).toContain('function handleLibraryScroll');
    expect(librarySource).toContain('async function loadMoreModels()');
    expect(librarySource).toContain('models.value = reset ? records : [...models.value, ...records];');
    expect(librarySource).toContain('pageSize: LIBRARY_PAGE_SIZE');
    expect(librarySource).not.toContain('v-model:value="pageSize"');
    expect(librarySource).not.toContain('<a-pagination');
    expect(librarySource).not.toContain('show-quick-jumper');
  });

  it('uses a 1-2-1 drawer layout with a scrollable middle README panel', () => {
    expect(drawerSource).toContain('flex: 0 0 25%;');
    expect(drawerSource).toContain('flex: 0 0 50%;');
    expect(drawerSource).toContain(':deep(.model-readme-wrap)');
    expect(drawerSource).toContain('min-height: 0;');
    expect(readmeSource).toContain('flex: 1;');
    expect(readmeSource).toContain('min-height: 0;');
    expect(readmeSource).toContain('overflow: visible;');
  });

  it('sets the drawer to viewport height and gives each column its own scrollbar', () => {
    expect(drawerSource).toContain(':global(.browse-models-drawer .ant-drawer-content)');
    expect(drawerSource).toContain('height: 100vh;');
    expect(drawerSource).toContain(':global(.browse-models-drawer .ant-drawer-body)');
    expect(drawerSource).toContain(':global(.browse-models-drawer .scrollbar__wrap)');
    expect(drawerSource).toContain('overflow: hidden !important;');
    expect(drawerSource).toContain(':global(.browse-models-drawer .scrollbar__view)');
    expect(drawerSource).toContain('display: flex;');
    expect(drawerSource).toContain(':global(.browse-models-drawer .scrollbar__bar)');
    expect(drawerSource).toContain('height: calc(100vh - 60px);');
    expect(drawerSource).toContain('max-height: calc(100vh - 60px);');
    expect(drawerSource).toContain('.bm-col {');
    expect(drawerSource).toContain('overflow-y: auto;');
    expect(drawerSource).toContain('.model-list {');
    expect(drawerSource).toContain('overflow: visible;');
    expect(drawerSource).toContain('.config-form {');
  });

  it('does not render fallback README content while model detail is loading', () => {
    expect(drawerSource).toContain('const selectedReadmeReady = ref(false);');
    expect(drawerSource).toContain('<ModelReadme');
    expect(drawerSource).toContain('v-if="selectedReadmeReady"');
    expect(drawerSource).toContain('selectedReadmeReady.value = false;');
    expect(drawerSource).toContain('selectedReadmeReady.value = true;');
    expect(drawerSource).toContain('selectedReadmeReady,');
  });

  it('renders deploy config as collapsible sections with only basic expanded by default', () => {
    expect(drawerSource).toContain("const configPanelKeys = ref<ConfigPanelKey[]>(['basic']);");
    expect(drawerSource).toContain("configPanelKeys.value = ['basic'];");
    expect(drawerSource).toContain('data-config-panel="basic"');
    expect(drawerSource).toContain('data-config-panel="perf"');
    expect(drawerSource).toContain('data-config-panel="schedule"');
    expect(drawerSource).toContain('data-config-panel="advanced"');
    expect(drawerSource).toContain('<a-collapse v-model:activeKey="configPanelKeys"');
    expect(drawerSource).toContain("openConfigPanel('basic');");
    expect(drawerSource).not.toContain('<a-tabs');
    expect(drawerSource).not.toContain('<a-tab-pane');
    expect(drawerSource).not.toContain('activeTab');
    expect(drawerSource).not.toContain('config-nav');
    expect(drawerSource).not.toContain('handleConfigNavClick');
  });

});
