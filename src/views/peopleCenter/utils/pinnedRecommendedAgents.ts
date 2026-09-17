import type { AgentItem } from '../agentApi';
import { getBuiltinAssistantFromMarketplace, listBuiltinAssistants } from '../builtinAssistants';

const MARKET_PINNED_NAMES = ['智能填表助手'] as const;

/** 主对话欢迎页按此顺序挑选；系统应用仅在服务端授权后才可入选。 */
export const PINNED_RECOMMENDED_AGENT_NAMES = [
  ...listBuiltinAssistants().map((assistant) => assistant.name),
  ...MARKET_PINNED_NAMES,
] as const;

export type PinnedRecommendedAgent = AgentItem & {
  open: 'market' | 'run';
  raw?: unknown;
};

function displayName(item: any): string {
  return String(item?.appName || item?.name || '').trim();
}

function findByName(list: any[], name: string) {
  return list.find((item) => displayName(item) === name);
}

function toMarketAgent(item: any): PinnedRecommendedAgent {
  return {
    id: String(item?.id || ''),
    name: displayName(item) || '未命名智能体',
    description: String(item?.appRemark || item?.description || ''),
    icon: String(item?.appIcon || item?.icon || ''),
    category: String(item?.appCategory || ''),
    is_recommend: true,
    status: Number(item?.status || 0),
    open: 'market',
    raw: item,
  };
}

function toRunAgent(item: any): PinnedRecommendedAgent {
  return {
    id: String(item?.id || item?.workflowAppId || ''),
    name: displayName(item) || '未命名智能体',
    description: String(item?.description || item?.appRemark || ''),
    icon: String(item?.appIcon || item?.icon || ''),
    category: String(item?.appCategory || ''),
    is_recommend: true,
    open: 'run',
    raw: item,
  };
}

export function pickPinnedRecommendedAgents(
  marketApps: any[] = [],
  workflowApps: any[] = [],
): PinnedRecommendedAgent[] {
  const picked: PinnedRecommendedAgent[] = [];
  // The administrator-filtered marketplace list is the only visibility source for these pages.
  // Match its fixed route/preset rather than the editable display name, and never recreate a
  // missing static card client-side.
  for (const assistant of listBuiltinAssistants()) {
    const market = marketApps.find(
      (item) => getBuiltinAssistantFromMarketplace(item)?.preset === assistant.preset,
    );
    if (market?.pcUrl) picked.push(toMarketAgent(market));
  }
  for (const name of MARKET_PINNED_NAMES) {
    const market = findByName(marketApps, name);
    const workflow = findByName(workflowApps, name);
    if (market?.pcUrl) {
      const agent = toMarketAgent(market);
      if (agent.id) picked.push(agent);
      continue;
    }
    if (workflow) {
      const agent = toRunAgent(workflow);
      if (agent.id) picked.push(agent);
      continue;
    }
    if (market) {
      const agent = toMarketAgent(market);
      if (agent.id) picked.push(agent);
    }
  }
  return picked;
}
