import { defHttp } from '/src/utils/http/axios';
import { useMessage } from '/src/hooks/web/useMessage';

const { createConfirm } = useMessage();

enum Api {
  list = '/app/appInfo/list',
  getBriefList = '/app/appInfo/list/brief',
  queryAppRole = '/app/appInfo/queryAppRole',
  queryAppDept = '/app/appInfo/queryAppDept',
  save = '/app/appInfo/save',
  updateStatus = '/app/appInfo/status',
  deleteOne = '/app/appInfo/delete',
  deleteBatch = '/app/appInfo/deleteBatch',
  importExcel = '/app/appInfo/importExcel',
  exportXls = '/app/appInfo/exportXls',
  myAppList = '/app/appInfo/my/all/list',
}
/**
 * 导出api
 * @param params
 */
export const getExportUrl = Api.exportXls;
/**
 * 导入api
 */
export const getImportUrl = Api.importExcel;
/**
 * 列表接口
 * @param params
 */
export const list = (params) => defHttp.get({ url: Api.list, params });

export const myAppList = (params) => defHttp.get({ url: Api.myAppList, params });

export const getBriefList = () => defHttp.get({ url: Api.getBriefList });
/**
 * 删除单个
 */
export const deleteOne = (params, handleSuccess) => {
  return defHttp.delete({ url: Api.deleteOne, params }, { joinParamsToUrl: true }).then(() => {
    handleSuccess();
  });
};
/**
 * 批量删除
 * @param params
 */
export const batchDelete = (params, handleSuccess) => {
  createConfirm({
    iconType: 'warning',
    title: '确认删除',
    content: '是否删除选中数据',
    okText: '确认',
    cancelText: '取消',
    onOk: () => {
      return defHttp.delete({ url: Api.deleteBatch, data: params }, { joinParamsToUrl: true }).then(() => {
        handleSuccess();
      });
    },
  });
};
/**
 * 保存或者更新
 * @param params
 */
export const saveOrUpdate = (params) => {
  return defHttp.post({ url: Api.save, params });
};

export const updateStatus = (params: { id: string; status: string }) => {
  return defHttp.put({ url: Api.updateStatus, data: params });
};

export const queryAppRole = (params) => defHttp.get({ url: Api.queryAppRole, params }, { errorMessageMode: 'none' });

export const queryAppDept = (params) => defHttp.get({ url: Api.queryAppDept, params }, { errorMessageMode: 'none' });

export const getBpmnDetails = (processDefinitionKey: string | number) =>
  defHttp.get({ url: `/app/appInfo/processDiagram/${processDefinitionKey}` }, { errorMessageMode: 'none' });

export const getProcessDiagramByInstance = (processInstanceId: string | number) =>
  defHttp.get({ url: `/app/appInfo/processDiagramByInstance/${processInstanceId}` }, { errorMessageMode: 'none' });

// 获取流程列表
export const getProcessList = () => defHttp.get({ url: `/flow/process/list/all` }, { errorMessageMode: 'none' });
