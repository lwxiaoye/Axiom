import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const read = (relative: string) => readFileSync(resolve(process.cwd(), relative), 'utf8');

describe('knowledge chunk rebuild entry point', () => {
  it('exposes the agent-api rebuild endpoint from the api layer', () => {
    const api = read('src/views/knowledge/knowledge.api.ts');
    const fn = api.slice(api.indexOf('export const rebuildKnowledgeChunks'));
    // 回填接口在 agent-api，必须走 KB 前缀 + KB_OPTS（裸 JSON、错误在 detail）
    expect(fn).toMatch(/url: `\$\{KB\}\/bases\/\$\{knowledgeId\}\/chunks\/rebuild`/);
    expect(fn).toMatch(/\}, KB_OPTS\);/);
  });

  it('only offers a rebuild when documents claim chunks the list cannot show', () => {
    const panel = read('src/views/knowledge/components/KnowledgeChunksPanel.vue');
    const gate = panel.slice(panel.indexOf('const needsRebuild = computed'), panel.indexOf('});', panel.indexOf('const needsRebuild = computed')));
    // 管理侧（旧路径）、加载中、列表非空、关键词搜索都不提示——这些不是「正本表没同步」
    expect(gate).toContain('props.management');
    expect(gate).toContain('loading.value');
    expect(gate).toContain('chunks.value.length');
    expect(gate).toContain('keyword.value.trim()');
    // 判定依据是文档自己记的 chunkCount，且尊重当前的文档筛选
    expect(gate).toMatch(/Number\(document\.chunkCount\) > 0/);
    expect(gate).toContain('props.documentId || selectedDocumentId.value');
  });

  it('rebuilds through the api, then refreshes the list and notifies the parent', () => {
    const panel = read('src/views/knowledge/components/KnowledgeChunksPanel.vue');
    expect(panel).toContain('v-if="needsRebuild"');
    expect(panel).toMatch(/<a-button v-if="canEdit"[^>]*@click="rebuildChunks"/);
    const fn = panel.slice(panel.indexOf('async function rebuildChunks'), panel.indexOf('async function rebuildChunks') + 900);
    expect(fn).toContain('await rebuildKnowledgeChunks(kid)');
    expect(fn).toContain('await loadChunks();');
    expect(fn).toContain("emit('changed');");
    // 失败要把服务端原因显示出来（KB_OPTS 关掉了自动报错）
    expect(fn).toContain("knowledgeErrorMessage(error, '重建分段索引失败')");
  });
});
