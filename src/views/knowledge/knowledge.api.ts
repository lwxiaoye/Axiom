import { defHttp } from '/@/utils/http/axios';
import { downloadFile as downloadBlobFile } from '/@/api/common/api';
import { recordAuditEvent } from '/@/api/audit/audit.api';
import { useGlobSetting } from '/@/hooks/setting';
import type {
  KnowledgeAcl,
  KnowledgeAnalyticsOverview,
  KnowledgeAnalyticsRange,
  KnowledgeAnalyticsRanking,
  KnowledgeBase,
  KnowledgeChunk,
  KnowledgeDocument,
  KnowledgeDocumentPreview,
  KnowledgePreviewImage,
  KnowledgeUploadOptions,
  PageResult,
  RetrievalResponse,
} from './knowledge.types';

const Api = {
  baseList: '/ai/knowledge/base/list',
  baseDetail: '/ai/knowledge/base/queryById',
  baseAdd: '/ai/knowledge/base/add',
  baseEdit: '/ai/knowledge/base/edit',
  baseEnable: '/ai/knowledge/base/enable',
  baseDisable: '/ai/knowledge/base/disable',
  baseDelete: '/ai/knowledge/base/delete',
  documentList: '/ai/knowledge/document/list',
  documentUpload: '/ai/knowledge/document/upload',
  documentPreview: '/ai/knowledge/document/preview',
  documentRetry: '/ai/knowledge/document/retry',
  documentEnable: '/ai/knowledge/document/enable',
  documentDisable: '/ai/knowledge/document/disable',
  documentDelete: '/ai/knowledge/document/delete',
  documentBatchDelete: '/ai/knowledge/document/delete-batch',
  documentDownload: '/ai/knowledge/document/download',
  documentDownloadZip: '/ai/knowledge/document/download-zip',
  chunkList: '/ai/knowledge/chunk/list',
  chunkEdit: '/ai/knowledge/chunk/edit',
  chunkImageUpload: '/ai/knowledge/chunk/image/upload',
  chunkEnable: '/ai/knowledge/chunk/enable',
  chunkDisable: '/ai/knowledge/chunk/disable',
  chunkDelete: '/ai/knowledge/chunk/delete',
  retrievalTest: '/ai/knowledge/retrieval/test',
  retrievalManualTest: '/ai/knowledge/retrieval/manual-test',
  aclList: '/ai/knowledge/acl/list',
  aclSave: '/ai/knowledge/acl/save',
};

// 后台知识库内容管理与用户侧 ACL 接口必须分开，不能依赖前端页面来源决定授权范围。
const ManagementApiPrefix = '/ai/knowledge/admin';
const managementUrl = (url: string) => `${ManagementApiPrefix}${url.replace('/ai/knowledge', '')}`;

// uploadFile bypasses the normal URL-prefix hook, so use the same relative API base as ordinary requests.
const knowledgeApiBaseUrl = useGlobSetting().apiUrl;

// ── 知识库迁移至 agent-api ───────────────────────────────────────────────
// 原 /ai/knowledge/* 由 JeecgBoot(Java) 提供，Java 下线后 auth-api 只剩一个统一
// 返回 503 的桩，页面表现为永远转圈。知识库现由 agent-api 自持（它握有 Qdrant 与
// Embedding 配置）。以下重写「我的知识库」实际用到的接口，保持原有函数签名与返回
// 结构，页面无需改动；后台管理页的其余接口仍走旧路径，不在本次范围内。
const KB = '/agent-api/knowledge';
// agent-api 不在 /api 前缀之下，且直接返回裸 JSON（不是 Jeecg 的 {success,result}
// 信封）。与 agentApi.ts 里既有的 agent-api 调用保持同一组选项。
const KB_OPTS = { apiUrl: '', isTransformResponse: false, errorMessageMode: 'none' } as const;

/** 后端返回裸数组，页面按 PageResult 消费，这里补齐分页外壳。 */
function asPage<T>(rows: T[]): PageResult<T> {
  const records = Array.isArray(rows) ? rows : [];
  return { records, total: records.length } as PageResult<T>;
}

/** 把响应体存成文件。 */
function saveBlob(blob: Blob, fileName: string) {
  if (!blob || blob.size === 0) throw new Error('文件下载失败');
  const objectUrl = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.style.display = 'none';
  link.href = objectUrl;
  link.setAttribute('download', fileName);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.URL.revokeObjectURL(objectUrl);
}


export const getKnowledgeList = (params: Recordable) =>
  defHttp
    .get<KnowledgeBase[]>(
      { url: `${KB}/bases`, params: { scope: params?.scope || 'owned' } },
      KB_OPTS,
    )
    .then((rows) => asPage<KnowledgeBase>(rows as any));

export const getKnowledgeDetail = (id: string) =>
  defHttp.get<KnowledgeBase>({ url: `${KB}/bases/${id}` }, KB_OPTS).then((result) => {
    recordAuditEvent({ category: 'knowledge_access', action: '查看知识库', resource: id });
    return result;
  });

export const createKnowledge = (params: Partial<KnowledgeBase>) =>
  defHttp.post<KnowledgeBase>({ url: `${KB}/bases`, params }, KB_OPTS);

export const updateKnowledge = (params: Partial<KnowledgeBase>) =>
  defHttp.put<KnowledgeBase>({ url: `${KB}/bases/${params.id}`, params }, KB_OPTS);

export const setKnowledgeEnabled = (id: string, enabled: boolean) =>
  defHttp.post<KnowledgeBase>({ url: `${KB}/bases/${id}/enabled`, params: { enabled } }, KB_OPTS);

export const deleteKnowledge = (id: string) =>
  defHttp.delete({ url: `${KB}/bases/${id}` }, KB_OPTS);

export const getDocumentList = (params: Recordable) =>
  defHttp
    .get<KnowledgeDocument[]>(
      { url: `${KB}/bases/${params?.knowledgeId}/documents` },
      KB_OPTS,
    )
    .then((rows) => asPage<KnowledgeDocument>(rows as any));

export const uploadKnowledgeDocument = (knowledgeId: string, file: File, options: KnowledgeUploadOptions, onUploadProgress?: (event: ProgressEvent) => void) =>
  defHttp.uploadFile<KnowledgeDocument>(
    { url: `${KB}/bases/${knowledgeId}/documents/upload`, baseURL: '', onUploadProgress },
    {
      file,
      data: {
        knowledgeId,
        ...options,
      },
    },
    { isReturnResponse: true },
  );

export const previewKnowledgeDocument = (knowledgeId: string, file: File, options: KnowledgeUploadOptions) =>
  defHttp.uploadFile<KnowledgeDocumentPreview>(
    { url: Api.documentPreview, baseURL: knowledgeApiBaseUrl },
    {
      file,
      data: {
        knowledgeId,
        ...options,
      },
    },
    { isReturnResponse: true },
  );

export const retryDocument = (id: string) =>
  defHttp.post({ url: `${KB}/documents/retry`, params: { id } }, KB_OPTS);

export const setDocumentEnabled = (id: string, enabled: boolean) =>
  defHttp.post({ url: `${KB}/documents/enabled`, params: { id, enabled } }, KB_OPTS);

export const deleteDocument = (id: string) =>
  defHttp.post({ url: `${KB}/documents/delete`, params: { ids: [id] } }, KB_OPTS);

export const deleteDocuments = (ids: string[]) =>
  defHttp.post({ url: `${KB}/documents/delete`, params: { ids } }, KB_OPTS);

// 不能复用 /@/api/common/api 的 downloadBlobFile：它走 defHttp 默认配置，会把
// 地址拼成 /api/agent-api/...，多一层前缀直接 404。
export const downloadKnowledgeDocument = (id: string, fileName: string) =>
  defHttp
    .get({ url: `${KB}/documents/${id}/download`, responseType: 'blob' }, KB_OPTS)
    .then((blob: any) => saveBlob(blob, fileName));

export const downloadKnowledgeDocumentArchive = (ids: string[]) =>
  defHttp
    .post({ url: `${KB}/documents/download-zip`, params: { ids }, responseType: 'blob' }, KB_OPTS)
    .then((blob: any) => saveBlob(blob, '知识库原始文档.zip'));

export const getChunkList = (params: Recordable) =>
  defHttp.get<PageResult<KnowledgeChunk>>({ url: Api.chunkList, params }, { errorMessageMode: 'none' });

export const updateChunk = (params: Pick<KnowledgeChunk, 'id' | 'content' | 'contentWithImages' | 'images'>) =>
  defHttp.put<KnowledgeChunk>({ url: Api.chunkEdit, params });

export const uploadKnowledgeChunkImage = (id: string, file: File) =>
  defHttp.uploadFile<KnowledgePreviewImage>(
    { url: Api.chunkImageUpload, baseURL: knowledgeApiBaseUrl },
    { file, data: { id } },
    { isReturnResponse: true },
  );

export const setChunkEnabled = (id: string, enabled: boolean) =>
  defHttp.post(
    { url: enabled ? Api.chunkEnable : Api.chunkDisable, params: { id } },
    { joinParamsToUrl: true },
  );

export const deleteChunk = (id: string) =>
  defHttp.delete({ url: Api.chunkDelete, params: { id } }, { joinParamsToUrl: true });

export const testRetrieval = (params: Recordable) =>
  defHttp.post<RetrievalResponse>({ url: Api.retrievalManualTest, params });

export const getKnowledgeAcl = (knowledgeId: string) =>
  defHttp.get<KnowledgeAcl[]>({ url: `${KB}/bases/${knowledgeId}/acl` }, KB_OPTS);

export const saveKnowledgeAcl = (knowledgeId: string, items: KnowledgeAcl[]) =>
  defHttp.post({ url: `${KB}/bases/${knowledgeId}/acl`, params: { acls: items } }, KB_OPTS);

export const getManagedKnowledgeList = (params: Recordable) =>
  defHttp.get<PageResult<KnowledgeBase>>({ url: managementUrl(Api.baseList), params }, { errorMessageMode: 'none' });

export const getManagedKnowledgeDetail = (id: string) =>
  defHttp.get<KnowledgeBase>({ url: managementUrl(Api.baseDetail), params: { id } }).then((result) => {
    recordAuditEvent({ category: 'knowledge_access', action: '查看知识库（管理）', resource: id });
    return result;
  });

export const createManagedKnowledge = (params: Partial<KnowledgeBase>) =>
  defHttp.post<KnowledgeBase>({ url: managementUrl(Api.baseAdd), params });

export const updateManagedKnowledge = (params: Partial<KnowledgeBase>) =>
  defHttp.put<KnowledgeBase>({ url: managementUrl(Api.baseEdit), params });

export const setManagedKnowledgeEnabled = (id: string, enabled: boolean) =>
  defHttp.post<KnowledgeBase>({ url: managementUrl(enabled ? Api.baseEnable : Api.baseDisable), params: { id } }, { joinParamsToUrl: true });

export const deleteManagedKnowledge = (id: string) =>
  defHttp.delete({ url: managementUrl(Api.baseDelete), params: { id } }, { joinParamsToUrl: true });

export const getManagedDocumentList = (params: Recordable) =>
  defHttp.get<PageResult<KnowledgeDocument>>({ url: managementUrl(Api.documentList), params }, { errorMessageMode: 'none' });

export const uploadManagedKnowledgeDocument = (knowledgeId: string, file: File, options: KnowledgeUploadOptions, onUploadProgress?: (event: ProgressEvent) => void) =>
  defHttp.uploadFile<KnowledgeDocument>(
    { url: managementUrl(Api.documentUpload), baseURL: knowledgeApiBaseUrl, onUploadProgress },
    { file, data: { knowledgeId, ...options } },
    { isReturnResponse: true },
  );

export const previewManagedKnowledgeDocument = (knowledgeId: string, file: File, options: KnowledgeUploadOptions) =>
  defHttp.uploadFile<KnowledgeDocumentPreview>(
    { url: managementUrl(Api.documentPreview), baseURL: knowledgeApiBaseUrl },
    { file, data: { knowledgeId, ...options } },
    { isReturnResponse: true },
  );

export const retryManagedDocument = (id: string) =>
  defHttp.post({ url: managementUrl(Api.documentRetry), params: { id } }, { joinParamsToUrl: true });

export const setManagedDocumentEnabled = (id: string, enabled: boolean) =>
  defHttp.post({ url: managementUrl(enabled ? Api.documentEnable : Api.documentDisable), params: { id } }, { joinParamsToUrl: true });

export const deleteManagedDocument = (id: string) =>
  defHttp.delete({ url: managementUrl(Api.documentDelete), params: { id } }, { joinParamsToUrl: true });

export const deleteManagedDocuments = (ids: string[]) =>
  defHttp.delete({ url: managementUrl(Api.documentBatchDelete), data: ids });

export const downloadManagedKnowledgeDocument = (id: string, fileName: string) => {
  recordAuditEvent({ category: 'export', action: '导出知识库文档（管理）', resource: id, detail: fileName });
  return downloadBlobFile(managementUrl(Api.documentDownload), fileName, { id });
};

export const downloadManagedKnowledgeDocumentArchive = (ids: string[]) => {
  recordAuditEvent({ category: 'export', action: '批量导出知识库文档（管理）', resource: ids.join(',') });
  return downloadBlobFile(managementUrl(Api.documentDownloadZip), '知识库原始文档.zip', { ids: ids.join(',') });
};

export const getManagedChunkList = (params: Recordable) =>
  defHttp.get<PageResult<KnowledgeChunk>>({ url: managementUrl(Api.chunkList), params }, { errorMessageMode: 'none' });

export const updateManagedChunk = (params: Pick<KnowledgeChunk, 'id' | 'content' | 'contentWithImages' | 'images'>) =>
  defHttp.put<KnowledgeChunk>({ url: managementUrl(Api.chunkEdit), params });

export const uploadManagedKnowledgeChunkImage = (id: string, file: File) =>
  defHttp.uploadFile<KnowledgePreviewImage>(
    { url: managementUrl(Api.chunkImageUpload), baseURL: knowledgeApiBaseUrl },
    { file, data: { id } },
    { isReturnResponse: true },
  );

export const setManagedChunkEnabled = (id: string, enabled: boolean) =>
  defHttp.post({ url: managementUrl(enabled ? Api.chunkEnable : Api.chunkDisable), params: { id } }, { joinParamsToUrl: true });

export const deleteManagedChunk = (id: string) =>
  defHttp.delete({ url: managementUrl(Api.chunkDelete), params: { id } }, { joinParamsToUrl: true });

export const testManagedRetrieval = (params: Recordable) =>
  defHttp.post<RetrievalResponse>({ url: managementUrl(Api.retrievalTest), params });

export const getManagedKnowledgeAcl = (knowledgeId: string) =>
  defHttp.get<KnowledgeAcl[]>({ url: managementUrl(Api.aclList), params: { knowledgeId } });

export const saveManagedKnowledgeAcl = (knowledgeId: string, items: KnowledgeAcl[]) =>
  defHttp.put({ url: managementUrl(Api.aclSave), params: { knowledgeId, items } });

export const getManagedKnowledgeAnalyticsOverview = (params: KnowledgeAnalyticsRange) =>
  defHttp.get<KnowledgeAnalyticsOverview>({ url: `${ManagementApiPrefix}/analytics/overview`, params }, { errorMessageMode: 'none' });

export const getManagedKnowledgeAnalyticsBases = (params: KnowledgeAnalyticsRange & { limit?: number }) =>
  defHttp.get<KnowledgeAnalyticsRanking[]>({ url: `${ManagementApiPrefix}/analytics/knowledge-bases`, params }, { errorMessageMode: 'none' });

export const getManagedKnowledgeAnalyticsDocuments = (params: KnowledgeAnalyticsRange & { knowledgeId?: string; limit?: number }) =>
  defHttp.get<KnowledgeAnalyticsRanking[]>({ url: `${ManagementApiPrefix}/analytics/documents`, params }, { errorMessageMode: 'none' });

export const getManagedKnowledgeBaseAnalytics = (id: string, params: KnowledgeAnalyticsRange) =>
  defHttp.get<KnowledgeAnalyticsOverview>({
    url: `${ManagementApiPrefix}/analytics/knowledge-bases/${encodeURIComponent(id)}`,
    params,
  }, { errorMessageMode: 'none' });

export const getOwnedKnowledgeBaseAnalytics = (id: string, params: KnowledgeAnalyticsRange) =>
  defHttp.get<KnowledgeAnalyticsOverview>({
    url: `/ai/knowledge/base/${encodeURIComponent(id)}/analytics`,
    params,
  }, { errorMessageMode: 'none' });
