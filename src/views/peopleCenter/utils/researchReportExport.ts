/**
 * Deep Research report export: copy / Markdown / Word / PDF.
 * Word is a real OOXML .docx; PDF paints the compiled HTML then wraps JPEG pages.
 */

import {
  downloadBlob,
  downloadTextFile,
  parseResearchReport,
  reportFilenameStem,
} from './researchReport';

export type ReportExportSource = {
  title: string;
  markdown: string;
  html: string;
};

type Block =
  | { type: 'h'; level: number; text: string }
  | { type: 'p'; text: string; indent?: boolean }
  | { type: 'table'; rows: string[][] };

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[i] = c >>> 0;
  }
  return table;
})();

export function crc32(bytes: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

export function resolveReportExportSource(html: string, filename?: string): ReportExportSource {
  const parsed = parseResearchReport(html);
  const title = reportFilenameStem(parsed?.title || '', filename);
  const markdown = String(parsed?.markdown || articlePlainText(html) || '').trim();
  return { title, markdown, html };
}

export function articlePlainText(html: string): string {
  if (typeof DOMParser === 'undefined') return '';
  const doc = new DOMParser().parseFromString(String(html || ''), 'text/html');
  const article = doc.querySelector('article.research-article, article.research-report, .research-article');
  return String((article || doc.body)?.innerText || '').trim();
}

export function downloadReportMarkdown(source: ReportExportSource) {
  downloadTextFile(`${source.title}.md`, source.markdown || articlePlainText(source.html), 'text/markdown;charset=utf-8');
}

export function downloadReportDocx(source: ReportExportSource) {
  const bytes = buildReportDocxBytes(source.html, source.title);
  downloadBlob(
    `${source.title}.docx`,
    new Blob([bytes], {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    }),
  );
}

export async function downloadReportPdf(source: ReportExportSource) {
  try {
    const blob = await buildReportPdfBlob(source.html);
    downloadBlob(`${source.title}.pdf`, blob);
  } catch {
    printReportHtml(source.html);
  }
}

export function printReportHtml(html: string) {
  const frame = document.createElement('iframe');
  frame.setAttribute('aria-hidden', 'true');
  frame.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;';
  document.body.appendChild(frame);
  const doc = frame.contentDocument;
  if (!doc) {
    frame.remove();
    return;
  }
  doc.open();
  doc.write(html || '');
  doc.close();
  const cleanup = () => frame.remove();
  const run = () => {
    try {
      frame.contentWindow?.focus();
      frame.contentWindow?.print();
    } finally {
      window.setTimeout(cleanup, 800);
    }
  };
  if (frame.contentDocument?.readyState === 'complete') run();
  else frame.onload = run;
}

export function buildReportDocxBytes(html: string, title: string): Uint8Array {
  const blocks = blocksFromHtml(html);
  if (!blocks.length) {
    blocks.push({ type: 'h', level: 1, text: title || '研究报告' });
  }
  const documentXml = renderDocumentXml(blocks);
  const enc = new TextEncoder();
  return zipStore([
    { name: '[Content_Types].xml', data: enc.encode(CONTENT_TYPES) },
    { name: '_rels/.rels', data: enc.encode(RELS) },
    { name: 'word/_rels/document.xml.rels', data: enc.encode(DOCUMENT_RELS) },
    { name: 'word/styles.xml', data: enc.encode(STYLES) },
    { name: 'word/document.xml', data: enc.encode(documentXml) },
  ]);
}

export function zipStore(files: Array<{ name: string; data: Uint8Array }>): Uint8Array {
  const chunks: Uint8Array[] = [];
  const centrals: Uint8Array[] = [];
  let offset = 0;
  const enc = new TextEncoder();
  for (const file of files) {
    const name = enc.encode(file.name);
    const crc = crc32(file.data);
    const local = new Uint8Array(30 + name.length);
    const localView = new DataView(local.buffer);
    localView.setUint32(0, 0x04034b50, true);
    localView.setUint16(4, 20, true);
    localView.setUint16(6, 0x0800, true);
    localView.setUint32(14, crc, true);
    localView.setUint32(18, file.data.length, true);
    localView.setUint32(22, file.data.length, true);
    localView.setUint16(26, name.length, true);
    local.set(name, 30);
    chunks.push(local, file.data);

    const central = new Uint8Array(46 + name.length);
    const centralView = new DataView(central.buffer);
    centralView.setUint32(0, 0x02014b50, true);
    centralView.setUint16(4, 20, true);
    centralView.setUint16(6, 20, true);
    centralView.setUint16(8, 0x0800, true);
    centralView.setUint32(16, crc, true);
    centralView.setUint32(20, file.data.length, true);
    centralView.setUint32(24, file.data.length, true);
    centralView.setUint16(28, name.length, true);
    centralView.setUint32(42, offset, true);
    central.set(name, 46);
    centrals.push(central);
    offset += local.length + file.data.length;
  }
  const centralStart = offset;
  for (const central of centrals) {
    chunks.push(central);
    offset += central.length;
  }
  const eocd = new Uint8Array(22);
  const eocdView = new DataView(eocd.buffer);
  eocdView.setUint32(0, 0x06054b50, true);
  eocdView.setUint16(8, files.length, true);
  eocdView.setUint16(10, files.length, true);
  eocdView.setUint32(12, offset - centralStart, true);
  eocdView.setUint32(16, centralStart, true);
  chunks.push(eocd);
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const out = new Uint8Array(total);
  let cursor = 0;
  for (const chunk of chunks) {
    out.set(chunk, cursor);
    cursor += chunk.length;
  }
  return out;
}

export async function buildReportPdfBlob(html: string): Promise<Blob> {
  const pages = await captureReportJpegPages(html);
  if (!pages.length) throw new Error('empty-pdf');
  return new Blob([buildJpegPdf(pages)], { type: 'application/pdf' });
}

function blocksFromHtml(html: string): Block[] {
  if (typeof DOMParser === 'undefined') return [];
  const doc = new DOMParser().parseFromString(String(html || ''), 'text/html');
  const root =
    doc.querySelector('article.research-article, article.research-report, .research-article') || doc.body;
  const blocks: Block[] = [];
  walkBlocks(root, blocks);
  return blocks;
}

function walkBlocks(root: Element, blocks: Block[]) {
  Array.from(root.childNodes).forEach((node) => {
    if (node.nodeType !== 1) return;
    const el = node as Element;
    const tag = el.tagName.toLowerCase();
    if (tag === 'script' || tag === 'style') return;
    if (/^h[1-4]$/.test(tag)) {
      const text = nodeText(el);
      if (text) blocks.push({ type: 'h', level: Number(tag.slice(1)), text });
      return;
    }
    if (tag === 'p') {
      const text = nodeText(el);
      if (text) blocks.push({ type: 'p', text });
      return;
    }
    if (tag === 'ul' || tag === 'ol') {
      const items = Array.from(el.children).filter((child) => child.tagName.toLowerCase() === 'li');
      items.forEach((item, index) => {
        const text = nodeText(item);
        if (!text) return;
        const prefix = tag === 'ol' ? `${index + 1}. ` : '• ';
        blocks.push({ type: 'p', text: prefix + text, indent: true });
      });
      return;
    }
    if (tag === 'table') {
      const rows = tableRows(el);
      if (rows.length) blocks.push({ type: 'table', rows });
      return;
    }
    if (tag === 'blockquote' || tag === 'pre') {
      const text = nodeText(el);
      if (text) {
        text.split('\n').forEach((line) => {
          if (line.trim()) blocks.push({ type: 'p', text: line.trim(), indent: tag === 'blockquote' });
        });
      }
      return;
    }
    walkBlocks(el, blocks);
  });
}

function nodeText(el: Element): string {
  const clone = el.cloneNode(true) as Element;
  clone.querySelectorAll('a.cite, a.cite-chip, sup.cite, span.cite, .cite').forEach((chip) => {
    const n = String(chip.textContent || '').trim();
    chip.replaceWith(`[${n}]`);
  });
  return String(clone.textContent || '').replace(/\s+/g, ' ').trim();
}

function tableRows(table: Element): string[][] {
  return Array.from(table.querySelectorAll('tr')).map((row) =>
    Array.from(row.querySelectorAll('th,td')).map((cell) => nodeText(cell)),
  ).filter((row) => row.some((cell) => cell));
}

function renderDocumentXml(blocks: Block[]): string {
  const body = blocks.map((block) => {
    if (block.type === 'h') {
      const style = block.level <= 1 ? 'Heading1' : block.level === 2 ? 'Heading2' : 'Heading3';
      return paraXml(block.text, style);
    }
    if (block.type === 'table') return tableXml(block.rows);
    return paraXml(block.text, 'Normal', block.indent);
  }).join('');
  return (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' +
    `<w:body>${body}` +
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>' +
    '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>' +
    '</w:body></w:document>'
  );
}

function paraXml(text: string, style: string, indent = false): string {
  const ind = indent ? '<w:ind w:left="420"/>' : '';
  return (
    '<w:p>' +
    `<w:pPr><w:pStyle w:val="${style}"/>${ind}</w:pPr>` +
    `<w:r><w:t xml:space="preserve">${xmlEscape(text)}</w:t></w:r>` +
    '</w:p>'
  );
}

function tableXml(rows: string[][]): string {
  const cols = Math.max(1, ...rows.map((row) => row.length));
  const width = Math.floor(9000 / cols);
  const grid = `<w:tblGrid>${Array.from({ length: cols }, () => `<w:gridCol w:w="${width}"/>`).join('')}</w:tblGrid>`;
  const tr = rows.map((row, rowIndex) => {
    const cells = Array.from({ length: cols }, (_, i) => {
      const text = row[i] || '';
      const fill = rowIndex === 0 ? '<w:shd w:val="clear" w:fill="F7F7F8"/>' : '';
      return (
        `<w:tc><w:tcPr><w:tcW w:w="${width}" w:type="dxa"/>${fill}</w:tcPr>` +
        `<w:p><w:r><w:t xml:space="preserve">${xmlEscape(text)}</w:t></w:r></w:p></w:tc>`
      );
    }).join('');
    return `<w:tr>${cells}</w:tr>`;
  }).join('');
  return (
    '<w:tbl><w:tblPr><w:tblW w:w="9000" w:type="dxa"/>' +
    '<w:tblBorders>' +
    '<w:bottom w:val="single" w:sz="4" w:color="ECECEC"/>' +
    '<w:insideH w:val="single" w:sz="4" w:color="ECECEC"/>' +
    '</w:tblBorders></w:tblPr>' +
    `${grid}${tr}</w:tbl>`
  );
}

function xmlEscape(value: string): string {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const CONTENT_TYPES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>`;

const RELS = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>`;

const DOCUMENT_RELS = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>`;

const STYLES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:styleId="Normal" w:default="1">
    <w:name w:val="Normal"/>
    <w:rPr><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>
    <w:pPr><w:outlineLvl w:val="0"/><w:spacing w:before="240" w:after="160"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="48"/><w:szCs w:val="48"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>
    <w:pPr><w:outlineLvl w:val="1"/><w:spacing w:before="280" w:after="120"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading3">
    <w:name w:val="heading 3"/><w:basedOn w:val="Normal"/>
    <w:pPr><w:outlineLvl w:val="2"/><w:spacing w:before="200" w:after="80"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr>
  </w:style>
</w:styles>`;

type JpegPage = { data: Uint8Array; width: number; height: number };

async function captureReportJpegPages(html: string): Promise<JpegPage[]> {
  const iframe = document.createElement('iframe');
  iframe.setAttribute('aria-hidden', 'true');
  iframe.style.cssText =
    'position:fixed;left:-12000px;top:0;width:794px;height:1123px;border:0;opacity:0;pointer-events:none;';
  document.body.appendChild(iframe);
  const doc = iframe.contentDocument;
  if (!doc) {
    iframe.remove();
    throw new Error('iframe');
  }
  doc.open();
  doc.write(html || '');
  doc.close();
  await wait(80);
  const body = doc.body;
  const totalHeight = Math.max(body.scrollHeight, body.offsetHeight, 1);
  iframe.style.height = `${Math.min(totalHeight, 24000)}px`;
  const html2canvas = (await import('html2canvas')).default;
  const pageHeight = 1123;
  const pages: JpegPage[] = [];
  try {
    for (let top = 0; top < totalHeight; top += pageHeight) {
      const slice = Math.min(pageHeight, totalHeight - top);
      const canvas = await html2canvas(body, {
        scale: 2,
        useCORS: true,
        backgroundColor: '#ffffff',
        x: 0,
        y: top,
        width: 794,
        height: slice,
        windowWidth: 794,
        windowHeight: slice,
        scrollX: 0,
        scrollY: -top,
      });
      pages.push(canvasToJpeg(canvas));
    }
  } finally {
    iframe.remove();
  }
  return pages;
}

function canvasToJpeg(canvas: HTMLCanvasElement): JpegPage {
  const dataUrl = canvas.toDataURL('image/jpeg', 0.92);
  const binary = atob(dataUrl.split(',')[1] || '');
  const data = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) data[i] = binary.charCodeAt(i);
  return { data, width: canvas.width, height: canvas.height };
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export function buildJpegPdf(pages: JpegPage[]): Uint8Array {
  const A4W = 595.27;
  const A4H = 841.89;
  const enc = new TextEncoder();
  const bodies: Array<Uint8Array | null> = [null, null, null];
  const kids: number[] = [];

  pages.forEach((page) => {
    const scale = Math.min(A4W / page.width, A4H / page.height);
    const w = page.width * scale;
    const h = page.height * scale;
    const x = (A4W - w) / 2;
    const y = A4H - h;
    const content = `q ${n(w)} 0 0 ${n(h)} ${n(x)} ${n(y)} cm /Im0 Do Q\n`;
    const contentBytes = enc.encode(content);
    const pageId = bodies.length;
    const contentId = pageId + 1;
    const imageId = pageId + 2;
    kids.push(pageId);
    bodies[pageId] = enc.encode(
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${n(A4W)} ${n(A4H)}] ` +
        `/Resources << /XObject << /Im0 ${imageId} 0 R >> /ProcSet [/PDF /ImageC] >> ` +
        `/Contents ${contentId} 0 R >>`,
    );
    bodies[contentId] = concatBytes(
      enc.encode(`<< /Length ${contentBytes.length} >>\nstream\n`),
      contentBytes,
      enc.encode('\nendstream'),
    );
    bodies[imageId] = concatBytes(
      enc.encode(
        `<< /Type /XObject /Subtype /Image /Width ${page.width} /Height ${page.height} ` +
          `/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${page.data.length} >>\nstream\n`,
      ),
      page.data,
      enc.encode('\nendstream'),
    );
  });

  bodies[1] = enc.encode('<< /Type /Catalog /Pages 2 0 R >>');
  bodies[2] = enc.encode(
    `<< /Type /Pages /Kids [${kids.map((id) => `${id} 0 R`).join(' ')}] /Count ${pages.length} >>`,
  );

  const header = enc.encode('%PDF-1.4\n');
  const parts: Uint8Array[] = [header];
  const offsets = [0];
  let cursor = header.length;
  const writeObj = (id: number, body: Uint8Array) => {
    const prefix = enc.encode(`${id} 0 obj\n`);
    const suffix = enc.encode('\nendobj\n');
    offsets[id] = cursor;
    parts.push(prefix, body, suffix);
    cursor += prefix.length + body.length + suffix.length;
  };
  for (let id = 1; id < bodies.length; id++) writeObj(id, bodies[id] as Uint8Array);

  const xrefStart = cursor;
  let xref = `xref\n0 ${bodies.length}\n0000000000 65535 f \n`;
  for (let i = 1; i < bodies.length; i++) {
    xref += `${String(offsets[i]).padStart(10, '0')} 00000 n \n`;
  }
  parts.push(
    enc.encode(xref),
    enc.encode(`trailer\n<< /Size ${bodies.length} /Root 1 0 R >>\nstartxref\n${xrefStart}\n%%EOF\n`),
  );
  return concatBytes(...parts);
}

function n(value: number): string {
  return (Math.round(value * 100) / 100).toString();
}

function concatBytes(...chunks: Uint8Array[]): Uint8Array {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const out = new Uint8Array(total);
  let cursor = 0;
  for (const chunk of chunks) {
    out.set(chunk, cursor);
    cursor += chunk.length;
  }
  return out;
}
