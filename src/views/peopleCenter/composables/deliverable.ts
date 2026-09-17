/**
 * 交付物判据（2026-07-27 用户拍板）——哪些文件该作为「产物」露给用户。
 *
 * 与后端 `agent-api/app/services/files/deliverable.py` 的 `DELIVERABLE_EXTS` 是**同一份清单**，
 * 改一边必须改另一边（下方 `DELIVERABLE_EXTS` 有对应的 pytest/jest 各自守着）。
 *
 * 为什么前端还要再判一次而不是只信后端下发的 `deliverable` 字段：历史消息回放时，
 * 事件里的文件行是**当初**序列化的，没有这个字段。只认字段的话老对话里的 build.py 卡片
 * 会一直挂着。所以字段优先、按后缀兜底。
 *
 * 产物卡里的文件必然是本轮生成的（source=generated），因此这里不需要像后端那样为
 * 「用户自己上传的 .py」开豁免。
 */

/** 文档类：用户口径的交付产物（任务模式的《任务计划.md》落在 .md 上） */
const DOC_EXTS = ['.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls', '.pdf', '.md', '.markdown', '.txt', '.csv', '.tsv'];
/** 网页：平台自己就能预览（用户明确点名保留） */
const WEB_EXTS = ['.html', '.htm'];
/** 图片：能走到交付层的已是最终产物（嵌进文档的中间图在后端 harvest 阶段就滤掉了） */
const IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg'];

export const DELIVERABLE_EXTS = [...DOC_EXTS, ...WEB_EXTS, ...IMAGE_EXTS];

export function isDeliverableName(filename: string): boolean {
  const name = String(filename || '').trim().toLowerCase();
  const dot = name.lastIndexOf('.');
  if (dot <= 0) return false; // 无扩展名 / 纯 dotfile：不是文档
  return DELIVERABLE_EXTS.includes(name.slice(dot));
}

export function isDeliverableFile(file: { filename?: string; deliverable?: boolean } | null | undefined): boolean {
  if (!file) return false;
  if (typeof file.deliverable === 'boolean') return file.deliverable;
  return isDeliverableName(file.filename || '');
}

/** 「我的文件」页打开时就带上「显示全部文件」的查询参数（MyFilesTab 读它初始化 showAll）。 */
export const MY_FILES_SHOW_ALL_QUERY = 'all=1';

/**
 * 时间线上「已下载 报告.pdf」「已写入 build.py」那颗按钮该跳到哪里。
 *
 * 「我的文件」默认清单是 `deliverables_only`：`download_url` 取回的材料（source=material，
 * **不看后缀**）和 .py/.json/.zip 这类过程文件都被滤掉。按钮上明明写着文件名、点下去却落进
 * 一片找不到它的列表——这比干脆不给按钮更让人confused。所以这类目标带上 `all=1` 落地，
 * 页面直接开着「显示全部文件」。
 *
 * 交付物照旧跳干净的默认视图：默认视图之所以克制，就是为了别让产物淹在过程文件里。
 *
 * 判不准时**偏向 all=1**：这里只看得到文件名和工具名，看不到 source。用户自己上传的 .py
 * 在后端是可交付的（上传件永远可见），按后缀会被这里判成非交付物 → 跳到全量视图。
 * 全量是默认视图的超集，代价只是列表吵一点；反过来判错就是跳到一个空列表。
 */
export function myFilesRouteFor(step: { name?: string; target?: string }): string {
  const visibleByDefault = step.name !== 'download_url' && isDeliverableName(step.target || '');
  return visibleByDefault ? '/center/files' : `/center/files?${MY_FILES_SHOW_ALL_QUERY}`;
}

type FileLike = { filename: string; deliverable?: boolean };

/**
 * 会真的出现在产物卡里的文件。
 * ① 交付物白名单（上面的判据）；
 * ② `.slides.json` 是幻灯片编辑器的内部编辑源（2026-07-21 用户拍板），数据留在
 *    generatedFiles 里供配对/「编辑」按钮用，但不作为文件卡露出。
 * ③ `.research.md` 是研究报告的 Markdown 伴生源，下载菜单再取，不单独出卡。
 */
const COMPANION_NAME_RE = /\.(slides\.json|research\.md)$/i;

export function isCompanionFile(filename: string | null | undefined): boolean {
  return COMPANION_NAME_RE.test(String(filename || ''));
}

export function visibleDeliverables<T extends FileLike>(files: T[] | null | undefined): T[] {
  return (files || []).filter((f) => !isCompanionFile(f.filename) && isDeliverableFile(f));
}

/**
 * 本轮是否交付了实质内容——**必须与产物卡同口径**，所以两者共用 visibleDeliverables。
 *
 * 2026-07-27 事故：判据写成「generatedFiles 非空」，而产物卡按交付物白名单筛。模型先
 * write_file 写了 build.py、本轮随后失败且正文为空时，两个口径打架——判定「已交付」于是
 * 隐掉错误横幅、执行头按正常终态显示，产物卡那边又把 build.py 滤掉，用户得到一条没有正文、
 * 没有卡片、也没有报错的空气泡。
 */
export function hasDeliveredContent(
  content: string | null | undefined,
  files: FileLike[] | null | undefined,
): boolean {
  return Boolean(String(content || '').trim() || visibleDeliverables(files).length);
}
