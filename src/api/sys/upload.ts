import { UploadApiResult } from './model/uploadModel';
import { defHttp } from '/@/utils/http/axios';
import { UploadFileParams } from '/#/axios';
import { getUploadRequestUrl } from '/@/utils/common/uploadUrl';

// 上传必须经现有的 /api 代理转发。该代理会剥离 /api 并转发到 Java 的 /admin-api；
// /upload 代理对应的是另一条文件服务路径，转发 /sys/common/upload 会得到 500。
const UPLOAD_PROXY_BASE_URL = '/api';

/**
 * @description: Upload interface
 */
export function uploadApi(params: UploadFileParams, onUploadProgress: (progressEvent: ProgressEvent) => void) {
  return defHttp.uploadFile<UploadApiResult>(
    {
      url: getUploadRequestUrl(),
      baseURL: UPLOAD_PROXY_BASE_URL,
      onUploadProgress,
    },
    params
  );
}
/**
 * @description: Upload interface
 */
export function uploadImg(
  params: UploadFileParams,
  onUploadProgress: (progressEvent: ProgressEvent) => void,
) {
  return defHttp.uploadFile<UploadApiResult>(
    {
      url: getUploadRequestUrl(),
      baseURL: UPLOAD_PROXY_BASE_URL,
      onUploadProgress,
    },
    params,
    { isReturnResponse: true }
  );
}
