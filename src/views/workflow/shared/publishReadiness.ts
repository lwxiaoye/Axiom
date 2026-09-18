/**
 * 发布前置检查与发布报错的归一化。
 *
 * 为什么放在前端也做一遍：后端 submitReview 的 400「工作流缺少画布模型」对用户来说是个
 * 死胡同——弹窗都填完了才被告知不能发，而且看不出下一步该干什么。这里在点「发布」
 * 之前就读草稿判断能不能发，不能发就直接指向配置页；后端校验仍是最终裁决。
 *
 * 纯函数、无别名依赖：jest.config.workflow 直接跑。
 */

export type PublishReadinessRecord = {
  aiAppType?: string;
  status?: string;
};

export type PublishReadinessDefinition = {
  draftJson?: string | null;
  publishedJson?: string | null;
} | null | undefined;

export type PublishReadiness =
  | { ready: true; workflowJson: string }
  | { ready: false; reason: string; nextStepLabel: string };

function isChatAgent(record: PublishReadinessRecord | null | undefined) {
  const kind = String(record?.aiAppType || '');
  return kind === 'chatAgent' || kind === 'simple' || kind === 'agent';
}

function parseCanvas(json: string): { nodes?: any[] } | null {
  try {
    const parsed = JSON.parse(json);
    const graph = parsed?.fastgpt;
    if (!graph || !Array.isArray(graph.nodes) || !graph.nodes.length) return null;
    return graph;
  } catch {
    return null;
  }
}

/** 对话 Agent 三节点图里的 agent 节点是否已经选了模型（配置页保存时会强制，但导入包/旧数据可能缺） */
function chatAgentModelSelected(graph: { nodes?: any[] }): boolean {
  const agentNode = (graph.nodes || []).find((node) => node?.flowNodeType === 'agent');
  if (!agentNode) return false;
  const modelInput = (agentNode.inputs || []).find((input: any) => input?.key === 'model');
  return Boolean(String(modelInput?.value || '').trim());
}

/** 「下一步」按钮文案：对话 Agent 是配置页，工作流是编辑器 */
export function getPublishNextStepLabel(record: PublishReadinessRecord | null | undefined): string {
  return isChatAgent(record) ? '去配置' : '去编排';
}

export function getPublishReadiness(
  record: PublishReadinessRecord | null | undefined,
  definition: PublishReadinessDefinition,
): PublishReadiness {
  const chatAgent = isChatAgent(record);
  const nextStepLabel = getPublishNextStepLabel(record);
  const workflowJson = definition?.draftJson || definition?.publishedJson || '';
  if (!workflowJson.trim()) {
    return {
      ready: false,
      nextStepLabel,
      reason: chatAgent
        ? '这个对话 Agent 还没有保存过配置：请进入配置页选择模型、填写提示词，点「保存草稿」后再发布。'
        : '这个工作流还没有保存过编排：请进入编辑器完成编排并保存草稿后再发布。',
    };
  }
  const graph = parseCanvas(workflowJson);
  if (!graph) {
    return {
      ready: false,
      nextStepLabel,
      reason: chatAgent
        ? '已保存的配置不完整（缺少画布模型）：请进入配置页重新保存一次草稿。'
        : '已保存的草稿缺少画布模型：请用编辑器重新保存一次草稿。',
    };
  }
  if (chatAgent && !chatAgentModelSelected(graph)) {
    return {
      ready: false,
      nextStepLabel,
      reason: '这个对话 Agent 还没有选择对话模型：请进入配置页选择模型并保存草稿。',
    };
  }
  return { ready: true, workflowJson };
}

export type SubmitReviewFailure = {
  /** HTTP 状态；未知时为 0 */
  status: number;
  message: string;
  problems: string[];
  /** 400 校验失败 / 未配置：下一步是回配置页 */
  needsConfiguration: boolean;
};

/** 把 submitReview 的各种失败形态（字符串 detail / {message, problems} / axios 错误）压成统一结构 */
export function describeSubmitReviewFailure(error: any): SubmitReviewFailure {
  const status = Number(error?.response?.status ?? error?.status ?? 0) || 0;
  const detail = error?.response?.data?.detail ?? error?.detail;
  let message = '';
  let problems: string[] = [];
  if (typeof detail === 'string') {
    message = detail;
  } else if (detail && typeof detail === 'object') {
    message = String(detail.message || '');
    problems = (Array.isArray(detail.problems) ? detail.problems : [])
      .map((problem: any) => {
        if (typeof problem === 'string') return problem;
        const name = String(problem?.name || '').trim();
        const reason = String(problem?.reason || '').trim();
        return name ? `${name}：${reason}` : reason;
      })
      .filter(Boolean);
  }
  if (!message) message = String(error?.message || '') || '提交发布失败';
  return { status, message, problems, needsConfiguration: status === 400 };
}
