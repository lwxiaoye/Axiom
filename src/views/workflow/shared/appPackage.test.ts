import {
  WORKFLOW_APP_PACKAGE_MAX_SIZE,
  buildWorkflowAppPackageFilename,
  buildWorkflowAppUploadHeaders,
  isWorkflowAppPackageFile,
} from './appPackage';

describe('workflow app package helpers', () => {
  it('builds a safe qz agent package filename from app name', () => {
    expect(buildWorkflowAppPackageFilename('课程/助手:V1')).toBe('课程-助手-V1.qz-agent.json');
    expect(buildWorkflowAppPackageFilename('')).toBe('智能体.qz-agent.json');
  });

  it('accepts json package files under the size limit', () => {
    expect(isWorkflowAppPackageFile({ name: 'agent.qz-agent.json', size: WORKFLOW_APP_PACKAGE_MAX_SIZE })).toBe(true);
    expect(isWorkflowAppPackageFile({ name: 'agent.json', size: 12 })).toBe(true);
  });

  it('rejects non-json files and oversize files', () => {
    expect(isWorkflowAppPackageFile({ name: 'agent.zip', size: 12 })).toBe(false);
    expect(isWorkflowAppPackageFile({ name: 'agent.json', size: WORKFLOW_APP_PACKAGE_MAX_SIZE + 1 })).toBe(false);
  });

  it('builds upload headers without content type so browsers add multipart boundary', () => {
    const headers = buildWorkflowAppUploadHeaders('token-1');

    expect(headers['X-Access-Token']).toBe('token-1');
    expect(headers.Authorization).toBe('token-1');
    expect(headers).not.toHaveProperty('Content-Type');
    expect(headers).not.toHaveProperty('content-type');
  });
});
