import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const read = (relative: string) => fs.readFileSync(path.join(root, relative), 'utf8');

describe('knowledge analytics frontend contract', () => {
  it('keeps manual retrieval separate from formal analytics routes', () => {
    const api = read('src/views/knowledge/knowledge.api.ts');
    expect(api).toContain("retrievalManualTest: '/ai/knowledge/retrieval/manual-test'");
    expect(api).toContain('getManagedKnowledgeAnalyticsOverview');
    expect(api).toContain('getManagedKnowledgeBaseAnalytics');
    expect(api).toContain('getOwnedKnowledgeBaseAnalytics');
  });

  it('renders the dense operations panel and protected dynamic menu destination', () => {
    const panel = read('src/views/knowledge/components/KnowledgeAnalyticsPanel.vue');
    const page = read('src/views/knowledge/analytics/index.vue');
    expect(panel).toContain('知识问答量');
    expect(panel).toContain('按命中归属累计');
    expect(panel).toContain("scope: 'admin' | 'owner'");
    expect(page).toContain("path: '/knowledge/base'");
    expect(page).toContain("tab: 'analytics'");
  });

  it('omits the knowledge-base total for an owner single-base report', () => {
    const panel = read('src/views/knowledge/components/KnowledgeAnalyticsPanel.vue');

    expect(panel).toContain('<article v-if="!isSingleBase"><span>知识库总量');
    expect(panel).toContain("'single-base-stock': isSingleBase");
  });
});
