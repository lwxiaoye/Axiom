import { defHttp } from '/@/utils/http/axios';
import { Modal } from 'ant-design-vue';

const QUIET_ERROR = { errorMessageMode: 'none' } as const;

/**
 * GPUStack 代理 API。
 *
 * 后端代理返回结构：Result<PageResult>，defHttp 解包 result 后得到：
 *   { items: [...], pagination: { page, perPage, total, totalPage } }
 *
 * 而 BasicTable 默认期望：
 *   - 请求参数 pageNo / pageSize
 *   - 响应字段 records / total
 *
 * 因此列表接口统一通过 {@link pageAdapter} 做双向适配。
 */

const Api = {
  // 模型
  modelList: '/gpustack/model/list',
  modelDetail: '/gpustack/model',
  modelDeploy: '/gpustack/model/deploy',
  modelEdit: '/gpustack/model/edit',
  modelScale: '/gpustack/model/scale',
  modelDelete: '/gpustack/model',
  modelInstances: '/gpustack/model/instances',
  modelInstanceByModel: '/gpustack/model', // /gpustack/model/{id}/instances
  modelOptions: '/gpustack/model/options',
  instanceLogs: '/gpustack/model/instance',
  // 资源监控
  workerList: '/gpustack/worker/list',
  workerDetail: '/gpustack/worker',
  workerDashboard: '/gpustack/worker',
  gpuList: '/gpustack/gpu/list',
  gpuDetail: '/gpustack/gpu',
  workerPoolList: '/gpustack/worker-pool/list',
  // 总览
  overview: '/gpustack/overview',
  overviewUsage: '/gpustack/overview/usage',
  overviewHealth: '/gpustack/overview/health',
  syncState: '/gpustack/overview/sync-state',
} as const;

/**
 * 列表分页适配器：BasicTable(pageNo/pageSize, records/total) <-> GPUStack(page/perPage, items/pagination.total)
 * 直接返回，供 useListPage 的 api 使用。
 */
function pageAdapter(url: string, requestOptions?: Record<string, unknown>) {
  return (params: any) => {
    const query: any = { ...params };
    // 入参：pageNo/pageSize -> page/perPage
    if (query.pageNo != null) {
      query.page = query.pageNo;
      delete query.pageNo;
    }
    if (query.pageSize != null) {
      query.perPage = query.pageSize;
      delete query.pageSize;
    }
    return defHttp.get({ url, params: query }, requestOptions).then((res: any) => {
      // 出参：{items, pagination:{total}} -> {records, total}
      const items = res?.items || [];
      const total = res?.pagination?.total ?? items.length;
      return { records: items, total };
    });
  };
}

// ============================== 模型部署 ==============================

export const modelList = pageAdapter(Api.modelList);

export const modelDetail = (id) => defHttp.get({ url: `${Api.modelDetail}/${id}` });

export const modelInstances = (id) =>
  defHttp.get({ url: `${Api.modelInstanceByModel}/${id}/instances` }, QUIET_ERROR).then((res: any) => res?.items || []);

export const allInstances = (params?: any) =>
  defHttp.get({ url: Api.modelInstances, params }).then((res: any) => res?.items || []);

export const modelOptions = () => defHttp.get({ url: Api.modelOptions });

export const instanceLogs = (id, params?: { tail?: number; previous?: boolean; workerId?: number; containerName?: string }) =>
  defHttp.get({ url: `${Api.instanceLogs}/${id}/logs`, params }, QUIET_ERROR);

export const modelDeploy = (params) => defHttp.post({ url: Api.modelDeploy, params });

export const modelEdit = (params) => defHttp.put({ url: Api.modelEdit, params });

export const modelScale = (id, replicas) =>
  defHttp.put({ url: Api.modelScale, params: { id, replicas } });

export const modelDelete = (record, handleSuccess) => {
  Modal.confirm({
    title: '确认删除',
    content: `是否删除模型"${record.name}"？该操作将从 GPUStack 卸载模型及其所有实例。`,
    okText: '确认删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: () =>
      defHttp.delete({ url: `${Api.modelDelete}/${record.id}` }).then(() => {
        handleSuccess();
      }),
  });
};

// ============================== 资源监控 ==============================

export const workerList = pageAdapter(Api.workerList);
export const workerDetail = (id) => defHttp.get({ url: `${Api.workerDetail}/${id}` }, QUIET_ERROR);
export const workerDashboard = (id) =>
  defHttp.get({ url: `${Api.workerDashboard}/${id}/dashboard` });
export const gpuList = pageAdapter(Api.gpuList);
export const allGpuDevices = (params?: any) =>
  defHttp.get({ url: Api.gpuList, params }, QUIET_ERROR).then((res: any) => res?.items || []);
export const gpuDetail = (id) => defHttp.get({ url: `${Api.gpuDetail}/${id}` });
export const workerPoolList = (params?) =>
  defHttp.get({ url: Api.workerPoolList, params }).then((res: any) => res?.items || []);

// ============================== 集群总览 ==============================

export const overview = () => defHttp.get({ url: Api.overview }, QUIET_ERROR);
export const overviewUsage = () => defHttp.get({ url: Api.overviewUsage }, QUIET_ERROR);
export const overviewHealth = () => defHttp.get({ url: Api.overviewHealth }, QUIET_ERROR);
export const syncState = () => defHttp.get({ url: Api.syncState }, QUIET_ERROR);

// ============================== 模型库(GPUStack model-sets) ==============================

const LibraryApi = {
  models: '/gpustack/library/models',
  modelScopeSearch: '/gpustack/library/modelscope/search',
  modelScopeDetail: '/gpustack/library/modelscope/detail',
  specs: '/gpustack/library',
  deploy: '/gpustack/library',
  evaluation: '/gpustack/model-evaluation',
} as const;

/**
 * 模型库目录（GPUStack model-sets，卡片展示）。
 * 走标准分页适配（pageNo/pageSize ↔ page/perPage，records/total）。
 */
export const libraryModels = pageAdapter(LibraryApi.models, QUIET_ERROR);

export const modelScopeSearch = (params) => defHttp.post({ url: LibraryApi.modelScopeSearch, params });

function buildModelScopeDetailUrl(repo: string) {
  const path = repo
    .split('/')
    .map((item) => encodeURIComponent(item))
    .join('/');
  return `https://modelscope.cn/api/v1/models/${path}`;
}

export const modelScopeModelDetail = async (repo: string) => {
  const normalizedRepo = String(repo || '').replace(/^\/+/, '');
  const proxyUrl = buildModelScopeDetailUrl(normalizedRepo);
  return defHttp.get({ url: LibraryApi.modelScopeDetail, params: { url: proxyUrl } }, QUIET_ERROR);
};

/** 取模型部署规格模板（一键部署预览用） */
export const modelSpecs = (id) =>
  defHttp.get({ url: `${LibraryApi.specs}/${id}/specs` }, QUIET_ERROR);

/**
 * 一键部署：基于模型库预制规格直接提交部署。
 * @param id 模型库目录 id
 * @param overrides 可选覆盖字段，如 { replicas, name }
 */
export const quickDeploy = (id, overrides?) =>
  defHttp.post({ url: `${LibraryApi.deploy}/${id}/deploy`, params: overrides || {} });

/**
 * 兼容性评估：检测给定规格在当前机器/集群是否可部署。
 * 约定（对齐 GPUStack POST /model-evaluations）：
 *   请求 { cluster_id?, model_specs: ModelSpec[] }
 *   响应 { results: [{ compatible, compatibility_messages, ... }] }（与 model_specs 位置对应）
 * 失败时由调用方降级，不在此抛错刷屏。
 */
export const modelEvaluation = (clusterId: number | null | undefined, modelSpecs: any[]) =>
  defHttp.post({
    url: LibraryApi.evaluation,
    params: { cluster_id: clusterId ?? null, model_specs: modelSpecs },
  }, QUIET_ERROR);
