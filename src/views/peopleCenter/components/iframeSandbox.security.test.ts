/**
 * 安全回归：产物预览 iframe 的 sandbox 姿态锁定（2026-07-24 srcdoc XSS 跟进）。
 *
 * 这些 iframe 的 srcdoc 都注入**未消毒**的 fetch/上传/模型产出 HTML。浏览器的沙箱
 * 源隔离（无 allow-same-origin ＝ opaque origin）是这里的主要安全控制，本测试把每处的
 * sandbox 令牌集钉死，防止有人日后无声地放宽（典型事故＝顺手加回 allow-scripts /
 * allow-same-origin，让未信任产物在预览时执行脚本、甚至读到主站会话 token）。
 *
 * 为什么是「读源码断言」而非挂载渲染：chat 测试环境是 node（jest.config.chat.cjs，
 * 不进 jsdom / .vue 编译链路），且沙箱源隔离是浏览器平台保证、无法在 jsdom 里行为化验证。
 * 帧内脚本「注入后是否真的不执行」用真机 Chromium headless 验证（见交付说明），本测试
 * 只负责在 CI 里守住属性不变量这一层。
 *
 * 注意本文件的**能力边界**：这里只能证明「调用点写了 sanitizeSlideHtml 这个名字」，证明不了
 * 「消毒器干得对」——2026-07-26 的 P0（DOMPurify 把整块 `<style>` 吞掉，PPT 存盘即毁）就是在
 * 本文件全绿的情况下发生的。消毒器的行为断言（保版式 CSS ＋ 拦 script 标签、on* 事件属性、javascript: URI）在
 * `utils/slideEditKit.sanitize.test.ts`（jsdom 环境，真调用），改消毒逻辑时那边才是闸门。
 */
import { readFileSync } from 'fs';
import { resolve } from 'path';

function read(rel: string): string {
  return readFileSync(resolve(__dirname, rel), 'utf8');
}

/** 取出 srcdoc 绑定表达式为 `expr` 的那个 <iframe …> 开标签文本（属性可跨行，值内无 `>`）。 */
function iframeWithSrcdoc(source: string, expr: string): string {
  const needle = `:srcdoc="${expr}"`;
  const tags = source.match(/<iframe\b[^>]*>/g) || [];
  const hit = tags.find((t) => t.includes(needle));
  if (!hit) throw new Error(`未找到 srcdoc="${expr}" 的 iframe`);
  return hit;
}

/** 解析静态 sandbox="…" 属性为令牌集合；缺失属性返回 null（＝未加沙箱，视为不合规）。 */
function sandboxTokens(iframeTag: string): string[] | null {
  const m = iframeTag.match(/\bsandbox="([^"]*)"/);
  if (!m) return null;
  return m[1].split(/\s+/).filter(Boolean);
}

describe('产物预览 iframe sandbox 安全不变量', () => {
  describe('非交互预览：禁脚本（sandbox=""）', () => {
    it('InlineFilePreview 对话内 HTML 产物预览帧不得开脚本', () => {
      const tag = iframeWithSrcdoc(read('InlineFilePreview.vue'), 'text');
      expect(sandboxTokens(tag)).toEqual([]); // sandbox="" —— 最严沙箱，脚本/表单/同源/弹窗全禁
    });

    it('FileThumb 网页缩略帧不得开脚本', () => {
      const tag = iframeWithSrcdoc(read('FileThumb.vue'), 'webSrc');
      expect(sandboxTokens(tag)).toEqual([]);
    });

    it('InlineFilePreview 活页首页封面帧（ifp-slide-frame）不得开脚本', () => {
      // 与上面的 HTML 产物帧同文件、不同帧：srcdoc 来源是 .slides.json 里的整页 HTML，
      // 同样是未消毒的模型产出，缩略展示不需要任何能力。
      const tag = iframeWithSrcdoc(read('InlineFilePreview.vue'), 'coverSrc');
      expect(sandboxTokens(tag)).toEqual([]);
    });

    it('MessageList 幻灯片直播预览帧（apc-frame）不得开脚本', () => {
      // 生成期直播帧：srcdoc 是**流式到一半**的模型输出，注入频率最高的一处，绝不放能力。
      const tag = iframeWithSrcdoc(read('MessageList.vue'), "apSrcdoc(apCur(message)?.html || '')");
      expect(sandboxTokens(tag)).toEqual([]);
    });
  });

  describe('全屏网页模式：可跑脚本，但必须为无源沙箱', () => {
    it('DocPagesViewer 网页预览帧：开 allow-scripts，但绝不含 allow-same-origin / allow-popups', () => {
      const tag = iframeWithSrcdoc(read('DocPagesViewer.vue'), 'htmlSrc');
      const tokens = sandboxTokens(tag);
      expect(tokens).not.toBeNull();
      expect(tokens).toContain('allow-scripts'); // 交互式网页产物需要脚本
      // 关键：无 allow-same-origin ＝ opaque origin，帧内脚本读不到主站 token；
      // 无 allow-popups ＝ 堵住脚本拉钓鱼窗。任一出现都视为回归。
      expect(tokens).not.toContain('allow-same-origin');
      expect(tokens).not.toContain('allow-popups');
      expect(tokens).not.toContain('allow-top-navigation');
    });
  });

  describe('账号接管级敏感帧不得放宽（同源＋脚本必须先消毒）', () => {
    const dpv = read('DocPagesViewer.vue');

    it('实时编辑帧仍是 allow-same-origin allow-scripts（编辑内核需要），且 srcdoc 必过 sanitizeSlideHtml', () => {
      const tag = iframeWithSrcdoc(dpv, 'liveHtml');
      const tokens = sandboxTokens(tag);
      expect(tokens).not.toBeNull();
      expect(new Set(tokens)).toEqual(new Set(['allow-same-origin', 'allow-scripts']));
      // 同源＋脚本＝可读会话 token，唯一防线是消毒：liveHtml 计算属性必须经 sanitizeSlideHtml。
      expect(/const liveHtml\s*=\s*computed\([\s\S]*?sanitizeSlideHtml\(/.test(dpv)).toBe(true);
    });

    it('缩略图/放映帧为 sandbox=""（且经 slideFrameSrc→sanitizeSlideHtml 消毒）', () => {
      // 这两处即便沙箱漏了也已被消毒；此处只兜住沙箱层不被放宽。
      const thumb = iframeWithSrcdoc(dpv, 'pageSrc(n)');
      const play = iframeWithSrcdoc(dpv, 'pageSrc(playPage)');
      expect(sandboxTokens(thumb)).toEqual([]);
      expect(sandboxTokens(play)).toEqual([]);
    });
  });
});
