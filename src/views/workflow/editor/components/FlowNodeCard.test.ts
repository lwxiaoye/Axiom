import { readFileSync } from 'fs';
import { resolve } from 'path';

describe('FlowNodeCard', () => {
  const source = readFileSync(resolve(__dirname, 'FlowNodeCard.vue'), 'utf8');

  it('does not render the node documentation help entry', () => {
    expect(source).not.toContain('查看节点使用文档');
    expect(source).not.toContain('course-link');
    expect(source).not.toContain('QuestionCircleOutlined');
  });

  it('does not render loop frame styling inside node cards', () => {
    expect(source).not.toContain("'loop-container': isLoopContainer");
    expect(source).not.toContain("'loop-body-node': isLoopBodyStart");
    expect(source).not.toContain('loopFrame?: { left: number; top: number; width: number; height: number }');
    expect(source).not.toContain("'--loop-body-width'");
    expect(source).not.toContain("'--loop-body-height'");
    expect(source).not.toContain('&.loop-body-node');
  });

  it('hides array input when loop mode is conditional', () => {
    expect(source).toContain("getNodeInput(node, 'loopRunMode')");
    expect(source).toContain("loopRunModeInput.value?.value === 'conditional'");
    expect(source).toContain("if (isConditionalLoop.value && input.key === 'loopRunInputArray') return false;");
  });
});
