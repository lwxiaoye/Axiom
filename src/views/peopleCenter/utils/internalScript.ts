/**
 * 内部沙箱脚本泄漏防护（2026-07-21 用户反馈「一次性修复」，2026-07-28 收窄判据）。
 *
 * 弱模型可能把 bash 脚本原样贴进正文（带 /workspace/tmp、svg_output、spec_lock
 * 等**流水线内部**路径），对用户是一大段无意义的 Python 噪声——脚本已通过工具参数执行了，
 * 正文无需重复。凡引用这些内部标记的代码围栏一律从正文剥除。
 *
 * ⚠️ `/workspace/files/` **不是**内部标记。统一文件系统落地后它就是「我的文件」在沙箱里的
 * 地址，`write_file` 的回执自己就在教模型说这个路径（`chat/tools/paths.py` 的
 * 「沙箱内路径：/workspace/files/<名>」），重编译提示词里也照写。旧判据是整个
 * `/workspace/`，于是用户说「把刚才那个脚本贴出来」时，只要脚本里出现过这个路径，
 * 整段代码就在正文里静默消失——用户看到的是一条答非所问的空回答。
 *
 * 判据保持**确定性**（列举具体路径/管线文件名），不做「这段像不像内部脚本」的启发式猜测：
 * 猜错的代价是吞掉用户真正索要的内容，而那种失败是静默的。
 *
 * 从 MessageList.vue 抽出成独立模块只为可单测（jest 链路不吃 .vue）。
 */

/** `/workspace/` 下除 `files/` 之外的都是流水线内部目录（tmp/proj/slides/outputs…）。
 *  `(?!files\b)` 只放行 `/workspace/files` 与 `/workspace/files/...`，
 *  `/workspace/filesystem` 这类仍算内部（`\b` 要求 files 后面是非单词字符）。 */
export const INTERNAL_CODE_MARKERS =
  /\/workspace\/(?!files\b)|spec_lock|svg_output|svg_to_pptx|build_deck|premium-deck/;

/** 剥掉引用内部路径/管线文件名的代码围栏。
 *  兼容流式：未闭合的尾部围栏若已含内部标记也一并隐藏，避免边生成边刷屏。 */
export function stripInternalScriptBlocks(content: string): string {
  const out = String(content || '')
    // 已闭合的代码围栏：命中内部标记则整块剥除
    .replace(/```[^\n]*\n[\s\S]*?```/g, (block) =>
      (INTERNAL_CODE_MARKERS.test(block) ? '' : block))
    // 流式中尚未闭合的尾部围栏：同样按内部标记隐藏
    .replace(/```[^\n]*\n[\s\S]*$/, (block) =>
      (INTERNAL_CODE_MARKERS.test(block) ? '' : block));
  return out.replace(/\n{3,}/g, '\n\n').trim();
}
