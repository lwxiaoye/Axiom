import {
  CENTER_NAV_COLLAPSED,
  CENTER_NAV_DEFAULT,
  CENTER_NAV_MOBILE,
  CENTER_NAV_NARROW,
  clampCenterNavWidth,
  effectiveCenterNavWidth,
  readStoredCenterNavWidth,
} from './centerNavWidth';

describe('centerNavWidth', () => {
  it('夹在最小最大之间，且不超过视口 45%', () => {
    expect(clampCenterNavWidth(280, 1440)).toBe(280);
    expect(clampCenterNavWidth(80, 1440)).toBe(200);
    expect(clampCenterNavWidth(900, 1440)).toBe(480);
    expect(clampCenterNavWidth(400, 600)).toBe(270);
  });

  it('存储非法值回退默认宽', () => {
    expect(readStoredCenterNavWidth(null, 1440)).toBe(CENTER_NAV_DEFAULT);
    expect(readStoredCenterNavWidth('abc', 1440)).toBe(CENTER_NAV_DEFAULT);
    expect(readStoredCenterNavWidth('320', 1440)).toBe(320);
  });

  it('窄屏与收起态覆盖展开宽', () => {
    expect(effectiveCenterNavWidth({ viewportWidth: 800, collapsed: false, expandedWidth: 320 })).toBe(
      CENTER_NAV_NARROW,
    );
    expect(effectiveCenterNavWidth({ viewportWidth: 1280, collapsed: true, expandedWidth: 320 })).toBe(
      CENTER_NAV_COLLAPSED,
    );
    expect(effectiveCenterNavWidth({ viewportWidth: 1280, collapsed: false, expandedWidth: 320 })).toBe(320);
  });

  it('手机端不保留左侧栏宽度，导航改为底栏', () => {
    expect(effectiveCenterNavWidth({ viewportWidth: 390, collapsed: false, expandedWidth: 320 })).toBe(
      CENTER_NAV_MOBILE,
    );
  });
});
