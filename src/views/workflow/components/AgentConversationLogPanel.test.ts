import fs from 'fs';
import path from 'path';

const source = fs.existsSync(path.join(__dirname, 'AgentConversationLogPanel.vue'))
  ? fs.readFileSync(path.join(__dirname, 'AgentConversationLogPanel.vue'), 'utf8')
  : '';

test('对话日志面板共用筛选状态，并区分后台和创建者接口', () => {
  expect(source).toContain("access: 'admin' | 'owner'");
  expect(source).toContain('queryAdminConversationLogs');
  expect(source).toContain('downloadOwnerConversationLogs');
  expect(source).toContain('查看详情');
});

test('对话日志按会话聚合，并复用监测的时间范围筛选项', () => {
  expect(source).toContain("const range = ref<MetricRange>('last_7_days')");
  expect(source).toContain("{ value: 'quarter_to_date', label: '本季度至今' }");
  expect(source).toContain("{ title: '消息数', key: 'messageCount'");
  expect(source).toContain('queryAdminConversationLogDetail');
});

test('对话详情以气泡展示消息，并在列表汇总点赞和点踩', () => {
  expect(source).toContain('class="conversation-bubble"');
  expect(source).toContain('<LikeOutlined />');
  expect(source).toContain('<DislikeOutlined />');
  expect(source).toContain("class=\"feedback-summary\"");
  expect(source).toContain("{ title: '最近消息时间', key: 'lastMessageAt'");
});

test('筛选条件以纵向单元排列，并让无标签控件与操作区底部对齐', () => {
  expect(source).toContain('class="log-filter-item log-filter-keyword"');
  expect(source).toContain('class="log-filter-actions"');
  expect(source).toContain('.log-filters { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px;');
  expect(source).toContain('.log-filter-item { display: flex; flex-direction: column; margin: 0; }');
});
