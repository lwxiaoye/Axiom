import { defHttp } from '/@/utils/http/axios';

enum Api {
  config = '/agent-api/platform-config/ocr',
  test = '/agent-api/platform-config/ocr/test',
}

// apiUrl: '' —— 目标是 Python agent-api（nginx /agent-api 代理），不能让 defHttp
// 拼默认 /api 前缀（否则打到 Java：No static resource agent-api/...）
const RAW = { isTransformResponse: false, apiUrl: '' } as const;

export const getOcrConfig = () => defHttp.get({ url: Api.config }, RAW);

export const saveOcrConfig = (params) => defHttp.put({ url: Api.config, data: params }, RAW);

export const testOcrConfig = (params) => defHttp.post({ url: Api.test, data: params }, RAW);
