export const SKILL_UPLOAD_MAX_SIZE = 100 * 1024 * 1024;
export const SKILL_UPLOAD_MAX_SIZE_LABEL = '100M';

export function isSkillUploadFileAllowed(file: { name?: string; size?: number }) {
  const name = String(file?.name || '').toLowerCase();
  const size = Number(file?.size || 0);
  return name.endsWith('.zip') && size <= SKILL_UPLOAD_MAX_SIZE;
}
