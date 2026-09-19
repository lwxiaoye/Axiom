import { computed, ref, watch } from 'vue';
import { defHttp } from '/@/utils/http/axios';
import { getMarketplaceModelOptions, listBuiltinApps, type AgentItem } from '../agentApi';
import { resolveAppJumpUrl } from '/@/utils/jump';
import { openAgentRunWindow } from '../../workflow/shared/runtimeRoute';
import { queryMarketplaceWorkflowApps } from '../../workflow/api/workflow.api';
import { decorateAppsWithModelAvailability } from './agentModelRequirements';
import { pickPinnedRecommendedAgents } from '../utils/pinnedRecommendedAgents';
import { decorateBuiltinCatalogApp, sortBuiltinCatalogApps } from '../builtinAssistants';
import {
  buildAgentCapabilityFilters,
  matchesMarketplaceApp,
  type MarketplaceApp,
} from '../agentMarketCapabilities';

export type CenterSectionKey = 'chat' | 'agent' | 'knowledge' | 'skill' | 'files' | 'models';

type UseAgentMarketOptions = {
  activeSection: { value: CenterSectionKey };
  modelPanelCollapsed: { value: boolean };
  showError: (error: unknown) => void;
};

/** auth-api 的「我的应用」目录（用户自建应用）；目录 404 不弹 axios 原文，由调用方按 catalogUnavailable 处理。 */
const myAppList = (params: Record<string, unknown>) =>
  defHttp.get(
    { url: '/app/appInfo/my/all/list', params },
    { errorMessageMode: 'none', successMessageMode: 'none' },
  );

function catalogHttpStatus(error: unknown): number {
  if (!error || typeof error !== 'object') return 0;
  const payload = error as { status?: unknown; response?: { status?: unknown } };
  const status = payload.response?.status ?? payload.status;
  return typeof status === 'number' ? status : Number(status) || 0;
}

export function useAgentMarket(options: UseAgentMarketOptions) {
  const rawAppList = ref<MarketplaceApp[]>([]);
  let catalogUnavailable = false;
  let catalogLoaded = false;
  const appList = computed<MarketplaceApp[]>(() => rawAppList.value);
  const appLoading = ref(false);
  const appVisibleCount = ref(12);
  const searchKeyword = ref('');
  const selectedCategory = ref('all');
  let appResizeTimer: number | null = null;

  const appCategoryFilters = computed(() => buildAgentCapabilityFilters(appList.value));

  const filteredAppList = computed(() =>
    appList.value.filter((item) => matchesMarketplaceApp(item, selectedCategory.value, searchKeyword.value)),
  );

  const visibleAppList = computed(() => filteredAppList.value.slice(0, appVisibleCount.value));
  const appHasMore = computed(() => appVisibleCount.value < filteredAppList.value.length);

  const recommendedAgents = computed<AgentItem[]>(() => pickPinnedRecommendedAgents(appList.value));

  function currentNavWidth() {
    if (typeof window === 'undefined') return options.modelPanelCollapsed.value ? 78 : 280;
    const raw = getComputedStyle(document.documentElement).getPropertyValue('--center-nav-width').trim();
    const parsed = parseFloat(raw);
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
    return options.modelPanelCollapsed.value ? 78 : 280;
  }

  function calculateAppPageSize() {
    const sidebarWidth = currentNavWidth();
    const availableWidth = Math.max(window.innerWidth - sidebarWidth - 128, 250);
    const availableHeight = Math.max(window.innerHeight - 230, 250);
    const columns = Math.max(1, Math.floor((availableWidth + 16) / 266));
    const rows = Math.max(2, Math.ceil(availableHeight / 318) + 1);
    return Math.min(30, Math.max(6, columns * rows));
  }

  function normalizeAppListResponse(response: any): MarketplaceApp[] {
    if (Array.isArray(response)) return response as MarketplaceApp[];
    if (Array.isArray(response?.records)) return response.records as MarketplaceApp[];
    if (Array.isArray(response?.list)) return response.list as MarketplaceApp[];
    return [];
  }

  /** 多路目录按 id 去重，先到先得：app_info 里若还有同一应用的老记录，以前面一路为准，不出现两张卡 */
  function mergeMarketplaceApps(...groups: MarketplaceApp[][]): MarketplaceApp[] {
    const seen = new Set<string>();
    const merged: MarketplaceApp[] = [];
    for (const group of groups) {
      for (const item of group) {
        const key = String(item?.id ?? '').trim();
        if (key && seen.has(key)) continue;
        if (key) seen.add(key);
        merged.push(item);
      }
    }
    return merged;
  }

  async function reloadApps(reloadOptions?: { force?: boolean }) {
    if (appLoading.value) return;
    // 主对话发送/切会话会在空目录时重试。目录一旦拉过（含 404 空结果）就不要再打断对话。
    // 发布/审核/删除之后目录确实变了，这类调用方显式 force 绕过这条守卫。
    if (!reloadOptions?.force && catalogLoaded && options.activeSection.value !== 'agent') return;
    appLoading.value = true;
    try {
      // 内置智能体的上架记录归 agent-api（app_info），用户自建应用仍走 myAppList。
      // 本地 auth-api 的 myAppList 固定返回空，所以审核通过的自建智能体另从 agent-api 的
      // 发布事实源取（queryMarketplaceWorkflowApps，可见性与运行页同一判定）。
      // 各路独立取，任一失败不影响其他路。
      const [appResult, modelResult, builtinResult, workflowResult] = await Promise.allSettled([
        myAppList({ column: 'createTime', order: 'desc' }),
        getMarketplaceModelOptions(),
        listBuiltinApps(),
        queryMarketplaceWorkflowApps(),
      ]);
      const builtinApps: MarketplaceApp[] =
        builtinResult.status === 'fulfilled' ? (builtinResult.value as MarketplaceApp[]) : [];
      if (builtinResult.status === 'rejected') {
        console.warn('load builtin assistants failed', builtinResult.reason);
      }
      const workflowApps: MarketplaceApp[] =
        workflowResult.status === 'fulfilled' ? normalizeAppListResponse(workflowResult.value) : [];
      if (workflowResult.status === 'rejected') {
        console.warn('load published workflow apps failed', workflowResult.reason);
      }
      if (appResult.status === 'rejected') {
        // 自建应用目录挂了不该连带内置智能体一起消失
        rawAppList.value = mergeMarketplaceApps(builtinApps, workflowApps);
        const status = catalogHttpStatus(appResult.reason);
        catalogUnavailable = status === 404 || status === 501 || status === 503;
        if (options.activeSection.value === 'agent' && !catalogUnavailable) {
          options.showError('智能体广场暂时无法加载，请稍后重试');
        } else if (!catalogUnavailable) {
          console.warn('load marketplace catalog failed', appResult.reason);
        }
        return;
      }

      catalogUnavailable = false;
      const apps = mergeMarketplaceApps(builtinApps, normalizeAppListResponse(appResult.value), workflowApps);
      // 模型目录失败时不做本地预警；运行端仍会以当前授权做最终校验。
      const availableModels = modelResult.status === 'fulfilled'
        ? modelResult.value.filter((model) => model.available !== false).map((model) => model.value)
        : null;
      const decoratedApps = (decorateAppsWithModelAvailability(apps, availableModels) as MarketplaceApp[])
        .map((item) => decorateBuiltinCatalogApp(item));
      // 应用目录已直接返回创建人的真实姓名与头像；避免逐创建人再查 sys_user。
      rawAppList.value = sortBuiltinCatalogApps(decoratedApps);
      appVisibleCount.value = calculateAppPageSize();
      if (!appCategoryFilters.value.some((category) => category.value === selectedCategory.value)) {
        selectedCategory.value = 'all';
      }
    } catch (error) {
      rawAppList.value = [];
      if (options.activeSection.value === 'agent') {
        options.showError('智能体广场暂时无法加载，请稍后重试');
      } else {
        console.warn('load marketplace catalog failed', error);
      }
    } finally {
      catalogLoaded = true;
      appLoading.value = false;
    }
  }

  function selectAppCategory(category: string) {
    if (selectedCategory.value === category) return;
    selectedCategory.value = category;
    appVisibleCount.value = calculateAppPageSize();
  }

  function loadMoreApps() {
    if (options.activeSection.value !== 'agent' || !appHasMore.value) return;
    appVisibleCount.value += calculateAppPageSize();
  }

  function handleAppViewportResize() {
    if (appResizeTimer) window.clearTimeout(appResizeTimer);
    appResizeTimer = window.setTimeout(() => {
      appResizeTimer = null;
      const nextPageSize = calculateAppPageSize();
      if (options.activeSection.value === 'agent' && nextPageSize > appVisibleCount.value) {
        appVisibleCount.value = nextPageSize;
      }
    }, 180);
  }

  // function getAppJumpHeaders() {
  //   const headers: Record<string, string> = { 'X-Version': 'v3' };
  //   const token = userStore.getToken;
  //   const info: any = userStore.getUserInfo || {};
  //   const tenantId = userStore.hasShareTenantId && userStore.shareTenantId !== 0 ? userStore.shareTenantId : userStore.getTenant;

  //   if (token) {
  //     headers['X-Access-Token'] = token;
  //     headers.Authorization = token;
  //   }
  //   if (tenantId !== undefined && tenantId !== null && tenantId !== '') headers['X-Tenant-Id'] = String(tenantId);
  //   if (info.id) headers['X-User-Id'] = String(info.id);
  //   if (info.username) headers['X-Username'] = String(info.username);

  //   return headers;
  // }

  // function resolveAppJumpUrl(url: string) {
  //   const headers = getAppJumpHeaders();
  //   const normalizedHeaders = Object.fromEntries(Object.entries(headers).map(([name, value]) => [name.toLowerCase(), value]));
  //   const missingHeaders = new Set<string>();
  //   const resolvedUrl = url.replace(/\$\{([^{}]+)\}/g, (placeholder, headerName: string) => {
  //     const normalizedName = headerName.trim().toLowerCase();
  //     const value = normalizedHeaders[normalizedName];
  //     if (value === undefined || value === '') {
  //       missingHeaders.add(headerName.trim());
  //       return placeholder;
  //     }
  //     return encodeURIComponent(value);
  //   });

  //   if (missingHeaders.size) {
  //     throw new Error(`閻犲搫鐤囧ù鍡涘捶閺夋寧绲荤紓鍌氭惈閻垳鎷犻柨瀣勾濠㈣埖娼欒ぐ澶愭煂韫囥儳绐?{Array.from(missingHeaders).join('闁?)}`);
  //   }
  //   return resolvedUrl;
  // }

  function openAgent(item: MarketplaceApp) {
    if (Array.isArray(item?.missingModels) && item.missingModels.length) {
      options.showError(`该智能体缺少可用模型：${item.missingModels.join('、')}`);
      return;
    }
    if (!item?.pcUrl) {
      options.showError('该智能体未配置访问地址');
      return;
    }
    let targetUrl = '';
    try {
      targetUrl = resolveAppJumpUrl(String(item.pcUrl));
    } catch (error) {
      options.showError(error);
      return;
    }
    const opened = openAgentRunWindow(targetUrl);
    if (!opened) options.showError('无法打开运行页，请允许浏览器弹出窗口');
  }

  function startChatWithAgent(agent: AgentItem) {
    const fullApp = appList.value.find((app) => String(app.id) === agent.id);
    if (fullApp?.pcUrl) {
      openAgent(fullApp);
    } else {
      options.showError('该智能体未配置访问地址');
    }
  }

  function openAgentFromChat(app: MarketplaceApp) {
    if (!app?.pcUrl) {
      options.showError('该智能体未配置访问地址');
      return;
    }
    openAgent(app);
  }

  watch(searchKeyword, () => {
    appVisibleCount.value = calculateAppPageSize();
  });

  watch(appCategoryFilters, (filters) => {
    if (!filters.some((category) => category.value === selectedCategory.value)) {
      selectedCategory.value = 'all';
      appVisibleCount.value = calculateAppPageSize();
    }
  });

  function clearResizeTimer() {
    if (appResizeTimer) {
      window.clearTimeout(appResizeTimer);
      appResizeTimer = null;
    }
  }

  return {
    appList,
    appLoading,
    appVisibleCount,
    searchKeyword,
    selectedCategory,
    appCategoryFilters,
    filteredAppList,
    visibleAppList,
    appHasMore,
    recommendedAgents,
    reloadApps,
    selectAppCategory,
    loadMoreApps,
    handleAppViewportResize,
    openAgent,
    startChatWithAgent,
    openAgentFromChat,
    clearResizeTimer,
  };
}
