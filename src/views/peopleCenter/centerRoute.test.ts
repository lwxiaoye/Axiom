import { centerSectionPath, centerSectionRouteName, centerRouteNameToSection, legacySectionToPath } from './centerRoute';

describe('model configuration navigation', () => {
  it('maps the menu section to its page and back', () => {
    expect(centerSectionPath.models).toBe('/center/models');
    expect(centerRouteNameToSection[centerSectionRouteName.models]).toBe('models');
    expect(legacySectionToPath('models')).toBe('/center/models');
  });
  it('preserves the fallback for unknown sections', () => {
    expect(legacySectionToPath('unknown')).toBe('/center/chat');
  });
});
