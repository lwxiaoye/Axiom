export type RunInspirationScene = {
  key: string;
  label: string;
  tasks: string[];
};

type RunInspirationMeta = {
  inspirationScenes?: unknown;
  quickQuestions?: unknown;
};

function configuredSuggestions(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return Array.from(new Set(value.map((item) => String(item || '').trim()).filter(Boolean)));
}

export function resolveRunInspirationScenes(meta: RunInspirationMeta): RunInspirationScene[] {
  if (Array.isArray(meta.inspirationScenes)) {
    const scenes = meta.inspirationScenes.flatMap((item, index) => {
      if (!item || typeof item !== 'object') return [];
      const source = item as Partial<RunInspirationScene>;
      const tasks = configuredSuggestions(source.tasks);
      if (!tasks.length) return [];
      return [{
        key: String(source.key || '').trim() || `scene-${index + 1}`,
        label: String(source.label || '').trim() || `场景 ${index + 1}`,
        tasks,
      }];
    });
    if (scenes.length) return scenes;
  }
  const tasks = configuredSuggestions(meta.quickQuestions);
  return tasks.length ? [{ key: 'configured', label: '推荐', tasks }] : [];
}

export function resolveRunWelcomeText(appName: string, welcomeText: unknown): string {
  const configured = String(welcomeText || '').trim();
  if (configured) return configured;
  const name = String(appName || '').trim() || '你的智能体';
  return `你好，我是${name}。告诉我你想完成什么，我会根据当前能力帮你推进。`;
}
