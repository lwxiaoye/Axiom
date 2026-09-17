import { defHttp } from '/@/utils/http/axios';
import { requestAgentApi } from '/@/views/peopleCenter/agentApi';

enum Api {
  list = '/sys/log/list',
  exportXls = '/sys/log/exportXls',
}

/** 统一审计事件来自 Agent API；该接口会自行携带当前登录用户的访问令牌。 */
export const getAuditLogList = (params: Record<string, unknown>) => {
  const query = new URLSearchParams();
  Object.entries(params || {}).forEach(([key, value]) => {
    if (key === 'logType' || key === 'fieldTime') return;
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value));
  });
  const suffix = query.toString();
  return requestAgentApi(`/audit/events${suffix ? `?${suffix}` : ''}`, { method: 'GET' });
};

/**
 * 查询日志列表。表格切换 API 的配置在当前轮次不会立即生效时，仍须阻止
 * `logType=audit` 落到 Java 的整型日志接口。
 */
export const getLogList = (params: Record<string, unknown>) => {
  if (String(params?.logType || '') === 'audit') {
    return getAuditLogList(params);
  }
  return defHttp.get({ url: Api.list, params });
};


/**
 * 导出api
 * @param params
 */
export const getExportUrl = Api.exportXls;
