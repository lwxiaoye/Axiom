import { isAlarmingToolFailure, stepIconStatus } from './executionIconStatus';

describe('stepIconStatus 警告三角只留给有副作用的失败', () => {
  it('读文件 / glob / 搜索找不到不换三角', () => {
    expect(stepIconStatus({ name: 'read_file', status: 'failed' })).toBe('completed');
    expect(stepIconStatus({ name: 'glob', status: 'failed' })).toBe('completed');
    expect(stepIconStatus({ name: 'search_web', status: 'failed' })).toBe('completed');
    expect(stepIconStatus({ name: 'fetch_tool_result', status: 'failed' })).toBe('completed');
    expect(stepIconStatus({ name: 'browser_fetch', status: 'failed' })).toBe('completed');
  });

  it('点页面、写文件、沙箱、启用技能、下载失败仍用三角', () => {
    expect(isAlarmingToolFailure('browser_act')).toBe(true);
    expect(stepIconStatus({ name: 'browser_act', status: 'failed' })).toBe('failed');
    expect(stepIconStatus({ name: 'write_file', status: 'failed' })).toBe('failed');
    expect(stepIconStatus({ name: 'edit_file', status: 'failed' })).toBe('failed');
    expect(stepIconStatus({ name: 'bash', status: 'failed' })).toBe('failed');
    expect(stepIconStatus({ name: 'use_skill', status: 'failed' })).toBe('failed');
    expect(stepIconStatus({ name: 'download_url', status: 'failed' })).toBe('failed');
  });

  it('成功和进行中保持原状态；联网族运行中仍不闪 running 图标', () => {
    expect(stepIconStatus({ name: 'read_file', status: 'completed' })).toBe('completed');
    expect(stepIconStatus({ name: 'read_file', status: 'running' })).toBe('running');
    expect(stepIconStatus({ name: 'search_web', status: 'running' })).toBe('completed');
    expect(stepIconStatus({ name: 'browser_act', status: 'running' })).toBe('completed');
  });
});
