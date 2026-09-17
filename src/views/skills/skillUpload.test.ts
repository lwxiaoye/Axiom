import { isSkillUploadFileAllowed, SKILL_UPLOAD_MAX_SIZE } from './skillUpload';

describe('Skill upload package validation', () => {
  it('accepts zip packages up to 100MiB', () => {
    expect(isSkillUploadFileAllowed({ name: 'demo.zip', size: SKILL_UPLOAD_MAX_SIZE })).toBe(true);
  });

  it('rejects zip packages larger than 100MiB', () => {
    expect(isSkillUploadFileAllowed({ name: 'demo.zip', size: SKILL_UPLOAD_MAX_SIZE + 1 })).toBe(false);
  });

  it('rejects non-zip packages even when they are under the size limit', () => {
    expect(isSkillUploadFileAllowed({ name: 'demo.txt', size: 1024 })).toBe(false);
  });

});
