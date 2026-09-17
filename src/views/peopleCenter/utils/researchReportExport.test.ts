/**
 * @jest-environment jsdom
 */
import { TextDecoder, TextEncoder } from 'node:util';
import {
  buildJpegPdf,
  buildReportDocxBytes,
  crc32,
  resolveReportExportSource,
  zipStore,
} from './researchReportExport';

Object.assign(globalThis, { TextDecoder, TextEncoder });

const SAMPLE = `
<!DOCTYPE html><html><head><title>库里生涯数据</title></head>
<body class="research-report" data-kind="research-report">
  <article class="research-article">
    <h1>斯蒂芬·库里生涯数据</h1>
    <h2 id="exec">执行摘要</h2>
    <p>场均 24.6 分<a class="cite" href="#ref-1">1</a>。</p>
    <ul><li>三分改变联盟</li></ul>
    <table><thead><tr><th>赛季</th><th>得分</th></tr></thead>
    <tbody><tr><td>2015-16</td><td>30.1</td></tr></tbody></table>
  </article>
  <script type="text/plain" id="research-markdown"># 斯蒂芬·库里生涯数据</script>
</body></html>
`;

describe('research report export', () => {
  it('crc32 matches the ISO 3309 check vector', () => {
    expect(crc32(new TextEncoder().encode('123456789')).toString(16)).toBe('cbf43926');
  });

  it('zip store files start with PK and keep uncompressed payloads', () => {
    const bytes = zipStore([{ name: 'a.txt', data: new TextEncoder().encode('hello') }]);
    expect(String.fromCharCode(bytes[0], bytes[1])).toBe('PK');
    expect(new TextDecoder().decode(bytes)).toContain('hello');
  });

  it('reads title and markdown from the compiled report', () => {
    const source = resolveReportExportSource(SAMPLE, 'report.html');
    expect(source.title).toBe('库里生涯数据');
    expect(source.markdown).toContain('斯蒂芬·库里生涯数据');
  });

  it('builds a docx that contains headings, cites and tables', () => {
    const bytes = buildReportDocxBytes(SAMPLE, '库里生涯数据');
    const text = new TextDecoder().decode(bytes);
    expect(String.fromCharCode(bytes[0], bytes[1])).toBe('PK');
    expect(text).toContain('word/document.xml');
    expect(text).toContain('Heading1');
    expect(text).toContain('斯蒂芬·库里生涯数据');
    expect(text).toContain('[1]');
    expect(text).toContain('2015-16');
  });

  it('wraps JPEG pages in a PDF catalog', () => {
    const jpeg = Uint8Array.from([0xff, 0xd8, 0xff, 0xd9]);
    const pdf = new TextDecoder('latin1').decode(buildJpegPdf([{ data: jpeg, width: 100, height: 100 }]));
    expect(pdf.startsWith('%PDF-1.4')).toBe(true);
    expect(pdf).toContain('/Type /Catalog');
    expect(pdf).toContain('/DCTDecode');
    expect(pdf).toContain('%%EOF');
  });
});
