import {
  composerBubbleAttachments,
  isComposerReferenceKind,
  threadRefAttachment,
} from './composerBubbleAttachments';

describe('composerBubbleAttachments', () => {
  it('把 composer 里引用过的资源都做成用户气泡卡', () => {
    const cards = composerBubbleAttachments({
      uploads: [{ filename: '简历.pdf', kind: 'pdf', file_id: 'f1' }],
      files: [{ id: 'img1', filename: '照片.png' }],
      threads: [{ title: '上周的方案讨论' }],
      knowledge: [{ name: '制度库' }],
      skills: [{ name: 'PPT 助手' }],
      subagent: { name: '面试助手' },
      webSearch: true,
    });
    expect(cards.map((item) => `${item.kind}:${item.filename}`)).toEqual([
      'pdf:简历.pdf',
      'image:照片.png',
      'thread_ref:上周的方案讨论（对话记录）',
      'knowledge:制度库',
      'skill:PPT 助手',
      'subagent:面试助手',
      'web:网页搜索',
    ]);
    expect(cards.find((item) => item.kind === 'skill')?.referenceId).toBeUndefined();
  });

  it('在用户气泡上保留 Skill 的稳定 id，供编辑重发恢复', () => {
    expect(composerBubbleAttachments({
      skills: [{ id: 'skill-ppt', name: 'PPT 助手' }],
    })).toEqual([
      { filename: 'PPT 助手', kind: 'skill', referenceId: 'skill-ppt' },
    ]);
  });

  it('同名同 kind 去重，空名字丢掉', () => {
    const cards = composerBubbleAttachments({
      skills: [{ name: 'PPT 助手' }, { name: 'PPT 助手' }, { name: '  ' }],
      subagent: { name: '' },
      webSearch: false,
    });
    expect(cards).toEqual([{ filename: 'PPT 助手', kind: 'skill' }]);
  });

  it('对话引用文件名口径与历史回放一致', () => {
    expect(threadRefAttachment({ title: '帮我改 report.xlsx' }).filename)
      .toBe('帮我改 report.xlsx（对话记录）');
    expect(threadRefAttachment({}).filename).toBe('未命名对话（对话记录）');
  });

  it('识别引用类 kind，避免当文件去打开', () => {
    expect(isComposerReferenceKind('skill')).toBe(true);
    expect(isComposerReferenceKind('pdf')).toBe(false);
  });
});
