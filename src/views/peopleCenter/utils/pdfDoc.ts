/**
 * pdf.js 共享加载器（2026-07-20 产物展示升级）：主对话内联预览与「我的文件」预览共用。
 * 按需动态 import——pdf.js 连 worker 约 1MB，只有真正要渲染 pdf/office 预览时才拉，
 * 主包不背；worker 用 ES module（.mjs），nginx 需正确 MIME（见 deploy/nginx.local.conf）。
 */
import type { PDFDocumentProxy } from 'pdfjs-dist';

let pdfjsPromise: Promise<typeof import('pdfjs-dist')> | null = null;

export function loadPdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = import('pdfjs-dist').then((m) => {
      m.GlobalWorkerOptions.workerSrc = new URL(
        'pdfjs-dist/build/pdf.worker.min.mjs',
        import.meta.url,
      ).toString();
      return m;
    });
  }
  return pdfjsPromise;
}

export async function loadPdfDocFromUrl(url: string): Promise<PDFDocumentProxy> {
  const pdfjs = await loadPdfjs();
  return pdfjs.getDocument({ url }).promise;
}

/**
 * 释放文档 + 终止后台 worker（永不抛，调用方后续的 `URL.revokeObjectURL` 一定跑得到）。
 *
 * **必须走这个函数，不要写 `doc.destroy()`**：pdf.js v6（本仓库实装 6.1.200）的 `destroy()`
 * 只存在于 `PDFDocumentLoadingTask` 上，`PDFDocumentProxy` 上压根没有（它只有 `cleanup()`，
 * 见 `pdfjs-dist/types/src/display/api.d.ts`）。直接调用是 `TypeError: doc.destroy is not
 * a function`——同步抛出，把紧随其后的 blob 释放整条打断（正是要修的泄漏），在 MyFilesTab
 * 里还会一路逃进 office 转换的 catch，把「预览被作废」误报成「转换失败」。
 * 文档已销毁时读 `loadingTask`（内部走 `_transport`）也可能抛，故整体包 try。
 */
export function destroyPdfDoc(doc: PDFDocumentProxy | null | undefined): void {
  if (!doc) return;
  try {
    void doc.loadingTask?.destroy()?.catch(() => undefined);
  } catch {
    // 已销毁 / transport 已置空：无需再释放
  }
}
