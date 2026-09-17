import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const drawer = readFileSync(resolve(__dirname, 'WorkflowAppApiDrawer.vue'), 'utf8');

describe('WorkflowAppApiDrawer credential boundary', () => {
  it('allows the publisher to copy a key after reload without using URLs or browser storage', () => {
    expect(drawer).toContain('key.secret');
    expect(drawer).toContain('之后仍可点击“复制”获取完整 Key');
    expect(drawer).not.toContain('仅显示一次');
    expect(drawer).not.toContain('完整密钥只在创建当次可复制');
    expect(drawer).not.toContain('localStorage.setItem');
    expect(drawer).not.toContain('URLSearchParams');
  });

  it('uses public switches and exposes no ticket embedding flow', () => {
    expect(drawer).toContain('公开配置');
    expect(drawer).toContain('启用 API');
    expect(drawer).toContain('启用 iframe 嵌入');
    expect(drawer).not.toContain('/tickets');
    expect(drawer).toContain('OpenAI 兼容');
  });

  it('offers an origin-bound qze key for a static iframe URL without using qza', () => {
    expect(drawer).toContain('qze_');
    expect(drawer).toContain('embedKeyId');
    expect(drawer).toContain('嵌入 Key');
  });

  it('keeps name and masked credential together while separating enablement from deletion', () => {
    expect(drawer).toContain('key-identity');
    expect(drawer).toContain('maskedKey');
    expect(drawer).toContain('<a-switch');
    expect(drawer).toContain('setKeyEnabled');
    expect(drawer).toContain('DeleteOutlined');
    expect(drawer).toContain('copyKey');
    expect(drawer).toContain('copyText');
    expect(drawer).toContain("document.execCommand('copy')");
    expect(drawer).not.toContain('撤销</a-button>');
  });
});
