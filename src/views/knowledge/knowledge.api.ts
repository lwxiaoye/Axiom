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

export const getKnowledgeList = (params: Recordable) =>
  defHttp.get<PageResult<KnowledgeBase>>({ url: Api.baseList, params }, { errorMessageMode: 'none' });

export const getKnowledgeDetail = (id: string) =>
  defHttp.get<KnowledgeBase>({ url: Api.baseDetail, params: { id } }).then((result) => {
    recordAuditEvent({ category: 'knowledge_access', action: '查看知识库', resource: id });
    return result;
  });

export const createKnowledge = (params: Partial<KnowledgeBase>) =>
  defHttp.post<KnowledgeBase>({ url: Api.baseAdd, params });

export const updateKnowledge = (params: Partial<KnowledgeBase>) =>
  defHttp.put<KnowledgeBase>({ url: Api.baseEdit, params });

export const setKnowledgeEnabled = (id: string, enabled: boolean) =>
  defHttp.post<KnowledgeBase>({ url: enabled ? Api.baseEnable : Api.baseDisable, params: { id } }, { joinParamsToUrl: true });

export const deleteKnowledge = (id: string) =>
  defHttp.delete({ url: Api.baseDelete, params: { id } }, { joinParamsToUrl: true });

export const getDocumentList = (params: Recordable) =>
  defHttp.get<PageResult<KnowledgeDocument>>({ url: Api.documentList, params }, { errorMessageMode: 'none' });

export const uploadKnowledgeDocument = (knowledgeId: string, file: File, options: KnowledgeUploadOptions, onUploadProgress?: (event: ProgressEvent) => void) =>
  defHttp.uploadFile<KnowledgeDocument>(
    { url: Api.documentUpload, baseURL: knowledgeApiBaseUrl, onUploadProgress },
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
  defHttp.post({ url: Api.documentRetry, params: { id } }, { joinParamsToUrl: true });

export const setDocumentEnabled = (id: string, enabled: boolean) =>
  defHttp.post(
    { url: enabled ? Api.documentEnable : Api.documentDisable, params: { id } },
    { joinParamsToUrl: true },
  );

export const deleteDocument = (id: string) =>
  defHttp.delete({ url: Api.documentDelete, params: { id } }, { joinParamsToUrl: true });

export const deleteDocuments = (ids: string[]) =>
  defHttp.delete({ url: Api.documentBatchDelete, data: ids });

export const downloadKnowledgeDocument = (id: string, fileName: string) => {
  recordAuditEvent({ category: 'export', action: '导出知识库文档', resource: id, detail: fileName });
  return downloadBlobFile(Api.documentDownload, fileName, { id });
};

export const downloadKnowledgeDocumentArchive = (ids: string[]) => {
  recordAuditEvent({ category: 'export', action: '批量导出知识库文档', resource: ids.join(',') });
  return downloadBlobFile(Api.documentDownloadZip, '知识库原始文档.zip', { ids: ids.join(',') });
};

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
  defHttp.get<KnowledgeAcl[]>({ url: Api.aclList, params: { knowledgeId } });

export const saveKnowledgeAcl = (knowledgeId: string, items: KnowledgeAcl[]) =>
  defHttp.put({ url: Api.aclSave, params: { knowledgeId, items } });

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
