// 必须在任何 Date 之前设置：Node 首次用到 Date 之后会缓存时区。
// 固定成 +08 是为了让「MySQL 本地时间」与「Python UTC」这两条路的差异真的显现出来——
// 在 UTC 机器上跑，两个解析器返回同一个值，任何断言都会**恰好通过**（假绿灯）。
process.env.TZ = 'Asia/Shanghai';

import { formatDbNaiveTime, parseDbNaiveTs, parseUtcNaiveTs } from './serverTime';

/** 真实样本：库里 `杭州介绍.pptx` 的 created_at（MySQL NOW() 写入，+08 墙上时间）。
 *  它在页面上曾被显示成 `2026-07-30 08:41` —— 凌晨 1:32 打开页面看到的未来时间。 */
const DB_NAIVE = '2026-07-30T00:41:21';

describe('时区哨兵：先证明环境真的是 +08，否则下面的断言全是假绿灯', () => {
  it('测试进程时区为 UTC+8', () => {
    // 用 1 月与 7 月各验一次：DST 地区在这里会露馅，+08 常年无夏令时
    expect(new Date('2026-01-15T00:00:00').getTimezoneOffset()).toBe(-480);
    expect(new Date('2026-07-15T00:00:00').getTimezoneOffset()).toBe(-480);
  });
});

describe('parseDbNaiveTs（MySQL func.now() → +08 墙上时间）', () => {
  it('按本地时区解读，不额外加 8 小时', () => {
    expect(parseDbNaiveTs(DB_NAIVE)).toBe(Date.UTC(2026, 6, 29, 16, 41, 21));
  });

  it('带显式偏移的串照样吃得下（后端哪天改成输出时区，这里不用动）', () => {
    expect(parseDbNaiveTs('2026-07-30T00:41:21+08:00')).toBe(parseDbNaiveTs(DB_NAIVE));
  });

  it('空值/垃圾值返回 NaN 而不是 0（0 会被显示成 1970）', () => {
    expect(Number.isNaN(parseDbNaiveTs(''))).toBe(true);
    expect(Number.isNaN(parseDbNaiveTs(null))).toBe(true);
    expect(Number.isNaN(parseDbNaiveTs('不是时间'))).toBe(true);
  });
});

describe('parseUtcNaiveTs（Python datetime.now() / PostgreSQL → UTC）', () => {
  it('无时区的串按 UTC 解读（补 Z）', () => {
    expect(parseUtcNaiveTs(DB_NAIVE)).toBe(Date.UTC(2026, 6, 30, 0, 41, 21));
  });

  it('已带 Z 或偏移时不再重复补', () => {
    expect(parseUtcNaiveTs('2026-07-30T00:41:21Z')).toBe(Date.UTC(2026, 6, 30, 0, 41, 21));
    expect(parseUtcNaiveTs('2026-07-30T08:41:21+08:00')).toBe(Date.UTC(2026, 6, 30, 0, 41, 21));
  });
});

describe('两个解析器必须给出不同结果 —— 这条就是防「混用」的哨兵', () => {
  it('同一个裸串，两个解析器相差正好 8 小时', () => {
    const diff = parseUtcNaiveTs(DB_NAIVE) - parseDbNaiveTs(DB_NAIVE);
    expect(diff).toBe(8 * 3600 * 1000);
  });
});

describe('formatDbNaiveTime', () => {
  it('原样显示库里的墙上时间（不是 08:41 那个未来时间）', () => {
    expect(formatDbNaiveTime(DB_NAIVE)).toBe('2026-07-30 00:41');
  });

  it('⚠️ 回归哨兵：若有人把它换成 parseUtcNaiveTs，这里会变成 08:41', () => {
    // 直接把「错误实现」算出来钉在这里，作为反向对照：
    // 只要 formatDbNaiveTime 的结果等于这个值，说明混用又发生了一次。
    const wrong = new Date(parseUtcNaiveTs(DB_NAIVE));
    const p = (n: number) => String(n).padStart(2, '0');
    const wrongText =
      `${wrong.getFullYear()}-${p(wrong.getMonth() + 1)}-${p(wrong.getDate())}` +
      ` ${p(wrong.getHours())}:${p(wrong.getMinutes())}`;
    expect(wrongText).toBe('2026-07-30 08:41');            // 先证明"错的样子"确实长这样
    expect(formatDbNaiveTime(DB_NAIVE)).not.toBe(wrongText); // 再断言我们不是那个样子
  });

  it('解析不出来时退回截断原串，而不是渲染 Invalid Date', () => {
    expect(formatDbNaiveTime('这不是时间戳啊啊啊啊')).toBe('这不是时间戳啊啊啊啊');
    expect(formatDbNaiveTime('')).toBe('');
    expect(formatDbNaiveTime(null)).toBe('');
  });
});
