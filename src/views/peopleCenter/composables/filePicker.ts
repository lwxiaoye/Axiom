/**
 * composer「我的文件」选择器的取数口径（2026-07-28）。
 *
 * 抽成模块的两个理由：① 选择器是 .vue，jest 链路不吃它，规则放在这里才守得住；
 * ② 「哪些文件能带进下一轮」是产品判据，不该藏在组件的一行 filter 里。
 */
import type { UserFileItem, UserFileSelection } from '../myfiles.api';

/** 与后端 build_chat_attachments 的单轮上限一致。 */
export const MAX_CHAT_FILE_REFS = 10;

/**
 * 选择器固定用扁平全量视图：含已归入文件夹的文件，否则整理进文件夹的文件反而选不到。
 * 顶层/文件夹的浏览语义只属于「我的文件」管理页。
 */
export const PICKER_FOLDER_ID = '__all__';

/**
 * 可选项 = 清单原样，**只**摘掉 `.slides.json`。
 *
 * 特别注意这里不做交付物筛选：`.py` 脚本、`.json` 中间数据、`download_url` 取回的材料
 * 都是用户可能要「带进下一轮」的东西——让模型接着改自己写的 build.py、拿刚下载的 PDF
 * 继续分析，都是正当需求。默认清单里看不到它们是**服务端**的 `deliverables_only` 干的，
 * 归 `show_all` 开关管（见 FileSelector 的「显示全部文件」），不该在这里再筛一道。
 *
 * `.slides.json` / `.research.md` 是内部伴生源，不是"用户的文件"，
 * 任何视图都不露出。
 */
export function pickerOptions(files: UserFileItem[] | null | undefined): UserFileItem[] {
  return (files || []).filter((f) => !/\.(slides\.json|research\.md)$/i.test(f.filename));
}

/**
 * 对所有入口的文件选择做同一个规范化：按首次出现去重，并限制为后端能处理的 10 个。
 * 除了点击选择器，历史草稿、队列快照也可能恢复选中态，因此不能只在按钮上拦截。
 */
export function limitFileSelection(selected: UserFileSelection[] | null | undefined): UserFileSelection[] {
  const seen = new Set<string>();
  const kept: UserFileSelection[] = [];
  for (const file of selected || []) {
    if (!file?.id || seen.has(file.id)) continue;
    seen.add(file.id);
    kept.push(file);
    if (kept.length >= MAX_CHAT_FILE_REFS) break;
  }
  return kept;
}

/**
 * 已选文件里剔掉清单中已不存在的（被删除/过期）——发送时后端 404 会降级成一句
 * 干巴巴的提示，不如打开选择器时就静默清干净。
 *
 * ⚠️ 只在**全量视图**下清理：默认视图看不到的非交付物仍然活着，按当前可见清单去剪
 * 会把用户上一轮刚选好的 build.py 悄悄踢掉。返回 null 表示"无需改动"。
 */
export function pruneSelection(
  selected: UserFileSelection[],
  options: UserFileItem[],
  showAll: boolean,
): UserFileSelection[] | null {
  const limited = limitFileSelection(selected);
  if (!showAll) return limited.length === selected.length ? null : limited;
  const alive = new Set(options.map((f) => f.id));
  const kept = limited.filter((f) => alive.has(f.id));
  return kept.length === selected.length ? null : kept;
}
