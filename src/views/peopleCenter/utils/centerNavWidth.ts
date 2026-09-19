export const CENTER_NAV_DEFAULT = 248;
export const CENTER_NAV_COLLAPSED = 64;
export const CENTER_NAV_NARROW = 72;
export const CENTER_NAV_MOBILE = 0;
export const CENTER_NAV_MIN = 200;
export const CENTER_NAV_MAX = 480;
export const CENTER_NAV_NARROW_MQ = 980;
export const CENTER_NAV_MOBILE_MQ = 719;
export const CENTER_NAV_STORAGE_KEY = 'center-nav-width';

export function clampCenterNavWidth(width: number, viewportWidth: number): number {
  const maxByViewport = Math.round(viewportWidth * 0.45);
  const max = Math.min(CENTER_NAV_MAX, Math.max(CENTER_NAV_MIN, maxByViewport));
  return Math.min(max, Math.max(CENTER_NAV_MIN, Math.round(width)));
}

export function readStoredCenterNavWidth(raw: string | null, viewportWidth: number): number {
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return CENTER_NAV_DEFAULT;
  return clampCenterNavWidth(n, viewportWidth);
}

export function effectiveCenterNavWidth(opts: {
  viewportWidth: number;
  collapsed: boolean;
  expandedWidth: number;
}): number {
  if (opts.viewportWidth <= CENTER_NAV_MOBILE_MQ) return CENTER_NAV_MOBILE;
  if (opts.viewportWidth <= CENTER_NAV_NARROW_MQ) return CENTER_NAV_NARROW;
  if (opts.collapsed) return CENTER_NAV_COLLAPSED;
  return clampCenterNavWidth(opts.expandedWidth, opts.viewportWidth);
}
