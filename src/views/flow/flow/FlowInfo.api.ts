import { defHttp } from '/src/utils/http/axios';
import { useMessage } from '/src/hooks/web/useMessage';

const { createConfirm } = useMessage();

enum Api {
  list = '/flow/flowInfo/list',
  getBriefList = '/flow/flowInfo/list/brief',
  save = '/flow/flowInfo/add',
  queryFlowRole = '/flow/flowInfo/queryFlowRole',
  queryFlowDept = '/flow/flowInfo/queryFlowDept',
  edit = '/flow/flowInfo/edit',
  deleteOne = '/flow/flowInfo/delete',
  deleteBatch = '/flow/flowInfo/deleteBatch',
  importExcel = '/flow/flowInfo/importExcel',
  exportXls = '/flow/flowInfo/exportXls',
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
  return defHttp.post({ url: Api.edit, params });
};

export const queryFlowRole = (params) => defHttp.get({ url: Api.queryFlowRole, params }, { errorMessageMode: 'none' });

export const queryFlowDept = (params) => defHttp.get({ url: Api.queryFlowDept, params }, { errorMessageMode: 'none' });

export const getBpmnDetails = (processDefinitionKey: string | number) =>
  defHttp.get({ url: `/flow/flowInfo/processDiagram/${processDefinitionKey}` }, { errorMessageMode: 'none' });

export const getProcessDiagramByInstance = (processInstanceId: string | number) =>
  defHttp.get({ url: `/flow/flowInfo/processDiagramByInstance/${processInstanceId}` }, { errorMessageMode: 'none' });
