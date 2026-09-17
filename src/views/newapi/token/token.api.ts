import { defHttp } from '/@/utils/http/axios';

enum Api {
  list = '/newapi/api/token/',
  search = '/newapi/api/token/search',
  users = '/newapi/api/users/',
  updateStatus = '/newapi/api/token/?status_only=true',
  listCanUseModel = '/newapi/api/user/models',
  syncUserKeys = '/newapi/api/token/sync-user-keys',
}

export const list = (params) => {
  return defHttp.get({ url: params.keyword||params.token ? Api.search : Api.list, params });
};

export const create = (params) => {
  return defHttp.post({ url: Api.list, params });
};

export const update = (params) => {
  return defHttp.put({ url: Api.list, params });
};

export const updateStatus = (params) => {
  return defHttp.put({ url: Api.updateStatus, params });
};
export const deleteToken = (params) => {
  return defHttp.delete({ url: `${Api.list}${params.id}` });
};

export const getById = (id) => {
  return defHttp.get({ url: `${Api.list}${id}` });
};

export const getTokenKey = (id) => {
  return defHttp.post({ url: `/newapi/api/token/${id}/key` });
};

export const getUsers = (params) => {
  return defHttp.get({ url: Api.users, params });
};

export const listCanUseModel = () => {
  return defHttp.get({ url: Api.listCanUseModel });
};

export const syncUserKeys = () => {
  return defHttp.post({ url: Api.syncUserKeys });
};
