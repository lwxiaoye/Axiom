import type { RecommendationSceneConfig } from '../core/type';

function cleanTextList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return Array.from(new Set(value.map((item) => String(item || '').trim()).filter(Boolean)));
}

export function normalizeRecommendationScenes(
  sceneList: unknown,
  legacyTextList: unknown = [],
): RecommendationSceneConfig[] {
  const usedKeys = new Set<string>();
  const scenes = Array.isArray(sceneList)
    ? sceneList.flatMap((item, index) => {
        if (!item || typeof item !== 'object') return [];
        const source = item as Partial<RecommendationSceneConfig>;
        const textList = cleanTextList(source.textList);
        const label = String(source.label || '').trim() || `场景 ${index + 1}`;
        let key = String(source.key || '').trim() || `scene-${index + 1}`;
        while (usedKeys.has(key)) key = `${key}-${index + 1}`;
        usedKeys.add(key);
        return [{ key, label, textList }];
      })
    : [];

  if (scenes.length) return scenes;
  const legacy = cleanTextList(legacyTextList);
  return legacy.length ? [{ key: 'recommended', label: '推荐', textList: legacy }] : [];
}

/**
 * 编辑过程的场景副本：保留刚新增、尚未填写的空白推荐项。
 * 归一化函数用于加载/保存，不能用于每一次输入事件，否则空输入框会立即消失。
 */
export function preserveRecommendationSceneDrafts(
  sceneList: unknown,
  legacyTextList: unknown = [],
): RecommendationSceneConfig[] {
  const usedKeys = new Set<string>();
  const scenes = Array.isArray(sceneList)
    ? sceneList.flatMap((item, index) => {
        if (!item || typeof item !== 'object') return [];
        const source = item as Partial<RecommendationSceneConfig>;
        const label = typeof source.label === 'string' ? source.label : `场景 ${index + 1}`;
        let key = String(source.key || '').trim() || `scene-${index + 1}`;
        while (usedKeys.has(key)) key = `${key}-${index + 1}`;
        usedKeys.add(key);
        const textList = Array.isArray(source.textList)
          ? source.textList.map((value) => (typeof value === 'string' ? value : String(value ?? '')))
          : [];
        return [{ key, label, textList }];
      })
    : [];

  return scenes.length ? scenes : normalizeRecommendationScenes([], legacyTextList);
}

export function flattenRecommendationScenes(sceneList: RecommendationSceneConfig[]): string[] {
  return Array.from(new Set(sceneList.flatMap((scene) => cleanTextList(scene.textList))));
}
