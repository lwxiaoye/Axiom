import dayjs from 'dayjs';

const MANAGE_DATE_TIME_FORMAT = 'YYYY-MM-DD HH:mm:ss';

export function formatManageDateTime(value?: string | number | Date | null): string {
  if (!value) return '-';
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format(MANAGE_DATE_TIME_FORMAT) : '-';
}
