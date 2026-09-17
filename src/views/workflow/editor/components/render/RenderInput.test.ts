import { readFileSync } from 'fs';
import { resolve } from 'path';

describe('RenderInput', () => {
  it('enables searchable select inputs for schema and timezone fields', () => {
    const source = readFileSync(resolve(__dirname, 'RenderInput.vue'), 'utf8');

    expect(source).toContain(':show-search="selectSearchable"');
    expect(source).toContain("props.input.key === 'from_timezone'");
    expect(source).toContain("props.input.key === 'to_timezone'");
    expect(source).toContain(':filter-option="selectFilterOption"');
    expect(source).toContain('function selectFilterOption');
  });

  it('renders secret schema fields with a password control', () => {
    const source = readFileSync(resolve(__dirname, 'RenderInput.vue'), 'utf8');

    expect(source).toContain('FlowNodeInputTypeEnum.password');
    expect(source).toContain('<a-input-password');
    expect(source).toContain('autocomplete="new-password"');
  });

  it('keeps switch inputs compact in the node config panel', () => {
    const source = readFileSync(resolve(__dirname, 'RenderInput.vue'), 'utf8');

    expect(source).toContain('class="compact-switch"');
    expect(source).toContain('align-self: flex-start');
    expect(source).toContain('width: auto');
  });

  it('gives node numeric inputs a fixed height so their value stays vertically centered', () => {
    const source = readFileSync(resolve(__dirname, 'RenderInput.vue'), 'utf8');

    expect(source).toContain(':deep(.node-number-input.ant-input-number-sm) {\n  display: inline-flex;\n  height: 34px;');
    expect(source).toContain('line-height: 32px;');
  });

  it('renders persisted reference values with the reference picker', () => {
    const source = readFileSync(resolve(__dirname, 'RenderInput.vue'), 'utf8');

    expect(source).toContain('if (isReferenceValue(props.input.value))');
    expect(source).toContain('return referenceIndex');
    expect(source).toContain('const index = effectiveRenderTypeIndex()');
  });

  it('shows database URI examples directly on the SQL query node', () => {
    const source = readFileSync(resolve(__dirname, 'RenderInput.vue'), 'utf8');

    expect(source).toContain("props.node.toolConfig?.systemTool?.toolId === 'builtin.sql_query'");
    expect(source).toContain('mysql+pymysql://');
    expect(source).toContain('postgresql+psycopg://');
    expect(source).toContain('sqlite:///');
  });

  it('keeps SQL query database URI examples selectable for copying', () => {
    const source = readFileSync(resolve(__dirname, 'RenderInput.vue'), 'utf8');

    expect(source).toContain('@mousedown.stop');
    expect(source).toContain('user-select: text');
    expect(source).toContain('-webkit-user-select: text');
  });

  it('routes AI chat skill input to the skill refs editor', () => {
    const source = readFileSync(resolve(__dirname, 'RenderInput.vue'), 'utf8');

    expect(source).toContain('SkillRefsEditor');
    expect(source).toContain('input.key === NodeInputKeyEnum.skills');
    expect(source).not.toContain('settingDatasetQuotePrompt');
  });

  it('imports the prompt debugger from the shared workflow components directory', () => {
    const source = readFileSync(resolve(__dirname, 'RenderInput.vue'), 'utf8');

    expect(source).toContain("import PromptDebugDrawer from '../../../components/PromptDebugDrawer.vue';");
  });

  it('only shows prompt debugging for the field labelled 提示词', () => {
    const source = readFileSync(resolve(__dirname, 'RenderInput.vue'), 'utf8');

    expect(source).toContain("props.input.label === '提示词'");
  });

  it('places the generator after the field help icon with a short label', () => {
    const source = readFileSync(resolve(__dirname, 'RenderInput.vue'), 'utf8');

    expect(source).toContain(`<a-tooltip v-if="input.description" :title="input.description">
        <QuestionCircleOutlined class="label-help" />
      </a-tooltip>
      <a-button
        v-if="canDebugPrompt"`);
    expect(source).toContain('>\n        生成\n      </a-button>');
  });
});
