/** composer 引用回执 → 用户气泡 AttachmentCard。发送后必须留下与输入框相同的卡。 */

export type ComposerBubbleAttachment = {
  filename: string;
  kind?: string;
  previewUrl?: string;
  status?: string;
  note?: string;
  fileId?: string;
  /** Skill/knowledge 等引用的稳定身份；不当作用户文件 id。 */
  referenceId?: string;
};

const IMAGE_NAME_RE = /\.(png|jpe?g|gif|webp|bmp|svg|ico|heic|heif|avif|tiff?)$/i;

export const COMPOSER_REFERENCE_KINDS = [
  'skill',
  'knowledge',
  'subagent',
  'web',
  'thread_ref',
] as const;

export type ComposerReferenceKind = (typeof COMPOSER_REFERENCE_KINDS)[number];

export function isComposerReferenceKind(kind?: string): kind is ComposerReferenceKind {
  return Boolean(kind && (COMPOSER_REFERENCE_KINDS as readonly string[]).includes(kind));
}

export function threadRefAttachment(thread: { title?: string }): ComposerBubbleAttachment {
  return {
    filename: `${(thread.title || '未命名对话').slice(0, 60)}（对话记录）`,
    kind: 'thread_ref',
  };
}

export function composerBubbleAttachments(opts: {
  uploads?: Array<{
    filename: string;
    kind?: string;
    previewUrl?: string;
    status?: string;
    note?: string;
    file_id?: string;
  }>;
  files?: Array<{ id?: string; filename: string }>;
  threads?: Array<{ title?: string }>;
  knowledge?: Array<{ name: string }>;
  skills?: Array<{ id?: string; name: string }>;
  subagent?: { name?: string } | null;
  webSearch?: boolean;
}): ComposerBubbleAttachment[] {
  const cards: ComposerBubbleAttachment[] = [];
  const seen = new Set<string>();
  const push = (item: ComposerBubbleAttachment) => {
    const filename = String(item.filename || '').trim();
    if (!filename) return;
    const key = `${item.kind || ''}:${filename}`;
    if (seen.has(key)) return;
    seen.add(key);
    cards.push({ ...item, filename });
  };
  for (const a of opts.uploads || []) {
    push({
      filename: a.filename,
      kind: a.kind,
      previewUrl: a.previewUrl,
      status: a.status,
      note: a.note,
      fileId: a.file_id,
    });
  }
  for (const f of opts.files || []) {
    push({
      filename: f.filename,
      kind: IMAGE_NAME_RE.test(f.filename || '') ? 'image' : 'user_file',
      fileId: f.id,
    });
  }
  for (const t of opts.threads || []) push(threadRefAttachment(t));
  for (const k of opts.knowledge || []) push({ filename: k.name, kind: 'knowledge' });
  for (const s of opts.skills || []) {
    push({ filename: s.name, kind: 'skill', referenceId: s.id });
  }
  if (opts.subagent?.name) push({ filename: opts.subagent.name, kind: 'subagent' });
  if (opts.webSearch) push({ filename: '网页搜索', kind: 'web' });
  return cards;
}
