import { computed, ref, watch } from 'vue';
import { getMarketplaceModelOptions, listBuiltinApps, type AgentItem } from '../agentApi';
import { openBuiltinAssistantPage } from '../utils/assistantRoute';
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

export function useAgentMarket(options: UseAgentMarketOptions) {
  const rawAppList = ref<MarketplaceApp[]>([]);
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

  async function reloadApps(reloadOptions?: { force?: boolean }) {
    if (appLoading.value) return;
    // 主对话发送/切会话会在空目录时重试。目录一旦拉过就不要再打断对话。
    // 管理员停用/启用之后目录确实变了，这类调用方显式 force 绕过这条守卫。
    if (!reloadOptions?.force && catalogLoaded && options.activeSection.value !== 'agent') return;
    appLoading.value = true;
    try {
      // 广场只有系统内置的三个助手，上架记录归 agent-api（app_info）；用户不自建智能体。
      // 模型目录另取，失败不影响目录本身。
      const [builtinResult, modelResult] = await Promise.allSettled([
        listBuiltinApps(),
        getMarketplaceModelOptions(),
      ]);
      if (builtinResult.status === 'rejected') {
        rawAppList.value = [];
        if (options.activeSection.value === 'agent') {
          options.showError('智能体广场暂时无法加载，请稍后重试');
        } else {
          console.warn('load builtin assistants failed', builtinResult.reason);
        }
        return;
      }
      const apps = builtinResult.value as MarketplaceApp[];
      // 模型目录失败时不做本地预警；运行端仍会以当前授权做最终校验。
      const availableModels = modelResult.status === 'fulfilled'
        ? modelResult.value.filter((model) => model.available !== false).map((model) => model.value)
        : null;
      const decoratedApps = (decorateAppsWithModelAvailability(apps, availableModels) as MarketplaceApp[])
        .map((item) => decorateBuiltinCatalogApp(item));
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

  function openAgent(item: MarketplaceApp) {
    if (Array.isArray(item?.missingModels) && item.missingModels.length) {
      options.showError(`该智能体缺少可用模型：${item.missingModels.join('、')}`);
      return;
    }
    if (!item?.pcUrl) {
      options.showError('该智能体未配置访问地址');
      return;
    }
    const opened = openBuiltinAssistantPage(String(item.pcUrl));
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
