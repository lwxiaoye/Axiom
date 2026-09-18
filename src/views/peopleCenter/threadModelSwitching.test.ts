import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(__dirname);

describe('thread model switching contract', () => {
  it('persists a thread next-model and keeps the active Run model visible', () => {
    const composable = fs.readFileSync(path.join(root, 'composables/useCenterChat.ts'), 'utf8');
    const page = fs.readFileSync(path.join(root, 'pages/ChatPage.vue'), 'utf8');
    const tab = fs.readFileSync(path.join(root, 'tabs/ChatTab.vue'), 'utf8');

    // 只钉前两个实参：updateThreadModel 后来加了第三个 threadScope（主对话 / PPT / 校园百事通
    // 三个历史域各自持久化下一轮模型，见 builtinHarnessApps.contract.test.ts）。本契约关心的是
    // 「切模型会落库到当前线程」，与是否带 scope 无关。
    expect(composable).toMatch(/updateThreadModel\(threadId, nextModel(?:, threadScope)?\)/);
    expect(composable).toContain('currentRunModel');
    expect(composable).toContain("model: payload.model || turnModel");
    expect(page).toContain(':current-run-model="currentRunModel"');
    expect(page).toContain('@update:model="handleModelChange"');
    expect(tab).toContain('本轮 {{ currentRunModel }} · 下一轮 {{ model }}');
  });

  it('does not let the new-chat local default override an opened thread model', () => {
    const selector = fs.readFileSync(path.join(root, 'components/ModelSelector.vue'), 'utf8');
    expect(selector).toContain('if (selectedIsValid)');
    expect(selector).not.toContain('localStorage.setItem(STORAGE_KEY, modelId)');
  });
});
