/**
 * FastGPT 对齐门禁（gap-audit §11-P0-1）前端侧：
 * parity.manifest.json 是唯一事实源，注册表/枚举与它不一致即失败。
 * 后端侧由 agent-api/scripts/check_parity_manifest.py 校验 pythonExecutor 列。
 */
import manifest from './parity.manifest.json';
import { FlowNodeInputTypeEnum, FlowNodeOutputTypeEnum, FlowNodeTypeEnum } from './constants';
import { allNodeTemplates } from './templates';

type ManifestNode = {
  flowNodeType: string;
  scope: string;
  frontendEnum: boolean;
  canvasTemplate: boolean;
  pythonExecutor: boolean;
};

const nodes = manifest.nodes as ManifestNode[];

describe('parity manifest gate (frontend)', () => {
  it('covers all 40 blueprint node types exactly once', () => {
    expect(nodes).toHaveLength(40);
    expect(new Set(nodes.map((n) => n.flowNodeType)).size).toBe(40);
  });

  it('frontend FlowNodeTypeEnum matches manifest frontendEnum column', () => {
    const expected = nodes.filter((n) => n.frontendEnum).map((n) => n.flowNodeType);
    const actual = Object.values(FlowNodeTypeEnum) as string[];
    expect([...actual].sort()).toEqual([...expected].sort());
  });

  it('registered canvas templates match manifest canvasTemplate column', () => {
    const expected = nodes.filter((n) => n.canvasTemplate).map((n) => n.flowNodeType);
    const actual = allNodeTemplates.map((t) => t.flowNodeType as string);
    expect([...actual].sort()).toEqual([...expected].sort());
  });

  it('every canvasTemplate node is in frontend enum and not planned/excluded', () => {
    nodes
      .filter((n) => n.canvasTemplate)
      .forEach((n) => {
        expect(n.frontendEnum).toBe(true);
        expect(['implemented', 'partial']).toContain(n.scope);
      });
  });

  it('frontend renderer enum matches manifest frontendEnum column', () => {
    const expected = (manifest.renderers as { value: string; frontendEnum: boolean }[])
      .filter((r) => r.frontendEnum)
      .map((r) => r.value);
    const actual = Object.values(FlowNodeInputTypeEnum) as string[];
    expect([...actual].sort()).toEqual([...expected].sort());
  });

  it('output type enum matches blueprint five kinds', () => {
    const actual = Object.values(FlowNodeOutputTypeEnum) as string[];
    expect([...actual].sort()).toEqual([...(manifest.outputTypes as string[])].sort());
  });
});
