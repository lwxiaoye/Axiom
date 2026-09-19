import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const read = (relative: string) => fs.readFileSync(path.join(root, relative), 'utf8');

describe('knowledge analytics frontend contract', () => {
  it('keeps manual retrieval separate from formal analytics routes', () => {
    const api = read('src/views/knowledge/knowledge.api.ts');
    // 原 Java 时代靠一条独立的 manual-test 路由避免「检索测试」被计入运营统计；
    // 现在检索测试与真实检索共用 agent-api 的 /knowledge/retrieval，服务端把它记成
    // source=TEST、运营统计默认不计入，所以这里钉的是：测试面板不再指向已下线的 Java 路径。
    expect(api).toContain('`${KB}/retrieval`');
    expect(api).not.toContain('/ai/knowledge/retrieval/manual-test');
    // 后台「全部知识库总览」统计随 Java 管理页一起下线，api 层只剩用户侧单库统计
    expect(api).not.toContain('/ai/knowledge/admin');
    expect(api).not.toContain('getManagedKnowledgeAnalyticsOverview');
    expect(api).toContain('getOwnedKnowledgeBaseAnalytics');
  });

  it('serves the owner single-base report from agent-api retrieval logs', () => {
    const api = read('src/views/knowledge/knowledge.api.ts');
    const owned = api.slice(api.indexOf('export const getOwnedKnowledgeBaseAnalytics'));
    // 用户侧单库统计的数据源是 agent-api 的检索日志；旧的 Java 路径 404，面板只会报错。
    expect(owned).toMatch(/url: `\$\{KB\}\/bases\/\$\{encodeURIComponent\(id\)\}\/analytics`/);
    expect(owned).not.toContain('/ai/knowledge/base/');
    // agent-api 返回裸 JSON、错误在 detail 里，必须带 KB_OPTS 关掉 Jeecg 信封转换
    expect(owned).toMatch(/\}, KB_OPTS\);/);
  });

  it('distinguishes "no retrieval yet" from a failed load', () => {
    const panel = read('src/views/knowledge/components/KnowledgeAnalyticsPanel.vue');
    // 接口通了但没数据是产品状态，不是故障：不能再拿「统计迁移」当空态文案
    expect(panel).not.toContain('统计迁移');
    expect(panel).toContain('message="还没有检索记录"');
    expect(panel).toMatch(/const hasRetrievals = computed\(\(\) => Number\(overview\.value\?\.metrics\?\.retrievalCount/);
    expect(panel).toContain('v-if="!hasRetrievals"');
    // 热门问题只在接口给了 topQueries 时渲染（管理侧旧接口没有这个字段）
    expect(panel).toContain('v-if="overview.topQueries?.length"');
  });

  it('renders the dense operations panel', () => {
    const panel = read('src/views/knowledge/components/KnowledgeAnalyticsPanel.vue');
    expect(panel).toContain('知识问答量');
    expect(panel).toContain('按命中文件累计');
  });

  it('is an owner single-base report only: no admin scope, no knowledge-base total or ranking', () => {
    const panel = read('src/views/knowledge/components/KnowledgeAnalyticsPanel.vue');

    // 管理侧总览（不带 knowledgeId、scope=admin）没有调用方也没有数据源了，面板必须挂在一个知识库上
    expect(panel).toContain('const props = defineProps<{ knowledgeId: string }>();');
    expect(panel).not.toContain("scope: 'admin'");
    expect(panel).not.toContain('getManagedKnowledgeBaseAnalytics');
    expect(panel).not.toContain('知识库总量');
    expect(panel).not.toContain('知识库排行');
    expect(panel).toContain('await getOwnedKnowledgeBaseAnalytics(props.knowledgeId, range)');
  });
});
