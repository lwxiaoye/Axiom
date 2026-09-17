/**
 * composer 文件选择器的取数口径（2026-07-28）。
 *
 * 上一批修好了 `listUserFiles` 的 `show_all` 与「我的文件」页的开关，选择器却没跟上：
 * 用户让模型生成 .py/.json/.zip、或用 download_url 取回材料后，在「我的文件」里看得到，
 * **却选不进下一轮对话**——「带进下一轮」那条路是断的。
 */
import {
  limitFileSelection,
  MAX_CHAT_FILE_REFS,
  PICKER_FOLDER_ID,
  pickerOptions,
  pruneSelection,
} from './filePicker';
import type { UserFileItem } from '../myfiles.api';

function file(filename: string, id = filename, source: UserFileItem['source'] = 'generated'): UserFileItem {
  return {
    id, filename, mime: '', size: 1, source,
    threadId: null, folderId: null, expiresAt: null, createdAt: null,
  };
}

describe('pickerOptions', () => {
  it('不做交付物筛选：脚本 / 中间数据 / 取回的材料都能带进下一轮', () => {
    const files = [file('汇报.pptx'), file('build.py'), file('data.json'), file('材料.pdf', 'm1', 'material')];
    expect(pickerOptions(files).map((f) => f.filename)).toEqual([
      '汇报.pptx', 'build.py', 'data.json', '材料.pdf',
    ]);
  });

  it('只摘内部伴生源：幻灯片编辑源与研究报告 Markdown 都不是用户文件', () => {
    const files = [file('汇报.pptx'), file('汇报.slides.json'), file('X.SLIDES.JSON'), file('报告.research.md')];
    expect(pickerOptions(files).map((f) => f.filename)).toEqual(['汇报.pptx']);
  });

  it('空/缺省输入回空数组，不抛', () => {
    expect(pickerOptions(null)).toEqual([]);
    expect(pickerOptions(undefined)).toEqual([]);
  });

  it('选择器固定用扁平全量视图（含已归入文件夹的文件）', () => {
    expect(PICKER_FOLDER_ID).toBe('__all__');
  });
});

describe('pruneSelection', () => {
  const options = [file('汇报.pptx', 'a'), file('build.py', 'b')];

  it('默认（非全量）视图下**不剪**：清单里看不到的非交付物仍然活着', () => {
    // 这是加「显示全部文件」开关时最容易踩的坑：默认清单只回交付物，照它剪会把用户
    // 上一轮刚选好的 build.py 悄悄踢出选中态，而且没有任何提示
    expect(pruneSelection([{ id: 'b', filename: 'build.py' }], [options[0]], false)).toBeNull();
  });

  it('全量视图下剪掉已删除/过期的文件（发送时后端 404 降级不如提前清干净）', () => {
    const kept = pruneSelection(
      [{ id: 'a', filename: '汇报.pptx' }, { id: 'gone', filename: '没了.md' }],
      options,
      true,
    );
    expect(kept).toEqual([{ id: 'a', filename: '汇报.pptx' }]);
  });

  it('全都还在时返回 null（无需改动，不触发多余的 update:modelValue）', () => {
    expect(pruneSelection([{ id: 'a', filename: '汇报.pptx' }], options, true)).toBeNull();
  });

  it('无论是否全量视图，都会把历史选中态收敛到后端的 10 个上限', () => {
    const selected = Array.from({ length: MAX_CHAT_FILE_REFS + 2 }, (_, index) => ({
      id: `f-${index}`,
      filename: `${index}.txt`,
    }));
    expect(pruneSelection(selected, [], false)).toHaveLength(MAX_CHAT_FILE_REFS);
  });
});

describe('limitFileSelection', () => {
  it('按首次出现去重，并最多保留 10 个', () => {
    const selected = [
      { id: 'same', filename: 'A.txt' },
      { id: 'same', filename: 'A-重复.txt' },
      ...Array.from({ length: 12 }, (_, index) => ({ id: `f-${index}`, filename: `${index}.txt` })),
    ];
    const limited = limitFileSelection(selected);
    expect(limited).toHaveLength(MAX_CHAT_FILE_REFS);
    expect(limited[0]).toEqual({ id: 'same', filename: 'A.txt' });
    expect(new Set(limited.map((item) => item.id)).size).toBe(MAX_CHAT_FILE_REFS);
  });
});
