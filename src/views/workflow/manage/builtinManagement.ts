import { listBuiltinAssistants } from '../../peopleCenter/builtinAssistants/registry';

export const MANAGED_BUILTIN_PREFIX = 'builtin-catalog:';

export function getManagedBuiltinCatalogId(record: {
  id?: unknown;
  appType?: unknown;
  pcUrl?: unknown;
  h5Url?: unknown;
}): string | undefined {
  if (!record.id || !['custom', 'external'].includes(String(record.appType || '').trim().toLowerCase())) return;
  const routes = [record.pcUrl, record.h5Url].map((value) => String(value || '').trim().replace(/\/$/, ''));
  const matches = listBuiltinAssistants().filter((assistant) => routes.includes(assistant.route));
  if (matches.length === 1) return `${MANAGED_BUILTIN_PREFIX}${record.id}`;
}
