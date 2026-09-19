/**
 * 前端单测：纯 TS 的 node 环境测试，不进 Vue/Vite 别名链路（组件由 @vue/compiler-sfc 编译检查、
 * 真实浏览器冒烟兜底）。按目录分成几个 project，`pnpm test` 全跑，`pnpm test -- --selectProjects chat` 单跑。
 */
const transform = {
  '^.+\\.ts$': [
    'ts-jest',
    {
      // 独立于项目 tsconfig：避免 vite 别名与 vue 类型进入 node 单测链路
      tsconfig: {
        target: 'ES2020',
        module: 'commonjs',
        esModuleInterop: true,
        skipLibCheck: true,
        resolveJsonModule: true,
      },
      isolatedModules: true,
    },
  ],
};

const project = (displayName, roots, extra = {}) => ({
  displayName,
  testEnvironment: 'node',
  roots,
  testMatch: ['**/*.test.ts'],
  transform,
  ...extra,
});

module.exports = {
  projects: [
    // 主对话 / 个人中心：时区钉成 +08（serverTime 的哨兵测试要求非 UTC 环境，见 globalSetup 注释）
    project('chat', ['<rootDir>/src/views/peopleCenter'], { globalSetup: '<rootDir>/jest.globalSetup.tz.cjs' }),
    project('knowledge', ['<rootDir>/src/views/knowledge']),
    project('skills', ['<rootDir>/src/components/Markdown']),
    project('login', ['<rootDir>/src/router'], { moduleNameMapper: { '^/@/(.*)$': '<rootDir>/src/$1' } }),
    project('file-url', ['<rootDir>/src/utils/common']),
  ],
};
