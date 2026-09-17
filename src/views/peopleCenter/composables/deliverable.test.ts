/**
 * 交付物判据（2026-07-27）：产物卡口径与「本轮是否已交付」口径必须是同一把尺子。
 * 两者一旦分家，失败轮次会被判成已交付 → 错误横幅被隐藏、执行头按正常终态显示，
 * 而产物卡那边又把过程文件滤光——用户拿到一条什么都没有的空气泡。
 */
import {
  hasDeliveredContent,
  isDeliverableFile,
  isDeliverableName,
  myFilesRouteFor,
  visibleDeliverables,
} from './deliverable';

describe('isDeliverableName / isDeliverableFile', () => {
  it.each(['报告.docx', '演示.pptx', '数据.xlsx', '说明.md', '页面.html', '图.png', 'a.PDF'])(
    '%s 是交付物',
    (name) => expect(isDeliverableName(name)).toBe(true),
  );

  it.each(['build.py', 'data.json', '包.zip', 'Dockerfile', '.env', 'noext'])(
    '%s 不是交付物',
    (name) => expect(isDeliverableName(name)).toBe(false),
  );

  it('PPT Studio 的 source ZIP 也不作为用户交付物', () => {
    expect(isDeliverableName('汇报-source.zip')).toBe(false);
    expect(isDeliverableName('汇报-SOURCE.ZIP')).toBe(false);
    expect(isDeliverableName('汇报.zip')).toBe(false);
  });

  it('后端下发的 deliverable 字段优先于后缀兜底（两边都认）', () => {
    expect(isDeliverableFile({ filename: 'README.md', deliverable: false })).toBe(false);
    expect(isDeliverableFile({ filename: 'build.py', deliverable: true })).toBe(true);
  });
});

describe('visibleDeliverables（产物卡口径）', () => {
  it('滤掉过程文件与幻灯片编辑源，保留真正的产物', () => {
    const files = [
      { filename: 'build.py' },
      { filename: '汇报.pptx' },
      { filename: '汇报.slides.json' },
      { filename: '材料.zip' },
      { filename: '汇报-source.zip' },
    ];
    expect(visibleDeliverables(files).map((f) => f.filename)).toEqual(['汇报.pptx']);
  });

  it('研究报告只露出 HTML，隐藏 .research.md 伴生源', () => {
    const files = [
      { filename: '竞品分析.html', source: 'research', deliverable: true },
      { filename: '竞品分析.research.md', source: 'research', deliverable: true },
    ];
    expect(visibleDeliverables(files).map((f) => f.filename)).toEqual(['竞品分析.html']);
  });

  it('空/缺省输入回空数组，不抛', () => {
    expect(visibleDeliverables(null)).toEqual([]);
    expect(visibleDeliverables(undefined)).toEqual([]);
  });
});

describe('myFilesRouteFor（时间线文件按钮的落点）', () => {
  it('download_url 取回的材料一律带 all=1：source=material 不看后缀，默认清单必然滤掉', () => {
    // 真机现象：时间线出现「已下载 报告.pdf」的可点按钮，点进「我的文件」却找不到它
    expect(myFilesRouteFor({ name: 'download_url', target: '报告.pdf' })).toBe('/center/files?all=1');
    expect(myFilesRouteFor({ name: 'download_url', target: 'README.md' })).toBe('/center/files?all=1');
  });

  it('write_file 写出的 .py/.json 同理：过程文件在默认清单里看不到', () => {
    expect(myFilesRouteFor({ name: 'write_file', target: 'build.py' })).toBe('/center/files?all=1');
    expect(myFilesRouteFor({ name: 'bash', target: 'data.json' })).toBe('/center/files?all=1');
  });

  it('真产物跳干净的默认视图：默认视图克制就是为了别让产物淹在过程文件里', () => {
    expect(myFilesRouteFor({ name: 'write_file', target: '汇报.pptx' })).toBe('/center/files');
    expect(myFilesRouteFor({ name: 'read_file', target: '需求.md' })).toBe('/center/files');
  });

  it('判不准时偏向 all=1（全量是默认视图的超集，跳错的代价只是列表吵一点）', () => {
    expect(myFilesRouteFor({})).toBe('/center/files?all=1');
    expect(myFilesRouteFor({ name: 'read_file', target: '' })).toBe('/center/files?all=1');
  });
});

describe('hasDeliveredContent（失败降噪判据）', () => {
  it('只写了 build.py 就失败的轮次 = 没交付：错误横幅必须照常显示', () => {
    expect(hasDeliveredContent('', [{ filename: 'build.py' }])).toBe(false);
    expect(hasDeliveredContent('   ', [{ filename: 'build.py' }])).toBe(false);
  });

  it('download_url 取回的材料同理：落库了不等于交付了', () => {
    expect(hasDeliveredContent('', [{ filename: '素材.zip' }])).toBe(false);
  });

  it('真出了产物或有正文才算交付', () => {
    expect(hasDeliveredContent('', [{ filename: '汇报.pptx' }])).toBe(true);
    expect(hasDeliveredContent('已经整理好了', [])).toBe(true);
    expect(hasDeliveredContent('', [])).toBe(false);
  });

  it('与产物卡口径逐条一致：有卡片 ⇔ 算交付', () => {
    const cases = [
      [{ filename: 'build.py' }],
      [{ filename: '汇报.pptx' }],
      [{ filename: '汇报.slides.json' }],
      [{ filename: 'build.py' }, { filename: '结果.md' }],
      [],
    ];
    for (const files of cases) {
      expect(hasDeliveredContent('', files)).toBe(visibleDeliverables(files).length > 0);
    }
  });
});
