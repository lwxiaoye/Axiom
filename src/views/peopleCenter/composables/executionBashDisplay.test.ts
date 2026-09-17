import { bashRowTitle, toolStepDisplay } from './executionTimeline';

describe('bash 行只说干了什么，不跟子级', () => {
  it('有 intent 时标题就是这件事，不出现已执行，也不留 target', () => {
    const view = toolStepDisplay({
      name: 'bash',
      status: 'completed',
      operation: 'execute',
      intent: '创建PPT设计文档',
      target: 'cd /workspace && python run_export.py',
      detail: '创建PPT设计文档',
    });
    expect(view.label).toBe('创建PPT设计文档');
    expect(view.label).not.toBe('已执行');
    expect(view.target).toBeUndefined();
    expect(view.detail).toBeUndefined();
  });

  it('intent 缺失时，中文任务短语从 detail 提升为标题，仍不跟子级', () => {
    const view = toolStepDisplay({
      name: 'bash',
      status: 'completed',
      operation: 'bash',
      target: 'mkdir -p /workspace/ppt',
      detail: '创建PPT设计文档',
    });
    expect(view.label).toBe('创建PPT设计文档');
    expect(view.target).toBeUndefined();
    expect(view.detail).toBeUndefined();
  });

  it('没有任务短语时用沙箱完成文案，不说已执行，命令不进行内', () => {
    const view = toolStepDisplay({
      name: 'bash',
      status: 'completed',
      operation: 'execute',
      target: 'python scripts/run_export.py --force',
    });
    expect(view.label).toBe('已在沙箱中完成处理');
    expect(view.label).not.toBe('已执行');
    expect(view.target).toBeUndefined();
  });

  it('进行中也用任务短语，而不是正在沙箱中执行', () => {
    const view = toolStepDisplay({
      name: 'bash',
      status: 'running',
      intent: '创建PPT设计文档',
    });
    expect(view.label).toBe('创建PPT设计文档');
  });

  it('已经投影成「已执行 > 子标题」的旧行，渲染时仍收成一句', () => {
    expect(bashRowTitle({
      status: 'completed',
      label: '已执行',
      detail: '创建PPT设计文档',
    })).toBe('创建PPT设计文档');
  });
});
