import { buildAgentSkillUploadHeaders } from './agentSkillPackage';

describe('agent skill package helpers', () => {
  it('builds upload headers without content type so browsers add multipart boundary', () => {
    const headers = buildAgentSkillUploadHeaders('token-1');

    expect(headers['X-Access-Token']).toBe('token-1');
    expect(headers.Authorization).toBe('token-1');
    expect(headers).not.toHaveProperty('Content-Type');
    expect(headers).not.toHaveProperty('content-type');
  });
});
