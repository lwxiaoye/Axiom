import { agentAuthHeaders } from '../utils/agentAuthHeaders';
import type { HydratedMainChatSkin, MainChatSkinRecord, MainChatSkinRuntimeResponse } from './types';

const API_ROOT = '/agent-api/campus-assistant';

async function errorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json();
    const detail = payload?.detail ?? payload?.message;
    if (typeof detail === 'string' && detail.trim()) return detail;
  } catch {
    // Keep the status-based fallback for non-JSON responses.
  }
  return fallback;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(agentAuthHeaders({ Accept: 'application/json' }));
  new Headers(init?.headers).forEach((value, key) => headers.set(key, value));
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    throw new Error(await errorMessage(response, `请求失败：${response.status}`));
  }
  return response.json() as Promise<T>;
}

export function getPublishedMainChatSkin(): Promise<MainChatSkinRuntimeResponse> {
  return requestJson('/runtime/skin');
}

export function listInstalledMainChatSkins(): Promise<{ scope: 'main_chat'; records: MainChatSkinRecord[] }> {
  return requestJson('/admin/skins');
}

export function getInstalledMainChatSkin(skinId: string): Promise<MainChatSkinRecord> {
  return requestJson(`/admin/skins/${encodeURIComponent(skinId)}`);
}

export async function importMainChatSkin(file: File): Promise<{ installed: boolean; skin: MainChatSkinRecord }> {
  const form = new FormData();
  form.append('file', file, file.name);
  return requestJson('/admin/skins/import', { method: 'POST', body: form });
}

export function updateMainChatSkinMetadata(
  skinId: string,
  metadata: { name: string; description: string },
): Promise<MainChatSkinRecord> {
  return requestJson(`/admin/skins/${encodeURIComponent(skinId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(metadata),
  });
}

export function deleteMainChatSkin(skinId: string): Promise<{ deleted: boolean; id: string }> {
  return requestJson(`/admin/skins/${encodeURIComponent(skinId)}`, { method: 'DELETE' });
}

function filenameFromDisposition(value: string | null, fallback: string): string {
  const match = String(value || '').match(/filename="([^"]+)"/i);
  return match?.[1] || fallback;
}

export async function exportMainChatSkin(skin: MainChatSkinRecord): Promise<void> {
  const response = await fetch(`${API_ROOT}/admin/skins/${encodeURIComponent(skin.id)}/export`, {
    headers: agentAuthHeaders({ Accept: 'application/vnd.axiom.skin+zip' }),
  });
  if (!response.ok) {
    throw new Error(await errorMessage(response, `导出失败：${response.status}`));
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filenameFromDisposition(
    response.headers.get('content-disposition'),
    `${skin.key}-${skin.version}.axiomskin`,
  );
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function hydrateMainChatSkin(skin: MainChatSkinRecord): Promise<HydratedMainChatSkin> {
  const assetUrls: Record<string, string> = {};
  try {
    for (const asset of skin.manifest?.assets || []) {
      if (!asset.url) continue;
      const response = await fetch(asset.url, {
        headers: agentAuthHeaders({ Accept: asset.mime }),
      });
      if (!response.ok) {
        throw new Error(await errorMessage(response, `素材加载失败：${asset.key}`));
      }
      assetUrls[asset.key] = URL.createObjectURL(await response.blob());
    }
    return { ...skin, assetUrls };
  } catch (error) {
    for (const url of Object.values(assetUrls)) URL.revokeObjectURL(url);
    throw error;
  }
}

export function releaseHydratedMainChatSkin(skin?: HydratedMainChatSkin | null): void {
  if (!skin) return;
  for (const url of Object.values(skin.assetUrls)) URL.revokeObjectURL(url);
}
