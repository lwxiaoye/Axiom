/** 主对话附件白名单：招标主流格式 + 已支持的 csv/json。 */

const ALLOWED_EXTENSIONS = new Set([
  'pdf',
  'docx',
  'pptx',
  'xlsx',
  'xlsm',
  'txt',
  'md',
  'markdown',
  'html',
  'htm',
  'csv',
  'json',
  'png',
  'jpg',
  'jpeg',
  'gif',
  'webp',
  'bmp',
]);

const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp']);

const LEGACY_OFFICE: Record<string, string> = {
  doc: 'docx',
  ppt: 'pptx',
  xls: 'xlsx',
};

export const CHAT_UPLOAD_FORMAT_HINT = 'PDF、Word、PPT、Excel、TXT、Markdown、HTML 或常见图片';

export const CHAT_UPLOAD_ACCEPT = [...ALLOWED_EXTENSIONS].map((ext) => `.${ext}`).join(',');
export const CHAT_IMAGE_UPLOAD_ACCEPT = [...IMAGE_EXTENSIONS].map((ext) => `.${ext}`).join(',');

function fileExtension(name: string): string {
  const trimmed = String(name || '').trim();
  const dot = trimmed.lastIndexOf('.');
  if (dot <= 0 || dot === trimmed.length - 1) return '';
  return trimmed.slice(dot + 1).toLowerCase();
}

function imageSubtype(mime: string): string {
  const subtype = String(mime || '')
    .split(';')[0]
    .trim()
    .toLowerCase()
    .replace(/^image\//, '');
  return subtype === 'jpeg' ? 'jpg' : subtype;
}

export function isChatImageFile(file: { name?: string; type?: string }): boolean {
  const ext = fileExtension(String(file?.name || ''));
  if (ext && IMAGE_EXTENSIONS.has(ext)) return true;
  const mime = String(file?.type || '').trim().toLowerCase();
  return mime.startsWith('image/') && IMAGE_EXTENSIONS.has(imageSubtype(mime));
}

export function chatUploadFileError(file: { name?: string; type?: string }): string {
  const name = String(file?.name || '').trim();
  const ext = fileExtension(name);
  const modern = LEGACY_OFFICE[ext];
  if (modern) {
    return `「${name}」是旧版 Office 格式，请另存为 .${modern} 后再上传`;
  }
  if (ext && ALLOWED_EXTENSIONS.has(ext)) return '';
  const mime = String(file?.type || '').trim().toLowerCase();
  if (!ext && mime.startsWith('image/') && IMAGE_EXTENSIONS.has(imageSubtype(mime))) return '';
  const label = name || '该文件';
  return `不支持「${label}」，请上传 ${CHAT_UPLOAD_FORMAT_HINT}`;
}
