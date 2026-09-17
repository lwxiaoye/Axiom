import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const drawer = readFileSync(resolve(__dirname, 'AdminAppDetailDrawer.vue'), 'utf8');

describe('AdminAppDetailDrawer', () => {
  it('concentrates application information, version history, and lifecycle actions in management', () => {
    expect(drawer).toContain('queryAdminAppDetail');
    expect(drawer).toContain('queryAdminAppMetrics');
    expect(drawer).toContain('应用名称');
    expect(drawer).toContain('能力分类');
    expect(drawer).toContain('拥有者名称');
    expect(drawer).toContain('发布时间');
    expect(drawer).toContain('版本历史');
    expect(drawer).toContain('adminRollbackApp');
    expect(drawer).toContain('删除应用');
    expect(drawer).toContain('adminDeleteApp');
    expect(drawer).toContain('下架应用');
    expect(drawer).toContain('恢复上线');
    expect(drawer).toContain('转移负责人');
    expect(drawer).toContain('版本差异');
    expect(drawer).toContain('应用操作审计');
    expect(drawer).toContain('提交发布审核');
    expect(drawer).toContain('审核通过并上线');
    expect(drawer).toContain('AgentMetricsTrendChart');
    expect(drawer).toContain("last_12_months");
    expect(drawer).toContain("metricsFailed.value = true");
    expect(drawer).toContain("message.error('下架失败')");
    expect(drawer).toContain("message.error('恢复上线失败')");
  });
});
