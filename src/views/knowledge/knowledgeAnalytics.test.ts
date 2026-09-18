import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const read = (relative: string) => fs.readFileSync(path.join(root, relative), 'utf8');

describe('knowledge analytics frontend contract', () => {
  it('keeps manual retrieval separate from formal analytics routes', () => {
    const api = read('src/views/knowledge/knowledge.api.ts');
    // 原 Java 时代靠一条独立的 manual-test 路由避免「检索测试」被计入运营统计；
    // 现在检索测试与真实检索共用 agent-api 的 /knowledge/retrieval，该接口本身不写
    // 任何统计，所以这里钉的是：测试面板不再指向已下线的 Java 路径。
    expect(api).toContain('`${KB}/retrieval`');
    expect(api).not.toContain('/ai/knowledge/retrieval/manual-test');
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
