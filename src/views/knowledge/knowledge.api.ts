import { defHttp } from '/@/utils/http/axios';
import { recordAuditEvent } from '/@/api/audit/audit.api';
import { useGlobSetting } from '/@/hooks/setting';
import type {
  KnowledgeAcl,
  KnowledgeAnalyticsOverview,
  KnowledgeAnalyticsRange,
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
  documentList: '/ai/knowledge/document/list',
  documentUpload: '/ai/knowledge/document/upload',
  documentPreview: '/ai/knowledge/document/preview',
  chunkList: '/ai/knowledge/chunk/list',
  chunkEdit: '/ai/knowledge/chunk/edit',
  chunkImageUpload: '/ai/knowledge/chunk/image/upload',
  chunkEnable: '/ai/knowledge/chunk/enable',
  chunkDisable: '/ai/knowledge/chunk/disable',
  chunkDelete: '/ai/knowledge/chunk/delete',
  retrievalTest: '/ai/knowledge/retrieval/test',
};

// 后台知识库内容管理与用户侧 ACL 接口必须分开，不能依赖前端页面来源决定授权范围。
// 后台管理页（KnowledgeList/KnowledgeDetail/analytics）已随 Java 下线删除；下面剩余的
// Managed* 函数只被 components/ 下共用面板的 `props.management` 分支引用，当前没有任何
// 调用方把 management 置真。
const ManagementApiPrefix = '/ai/knowledge/admin';
const managementUrl = (url: string) => `${ManagementApiPrefix}${url.replace('/ai/knowledge', '')}`;

// uploadFile bypasses the normal URL-prefix hook, so use the same relative API base as ordinary requests.
const knowledgeApiBaseUrl = useGlobSetting().apiUrl;

// ── 知识库迁移至 agent-api ───────────────────────────────────────────────
// 原 /ai/knowledge/* 由 JeecgBoot(Java) 提供，Java 下线后 auth-api 只剩一个统一
// 返回 503 的桩，页面表现为永远转圈。知识库现由 agent-api 自持（它握有 Qdrant 与
// Embedding 配置）。以下重写「我的知识库」实际用到的接口，保持原有函数签名与返回
// 结构，页面无需改动。
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

// 上传向导第三步的「预览分段」：只切不入库。options 里的 splitStrategy / chunkSize 等是老
// Java 的切分参数，agent-api 入库时并不读（切法固定），照样带上只是保持签名不变。
export const previewKnowledgeDocument = (knowledgeId: string, file: File, options: KnowledgeUploadOptions) =>
  defHttp.uploadFile<KnowledgeDocumentPreview>(
    { url: `${KB}/bases/${knowledgeId}/documents/preview`, baseURL: '' },
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

// KB_OPTS 关掉了自动报错提示，agent-api 的错误在 response.data.detail 里；
// 分段面板/编辑器拿这个把原因显示出来，否则失败就是静默的。
export function knowledgeErrorMessage(error: unknown, fallback: string): string {
  const detail = (error as any)?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  const message = (error as any)?.message;
  return typeof message === 'string' && message.trim() ? message : fallback;
}

// 分段：正本在 agent-api 的 agent_knowledge_chunk，接口直接返回 {records,total}。
// 查询参数名（knowledgeId / documentId / keyword / pageNo / pageSize）服务端原样接收。
export const getChunkList = (params: Recordable) =>
  defHttp.get<PageResult<KnowledgeChunk>>({ url: `${KB}/chunks`, params }, KB_OPTS);

// 只有 content 会被保存：agent-api 的分段没有图片存储，contentWithImages / images 这两个
// 老 Java 的图文字段服务端忽略；签名保留是为了让编辑器组件在用户侧/管理侧共用一套调用。
export const updateChunk = (params: Pick<KnowledgeChunk, 'id' | 'content' | 'contentWithImages' | 'images'>) =>
  defHttp.put<KnowledgeChunk>({ url: `${KB}/chunks/${params.id}`, params: { content: params.content } }, KB_OPTS);

// 用户侧分段图片上传没有对应的 agent-api 接口（分段表没有图片列，也没有跨用户可读的
// 图片存储），编辑器在用户侧已把插图入口藏起来；这里保留函数只为防止某条路径漏网时
// 报一个看得懂的错，而不是打到已下线的 Java 地址得到 503。
export const uploadKnowledgeChunkImage = (_id: string, _file: File): Promise<KnowledgePreviewImage> =>
  Promise.reject(new Error('当前版本的分段暂不支持插入图片'));

export const setChunkEnabled = (id: string, enabled: boolean) =>
  defHttp.post({ url: `${KB}/chunks/${id}/enabled`, params: { enabled } }, KB_OPTS);

// 切片正本表（agent_knowledge_chunk）上线前入库的文档只在 Qdrant 里有切片：卡片写着
// 「N 个分段」、「分段」面板却是空的。这个接口从 Qdrant 按 payload 回填正本表（幂等），
// 返回 { rebuilt: 新写入行数 }。需要编辑权限。
export const rebuildKnowledgeChunks = (knowledgeId: string) =>
  defHttp.post<{ rebuilt: number }>({ url: `${KB}/bases/${knowledgeId}/chunks/rebuild` }, KB_OPTS);

export const deleteChunk = (id: string) =>
  defHttp.delete({ url: `${KB}/chunks/${id}` }, KB_OPTS);

// 检索测试与真实检索是同一个接口：测试面板里试出来的检索方式和权重，
// 就是对话里会用到的那套，不另做一条只为演示的路径。
export const testRetrieval = (params: Recordable) =>
  defHttp.post<RetrievalResponse>({ url: `${KB}/retrieval`, params }, KB_OPTS);

export const getKnowledgeAcl = (knowledgeId: string) =>
  defHttp.get<KnowledgeAcl[]>({ url: `${KB}/bases/${knowledgeId}/acl` }, KB_OPTS);

export const saveKnowledgeAcl = (knowledgeId: string, items: KnowledgeAcl[]) =>
  defHttp.post({ url: `${KB}/bases/${knowledgeId}/acl`, params: { acls: items } }, KB_OPTS);

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

export const getManagedKnowledgeAnalyticsOverview = (params: KnowledgeAnalyticsRange) =>
  defHttp.get<KnowledgeAnalyticsOverview>({ url: `${ManagementApiPrefix}/analytics/overview`, params }, { errorMessageMode: 'none' });

export const getManagedKnowledgeBaseAnalytics = (id: string, params: KnowledgeAnalyticsRange) =>
  defHttp.get<KnowledgeAnalyticsOverview>({
    url: `${ManagementApiPrefix}/analytics/knowledge-bases/${encodeURIComponent(id)}`,
    params,
  }, { errorMessageMode: 'none' });

// 用户侧单库运营统计：数据源是 agent-api 的检索日志（agent_knowledge_retrieval_log），
// 原 /ai/knowledge/base/{id}/analytics 归已下线的 Java，面板一直报「统计迁移」。
// 参数仍是 from / to（YYYY-MM-DD），服务端按平台业务时区分桶。
export const getOwnedKnowledgeBaseAnalytics = (id: string, params: KnowledgeAnalyticsRange) =>
  defHttp.get<KnowledgeAnalyticsOverview>({
    url: `${KB}/bases/${encodeURIComponent(id)}/analytics`,
    params,
  }, KB_OPTS);
