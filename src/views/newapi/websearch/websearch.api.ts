import { defHttp } from '/@/utils/http/axios';

enum Api {
  config = '/agent-api/platform-config/web-search',
  test = '/agent-api/platform-config/web-search/test',
}

// apiUrl: '' —— 目标是 Python agent-api（nginx /agent-api 代理），不能让 defHttp
// 拼默认 /api 前缀（否则打到 Java：No static resource agent-api/...）
const RAW = { isTransformResponse: false, apiUrl: '' } as const;

export const getWebSearchConfig = () => defHttp.get({ url: Api.config }, RAW);

export const saveWebSearchConfig = (params) => defHttp.put({ url: Api.config, data: params }, RAW);

export const testWebSearchConfig = (params) => defHttp.post({ url: Api.test, data: params, timeout: 95_000 }, RAW);
