import { defHttp } from '/@/utils/http/axios';
import type { ApisixOverview, ApisixRoute, ApisixRouteListResult } from './apisix.types';

enum Api {
  routes = '/sys/apisix/routes',
  overview = '/sys/apisix/overview',
  upstreams = '/sys/apisix/upstreams/options',
  validate = '/sys/apisix/routes/validate',
}

export const getApisixRouteList = (params: Recordable) =>
  defHttp.get<ApisixRouteListResult>({ url: Api.routes, params });

export const getApisixOverview = () =>
  defHttp.get<ApisixOverview>({ url: Api.overview });

export const getApisixUpstreamOptions = () =>
  defHttp.get<Array<{ label: string; value: string }>>({ url: Api.upstreams });

export const getApisixRoute = (id: string) =>
  defHttp.get<ApisixRoute>({ url: `${Api.routes}/${id}` });

export const saveApisixRoute = (route: ApisixRoute) => {
  if (route.id) {
    return defHttp.put<ApisixRoute>({ url: `${Api.routes}/${route.id}`, params: route });
  }
  return defHttp.post<ApisixRoute>({ url: Api.routes, params: route });
};

export const validateApisixRoute = (route: ApisixRoute) =>
  defHttp.post<{ valid: boolean; messages: string[] }>({ url: Api.validate, params: route });

export const changeApisixRouteStatus = (id: string, status: 0 | 1) =>
  defHttp.request({ method: 'PATCH', url: `${Api.routes}/${id}/status`, params: { status } });

export const deleteApisixRoute = (id: string) =>
  defHttp.delete({ url: `${Api.routes}/${id}` });

export const copyApisixRoute = (id: string) =>
  defHttp.post<ApisixRoute>({ url: `${Api.routes}/${id}/copy` });