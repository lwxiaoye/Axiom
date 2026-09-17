import { defHttp } from '/@/utils/http/axios';
import { Modal } from 'ant-design-vue';

const Api = {
  list: '/newapi/api/channel/search',
  getById: '/newapi/api/channel/',
  create: '/newapi/api/channel/',
  update: '/newapi/api/channel/',
  delete: '/newapi/api/channel/',
  models: '/newapi/api/models',
  test: '/newapi/api/channel/test',
  updateStatus: '/newapi/api/channel/',
} as const;

export interface ChannelTestOptions {
  model?: string;
  endpoint_type?: 'openai-response' | 'openai-response-compact';
  stream?: boolean;
}

export const list = (params) => {
  return defHttp.get({ url: Api.list, params });
};

export const listModels = () => {
  return defHttp.get({ url: Api.models });
};

export const getById = (id) => {
  return defHttp.get({ url: `${Api.getById}${id}` });
};

export const test = (id, params?: ChannelTestOptions) => {
  return defHttp.get({ url: `${Api.test}/${id}`, params });
};

export const create = (params) => {
  return defHttp.post({ url: Api.create, params });
};

export const update = (params) => {
  return defHttp.put({ url: Api.update, params });
};

export const updateStatus = (params) => {
  return defHttp.put({ url: Api.updateStatus, params });
};

export const deleteChannel = (params, handleSuccess) => {
  Modal.confirm({
    title: '确认删除',
    content: `是否删除名称为"${params.name}"的模型？`,
    okText: '确认',
    cancelText: '取消',
    onOk: () => {
      return defHttp.delete({ url: `${Api.delete}${params.id}` }, { joinParamsToUrl: true }).then(() => {
        handleSuccess();
      });
    },
  });
};

