import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { deleteWorkflowApp, queryWorkflowAppPage } from '../../workflow/api/workflow.api';
import { getAiAppRoute } from '../../workflow/shared/agentApp';
import { openAgentRunWindow } from '../../workflow/shared/runtimeRoute';

export function useMyAgents(showError: (error: unknown) => void) {
  const router = useRouter();
  const myAgentList = ref<any[]>([]);
  const myAgentLoading = ref(false);
  const myAgentLoadFailed = ref(false);
  const myAgentLoaded = ref(false);

  function normalizeListResponse(response: any) {
    if (Array.isArray(response)) return response;
    if (Array.isArray(response?.records)) return response.records;
    if (Array.isArray(response?.list)) return response.list;
    return [];
  }

  async function loadMyAgents() {
    myAgentLoading.value = true;
    try {
      const response = await queryWorkflowAppPage({
        pageNo: 1,
        pageSize: 20,
        scope: 'all',
      });
      myAgentList.value = normalizeListResponse(response);
      myAgentLoadFailed.value = false;
    } catch (error) {
      // 初始化加载失败走面板内稳定空态提示，不向用户抛原始后端报错
      console.error('load my agents failed', error);
      myAgentList.value = [];
      myAgentLoadFailed.value = true;
    } finally {
      myAgentLoading.value = false;
      myAgentLoaded.value = true;
    }
  }

  function openAiAppDesigner(record: any) {
    if (!record?.id) {
      showError('未找到应用ID');
      return;
    }
    router.push(getAiAppRoute(record));
  }

  function openAiAppRunner(record: any) {
    if (!record?.id && !record?.workflowAppId) {
      showError('未找到应用ID');
      return;
    }
    const opened = openAgentRunWindow(record);
    if (!opened) showError('无法打开运行页，请允许浏览器弹出窗口');
  }

  async function deleteMyAgent(record: any) {
    await deleteWorkflowApp(record.id);
    await loadMyAgents();
  }

  return {
    myAgentList,
    myAgentLoading,
    myAgentLoadFailed,
    myAgentLoaded,
    loadMyAgents,
    openAiAppDesigner,
    openAiAppRunner,
    deleteMyAgent,
  };
}
