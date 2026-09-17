import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const source = readFileSync(resolve(__dirname, 'PromptDebugDrawer.vue'), 'utf8');

describe('PromptDebugDrawer', () => {
  it('keeps prompt experiments isolated and requires explicit application', () => {
    expect(source).toContain('不会保存提示词、执行工作流或影响线上版本');
    expect(source).toContain("emit('apply', workingPrompt.value)");
  });

  it('shows only the unmodified model response and supports candidate generation', () => {
    expect(source).toContain('模型原始响应');
    expect(source).not.toContain('格式化输出');
    expect(source).not.toContain('formatValid');
    expect(source).toContain('generateWorkflowPrompt');
    expect(source).toContain('debugWorkflowPrompt');
  });

  it('places the generation goal beside the prompt generation button', () => {
    expect(source).toMatch(
      /<div class="panel-actions">\s*<a-input v-model:value="generationGoal"[^>]*>\s*<a-button size="small" :loading="generating" :disabled="!model" @click="generate">/
    );
  });

  it('places the short apply action after prompt generation', () => {
    expect(source).toMatch(
      /自动生成提示词\s*<\/a-button>\s*<a-button size="small" :disabled="workingPrompt === prompt" @click="applyPrompt">应用<\/a-button>/
    );
    expect(source).not.toContain('>应用到编辑器</a-button>');
  });
});
