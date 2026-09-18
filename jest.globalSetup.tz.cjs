/**
 * 把测试进程时区钉成 +08（与生产 MySQL 会话时区一致）。
 *
 * 为什么放在 globalSetup 而不是测试文件顶部：Jest 给每个测试文件的 `process.env`
 * 是沙箱里的一份拷贝（jest-util createProcessEnv 用 Proxy 包了一层），测试里写
 * `process.env.TZ = …` 只改到拷贝，永远触发不了 Node 的 tzset —— 所以那种写法在
 * 开发机（系统时区本来就是 +08）上是假绿灯，到了 UTC 服务器上就红。
 * globalSetup 跑在真实进程里、且早于 worker fork / in-band 执行，这里赋值才真的生效。
 *
 * 需要 +08 的原因见 src/views/peopleCenter/utils/serverTime.test.ts 开头的哨兵说明。
 */
module.exports = async () => {
  process.env.TZ = 'Asia/Shanghai';
};
