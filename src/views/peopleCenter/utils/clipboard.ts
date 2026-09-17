/**
 * 复制文本到剪贴板，带非安全上下文兜底。
 *
 * 背景：
 * - `navigator.clipboard` 仅在安全上下文（https / localhost）可用；
 *   Docker 部署若走 http + 局域网 IP，Clipboard API 不可用或权限失败。
 * - 仅用离屏 `textarea` + `execCommand('copy')` 在部分浏览器（尤其未 focus、
 *   继承 `user-select: none`、用户手势被 async 打断）会静默失败。
 *
 * 策略：
 * 1. 安全上下文优先 Clipboard API；
 * 2. 失败或不适用时，用「可选中 span + Range + execCommand」同步兜底
 *    （对齐 ant-design-vue copy-to-clipboard 的成熟路径）。
 *
 * @returns 是否复制成功
 */
export async function copyText(text: string): Promise<boolean> {
  const value = String(text ?? '');
  if (!value) return false;

  if (canUseClipboardApi()) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      // 权限拒绝 / 焦点丢失：落到同步兜底，不能直接当失败。
    }
  }

  return copyTextLegacy(value);
}

function canUseClipboardApi(): boolean {
  try {
    return (
      typeof navigator !== 'undefined' &&
      typeof navigator.clipboard?.writeText === 'function' &&
      typeof window !== 'undefined' &&
      Boolean(window.isSecureContext)
    );
  } catch {
    return false;
  }
}

/**
 * 同步兜底：不依赖 Clipboard API / 安全上下文。
 * 必须在用户手势触发的调用栈里尽量同步执行（避免 async 丢失激活态）。
 */
export function copyTextLegacy(text: string): boolean {
  if (typeof document === 'undefined') return false;
  const value = String(text ?? '');
  if (!value) return false;

  const selection = document.getSelection();
  let originalRange: Range | null = null;
  if (selection && selection.rangeCount > 0) {
    originalRange = selection.getRangeAt(0);
  }

  const mark = document.createElement('span');
  mark.textContent = value;
  // 重置样式，避免继承父级 user-select:none / 行高导致选区失败。
  mark.style.all = 'unset';
  mark.style.position = 'fixed';
  mark.style.top = '0';
  mark.style.left = '0';
  mark.style.clipPath = 'inset(50%)';
  mark.style.whiteSpace = 'pre';
  mark.style.setProperty('-webkit-user-select', 'text');
  mark.style.setProperty('-moz-user-select', 'text');
  mark.style.setProperty('-ms-user-select', 'text');
  mark.style.userSelect = 'text';
  mark.setAttribute('data-copy-helper', '1');

  document.body.appendChild(mark);

  let ok = false;
  try {
    const range = document.createRange();
    range.selectNodeContents(mark);
    selection?.removeAllRanges();
    selection?.addRange(range);
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  } finally {
    if (selection) {
      selection.removeAllRanges();
      if (originalRange) {
        try {
          selection.addRange(originalRange);
        } catch {
          // 原选区可能已失效，忽略。
        }
      }
    }
    mark.remove();
  }

  return ok;
}
