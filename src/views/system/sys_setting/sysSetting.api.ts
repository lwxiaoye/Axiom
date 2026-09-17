import { defHttp } from '/@/utils/http/axios';

export interface ModelSetting {
  model: string;
  base_url: string;
  api_key?: string;
  api_key_masked?: string;
  test_status?: 'success' | 'failed';
  test_message?: string;
}

export interface EmbeddingSetting extends ModelSetting {
  dimension: number | null;
}

export interface SystemModelSetting {
  llm: ModelSetting;
  embedding: EmbeddingSetting;
}

const Api = {
  settings: '/sys/sysSetting',
  test: '/sys/sysSetting/test',
};

export const getSystemModelSetting = () =>
  defHttp.get<SystemModelSetting>({ url: Api.settings });

export const saveSystemModelSetting = (data: SystemModelSetting) =>
  defHttp.put({ url: Api.settings, data });

export const testSystemModel = (type: 'llm' | 'embedding', data: ModelSetting) =>
  defHttp.post<{ status: string; message: string; dimension?: number }>(
    { url: Api.test + '/' + type, data },
  );

