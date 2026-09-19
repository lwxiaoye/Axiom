import type { AgentItem } from '../agentApi';
import { getBuiltinAssistantFromMarketplace, listBuiltinAssistants } from '../builtinAssistants';

/** 主对话欢迎页按此顺序挑选；系统应用仅在服务端授权后才可入选。 */
export const PINNED_RECOMMENDED_AGENT_NAMES = listBuiltinAssistants().map((assistant) => assistant.name);

function displayName(item: any): string {
  return String(item?.appName || item?.name || '').trim();
}

function toMarketAgent(item: any): AgentItem {
  return {
    id: String(item?.id || ''),
    name: displayName(item) || '未命名智能体',
    description: String(item?.appRemark || item?.description || ''),
    icon: String(item?.appIcon || item?.icon || ''),
    category: String(item?.appCategory || ''),
    is_recommend: true,
    status: Number(item?.status || 0),
  };
}

/** 主对话欢迎页的固定推荐位：只从管理员过滤后的广场列表里挑内置助手，前端不补卡。 */
export function pickPinnedRecommendedAgents(marketApps: any[] = []): AgentItem[] {
  const picked: AgentItem[] = [];
  // The administrator-filtered marketplace list is the only visibility source for these pages.
  // Match its fixed route/preset rather than the editable display name, and never recreate a
  // missing static card client-side.
  for (const assistant of listBuiltinAssistants()) {
    const market = marketApps.find(
      (item) => getBuiltinAssistantFromMarketplace(item)?.preset === assistant.preset,
    );
    if (market?.pcUrl) picked.push(toMarketAgent(market));
  }
  return picked;
}
