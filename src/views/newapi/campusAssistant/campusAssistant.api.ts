import { defHttp } from '/@/utils/http/axios';
import type { CampusConfig, ValidationResult } from './campusAssistant.types';

enum Api {
  config = '/agent-api/campus-assistant/admin/config',
  draft = '/agent-api/campus-assistant/admin/draft',
  validate = '/agent-api/campus-assistant/admin/draft/validate',
  publish = '/agent-api/campus-assistant/admin/draft/publish',
  releases = '/agent-api/campus-assistant/admin/releases',
}

// apiUrl: '' —— 目标是 Python agent-api（nginx /agent-api 代理），不能让 defHttp
// 拼默认 /api 前缀（否则打到 Java：No static resource agent-api/...）
const RAW = { isTransformResponse: false, apiUrl: '' } as const;

export const getCampusConfig = () => defHttp.get<CampusConfig>({ url: Api.config }, RAW);

export const listCampusModels = () =>
  defHttp.get<Array<{ id?: string; name?: string; is_default?: boolean }>>(
    { url: '/agent-api/models' },
    { ...RAW, errorMessageMode: 'none' },
  );

export const ensureCampusDraft = () => defHttp.post<CampusConfig>({ url: Api.draft }, RAW);

export const saveCampusDraft = (data: Record<string, unknown>) =>
  defHttp.put<CampusConfig>({ url: Api.draft, data }, RAW);

export const validateCampusDraft = (data?: Record<string, unknown>) =>
  defHttp.post<ValidationResult>({ url: Api.validate, data: data || {} }, RAW);

export const publishCampusDraft = (data: Record<string, unknown>) =>
  defHttp.post<CampusConfig>({ url: Api.publish, data }, RAW);

export const abandonCampusDraft = (expectedRevision: number) =>
  defHttp.delete<CampusConfig>({ url: `${Api.draft}?expected_revision=${expectedRevision}` }, RAW);

export const listCampusReleases = () =>
  defHttp.get<{ total: number; items: any[] }>({ url: Api.releases }, RAW);

export const rollbackCampusRelease = (releaseId: string, data: Record<string, unknown>) =>
  defHttp.post<CampusConfig>({ url: `${Api.releases}/${releaseId}/rollback`, data }, RAW);
