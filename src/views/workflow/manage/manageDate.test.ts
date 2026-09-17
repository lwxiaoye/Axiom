import { formatManageDateTime } from './manageDate';

describe('workflow manage date formatting', () => {
  it('formats published time as yyyy-MM-dd HH:mm:ss', () => {
    expect(formatManageDateTime('2026-07-22T13:04:05.123')).toBe('2026-07-22 13:04:05');
  });

  it('shows a stable empty value for missing dates', () => {
    expect(formatManageDateTime()).toBe('-');
  });
});
