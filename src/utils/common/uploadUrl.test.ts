import { SYSTEM_UPLOAD_PATH, buildUploadActionUrl, getUploadRequestUrl } from './uploadUrl';

describe('upload url helpers', () => {
  it('builds an upload action url from a relative api base without duplicating the api prefix', () => {
    expect(buildUploadActionUrl('/api')).toBe('/api/sys/common/upload');
  });

  it('normalizes slash-only upload bases to the upload endpoint path', () => {
    expect(buildUploadActionUrl('/')).toBe('/sys/common/upload');
  });

  it('keeps uploadFile request urls as backend paths because axios supplies upload baseURL separately', () => {
    expect(getUploadRequestUrl()).toBe(SYSTEM_UPLOAD_PATH);
  });
});
