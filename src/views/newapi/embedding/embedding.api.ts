import { defHttp } from '/@/utils/http/axios';

enum Api {
  config = '/agent-api/embedding-config',
  test = '/agent-api/embedding-config/test',
  reindex = '/agent-api/embedding-config/reindex',
}

export const getConfig = () => defHttp.get({ url: Api.config }, { isTransformResponse: false });

export const saveConfig = (params) => defHttp.put({ url: Api.config, data: params }, { isTransformResponse: false });

export const testConfig = (params) => defHttp.post({ url: Api.test, data: params }, { isTransformResponse: false });

export const startReindex = () => defHttp.post({ url: Api.reindex }, { isTransformResponse: false });

export const getReindexStatus = (jobId) => defHttp.get({ url: `${Api.reindex}/${jobId}` }, { isTransformResponse: false });
