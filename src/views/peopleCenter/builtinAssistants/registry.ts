import type { MarketplaceApp } from '../agentMarketCapabilities';
import { BUILTIN_ASSISTANT_MODULES } from './modules';
import type {
  AssistantPreset,
  BuiltinAssistant,
  BuiltinUiFlags,
  BuiltinUiPolicyKey,
} from './types';

const BUILTIN_ASSISTANTS: readonly BuiltinAssistant[] = BUILTIN_ASSISTANT_MODULES.map(
  (module) => module.assistant,
);

function normalizePreset(value: unknown): string {
  return String(value || '').trim().toLowerCase();
}

function normalizeId(value: unknown): string {
  return String(value || '').trim();
}

function parseFormOptions(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object') return value as Record<string, unknown>;
  if (typeof value !== 'string' || !value.trim()) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

export function listBuiltinAssistants(): BuiltinAssistant[] {
  return [...BUILTIN_ASSISTANTS].sort((left, right) => left.recommendOrder - right.recommendOrder);
}

export function listRecommendedBuiltinAssistants(): BuiltinAssistant[] {
  return listBuiltinAssistants();
}

export function getBuiltinAssistantByPreset(preset: unknown): BuiltinAssistant | undefined {
  const key = normalizePreset(preset);
  if (!key) return undefined;
  return BUILTIN_ASSISTANTS.find((item) => item.preset === key);
}

export function getBuiltinAssistantById(id: unknown): BuiltinAssistant | undefined {
  const key = normalizeId(id);
  if (!key) return undefined;
  return BUILTIN_ASSISTANTS.find((item) => item.appId === key);
}

export function getBuiltinAssistantFromMarketplace(
  item: {
    id?: unknown;
    appId?: unknown;
    builtinPreset?: unknown;
    formOptions?: unknown;
    form_options?: unknown;
    pcUrl?: unknown;
    pc_url?: unknown;
    appName?: unknown;
  } | null | undefined,
): BuiltinAssistant | undefined {
  if (!item) return undefined;
  const options = parseFormOptions(item.formOptions ?? item.form_options);
  const preset = item.builtinPreset ?? options.builtinPreset;
  const route = String(item.pcUrl ?? item.pc_url ?? '').trim();
  return getBuiltinAssistantByPreset(preset)
    || getBuiltinAssistantById(item.id)
    || getBuiltinAssistantById(item.appId)
    || BUILTIN_ASSISTANTS.find((assistant) => assistant.route === route);
}

export function isBuiltinAssistant(
  item: {
    id?: unknown;
    appId?: unknown;
    builtinPreset?: unknown;
    formOptions?: unknown;
    form_options?: unknown;
    pcUrl?: unknown;
    pc_url?: unknown;
    appName?: unknown;
  } | string | null | undefined,
): boolean {
  if (item == null) return false;
  if (typeof item === 'string') {
    return Boolean(getBuiltinAssistantById(item) || getBuiltinAssistantByPreset(item));
  }
  return Boolean(getBuiltinAssistantFromMarketplace(item));
}

export function isBuiltinAssistantId(id: unknown): boolean {
  return Boolean(getBuiltinAssistantById(id));
}

export function decorateBuiltinCatalogApp<T extends MarketplaceApp>(item: T): T {
  const assistant = getBuiltinAssistantFromMarketplace(item);
  if (!assistant) return item;
  const configuredIcon = String(item.appIcon || '').trim();
  const configuredRemark = String(item.appRemark || '').trim();
  return {
    ...item,
    appIcon: configuredIcon && configuredIcon !== assistant.legacyIcon ? configuredIcon : assistant.icon,
    appRemark: configuredRemark ? item.appRemark : assistant.description,
    builtinPreset: assistant.preset,
  } as T;
}

/**
 * 只调整应用目录已返回的记录；不补卡、不改变其他应用的相对顺序。
 */
export function sortBuiltinCatalogApps<T extends MarketplaceApp>(items: readonly T[]): T[] {
  return items
    .map((item, index) => ({
      item,
      index,
      order: getBuiltinAssistantFromMarketplace(item)?.recommendOrder ?? Number.MAX_SAFE_INTEGER,
    }))
    .sort((left, right) => left.order - right.order || left.index - right.index)
    .map(({ item }) => item);
}

export function isRestrictedAssistantPreset(value: unknown): boolean {
  return Boolean(getBuiltinAssistantByPreset(value));
}

export function getBuiltinUiFlags(policy: BuiltinUiPolicyKey | undefined): BuiltinUiFlags | undefined {
  return BUILTIN_ASSISTANT_MODULES.find((module) => module.assistant.uiPolicy === policy)?.ui;
}

export function getBuiltinUiPolicy(preset: unknown): BuiltinUiFlags | undefined {
  return getBuiltinUiFlags(getBuiltinAssistantByPreset(preset)?.uiPolicy);
}

export function isAssistantPreset(value: unknown): value is AssistantPreset {
  return Boolean(getBuiltinAssistantByPreset(value));
}
