/**
 * 幻灯片页编辑内核（2026-07-21，Manus 式画布交互，挂在 DocPagesViewer 的主画布上——
 * 产物查看器本身即编辑器，没有独立的「编辑幻灯片」页面）。
 *
 * 架构（真机踩坑后的定案）：交互脚本**注入到 iframe 帧内执行**，不在父页注册监听——
 * Chrome 对 sandbox iframe 会静音父页世界在帧文档上的事件监听（哪怕 allow-scripts、
 * 事件确实派发，父页监听就是不回调；帧内世界监听原生可靠，真机探针实证）。
 * 因此 iframe 必须 sandbox="allow-same-origin allow-scripts"，页面 HTML 注入前必须过
 * sanitizeSlideHtml 消毒（剥 script/内联事件/javascript: 链接），我们的注入脚本在消毒后追加。
 * 父页通过 contentWindow.__slideKit 句柄收割/查脏/销毁，postMessage 接收变更通知。
 * 交互：单击选中（选框+两侧宽度手柄+Manus 式排版工具条）、拖拽移动（写回 top/left）、
 * 双击该元素文字编辑（编辑态点击放行原生光标/划选）、Esc/Delete 键盘。
 * 工具条：字号(px 输入) · 加粗/斜体/下划线/删除线 · 左/中/右对齐 · 行距 · 文字颜色(色板) · 复制/删除。
 * 加粗/斜体/下划线/删除线/颜色在“划选文字”时按选区走 execCommand（生成内联标签/span），
 * 未划选时整块走元素级内联 style；对齐/行距/字号恒为元素级。产物 HTML 交 html_to_pptx.py
 * 透传为 pptx run/段落格式（编译器读取这些内联样式覆盖，未编辑的页 1:1 不变）。
 */

import DOMPurify from 'dompurify';

const KIT = 'data-edit-kit';

// DOMPurify 消毒配置：内联 style 在默认白名单内，此处只额外禁掉危险容器标签。
// `<style>` 标签虽然也在白名单内，却会被 DOMPurify 的 mXSS 探针整块吞掉（见下方 MARKUP_PROBE），
// 因此版式 CSS 的保留另有一套「占位符 + textContent 写回」机制，不能只靠白名单。
// 提到模块级常量，避免缩略图轨按页多次调用 sanitizeSlideHtml 时反复重建配置对象。
const SLIDE_SANITIZE_CONFIG = {
  WHOLE_DOCUMENT: true,
  FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'base', 'link', 'meta', 'form'],
  ALLOW_DATA_ATTR: true,
};

const HTML_NS = 'http://www.w3.org/1999/xhtml';

/**
 * DOMPurify（3.4.x `_isUnsafeNode`）的 mXSS 探针 `/<[/\w!]/`：节点**没有元素子节点**、且
 * textContent 与 innerHTML **双双**「看起来像标签」时，整个节点 fail-closed 删除。
 * 普通元素的 innerHTML 会把 `<` 转义成 `&lt;` 故永不命中；`<style>` 是 raw text 元素，
 * innerHTML 就是原始 CSS 文本，于是 CSS 注释里随便一个 `<div>` / `<br/>` / `</div>` 子串
 * 就会让**整块 `<style>` 消失**。本仓库 ppt-html 技能的 design-system.css 头部通篇是
 * `/* DOM: <div class="…"> *\/` 这类注释，等于每一份 AI 生成的 PPT 必然命中（实测 14321 → 664 字符）。
 */
const MARKUP_PROBE = /<[/\w!]/;

/** `<style>` 的 raw text 只可能被 `</style` 终止——写回前中和它，杜绝序列化后再解析时的越界。 */
function hardenStyleText(css: string): string {
  // CSS 里 `\/` 与 `/` 等价（注释内更是无所谓），中和后视觉与语义均不变。
  return css.replace(/<\/style/gi, '<\\/style');
}

/** 只处理「HTML 命名空间 + 无元素子节点」的 style：`<svg><style>` 那类命名空间混淆向量原样交给 DOMPurify 删。 */
function isPlainHtmlStyle(el: Element): boolean {
  return el.namespaceURI === HTML_NS && !el.firstElementChild;
}

/**
 * 幻灯片页 HTML 消毒：srcdoc 开 allow-scripts + allow-same-origin 前必须过一遍。
 * 用 DOMPurify 解析器级白名单消毒——此前的正则黑名单可被 `<svg/onload=…>`（斜杠非空白，
 * 绕过 on* 规则）、未闭合 `<script src=…>` 等绕过；而实时编辑帧与主站同源，帧内一旦有脚本
 * 存活即可读主站 localStorage 里的会话 token，属账号接管级风险（提示词注入/投毒 deck 可触达）。
 *
 * **版式 CSS 的保留靠三步走，不靠白名单**（`<style>` 在白名单内但会被 mXSS 探针整块吞，见 MARKUP_PROBE）：
 *   ① 先用 DOMParser 惰性解析（不执行脚本、不发请求），把每个 `<style>` 的 CSS 原文换成本次调用专属的随机占位符；
 *   ② 拿占位符版本走**完整**消毒（`SAFE_FOR_XML` 保持默认开启，mXSS/风险注释/属性越界三道防线一条不减；
 *      实测把它关掉会让 `<a title="</style><img src=x onerror=…>">` 这类突破载荷原样出现在返回串里）；
 *   ③ 在消毒后的 DOM 上按占位符 `textContent = 原文` 写回——textContent 赋值不解析 HTML，
 *      原文只来自同一份输入的同一个 style 节点，并额外过 hardenStyleText 掐掉 `</style` 越界。
 * 剥离 script/事件处理器/javascript: URI 及 iframe/object/embed/base/link/meta/form 等危险标签。
 * 我方注入的编辑脚本在消毒之后经 DOM API 追加（见 attachSlideEditKit），不经此字符串，故不受影响。
 */
export function sanitizeSlideHtml(html: string): string {
  const raw = String(html || '');
  if (!raw) return raw;
  const hit = SANITIZE_CACHE.get(raw);
  if (hit !== undefined) return hit;
  return rememberSanitized(raw, sanitizeSlideHtmlUncached(raw));
}

/**
 * 消毒结果记忆化（2026-07-26 性能）。
 *
 * 病灶：缩略图轨的 `:srcdoc="pageSrc(n)"` 在 `v-for` 里，任何一次重渲染——切页、liveDirty
 * 首次翻转、拖拽排序——都会把**全部页面**重新消毒一遍（每页两次 DOMParser 解析 + 完整
 * DOMPurify）。30 页实测 65ms/次，翻页即掉帧。
 *
 * 放在这里而不是在 DocPagesViewer 里做 computed 缓存：`sanitizeSlideHtml` 是唯一入口，
 * `slideFrameSrc`（缩略图/放映/InlineFilePreview 封面）和 `liveHtml` 都从这里过，
 * 一处改动就全链路受益，组件侧零改动。
 *
 * 正确性：同一输入串 ⇒ 同一输出串（内部随机 token 只在函数体内周转，不进返回值）。
 * 按**完整输入串**做键，不同页/不同文档之间不可能串味。
 * 内存：条数与总字符双上限，FIFO 淘汰（Map 的迭代序即插入序）——单页内嵌 base64 图时
 * 光靠条数封不住，故再加一道字符预算。
 */
const SANITIZE_CACHE = new Map<string, string>();
const SANITIZE_CACHE_MAX_ENTRIES = 200;
const SANITIZE_CACHE_MAX_CHARS = 8_000_000; // ≈16MB（UTF-16）
let sanitizeCacheChars = 0;

function rememberSanitized(key: string, value: string): string {
  SANITIZE_CACHE.set(key, value);
  sanitizeCacheChars += key.length + value.length;
  while (
    SANITIZE_CACHE.size > SANITIZE_CACHE_MAX_ENTRIES ||
    (sanitizeCacheChars > SANITIZE_CACHE_MAX_CHARS && SANITIZE_CACHE.size > 1)
  ) {
    const oldest = SANITIZE_CACHE.keys().next();
    if (oldest.done) break;
    sanitizeCacheChars -= oldest.value.length + (SANITIZE_CACHE.get(oldest.value) || '').length;
    SANITIZE_CACHE.delete(oldest.value);
  }
  return value;
}

/** 真正的消毒实现（三步走）；缓存判定在 sanitizeSlideHtml 里，此处只管算。 */
function sanitizeSlideHtmlUncached(raw: string): string {
  // 没有 <style>（或环境无 DOMParser）时走朴素单遍，省掉一次解析——缩略图轨会按页反复调用。
  if (typeof DOMParser === 'undefined' || !/<style[\s/>]/i.test(raw)) {
    return withDoctype(DOMPurify.sanitize(raw, SLIDE_SANITIZE_CONFIG));
  }

  // ① 惰性解析 + 占位符替换
  const staging = new DOMParser().parseFromString(raw, 'text/html');
  const cssTexts: string[] = [];
  const token = `slide-css-${Math.random().toString(36).slice(2)}`; // 每次调用现取，输入无法预埋伪造
  staging.querySelectorAll('style').forEach((el) => {
    if (!isPlainHtmlStyle(el)) return;
    const css = el.textContent || '';
    if (!MARKUP_PROBE.test(css)) return; // 不会被探针误杀的 CSS 无需绕道
    el.textContent = `${token}-${cssTexts.push(css) - 1}`;
  });
  if (!cssTexts.length) return withDoctype(DOMPurify.sanitize(raw, SLIDE_SANITIZE_CONFIG));

  // ② 完整消毒（RETURN_DOM：拿 DOM 回来写回，避免在字符串上拼接原文——那才是真正会被突破的写法）
  const clean = DOMPurify.sanitize(`<!DOCTYPE html>${staging.documentElement.outerHTML}`, {
    ...SLIDE_SANITIZE_CONFIG,
    RETURN_DOM: true,
  }) as HTMLElement | null;
  if (!clean) return '';

  // ③ 按占位符写回 CSS 原文
  const slot = new RegExp(`^${token}-(\\d+)$`);
  clean.querySelectorAll('style').forEach((el) => {
    if (el.namespaceURI !== HTML_NS) return;
    const hit = slot.exec((el.textContent || '').trim());
    if (hit) el.textContent = hardenStyleText(cssTexts[Number(hit[1])] || '');
  });
  return withDoctype(clean.outerHTML);
}

/** WHOLE_DOCUMENT 会丢掉 `<!DOCTYPE>`；srcdoc 无 doctype 会进 quirks 模式改变盒模型——补回。 */
function withDoctype(clean: string): string {
  return clean && !/^\s*<!doctype/i.test(clean) ? `<!DOCTYPE html>${clean}` : clean;
}

/**
 * 只读帧（缩略图/行内首页/放映）的 srcdoc：消毒后钉死 1280×720 画布。
 * `.slide` 在设计系统里带 24px 外边距（单页 HTML 单独打开时的留白），塞进定尺帧里会
 * 整体下移并裁掉底部一条——这里统一清零，编译出的 pptx 不受影响（消毒/复位只在预览侧）。
 */
export function slideFrameSrc(html: string): string {
  const clean = sanitizeSlideHtml(html);
  if (!clean) return clean;
  const reset =
    '<style>html,body{margin:0;padding:0;width:1280px;height:720px;overflow:hidden}' +
    'body>*{margin:0 !important}</style>';
  return clean.includes('</head>') ? clean.replace('</head>', `${reset}</head>`) : reset + clean;
}

export interface SlideEditKitHandle {
  /** 收割纯净整页 HTML（剥掉注入物与编辑痕迹）；收割后帧内交互失效，调用方负责重载 */
  harvest(): string;
  destroy(): void;
  isDirty(): boolean;
}

/** 帧内交互脚本（纯 ES5 字符串，运行于 iframe 自己的世界）。 */
function buildKitScript(scale: number): string {
  return String.raw`(function(){
var KIT='data-edit-kit', S=${scale.toFixed(4)}, doc=document, dirty=false;
/* 脏标记要能**退回干净**（2026-07-30）：全部撤销后仍报 dirty 的话，用户明明退回了原样，
   点 ✕ 还是被问一次「放弃未保存的修改？」。所以消息里带上当前状态，由外层照单同步。 */
function md(v){ dirty=v!==false; try{parent.postMessage({type:'slide-edit-dirty',dirty:dirty},'*');}catch(e){} }
var st=doc.createElement('style'); st.setAttribute(KIT,'1');
st.textContent='html,body{margin:0;padding:0;overflow:hidden;width:100%;height:100%}'
 +'body>*:not(['+KIT+']){margin:0 !important;transform-origin:0 0}'
 +'.slide>*:not(['+KIT+']):hover,.grid>*:hover{outline:1px dashed rgba(35,39,46,.4);outline-offset:-1px}'
 +'[contenteditable]:focus{outline:2px solid #2f6df6 !important;outline-offset:0}'
 +'['+KIT+'] svg{pointer-events:none;display:block}'
 +'['+KIT+'] button:hover{background:#f2f4f7 !important}'
 +'['+KIT+'] input[type=number]::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}';
doc.head.appendChild(st);
/* 画布缩放改成**每次按当前宽度重算**（2026-07-30 修）：原先 scale 是挂载时由外层算好写死在
   样式里的，窗口一变（拖动窗口/收起侧栏/进出全屏）比例就过期——画布要么溢出被裁，要么右下
   留一大块白，而且不会自愈。浮层不受影响（position:fixed + getBoundingClientRect 拿的已是
   缩放后的视口坐标），所以只需要重写这一条规则。 */
var sst=doc.createElement('style'); sst.setAttribute(KIT,'1'); doc.head.appendChild(sst);
function applyScale(){
 S=(doc.documentElement.clientWidth||1280)/1280;
 sst.textContent='body>*:not(['+KIT+']){transform:scale('+S.toFixed(4)+')}';
}
applyScale();
window.addEventListener('resize',function(){applyScale();if(sel)pos();});
function mk(css,html){var d=doc.createElement('div');d.setAttribute(KIT,'1');d.style.cssText=css;if(html)d.innerHTML=html;doc.body.appendChild(d);return d;}
var box=mk('position:fixed;display:none;border:2px solid #2f6df6;border-radius:2px;pointer-events:none;z-index:9998;');
var btn='display:inline-flex;align-items:center;justify-content:center;min-width:26px;height:26px;border:0;background:#fff;border-radius:6px;padding:0 6px;font-size:13px;line-height:1;color:#374151;cursor:pointer';
var fld='height:24px;border:1px solid #e2e4e9;border-radius:6px;font-size:12px;color:#374151;background:#fff';
var dv='<span '+KIT+'="1" style="width:1px;align-self:stretch;background:#eceef2;margin:1px 2px"></span>';
var svgL='<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M2.5 4h11"/><path d="M2.5 8h7"/><path d="M2.5 12h9.5"/></svg>';
var svgC='<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M2.5 4h11"/><path d="M4.5 8h7"/><path d="M3.5 12h9"/></svg>';
var svgR='<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M2.5 4h11"/><path d="M6.5 8h7"/><path d="M4 12h9.5"/></svg>';
var bar=mk('position:fixed;display:none;align-items:center;gap:1px;z-index:9999;background:#fff;border:1px solid #e2e4e9;border-radius:10px;padding:3px 4px;box-shadow:0 4px 14px rgba(30,35,44,.18);font-family:-apple-system,sans-serif;',
 '<span '+KIT+'="1" style="display:inline-flex;align-items:center;gap:2px;padding:0 2px">'
+'<input '+KIT+'="1" data-ctl="size" type="number" min="8" max="240" title="字号" style="'+fld+';width:40px;text-align:center;padding:0 2px"/>'
+'<span style="font-size:11px;color:#9aa0ab;pointer-events:none">px</span></span>'+dv
+'<button '+KIT+'="1" data-act="bold" title="加粗" style="'+btn+';font-weight:700">B</button>'
+'<button '+KIT+'="1" data-act="italic" title="斜体" style="'+btn+';font-style:italic;font-family:serif">I</button>'
+'<button '+KIT+'="1" data-act="underline" title="下划线" style="'+btn+';text-decoration:underline">U</button>'
+'<button '+KIT+'="1" data-act="strike" title="删除线" style="'+btn+';text-decoration:line-through">S</button>'+dv
+'<button '+KIT+'="1" data-act="alignL" title="左对齐" style="'+btn+'">'+svgL+'</button>'
+'<button '+KIT+'="1" data-act="alignC" title="居中" style="'+btn+'">'+svgC+'</button>'
+'<button '+KIT+'="1" data-act="alignR" title="右对齐" style="'+btn+'">'+svgR+'</button>'+dv
+'<select '+KIT+'="1" data-ctl="lh" title="行距" style="'+fld+';padding:0 1px">'
+'<option value="">↕</option><option value="1">1.0</option><option value="1.15">1.15</option>'
+'<option value="1.3">1.3</option><option value="1.5">1.5</option><option value="1.8">1.8</option><option value="2">2.0</option></select>'+dv
+'<button '+KIT+'="1" data-act="color" title="文字颜色" style="'+btn+';flex-direction:column;gap:1px;padding:0 5px">'
+'<span style="font-weight:700;pointer-events:none">A</span><span '+KIT+'="1" data-cur="1" style="width:16px;height:3px;border-radius:1px;background:#111;pointer-events:none"></span></button>'+dv
+'<button '+KIT+'="1" data-act="copy" style="'+btn+'">复制</button>'
+'<button '+KIT+'="1" data-act="del" style="'+btn+';color:#c04a4a">删除</button>');
var PAL=['#10233f','#0b1e3d','#1d63d8','#2f7bf0','#4fc3e8','#16a34a','#f59e0b','#ef4444','#6b7280','#ffffff'];
var swHtml=''; for(var pi=0;pi<PAL.length;pi++){ swHtml+='<button '+KIT+'="1" data-color="'+PAL[pi]+'" title="'+PAL[pi]+'" style="width:22px;height:22px;border-radius:5px;border:1px solid rgba(0,0,0,.14);background:'+PAL[pi]+';cursor:pointer;padding:0"></button>'; }
var pop=mk('position:fixed;display:none;z-index:10000;background:#fff;border:1px solid #e2e4e9;border-radius:10px;padding:8px;box-shadow:0 6px 20px rgba(30,35,44,.2);width:154px;font-family:-apple-system,sans-serif;',
 '<div '+KIT+'="1" style="display:flex;flex-wrap:wrap;gap:4px">'+swHtml+'</div>'
+'<label '+KIT+'="1" style="display:flex;align-items:center;justify-content:space-between;gap:6px;margin-top:8px;font-size:12px;color:#6b7280">自定义'
+'<input '+KIT+'="1" data-ctl="custom" type="color" style="width:26px;height:22px;border:0;background:none;padding:0;cursor:pointer"/></label>');
var hcss='position:fixed;display:none;width:12px;height:20px;border-radius:6px;background:#2f6df6;border:2px solid #fff;box-shadow:0 1px 4px rgba(30,35,44,.3);cursor:ew-resize;z-index:9999;';
var hl=mk(hcss), hr=mk(hcss);
var sel=null, editing=null, drag=null, rez=null, savedRange=null;
/* ===== 撤销栈（2026-07-30 用户要求 Ctrl+Z）=====
   此前**完全没有撤销**：选中元素按一下 Delete 就没了，误删一个标题只能关掉丢弃全部改动重来。
   快照存的是「body 下所有非 kit 节点的 outerHTML」——必须排除 kit 自己注入的浮层，否则恢复时
   会把工具条/选中框一起写回去，出现两套浮层。文字编辑（contenteditable）态下不接管 Ctrl+Z，
   交给浏览器原生的字符级撤销，否则打错一个字要整页回滚。 */
var undoStack=[], UNDO_MAX=40, restoring=false, baseHtml=null;
function slideNodes(){var out=[],ch=doc.body.children;for(var i=0;i<ch.length;i++)if(!ch[i].hasAttribute(KIT))out.push(ch[i]);return out;}
function curHtml(){var ns=slideNodes(),h='';for(var i=0;i<ns.length;i++)h+=ns[i].outerHTML;return h;}
function snap(){
 if(restoring)return;
 var html=curHtml();
 if(baseHtml===null)baseHtml=html; // 首次改动前的原样，用来判断"是不是已经退回干净"
 if(undoStack.length&&undoStack[undoStack.length-1]===html)return; // 同状态不重复入栈
 undoStack.push(html); if(undoStack.length>UNDO_MAX)undoStack.shift();
}
function undo(){
 if(!undoStack.length)return;
 restoring=true;
 var html=undoStack.pop();
 stopEdit(); select(null);
 var ns=slideNodes(); for(var i=0;i<ns.length;i++)ns[i].remove();
 var tmp=doc.createElement('div'); tmp.innerHTML=html;
 var frag=doc.createDocumentFragment(); while(tmp.firstChild)frag.appendChild(tmp.firstChild);
 doc.body.insertBefore(frag,doc.body.firstChild);
 restoring=false;
 /* 退回到首次改动前的原样时，脏标记要跟着回落——否则「改了又全撤销」还会被问一次
    未保存。注意判据是**和 baseHtml 逐字符比**，不是「栈空了」：栈会因 UNDO_MAX 截断，
    栈空不等于回到了原点。 */
 md(curHtml()!==baseHtml);
}
function pick(t){var n=t instanceof Element?t:null;while(n&&n!==doc.body){if(n.nodeType===1&&!n.hasAttribute(KIT)){var p=n.parentElement;if(p&&(p.classList.contains('slide')||p.classList.contains('grid')))return n;}n=n.parentElement;}return null;}
function pos(){
 if(!sel){box.style.display=bar.style.display=hl.style.display=hr.style.display=pop.style.display='none';return;}
 var r=sel.getBoundingClientRect();
 box.style.display='block';box.style.left=(r.left-2)+'px';box.style.top=(r.top-2)+'px';box.style.width=(r.width+4)+'px';box.style.height=(r.height+4)+'px';
 bar.style.display='flex';
 var bw=bar.offsetWidth||360, vw=doc.documentElement.clientWidth||1280;
 bar.style.left=Math.max(4,Math.min(r.left+r.width/2-bw/2, vw-bw-4))+'px';bar.style.top=Math.max(4,r.top-40)+'px';
 hl.style.display='block';hl.style.left=(r.left-7)+'px';hl.style.top=(r.top+r.height/2-10)+'px';
 hr.style.display='block';hr.style.left=(r.right-5)+'px';hr.style.top=(r.top+r.height/2-10)+'px';
 sync();
}
function q(a){return bar.querySelector('['+a+']');}
function setAct(act,on){var b=bar.querySelector('[data-act="'+act+'"]');if(b)b.style.background=on?'#e7f0fd':'#fff',b.style.color=on?'#1d63d8':'#374151';}
function sync(){ if(!sel)return; var cs=getComputedStyle(sel);
 var si=q('data-ctl=size'); if(si&&doc.activeElement!==si){var px=Math.round(parseFloat(cs.fontSize)); si.value=isNaN(px)?'':px;}
 setAct('bold',(parseInt(cs.fontWeight,10)||400)>=600);
 setAct('italic',cs.fontStyle==='italic'||cs.fontStyle.indexOf('italic')>=0);
 var deco=(cs.textDecorationLine||cs.textDecoration||'');
 setAct('underline',deco.indexOf('underline')>=0); setAct('strike',deco.indexOf('line-through')>=0);
 var al=cs.textAlign; setAct('alignL',al==='left'||al==='start'); setAct('alignC',al==='center'); setAct('alignR',al==='right'||al==='end');
 var cbar=bar.querySelector('[data-cur]'); if(cbar)cbar.style.background=cs.color||'#111';
 var lh=q('data-ctl=lh'); if(lh&&doc.activeElement!==lh){var ratio=parseFloat(cs.lineHeight)/parseFloat(cs.fontSize),vals=['1','1.15','1.3','1.5','1.8','2'],best='';for(var i=0;i<vals.length;i++){if(Math.abs(parseFloat(vals[i])-ratio)<0.06){best=vals[i];break;}}lh.value=best;}
}
function stopEdit(){ if(editing){editing.removeAttribute('contenteditable');editing=null;} }
function select(el){ if(editing&&editing!==el)stopEdit(); pop.style.display='none'; sel=el; pos(); }
function selRange(){var s=doc.getSelection();return (editing&&s&&s.rangeCount&&!s.isCollapsed&&editing.contains(s.anchorNode)&&editing.contains(s.focusNode))?s:null;}
function exec(cmd,val){try{doc.execCommand('styleWithCSS',false,true);}catch(e){}try{doc.execCommand(cmd,false,val===undefined?null:val);}catch(e){}}
function elDeco(kind){var v=sel.style.textDecorationLine||sel.style.textDecoration||'',set={};if(v.indexOf('underline')>=0)set.u=1;if(v.indexOf('line-through')>=0)set.s=1;
 if(kind==='underline')set.u=set.u?0:1; else set.s=set.s?0:1;
 var parts=[];if(set.u)parts.push('underline');if(set.s)parts.push('line-through');sel.style.textDecoration=parts.length?parts.join(' '):'none';}
function togglePop(){ if(pop.style.display==='block'){pop.style.display='none';return;} savedRange=selRange();
 var br=bar.getBoundingClientRect(),vw=doc.documentElement.clientWidth||1280;
 pop.style.display='block'; pop.style.left=Math.max(4,Math.min(br.left,vw-158))+'px'; pop.style.top=(br.bottom+4)+'px'; }
function applyColor(hex){ if(!sel)return; var rng=savedRange; snap();
 if(rng&&editing){ editing.focus(); var s=doc.getSelection(); s.removeAllRanges(); s.addRange(rng); exec('foreColor',hex); }
 else {
  /* 元素级着色（2026-07-30 修用户报的「颜色改不动」）：只设 sel.style.color 不够——
     设计包普遍把标题拆成两段颜色（「全球显示器」+ 亮蓝「市场洞察」），那半截有自己的
     颜色来源，父级的 color 盖不住它，用户点了色只看到**变了一半**，像是没反应。

     ⚠️ 第一版我只清后代的内联 color 与 <font color>，**真机复现失败**：报障那份
     设计包里 <span class="accent"> 的蓝色来自样式表里的类规则
     （.slide-title--on-light .accent{color:var(--color-blue-600)}），它既没有内联
     color 也没有 font 标签，清内联对它是空操作，而类规则的优先级高于继承。
     ⚠️ 本段注释在 String.raw 模板串内部，**不能出现反引号**（会当场截断模板字符串）。

     所以判据不能看「有没有内联色」，要看**算出来的颜色是不是和父级不同**——那是唯一
     能同时覆盖内联、类规则、CSS 变量三种来源的口径。这些后代直接写内联色压过去
     （内联 > 类规则）；本来就继承父级的后代不动，免得给 harvest 出去的 HTML 平添噪音。
     必须在改父级之前先算，否则改完大家都一样，就分不出谁原本有自己的颜色了。 */
  var base=getComputedStyle(sel).color, all=sel.querySelectorAll('*'), own=[];
  for(var i=0;i<all.length;i++){ if(getComputedStyle(all[i]).color!==base) own.push(all[i]); }
  sel.style.color=hex;
  for(var j=0;j<own.length;j++){ own[j].style&&(own[j].style.color=hex); own[j].removeAttribute&&own[j].removeAttribute('color'); }
 }
 pop.style.display='none'; pos(); md(); }
bar.addEventListener('mousedown',function(e){e.stopPropagation();var tn=e.target.tagName;if(tn==='INPUT'||tn==='SELECT'||tn==='OPTION')return;e.preventDefault();});
bar.addEventListener('click',function(e){
 var t=e.target.closest?e.target.closest('[data-act]'):null, act=t&&t.getAttribute('data-act');
 if(!act||!sel)return; e.stopPropagation();
 if(act==='color'){togglePop();return;} // 只是开色板，还没改动，不入栈
 snap();
 if(act==='copy'){var c=sel.cloneNode(true),tp=parseFloat(c.style.top),lf=parseFloat(c.style.left);if(!isNaN(tp))c.style.top=(tp+24)+'px';if(!isNaN(lf))c.style.left=(lf+24)+'px';sel.parentElement.insertBefore(c,sel.nextSibling);select(c);md();return;}
 if(act==='del'){sel.remove();select(null);md();return;}
 var rng=selRange();
 if(act==='bold'){ rng?exec('bold'):(sel.style.fontWeight=(parseInt(getComputedStyle(sel).fontWeight,10)||400)>=600?'400':'700'); }
 else if(act==='italic'){ rng?exec('italic'):(sel.style.fontStyle=getComputedStyle(sel).fontStyle==='italic'?'normal':'italic'); }
 else if(act==='underline'){ rng?exec('underline'):elDeco('underline'); }
 else if(act==='strike'){ rng?exec('strikeThrough'):elDeco('line-through'); }
 else if(act==='alignL'){ sel.style.textAlign='left'; }
 else if(act==='alignC'){ sel.style.textAlign='center'; }
 else if(act==='alignR'){ sel.style.textAlign='right'; }
 pos(); md();
});
/* 字号夹到 8–240（2026-07-30 修）：输入框的 min/max 只约束加减箭头，**手输不受限**——
   原先只挡了 v>0，敲个 9999 当场炸版，而且没有撤销可退。空值/非数字保持原样不动。 */
bar.addEventListener('input',function(e){var c=e.target.getAttribute&&e.target.getAttribute('data-ctl');
 if(c==='size'&&sel){var v=parseInt(e.target.value,10);if(!isNaN(v)&&v>0){var px=Math.max(8,Math.min(240,v));snap();sel.style.fontSize=px+'px';box.style.display!=='none'&&pos();md();}}});
bar.addEventListener('change',function(e){var c=e.target.getAttribute&&e.target.getAttribute('data-ctl');
 if(c==='size'&&sel){var v=parseInt(e.target.value,10);if(!isNaN(v)&&v>0){var px=Math.max(8,Math.min(240,v));if(px!==v)e.target.value=px;}} // 越界值失焦时回显被夹后的真实值
 if(c==='lh'&&sel){snap();sel.style.lineHeight=e.target.value||'';pos();md();}});
pop.addEventListener('mousedown',function(e){e.stopPropagation();if(e.target.tagName!=='INPUT')e.preventDefault();});
pop.addEventListener('click',function(e){var c=e.target.getAttribute&&e.target.getAttribute('data-color');if(c){e.stopPropagation();applyColor(c);}});
pop.addEventListener('input',function(e){if(e.target.getAttribute&&e.target.getAttribute('data-ctl')==='custom')applyColor(e.target.value);});
doc.addEventListener('mousedown',function(e){
 if(e.target&&e.target.hasAttribute&&e.target.hasAttribute(KIT))return;
 var el=pick(e.target);
 if(el&&editing===el)return;
 select(el);
 if(el&&el.style.top){snap();drag={el:el,sx:e.clientX,sy:e.clientY,top:parseFloat(el.style.top)||0,left:parseFloat(el.style.left)||0};e.preventDefault();}
});
function startRez(dir){return function(e){ if(!sel)return; e.preventDefault(); e.stopPropagation(); snap();
 rez={el:sel,sx:e.clientX,w:sel.getBoundingClientRect().width/S,dir:dir}; };}
hl.addEventListener('mousedown',startRez(-1)); hr.addEventListener('mousedown',startRez(1));
doc.addEventListener('mousemove',function(e){
 if(rez){var dw=(e.clientX-rez.sx)/S*rez.dir; rez.el.style.width=Math.max(60,Math.round(rez.w+dw))+'px'; pos(); md();}
 else if(drag){drag.el.style.top=Math.round(drag.top+(e.clientY-drag.sy)/S)+'px'; drag.el.style.left=Math.round(drag.left+(e.clientX-drag.sx)/S)+'px'; pos(); md();}
});
doc.addEventListener('mouseup',function(){drag=null;rez=null;});
doc.addEventListener('dblclick',function(e){
 var el=pick(e.target); if(!el)return; drag=null; editing=el;
 snap(); // 进编辑态先存一版：整段文字编辑作为一步可撤销（编辑期间的逐字撤销走浏览器原生）
 el.setAttribute('contenteditable','true'); el.focus();
});
doc.addEventListener('input',function(){md();});
doc.addEventListener('keyup',function(){ if(editing&&bar.style.display!=='none')sync(); });
doc.addEventListener('mouseup',function(){ if(editing&&bar.style.display!=='none')setTimeout(sync,0); });
doc.addEventListener('keydown',function(e){
 /* Ctrl/Cmd+Z 撤销（2026-07-30 用户要求）。**编辑文字时不接管**：那时交给浏览器原生的
    字符级撤销，否则打错一个字要整页回滚。Shift+Z（重做）没做，不做假入口。 */
 if((e.metaKey||e.ctrlKey)&&(e.key==='z'||e.key==='Z')&&!e.shiftKey){
  if(editing)return; e.preventDefault(); undo(); return;
 }
 if(e.key==='Escape'){ if(pop.style.display==='block')pop.style.display='none'; else if(editing)stopEdit(); else select(null); pos(); }
 else if((e.key==='Delete'||e.key==='Backspace')&&sel&&!editing){ e.preventDefault(); snap(); sel.remove(); select(null); md(); }
});
window.__slideKit={
 isDirty:function(){return dirty;},
 harvest:function(){
   stopEdit(); select(null);
   var kits=doc.querySelectorAll('['+KIT+']'); for(var i=0;i<kits.length;i++)kits[i].remove();
   var ce=doc.querySelectorAll('[contenteditable]'); for(var j=0;j<ce.length;j++)ce[j].removeAttribute('contenteditable');
   return '<!DOCTYPE html>'+doc.documentElement.outerHTML;
 },
 destroy:function(){ try{select(null);}catch(e){} }
};
})();`;
}

interface KitWindow extends Window {
  __slideKit?: SlideEditKitHandle;
}

export function attachSlideEditKit(
  frame: HTMLIFrameElement,
  /** 脏状态变化回调。**带布尔参数**：全部撤销回原样时会回落到 false（2026-07-30） */
  onChange?: (dirty: boolean) => void,
): SlideEditKitHandle | null {
  const doc = frame.contentDocument;
  if (!doc || !doc.body) return null;
  const scale = (frame.clientWidth || 640) / 1280;
  const sc = doc.createElement('script');
  sc.setAttribute(KIT, '1');
  sc.textContent = buildKitScript(scale);
  doc.body.appendChild(sc);

  const kitWin = frame.contentWindow as KitWindow | null;
  const msgHandler = (e: MessageEvent) => {
    if (e.source === frame.contentWindow && e.data && e.data.type === 'slide-edit-dirty') {
      onChange?.(e.data.dirty !== false);
    }
  };
  window.addEventListener('message', msgHandler);

  return {
    isDirty: () => Boolean(kitWin?.__slideKit?.isDirty()),
    harvest: () => String(kitWin?.__slideKit?.harvest() || ''),
    destroy: () => {
      window.removeEventListener('message', msgHandler);
      try {
        kitWin?.__slideKit?.destroy();
      } catch {
        // 帧已卸载时静默
      }
    },
  };
}
