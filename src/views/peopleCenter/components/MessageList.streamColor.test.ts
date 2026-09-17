import { readFileSync } from 'fs';
import { join } from 'path';

const source = readFileSync(join(__dirname, 'MessageList.vue'), 'utf-8');

test('最终回答从首个流式字符起保持黑色，不等待 Run 终态再变色', () => {
  expect(source).toContain('class="message-bubble markdown-body narrative-final"');
  expect(source).not.toContain("isExecutionRunning(message) ? 'narrative-commentary' : 'narrative-final'");
  expect(source).toMatch(
    /\.message-bubble\.narrative-final\s*\{[\s\S]*?color:\s*#111/,
  );
  expect(source).toMatch(
    /\.message-bubble\.narrative-commentary,[\s\S]*?color:\s*var\(--execution-text-muted,\s*#8e8e8e\)/,
  );
});

test('执行过程常态保持灰色，只有执行步骤 hover 变黑', () => {
  expect(source).toContain(
    'class="message-bubble markdown-body preamble-body narrative-commentary"',
  );
  expect(source).not.toMatch(
    /class="message-bubble markdown-body preamble-body"\s*:class="narrativeClass\(message\)"/,
  );
  expect(source).toMatch(
    /\.execution-stream\s*\{[\s\S]*?--execution-text:\s*#8e8e8e;[\s\S]*?--execution-text-hover:\s*#111;[\s\S]*?--execution-text-muted:\s*#8e8e8e;/,
  );
  expect(source).toMatch(
    /\.execution-stream \.preamble-body\.narrative-commentary\s*\{\s*color:\s*var\(--execution-text-muted,\s*#8e8e8e\);/,
  );
  expect(source).toMatch(
    /\.execution-stream :is\([\s\S]*?\.agent-step-tool,[\s\S]*?\):hover[\s\S]*?color:\s*var\(--execution-text-hover\);/,
  );
  expect(source).not.toMatch(/\.preamble-body[^,{]*:hover/);
});
