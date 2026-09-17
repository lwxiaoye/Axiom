import { defHttp } from '/@/utils/http/axios';
import type { PortableRunSkinRecord } from '../core/type';
import { agentAuthHeaders } from '../../peopleCenter/utils/agentAuthHeaders';

export type PresentationPresetRecord = {
  key: string;
  version: number | string;
  name: string;
  description: string;
  previewKey: string;
  sourceType: string;
  materialReady?: boolean;
  materialProblem?: string;
  packageKey?: string;
  portableSkin?: PortableRunSkinRecord;
};

const RAW = { isTransformResponse: false, apiUrl: '' } as const;

export const queryAvailablePresentationPresets = (appId?: string) =>
  defHttp.get<{ records: PresentationPresetRecord[] }>(
    {
      url: '/agent-api/workflow/presentation/presets',
      params: appId ? { appId } : undefined,
    },
    { ...RAW, errorMessageMode: 'none' },
  );

export const queryAdminPresentationPresets = () =>
  defHttp.get<{ records: PresentationPresetRecord[] }>(
    { url: '/agent-api/workflow/presentation/admin/presets' },
    { ...RAW, errorMessageMode: 'none' },
  );

async function responseMessage(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json();
    const detail = payload?.detail ?? payload?.message;
    if (typeof detail === 'string' && detail.trim()) return detail;
  } catch {
    // Keep fallback for non-JSON responses.
  }
  return fallback;
}

export async function importSubAgentSkin(file: File) {
  const form = new FormData();
  form.append('file', file, file.name);
  const response = await fetch(
    '/agent-api/workflow/presentation/admin/skins/import',
    { method: 'POST', headers: agentAuthHeaders(), body: form },
  );
  if (!response.ok) throw new Error(await responseMessage(response, `导入失败：${response.status}`));
  return response.json() as Promise<{ installed: boolean; skin: PortableRunSkinRecord }>;
}

export async function getSubAgentSkin(skinId: string): Promise<PortableRunSkinRecord> {
  const response = await fetch(
    `/agent-api/workflow/presentation/admin/skins/${encodeURIComponent(skinId)}`,
    { headers: agentAuthHeaders({ Accept: 'application/json' }) },
  );
  if (!response.ok) throw new Error(await responseMessage(response, `读取失败：${response.status}`));
  return response.json() as Promise<PortableRunSkinRecord>;
}

export async function updateSubAgentSkinMetadata(
  skinId: string,
  metadata: { name: string; description: string },
): Promise<PortableRunSkinRecord> {
  const response = await fetch(
    `/agent-api/workflow/presentation/admin/skins/${encodeURIComponent(skinId)}`,
    {
      method: 'PATCH',
      headers: agentAuthHeaders({ Accept: 'application/json', 'Content-Type': 'application/json' }),
      body: JSON.stringify(metadata),
    },
  );
  if (!response.ok) throw new Error(await responseMessage(response, `更新失败：${response.status}`));
  return response.json() as Promise<PortableRunSkinRecord>;
}

export async function deleteSubAgentSkin(
  skinId: string,
): Promise<{ deleted: boolean; id: string; fallbackAgentCount: number }> {
  const response = await fetch(
    `/agent-api/workflow/presentation/admin/skins/${encodeURIComponent(skinId)}`,
    { method: 'DELETE', headers: agentAuthHeaders({ Accept: 'application/json' }) },
  );
  if (!response.ok) throw new Error(await responseMessage(response, `删除失败：${response.status}`));
  return response.json() as Promise<{ deleted: boolean; id: string; fallbackAgentCount: number }>;
}

function downloadFilename(value: string | null, fallback: string): string {
  return String(value || '').match(/filename="([^"]+)"/i)?.[1] || fallback;
}

export async function exportSubAgentSkin(record: PresentationPresetRecord): Promise<void> {
  const skin = record.portableSkin;
  if (!skin?.id) throw new Error('该外观不是可导出的皮肤包');
  const response = await fetch(
    `/agent-api/workflow/presentation/admin/skins/${encodeURIComponent(skin.id)}/export`,
    { headers: agentAuthHeaders({ Accept: 'application/vnd.axiom.skin+zip' }) },
  );
  if (!response.ok) throw new Error(await responseMessage(response, `导出失败：${response.status}`));
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = downloadFilename(
    response.headers.get('content-disposition'),
    `${skin.key}-${skin.version}.axiomskin`,
  );
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
