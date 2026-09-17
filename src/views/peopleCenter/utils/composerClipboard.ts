/** 子智能体 / 主对话 composer：从剪贴板取出可上传的文件。 */
export const PASTE_AS_FILE_MIN_CHARS = 800;
export const PASTE_AS_FILE_MIN_LINES = 12;

export function pastedTextFilename(text: string): string {
  const snippet = text
    .replace(/\s+/g, ' ')
    .replace(/[\\/:*?"<>|]/g, '')
    .trim()
    .slice(0, 12);
  return snippet ? `粘贴的文本-${snippet}.txt` : '粘贴的文本.txt';
}

export function filesFromClipboard(
  data: DataTransfer | null | undefined,
): File[] {
  if (!data) return [];
  const files: File[] = [];
  for (const item of Array.from(data.items || [])) {
    if (item.kind !== 'file') continue;
    const file = item.getAsFile();
    if (file) files.push(normalizeClipboardFile(file));
  }
  if (!files.length && data.files?.length) {
    return Array.from(data.files).map(normalizeClipboardFile);
  }
  return files;
}

export function longTextAsPastedFile(text: string): File | null {
  if (!text) return null;
  const lines = text.split('\n').length;
  if (text.length < PASTE_AS_FILE_MIN_CHARS && lines < PASTE_AS_FILE_MIN_LINES) return null;
  return new File([text], pastedTextFilename(text), { type: 'text/plain' });
}

function normalizeClipboardFile(file: File): File {
  if (file.name && file.name.trim()) return file;
  const ext = (file.type.split('/')[1] || 'bin').replace(/[^a-z0-9]/gi, '') || 'bin';
  const prefix = file.type.startsWith('image/') ? '粘贴的图片' : '粘贴的文件';
  return new File([file], `${prefix}.${ext}`, { type: file.type || 'application/octet-stream' });
}
