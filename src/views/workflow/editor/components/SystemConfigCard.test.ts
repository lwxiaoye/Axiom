import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const source = readFileSync(resolve(__dirname, 'SystemConfigCard.vue'), 'utf8');
const chatAgentSource = readFileSync(resolve(__dirname, '../../agent/index.vue'), 'utf8');

describe('SystemConfigCard', () => {
  it('stores manually configured right-sidebar recommendations in chatInputGuide', () => {
    expect(source).toContain('右侧推荐内容（人工配置）');
    expect(chatAgentSource).toContain('<span>右侧推荐内容</span>');
    expect(chatAgentSource).toContain('<RecommendationSceneEditor v-model="form.recommendationScenes" />');
    expect(source).toContain('先创建场景，再添加该场景下的推荐内容');
    expect(source).toContain('<RecommendationSceneEditor');
    expect(source).toContain('const chatInputGuide = computed');
    expect(source).toContain('preserveRecommendationSceneDrafts');
    expect(source).toContain('chatConfig.value.chatInputGuide =');
    expect(source).not.toContain('点击后直接发起对话');
  });

  it('keeps the maximum file count value vertically centered', () => {
    expect(source).toContain('class="system-file-count"');
    expect(source).toContain(':deep(.system-file-count.ant-input-number-sm) {\n  display: inline-flex;\n  height: 24px;');
    expect(source).toContain(':deep(.system-file-count.ant-input-number-sm .ant-input-number-input) {\n  height: 22px;');
  });
});
