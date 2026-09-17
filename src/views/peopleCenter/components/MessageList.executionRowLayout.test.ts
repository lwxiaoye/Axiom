import fs from 'node:fs';
import path from 'node:path';

const source = fs.readFileSync(path.resolve(__dirname, 'MessageList.vue'), 'utf8');

function styleBlock(selector: string, nextSelector: string): string {
  const start = source.indexOf(selector);
  const end = source.indexOf(nextSelector, start);
  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  return source.slice(start, end);
}

describe('MessageList 执行步骤单行布局', () => {
  it('所有结构化执行行都以单行弹性布局承载，不把目标值挤到下一行', () => {
    const tool = styleBlock('.agent-step-tool {', '.agent-step-tool > .step-node');
    const artifact = styleBlock('.agent-step-artifact {', '.agent-step-artifact.failed');
    const verification = styleBlock('.agent-step-verification {', '.agent-step-verification.failed');
    const read = styleBlock('.agent-step-read {', '.agent-step-read .ast-page-summary');
    const subagent = styleBlock('.agent-step-sub {', '.agent-step-sub > .step-node');

    for (const block of [tool, artifact, verification, read, subagent]) {
      expect(block).toContain('flex-wrap: nowrap');
      expect(block).toContain('align-items: center');
    }
  });

  it('动作标题、目标、结果和产物名都在行内截断，完整文本仍可通过 title 获取', () => {
    const label = styleBlock('.ast-label {', '/* 动作目标');
    const target = styleBlock('.ast-target,', '/* 非文件类目标');
    const result = styleBlock('.artifact-file-names,', '.ast-result.failed');

    for (const block of [label, target, result]) {
      expect(block).toContain('min-width: 0');
      expect(block).toContain('overflow: hidden');
      expect(block).toContain('text-overflow: ellipsis');
      expect(block).toContain('white-space: nowrap');
    }
    expect(target).toContain('flex: 0 1 auto');
    expect(result).not.toContain('flex: 1 1 100%');
    expect(source).toContain(':title="toolRowTitle(row.step, isLiveRunningAction(message.agentSteps, row.stepIndex))"');
    expect(source).toContain("'action-running': isLiveRunningAction(message.agentSteps, row.stepIndex)");
    expect(source).not.toContain("'action-running': 'status' in row.step && row.step.status === 'running'");
    expect(source).not.toContain('{{ visibleStepTarget(row.step) }}');
    expect(source).toContain('class="artifact-file-names" :title=');
  });

  it('只有展开的原始命令详情可以另起一行，动作回执本身保持单行', () => {
    const shell = styleBlock('.agent-step-tool.has-shell-panel {', '.ast-shell-toggle {');
    expect(shell).toContain('flex-wrap: wrap');
    expect(shell).toContain('独立详情区');
    expect(source).toContain('.ast-shell-panel {\n  flex: 1 1 100%');
  });

  it('执行步骤箭头默认隐藏，悬停、聚焦或展开后显示灰色箭头', () => {
    const group = styleBlock('.agent-step-tool.run-group .rg-toggle {', '.agent-step-tool.run-group:hover .rg-toggle,');
    const shell = styleBlock('.ast-shell-toggle {', '.agent-step-tool:hover .ast-shell-toggle,');
    expect(group).toContain('opacity: 0;');
    expect(group).toContain('color: #b0b4bb;');
    expect(shell).toContain('opacity: 0;');
    expect(source).not.toContain('.rg-toggle.is-search-group');
    expect(source).not.toContain("'is-search-group': row.step.familyId === 'search'");
    expect(source).toContain('.agent-step-tool.run-group:hover .rg-toggle');
    expect(source).toContain('.agent-step-tool.run-group:focus-visible .rg-toggle');
    expect(source).toContain('.agent-step-tool.run-group .rg-toggle.open');
    expect(source).toContain('.ast-shell-toggle.open');
    expect(source).toContain(":class=\"['ast-shell-toggle', { open: shellOpen[message.id + ':' + row.stepIndex] }]\"");
  });

  it('普通归拢组显示步骤数量，网页搜索组只显示完成标签', () => {
    const summaryStart = source.indexOf('function runGroupSummary');
    const summaryEnd = source.indexOf('/** 行尾是否铺 target/detail', summaryStart);
    const summary = source.slice(summaryStart, summaryEnd);
    const groupTarget = styleBlock('.agent-step-tool.run-group > .ast-target-text {', '.agent-step-tool.run-group.is-static');

    expect(summary).toContain('return `${step.count} ${unit}`');
    expect(summary).toContain("if (step.familyId === 'search') return ''");
    expect(summary).not.toContain('return `${step.count} ${unit} · 已完成`');
    expect(source).toContain('v-if="runGroupSummary(row.step)"');
    expect(groupTarget).toContain('flex: 0 1 auto');
    expect(groupTarget).toContain('max-width: 18ch');
  });

  it('网页搜索使用地球图标、单行详情和 Enter/Space 展开操作', () => {
    const searchLabel = styleBlock('.agent-step-tool > .ast-label.ast-search-label {', '/* 行级白色高光扫过');
    expect(source).toContain('v-if="row.step.name === \'search_web\'"');
    expect(source).toContain('kind="web"');
    expect(source).toContain('searchWebStepTitle(row.step, isLiveRunningAction(message.agentSteps, row.stepIndex))');
    expect(source).toContain('@keydown.enter="row.step.expandable !== false && toggleRunGroup(row.step.groupKey)"');
    expect(source).toContain('@keydown.space.prevent="row.step.expandable !== false && toggleRunGroup(row.step.groupKey)"');
    expect(searchLabel).toContain('max-width: 100%');
    expect(searchLabel).toContain('flex: 1 1 auto');
    expect(source).toContain("'run-group-member': row.groupMember");
  });

  it('所有执行步骤通过 TransitionGroup 使用对称的展开与收起动画', () => {
    expect(source).toContain('<TransitionGroup');
    expect(source).toContain('name="execution-row"');
    expect(source).toContain('class="execution-row-transition"');
    expect(source).toContain('.execution-row-enter-active,\n.execution-row-leave-active');
    expect(source).toContain('.execution-row-enter-from,\n.execution-row-leave-to');
    expect(source).toMatch(/\.execution-row-leave-to\s*\{[\s\S]*?grid-template-rows:\s*0fr;[\s\S]*?opacity:\s*0;/);
    expect(source).toMatch(/@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.execution-row-leave-active[\s\S]*?transition:\s*none;/);
    expect(source).not.toContain('animation: exec-row-in');
  });
});
