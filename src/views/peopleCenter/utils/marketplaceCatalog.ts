/** 广场走 Java app_info，删除走 Python 工作流应用。目录残留时只藏当前用户已删的运行时智能体。 */

const AGENT_RUN_PATH = /\/agent\/run\/([^/?#]+)/;

export type MarketplaceUserIdentity = {
  id?: unknown;
  userId?: unknown;
  username?: unknown;
  realname?: unknown;
};

export function marketplaceCreatorKeys(user: MarketplaceUserIdentity | null | undefined): string[] {
  if (!user) return [];
  return [user.id, user.userId, user.username, user.realname]
    .map((value) => String(value ?? '').trim())
    .filter(Boolean);
}

export function catalogRuntimeAppId(item: unknown): string {
  const record = item as {
    pcUrl?: unknown;
    pc_url?: unknown;
    id?: unknown;
    formOptions?: unknown;
    form_options?: unknown;
  } | null;
  const pcUrl = String(record?.pcUrl || record?.pc_url || '');
  const fromUrl = pcUrl.match(AGENT_RUN_PATH)?.[1];
  if (fromUrl) return fromUrl;

  const optionsRaw = record?.formOptions ?? record?.form_options;
  let sourceAppId = '';
  if (typeof optionsRaw === 'string' && optionsRaw.trim()) {
    try {
      const parsed = JSON.parse(optionsRaw);
      sourceAppId = String(parsed?.sourceAppId || '').trim();
    } catch {
      sourceAppId = '';
    }
  } else if (optionsRaw && typeof optionsRaw === 'object') {
    sourceAppId = String((optionsRaw as { sourceAppId?: unknown }).sourceAppId || '').trim();
  }
  return sourceAppId;
}

export function isAgentRuntimeCatalogItem(item: unknown): boolean {
  const record = item as { pcUrl?: unknown; pc_url?: unknown } | null;
  const pcUrl = String(record?.pcUrl || record?.pc_url || '');
  return AGENT_RUN_PATH.test(pcUrl) || Boolean(catalogRuntimeAppId(item));
}

export function isCatalogItemOwnedBy(item: unknown, user: MarketplaceUserIdentity | null | undefined): boolean {
  const keys = new Set(marketplaceCreatorKeys(user));
  if (!keys.size) return false;
  const record = item as { createBy?: unknown; createBy_dictText?: unknown; create_by?: unknown } | null;
  return [record?.createBy, record?.createBy_dictText, record?.create_by]
    .map((value) => String(value ?? '').trim())
    .some((value) => value && keys.has(value));
}

export function excludeOwnedDeletedRuntimeApps(
  apps: any[] = [],
  liveWorkflowApps: any[] = [],
  user: MarketplaceUserIdentity | null | undefined,
  liveReady = false,
): any[] {
  if (!liveReady || !Array.isArray(apps) || !apps.length) return apps;
  const liveIds = new Set(
    (Array.isArray(liveWorkflowApps) ? liveWorkflowApps : [])
      .map((item) => String(item?.id || item?.workflowAppId || '').trim())
      .filter(Boolean),
  );
  return apps.filter((item) => {
    if (!isAgentRuntimeCatalogItem(item) || !isCatalogItemOwnedBy(item, user)) return true;
    const runtimeId = catalogRuntimeAppId(item);
    const catalogId = String(item?.id || '').trim();
    if (runtimeId && liveIds.has(runtimeId)) return true;
    if (catalogId && liveIds.has(catalogId)) return true;
    return false;
  });
}
