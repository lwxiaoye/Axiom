import fs from 'fs';
import path from 'path';

describe('FormInputFieldsEditor field configuration', () => {
  const source = fs.readFileSync(path.join(__dirname, 'FormInputFieldsEditor.vue'), 'utf8');

  it('offers multiline text fields instead of LLM model selection', () => {
    expect(source).toContain("{ label: '多行文本', value: 'textarea' }");
    expect(source).not.toContain("label: '模型选择'");
    expect(source).not.toContain("selectLLMModel: WorkflowIOValueTypeEnum.string");
  });

  it('defaults new fields to textarea and keeps choice editing as three-line text areas', () => {
    expect(source).toContain("type: 'textarea'");
    expect(source).toContain("placeholder=\"候选项，每行一个\"");
  });

  it('centers the inner input text for numeric property editors', () => {
    expect(source).toContain('forms-number-control');
    expect(source).toContain(':deep(.forms-number-control.ant-input-number-sm)');
    expect(source).toContain(':deep(.forms-number-control.ant-input-number-sm .ant-input-number-input-wrap)');
    expect(source).toContain(':deep(.forms-number-control.ant-input-number-sm .ant-input-number-input)');
    expect(source).toContain('align-items: center');
    expect(source).toContain('line-height: 22px');
  });

  it('keeps multiline default value textarea height aligned with description input', () => {
    expect(source).toContain('forms-default forms-default-textarea');
    expect(source).toContain(':rows="1"');
    expect(source).toContain(':deep(.forms-default-textarea.ant-input-sm)');
    expect(source).toContain('height: 24px');
    expect(source).toContain('min-height: 24px');
    expect(source).toContain('font-size: 12px');
    expect(source).toContain('padding: 0 7px');
    expect(source).toContain('line-height: 22px');
  });
});
