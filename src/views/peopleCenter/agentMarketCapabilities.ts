export const AGENT_CAPABILITY_KEYS = [
  'communication',
  'document_knowledge',
  'data_table',
  'content_creation',
  'planning_structure',
  'developer_automation',
  'image_multimedia',
  'other',
] as const;

export type AgentCapabilityKey = (typeof AGENT_CAPABILITY_KEYS)[number];

export type MarketplaceApp = {
  id: string | number;
  appName: string;
  appRemark?: string;
  appIcon?: string;
  appCategory?: string;
  appCategory_dictText?: string;
  appType?: string;
  pcUrl?: string;
  createBy?: string;
  createBy_dictText?: string;
  createByName?: string;
  createByAvatar?: string;
  create_by?: string;
  createTime?: string;
  useModels?: string;
  use_models?: string;
  missingModels?: string[];
  [key: string]: unknown;
};

export type MarketplaceCreatorProfile = {
  id?: unknown;
  userId?: unknown;
  username?: unknown;
  realname?: unknown;
  avatar?: unknown;
};

export type AgentCapabilityDefinition = {
  key: AgentCapabilityKey;
  label: string;
  tint: string;
};

export type AgentCapabilityFilter = {
  value: 'all' | AgentCapabilityKey;
  label: string;
  count: number;
};

export const AGENT_CAPABILITY_DEFINITIONS: readonly AgentCapabilityDefinition[] = [
  { key: 'communication', label: '沟通交互', tint: '#f4f6ff' },
  { key: 'document_knowledge', label: '文档与知识', tint: '#f2f9fb' },
  { key: 'data_table', label: '数据与表格', tint: '#f2faf7' },
  { key: 'content_creation', label: '内容创作', tint: '#fff8f2' },
  { key: 'planning_structure', label: '规划与结构', tint: '#f4faf4' },
  { key: 'developer_automation', label: '开发与自动化', tint: '#f5f6fa' },
  { key: 'image_multimedia', label: '图像与多媒体', tint: '#f8f4fb' },
  { key: 'other', label: '综合/其他', tint: '#f8fafc' },
];

const capabilityKeySet = new Set<string>(AGENT_CAPABILITY_KEYS);
const capabilityDefinitionMap = new Map(
  AGENT_CAPABILITY_DEFINITIONS.map((definition) => [definition.key, definition] as const),
);

export function normalizeAgentCapability(value: unknown): AgentCapabilityKey {
  const normalized = String(value || '').trim().toLowerCase();
  return capabilityKeySet.has(normalized) ? (normalized as AgentCapabilityKey) : 'other';
}

export function getAgentCapability(item: Pick<MarketplaceApp, 'appCategory'>): AgentCapabilityKey {
  return normalizeAgentCapability(item.appCategory);
}

export function getAgentCapabilityDefinition(
  value: AgentCapabilityKey | unknown,
): AgentCapabilityDefinition {
  const key = normalizeAgentCapability(value);
  return capabilityDefinitionMap.get(key) || capabilityDefinitionMap.get('other')!;
}

export function buildAgentCapabilityFilters(apps: readonly MarketplaceApp[]): AgentCapabilityFilter[] {
  const counts = new Map<AgentCapabilityKey, number>();
  for (const app of apps) {
    const key = getAgentCapability(app);
    counts.set(key, (counts.get(key) || 0) + 1);
  }

  return [
    { value: 'all', label: '全部应用', count: apps.length },
    ...AGENT_CAPABILITY_DEFINITIONS.filter((definition) => (counts.get(definition.key) || 0) > 0).map(
      (definition) => ({
        value: definition.key,
        label: definition.label,
        count: counts.get(definition.key) || 0,
      }),
    ),
  ];
}

export function matchesMarketplaceApp(
  item: MarketplaceApp,
  selectedCategory: 'all' | AgentCapabilityKey | string,
  searchKeyword: string,
): boolean {
  const matchesCategory = selectedCategory === 'all' || getAgentCapability(item) === selectedCategory;
  if (!matchesCategory) return false;

  const keyword = searchKeyword.trim().toLowerCase();
  if (!keyword) return true;
  return [item.appName, item.appRemark].some((value) => String(value || '').toLowerCase().includes(keyword));
}

export function getAgentVisualVariant(item: Pick<MarketplaceApp, 'id'>): 'a' | 'b' {
  const stableId = String(item.id || '');
  let hash = 0;
  for (let index = 0; index < stableId.length; index += 1) {
    hash = (hash * 31 + stableId.charCodeAt(index)) >>> 0;
  }
  return hash % 2 === 0 ? 'a' : 'b';
}

export function getMarketplaceCreatorName(
  item: Pick<MarketplaceApp, 'createByName' | 'createBy_dictText' | 'createBy'>,
): string {
  const candidates = [item.createByName, item.createBy_dictText, item.createBy]
    .map((value) => String(value || '').trim())
    .filter(Boolean);
  const readable = candidates.find((value) => !/^\d{8,}$/.test(value) && !/^[0-9a-f-]{24,}$/i.test(value));
  return readable || '未知';
}

function identityValues(values: unknown[]): string[] {
  return values
    .map((value) => String(value ?? '').trim())
    .filter(Boolean);
}

/** 已有实名翻译时不再查；只回溯仍显示原始用户名/ID 的记录。 */
export function marketplaceCreatorLookupKey(item: MarketplaceApp): string {
  const raw = identityValues([item.createBy, item.create_by])[0] || '';
  if (!raw) return '';
  const translated = identityValues([item.createByName, item.createBy_dictText])[0] || '';
  return translated && translated !== raw ? '' : raw;
}

/** 用同一 sys_user 资料统一兼容 create_by 中的用户 ID 和登录名。 */
export function decorateMarketplaceCreator<T extends MarketplaceApp>(
  item: T,
  profile: MarketplaceCreatorProfile | null | undefined,
): T {
  if (!profile) return item;
  const itemKeys = new Set(identityValues([
    item.createBy,
    item.create_by,
    item.createByName,
    item.createBy_dictText,
  ]));
  const profileKeys = identityValues([
    profile.id,
    profile.userId,
    profile.username,
    profile.realname,
  ]);
  if (!profileKeys.some((key) => itemKeys.has(key))) return item;

  const realname = String(profile.realname ?? '').trim();
  const username = String(profile.username ?? '').trim();
  const avatar = String(profile.avatar ?? '').trim();
  return {
    ...item,
    createByName: realname || item.createByName || item.createBy_dictText || username,
    createByAvatar: avatar || item.createByAvatar,
  } as T;
}

export function getMarketplaceCreatorInitials(
  item: Pick<MarketplaceApp, 'createByName' | 'createBy_dictText' | 'createBy'>,
): string {
  const name = getMarketplaceCreatorName(item);
  if (name === '未知') return '?';
  const latin = name.match(/[A-Za-z0-9]+/g)?.join('') || '';
  if (latin) return latin.slice(0, 2).toUpperCase();
  return Array.from(name)[0] || '?';
}
