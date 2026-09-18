import type { CenterSectionKey } from './composables/useAgentMarket';

/** 板块 → 子路由 name（WS1：centerNew 改路由多页） */
export const centerSectionRouteName: Record<CenterSectionKey, string> = {
  chat: 'CenterChat',
  agent: 'CenterAgent',
  myAgent: 'CenterMyAgent',
  knowledge: 'CenterKnowledge',
  skill: 'CenterSkill',
  files: 'CenterFiles',
  models: 'CenterModels',
};

/** 子路由 name → 板块 */
export const centerRouteNameToSection: Record<string, CenterSectionKey> = Object.entries(
  centerSectionRouteName,
).reduce((acc, [section, name]) => {
  acc[name] = section as CenterSectionKey;
  return acc;
}, {} as Record<string, CenterSectionKey>);

/** 板块 → 绝对路径（router.push 用） */
export const centerSectionPath: Record<CenterSectionKey, string> = {
  chat: '/center/chat',
  agent: '/center/agent',
  myAgent: '/center/my-agent',
  knowledge: '/center/knowledge',
  skill: '/center/skill',
  files: '/center/files',
  models: '/center/models',
};

/** 旧 /centerNew?section=xxx 兼容映射 */
export function legacySectionToPath(section: unknown): string {
  const key = Array.isArray(section) ? section[0] : section;
  return centerSectionPath[(key as CenterSectionKey) || 'chat'] || centerSectionPath.chat;
}
