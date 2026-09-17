import fs from 'node:fs';
import path from 'node:path';

const activity = fs.readFileSync(path.resolve(__dirname, 'ResearchActivityItem.vue'), 'utf8');
const panel = fs.readFileSync(path.resolve(__dirname, 'ResearchTeamPanel.vue'), 'utf8');

describe('研究协作过程不向右溢出裁切', () => {
  it('搜索结果卡把左缩进算进宽度，避免 100% + margin 把右侧圆角切掉', () => {
    expect(activity).toContain('.search-activity { min-width: 0; max-width: 100%; }');
    expect(activity).toContain('max-width: calc(100% - 32px)');
    expect(activity).toContain('.results-clip-inner { min-width: 0; min-height: 0; overflow: hidden; }');
    expect(activity).toContain('.result-card { display: block; min-width: 0;');
  });

  it('折叠动画容器允许子项在列宽内收缩，而不是撑出后再被 overflow 裁切', () => {
    expect(panel).toContain('.research-team { min-width: 0; max-width: 100%;');
    expect(panel).toContain('.activity-row { display: grid; grid-template-rows: 1fr; width: 100%; min-width: 0; max-width: 100%; }');
    expect(panel).toContain('.activity-row > * { min-width: 0; min-height: 0; }');
  });
});
