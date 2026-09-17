import { computed, ref, watch, type Ref } from 'vue';
import { myAppList } from '../../flow/app/AppInfo.api';
import { getMarketplaceModelOptions, type AgentItem } from '../agentApi';
import { resolveAppJumpUrl } from '/@/utils/jump';
import { openAgentRunWindow } from '../../workflow/shared/runtimeRoute';
import { decorateAppsWithModelAvailability } from './agentModelRequirements';
import { pickPinnedRecommendedAgents } from '../utils/pinnedRecommendedAgents';
import { excludeOwnedDeletedRuntimeApps } from '../utils/marketplaceCatalog';
import { decorateBuiltinCatalogApp, sortBuiltinCatalogApps } from '../builtinAssistants';
import { useUserStore } from '/@/store/modules/user';
import {
  buildAgentCapabilityFilters,
  matchesMarketplaceApp,
  type MarketplaceApp,
} from '../agentMarketCapabilities';

export type CenterSectionKey = 'chat' | 'agent' | 'myAgent' | 'knowledge' | 'skill' | 'files';

type UseAgentMarketOptions = {
  activeSection: { value: CenterSectionKey };
  modelPanelCollapsed: { value: boolean };
  showError: (error: unknown) => void;
  liveWorkflowApps?: Ref<any[]>;
  liveWorkflowReady?: Ref<boolean>;
};

export function useAgentMarket(options: UseAgentMarketOptions) {
  const userStore = useUserStore();
  const rawAppList = ref<MarketplaceApp[]>([]);
  const appList = computed<MarketplaceApp[]>(() => {
    return excludeOwnedDeletedRuntimeApps(
      rawAppList.value,
      options.liveWorkflowApps?.value || [],
      userStore.getUserInfo,
      Boolean(options.liveWorkflowReady?.value),
    ) as MarketplaceApp[];
  });
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

  const recommendedAgents = computed<AgentItem[]>(() => pickPinnedRecommendedAgents(appList.value, []));

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

  async function reloadApps() {
    appLoading.value = true;
    try {
      const [appResult, modelResult] = await Promise.allSettled([
        myAppList({ column: 'createTime', order: 'desc' }),
        getMarketplaceModelOptions(),
      ]);
      if (appResult.status === 'rejected') throw appResult.reason;

      const apps = normalizeAppListResponse(appResult.value);
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
      options.showError(error);
    } finally {
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
