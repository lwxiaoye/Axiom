import { defHttp } from '/@/utils/http/axios';
// import { useMessage } from '/@/hooks/web/useMessage';

// const { createConfirm } = useMessage();

enum Api {
  queryById = '/flow/flowData/queryById/',
  list = '/flow/flowData/list',
  save = '/flow/flowData/add',
  resubmit = '/flow/flowData/resubmit',
  edit = '/flow/flowData/edit',
  importExcel = '/flow/flowData/importExcel',
  exportXls = '/flow/flowData/myFlow/exportXls',
  myFlow = '/flow/flowData/myFlow',
  myToDo = '/flow/flowData/my/todo',
  myStart = '/flow/flowData/my/start',
  myDo = '/flow/flowData/my/do',
  queryTransferRecords = '/flow/flowData/transferRecords/',
  pass = '/flow/flowData/pass',
  reject = '/flow/flowData/reject',
  close = '/flow/flowData/close',
  revoke = '/flow/flowData/revoke',
  rejectNodeList = '/flow/flowData/rejectNodeList/',
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

export const apply = (params) => {
  return defHttp.post({ url: Api.save, params });
};

export const resubmit = (params) => {
  return defHttp.post({ url: Api.resubmit, params });
};

export const getMyFlow = (params) => defHttp.get({ url: Api.myFlow, params });
export const getMyToDo = (params) => defHttp.get({ url: Api.myToDo, params });
export const getMyStart = (params) => defHttp.get({ url: Api.myStart, params });
export const getMyDo = (params) => defHttp.get({ url: Api.myDo, params });
export const queryById = (id: string) => defHttp.get({ url: '/flow/flowData/queryById/' + id });
export const rejectNodeList = (processInstanceId: string) => defHttp.get({ url: '/flow/flowData/rejectNodeList/' + processInstanceId });
export const queryTransferRecords = (processInstanceId: string) => defHttp.get({ url: Api.queryTransferRecords + processInstanceId });

export const pass = (params) => {
  return defHttp.put({ url: Api.pass, params });
};

export const reject = (params) => {
  return defHttp.put({ url: Api.reject, params });
};

export const close = (params) => {
  return defHttp.put({ url: Api.close, params });
};

export const revoke = (params) => {
  return defHttp.put({ url: Api.revoke, params });
};

// 通过流程id查询
export const queryByProcessInstanceId = (params) => {
  return defHttp.get({ url: '/integrate/integrateTrainingProgram/queryByProcessInstanceId', params });
};

// 获取个人信息
export const getUserInfo = () => defHttp.get({ url: '/sys/school/userInfo' });

// 获取菜单
export const getMenu = () => defHttp.get({ url: '/sys/permission/getUserPermissionByToken' });

// 分页查询全部流程
export const getFlowList = (params) => defHttp.get({ url: '/flow/flowData/list/all', params });

// 指派
export const assign = (params) => defHttp.put({ url: '/flow/flowData/assign', params });

// 查询流程类型api
export const getProcessLists = () => defHttp.get({ url: '/app/appInfo/flow/app/list' });

// 导出查询表单
export const exportFormSchemaApi = (params) => defHttp.get({ url: '/flow/flowData/app/flow/exportXls', params });

// 查询全部用户
export const getAllUserList = (params) => defHttp.get({ url: '/sys/user/listAll', params });
