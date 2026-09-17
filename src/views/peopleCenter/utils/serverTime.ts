/**
 * 服务端时间戳解析（2026-07-30 抽出）。
 *
 * 抽出来的唯一目的：**让"这个时间戳出自哪个时钟"变成函数名的一部分**。
 *
 * 后端有两个时钟，而它们序列化出来的字符串**长得完全一样**（都是无时区的裸 ISO 串）：
 *
 *   来源                                   | 实际时区 | 用哪个函数
 *   ---------------------------------------|---------|------------------------
 *   MySQL `func.now()`（server_default）    | +08:00  | parseDbNaiveTs / formatDbNaiveTs
 *   Python `datetime.now()`（容器无 TZ）     | UTC     | parseUtcNaiveTs
 *   PostgreSQL runtime 库（时区 = UTC）      | UTC     | parseUtcNaiveTs
 *
 * 2026-07-26 有人给「N 天后过期」修对了 UTC 那条（`expires_at` 确实是 Python 写的），
 * 然后把同一个解析器复用到了 `created_at`（MySQL 写的）上 —— 于是「我的文件」的创建
 * 时间整整晚 8 小时，凌晨 1:32 打开页面看到的是 08:41 这种**未来时间**。
 *
 * 这就是本仓库反复出现的那个母题：**一个信号兼职两个来源，必然在其中一个方向静默失效**。
 * 而"无时区裸串"恰好是最难察觉的一种——两边的值都合法、都能解析、都不报错。
 *
 * 所以这里不提供任何"通用"的 parseServerTs：**调用方必须先回答"谁写的"**。
 *
 * 判据（改这里之前先对表，别凭时区做算术）：
 *   docker exec agent-api date                      # 容器时钟（UTC）
 *   docker exec agent-api python -c "…SELECT @@session.time_zone, NOW(), UTC_TIMESTAMP()"
 *   → session.time_zone=+08:00 且 NOW() 与宿主 `date` 一致 ＝ MySQL 存的是本地墙上时间
 */

/** 串里是否已带显式时区（`Z` 或 `±HH:MM` / `±HHMM`）。 */
function hasExplicitTz(iso: string): boolean {
  return /Z$|[+-]\d{2}:?\d{2}$/.test(iso);
}

/**
 * 解析**Python / PostgreSQL 写入**的时间戳（UTC naive）。
 *
 * 裸 `new Date(串)` 会按浏览器本地时区解读，UTC+8 下整整早 8 小时：文件真实还剩 8 小时
 * 就显示「已过期」（而后端不会删、仍可下载），天数也系统性少一天。所以要补 `Z`。
 *
 * ⚠️ 不要拿它解 MySQL 写的 `created_at`/`updated_at`——那会凭空加 8 小时。
 */
export function parseUtcNaiveTs(iso: string | null | undefined): number {
  if (!iso) return NaN;
  return new Date(hasExplicitTz(iso) ? iso : `${iso}Z`).getTime();
}

/**
 * 解析**MySQL `func.now()` 写入**的时间戳（+08:00 本地墙上时间，naive）。
 *
 * 直接按浏览器本地时区解读即可——这与库所在时区一致（单区域部署）。带了显式偏移的串
 * 也能正确吃下，所以后端哪天改成输出显式时区，这里不用跟着动。
 */
export function parseDbNaiveTs(iso: string | null | undefined): number {
  if (!iso) return NaN;
  return new Date(iso).getTime();
}

/** 把 MySQL 写入的时间戳格式化成 `YYYY-MM-DD HH:mm`；解析不出来时退回截断原串。 */
export function formatDbNaiveTime(value: string | null | undefined): string {
  if (!value) return '';
  const ts = parseDbNaiveTs(value);
  if (Number.isNaN(ts)) return value.slice(0, 16).replace('T', ' ');
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
