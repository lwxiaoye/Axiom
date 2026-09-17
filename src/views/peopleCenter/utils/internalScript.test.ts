/**
 * 内部脚本围栏剥除的判据边界（2026-07-28）。
 *
 * 这条闸只有一个失败模式：**吞掉用户真正索要的代码**，而且是静默的——用户说「把刚才那个
 * 脚本贴出来」，模型照做了，正文里那段代码却在渲染前被剥光，屏幕上只剩一句「好的，这是
 * 刚才的脚本：」。统一文件系统落地后 `/workspace/files/` 变成面向用户的路径（write_file
 * 的回执自己就在教模型说它），旧的整段 `/workspace/` 判据于是天天误伤。
 */
import { stripInternalScriptBlocks } from './internalScript';

const fence = (code: string) => ['```python', code, '```'].join('\n');

describe('stripInternalScriptBlocks', () => {
  it('/workspace/files/ 是用户路径，代码块必须留下', () => {
    const body = `这是刚才那个脚本：\n\n${fence("open('/workspace/files/报告.md').read()")}`;
    expect(stripInternalScriptBlocks(body)).toContain('/workspace/files/报告.md');
  });

  it.each([
    'cd /workspace/files && ls',
    "path = '/workspace/files'",
    'shutil.copy(out, "/workspace/files/汇报.pptx")',
  ])('%s 不算内部标记', (code) => {
    expect(stripInternalScriptBlocks(fence(code))).toContain(code);
  });

  it.each([
    ['/workspace/tmp/build.py', "open('/workspace/tmp/build.py')"],
    ['/workspace/slides', "glob('/workspace/slides/page-*.html')"],
    ['/workspace/proj', 'os.chdir("/workspace/proj")'],
    ['spec_lock', 'load(spec_lock)'],
    ['svg_output', 'svg_output(page)'],
    ['build_deck', 'build_deck.py --name x'],
  ])('%s 仍然是内部标记，整块剥除', (_marker, code) => {
    expect(stripInternalScriptBlocks(fence(code))).toBe('');
  });

  it('/workspace/filesystem 这类前缀撞名不放行（\\b 要求 files 后面是非单词字符）', () => {
    expect(stripInternalScriptBlocks(fence('ls /workspace/filesystem'))).toBe('');
  });

  it('同一段正文里内部围栏被剥、用户围栏留下，正文其余部分不动', () => {
    const body = [
      '先看生成脚本：',
      fence('open("/workspace/tmp/gen.py")'),
      '再看你要的这段：',
      fence('print("hello")'),
    ].join('\n\n');
    const out = stripInternalScriptBlocks(body);
    expect(out).not.toContain('/workspace/tmp/gen.py');
    expect(out).toContain('print("hello")');
    expect(out).toContain('再看你要的这段：');
  });

  it('流式中未闭合的尾部围栏同样按标记判定（内部的先藏住，用户的照常边出边看）', () => {
    expect(stripInternalScriptBlocks('生成中：\n```python\nopen("/workspace/tmp/a")')).toBe('生成中：');
    expect(stripInternalScriptBlocks('生成中：\n```python\nopen("/workspace/files/a")'))
      .toContain('/workspace/files/a');
  });

  it('无围栏正文原样返回（只是 trim + 折叠多余空行）', () => {
    expect(stripInternalScriptBlocks('  已经完成了。\n\n\n还有一点。  ')).toBe('已经完成了。\n\n还有一点。');
    expect(stripInternalScriptBlocks('')).toBe('');
  });
});
