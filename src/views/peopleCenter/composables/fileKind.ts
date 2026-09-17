/**
 * 文件类型判定 + 图标映射（2026-07-28 从 MyFilesTab 抽出共享）。
 *
 * 抽出的理由：「我的文件」页与 composer 的文件选择器展示的是同一批文件，图标却各写各的
 * 一份，迟早漂移成两套观感。判据、图标、着色三样统一从这里出，两处永远一致。
 *
 * 着色类名约定：容器上写 `k-${kind}`，配色见各处的 `.k-*` 规则（同一组低饱和 tint）。
 *
 * ⚠️ 本模块 import 了 @ant-design/icons-vue（ESM）——ts-jest 的 node 单测链路不吃它。
 * 若要给判定逻辑写单测，先把 fileKindOf 拆到无 vue 依赖的文件里再测。
 */
import {
  FileExcelOutlined,
  FileImageOutlined,
  FileMarkdownOutlined,
  FileOutlined,
  FilePdfOutlined,
  FilePptOutlined,
  FileTextOutlined,
  FileWordOutlined,
  FileZipOutlined,
} from '@ant-design/icons-vue';

export type FileKind =
  | 'image'
  | 'pdf'
  | 'word'
  | 'excel'
  | 'ppt'
  | 'markdown'
  | 'text'
  | 'archive'
  | 'other';

export const KIND_ICON: Record<FileKind, unknown> = {
  image: FileImageOutlined,
  pdf: FilePdfOutlined,
  word: FileWordOutlined,
  excel: FileExcelOutlined,
  ppt: FilePptOutlined,
  markdown: FileMarkdownOutlined,
  text: FileTextOutlined,
  archive: FileZipOutlined,
  other: FileOutlined,
};

/** 按文件名扩展名（辅以 mime）归类；判定顺序与「我的文件」页历史行为逐条等价 */
export function fileKindOf(filename: string, mime?: string | null): FileKind {
  const name = (filename || '').toLowerCase();
  const type = mime || '';
  if (
    type.startsWith('image/')
    || /\.(png|jpe?g|gif|webp|bmp|svg|ico|heic|heif|avif|tiff?)$/.test(name)
  ) {
    return 'image';
  }
  if (name.endsWith('.pdf')) return 'pdf';
  // 网页文件（含 mime 缺失的场景）：归入 text 组，拿到缩略图（FileThumb 会渲染成网页首屏）
  if (name.endsWith('.html') || name.endsWith('.htm')) return 'text';
  // markdown 走展览区文章模式（2026-07-20）：渲染排版而不是源码文本
  if (/\.(md|markdown)$/.test(name)) return 'markdown';
  if (name.endsWith('.doc') || name.endsWith('.docx')) return 'word';
  if (name.endsWith('.xls') || name.endsWith('.xlsx') || name.endsWith('.xlsm') || name.endsWith('.csv')) return 'excel';
  if (name.endsWith('.ppt') || name.endsWith('.pptx')) return 'ppt';
  if (type.startsWith('text/') || name.endsWith('.txt') || name.endsWith('.json')) return 'text';
  if (/\.(zip|rar|7z|gz|tar|dmg)$/.test(name)) return 'archive';
  return 'other';
}
