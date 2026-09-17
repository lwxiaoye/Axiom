import { normalizeRecommendationScenes, preserveRecommendationSceneDrafts } from './recommendationScenes';

describe('recommendation scene editing', () => {
  it('keeps a newly added blank item during editing but removes it for persisted recommendations', () => {
    const draft = preserveRecommendationSceneDrafts([
      { key: 'draft', label: '草稿场景', textList: [''] },
    ]);

    expect(draft).toEqual([{ key: 'draft', label: '草稿场景', textList: [''] }]);
    expect(normalizeRecommendationScenes(draft)).toEqual([
      { key: 'draft', label: '草稿场景', textList: [] },
    ]);
  });
});
