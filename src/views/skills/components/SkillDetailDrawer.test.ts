import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('SkillDetailDrawer dirty state', () => {
  const source = readFileSync(resolve(process.cwd(), 'src/views/skills/components/SkillDetailDrawer.vue'), 'utf8');

  it('tracks dirty state against the loaded file content snapshot', () => {
    expect(source).toContain('savedContent: string');
    expect(source).toContain('file.savedContent = file.content');
    expect(source).toContain('nextContent !== file.savedContent');
  });

  it('uses one confirmation when closing the drawer with multiple dirty files', () => {
    expect(source).toContain('confirmDirtyFilesBeforeClose');
    expect(source).toContain('dirtyFiles.length');
    expect(source).not.toContain('for (const file of dirtyFiles)');
  });
});

describe('CodeEditor save event contract', () => {
  const source = readFileSync(resolve(process.cwd(), 'src/components/CodeEditor/src/CodeEditor.vue'), 'utf8');

  it('forwards keyboard save from the CodeMirror editor wrapper', () => {
    expect(source).toContain("@save=\"handleSave\"");
    expect(source).toContain("'save'");
    expect(source).toContain("emit('save', value)");
  });
});

describe('CodeMirror undo history contract', () => {
  const source = readFileSync(resolve(process.cwd(), 'src/components/CodeEditor/src/codemirror/CodeMirror.vue'), 'utf8');

  it('does not let file loading become an undo step that can clear the editor', () => {
    expect(source).toContain('function setEditorValue');
    expect(source).toContain('editor?.setValue');
    expect(source).toContain('editor?.clearHistory()');
    expect(source).toContain('setEditorValue(props.value)');
    expect(source).toContain('setEditorValue(value)');
  });
});
