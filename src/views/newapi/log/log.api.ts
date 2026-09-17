import { defHttp } from '/@/utils/http/axios';

enum Api {
  list = '/newapi/api/log/',
}

export const list = (params) => {
  return defHttp.get({ url: Api.list, params });
};