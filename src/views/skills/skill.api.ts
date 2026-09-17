import { useGlobSetting } from '/@/hooks/setting';
import { defHttp } from '/@/utils/http/axios';

enum Api {
  list = '/ai/skill/list',
  queryById = '/ai/skill/queryById',
  upload = '/ai/skill/upload',
  enable = '/ai/skill/enable',
  disable = '/ai/skill/disable',
  delete = '/ai/skill/delete',
  readme = '/ai/skill/readme',
  files = '/ai/skill/files',
  file = '/ai/skill/file',
  uploadFile = '/ai/skill/file/upload',
  logs = '/ai/skill/log/list',
}

export const list = (params) => defHttp.get({ url: Api.list, params });

export const queryById = (params) => defHttp.get({ url: Api.queryById, params });

const apiBaseUrl = useGlobSetting().apiUrl;

export const uploadSkill = (params) => defHttp.uploadFile({ url: Api.upload, baseURL: apiBaseUrl }, params);

export const enableSkill = (params) => defHttp.post({ url: Api.enable, params }, { joinParamsToUrl: true });

export const disableSkill = (params) => defHttp.post({ url: Api.disable, params }, { joinParamsToUrl: true });

export const getReadme = (params) => defHttp.get({ url: Api.readme, params });

export const getFiles = (params) => defHttp.get({ url: Api.files, params });

export const getSkillFile = (params) => defHttp.get({ url: Api.file, params }, { errorMessageMode: 'none' });

export const saveSkillFile = (data) => defHttp.put({ url: Api.file, data });

export const createSkillFile = (data) => defHttp.post({ url: Api.file, data });

export const uploadSkillFile = (params) => defHttp.uploadFile({ url: Api.uploadFile, baseURL: apiBaseUrl }, params);

export const listLogs = (params) => defHttp.get({ url: Api.logs, params });

export const deleteSkill = (params) => defHttp.delete({ url: Api.delete, params }, { joinParamsToUrl: true });
