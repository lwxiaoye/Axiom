import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const page = readFileSync(resolve(__dirname, 'AgentMetricsPage.vue'), 'utf8');

describe('AgentMetricsPage', () => {
  it('has every requested range and inline failure behavior', () => {
    expect(page).toContain("const selectedRange = ref<MetricRange>('last_7_days')");
    expect(page).toContain("{ value: 'last_4_weeks', label: '过去 4 周' }");
    expect(page).toContain("{ value: 'all_time', label: '所有时间' }");
    expect(page).toContain('queryWorkflowAppMetrics(currentAppId, selectedRange.value)');
    expect(page).toContain('监测数据暂时无法加载');
    expect(page).toContain('平均每会话消息数');
    expect(page).toContain('.back-link:focus-visible');
  });

  it('renders each metric as its own trend card', () => {
    expect(page).toContain('v-for="item in metricCards"');
    expect(page).toContain(':range-label="selectedRangeLabel"');
    expect(page).toContain("key: 'sessions'");
    expect(page).toContain("key: 'activeUsers'");
    expect(page).toContain("key: 'averageMessages'");
    expect(page).toContain("key: 'messages'");
    expect(page).toContain("key: 'newUsers'");
    expect(page).toContain("key: 'returningUsers'");
    expect(page).toContain('新增用户数');
    expect(page).toContain('回访用户数');
  });

  it('refreshes when navigating between different agents', () => {
    expect(page).toContain("const appId = computed(() => String(route.params.appId || ''));");
    expect(page).toContain('() => route.params.appId');
    expect(page).toContain('if (nextAppId !== previousAppId) loadPage();');
    expect(page).toContain('currentAppId === appId.value');
  });
});
