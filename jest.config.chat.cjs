/**
 * 定向测试配置：只跑主对话（peopleCenter）与独立运行栈（agent/run）下的纯 TS 单测——
 * 执行时间线 reducer、产物围栏解析、SSE sequence 闸、useAgentRun 收尾语义。
 * 组件与 /@/ 别名不进链路（useAgentRun 测试通过 jest.mock 掐断 api/stream 模块）。
 * 运行：pnpm test:chat
 */
module.exports = {
  testEnvironment: 'node',
  // 时区钉成 +08：serverTime 的哨兵测试要求非 UTC 环境，而测试文件内部无法自行设置（见该文件注释）
  globalSetup: '<rootDir>/jest.globalSetup.tz.cjs',
  roots: ['<rootDir>/src/views/peopleCenter', '<rootDir>/src/views/agent/run'],
  testMatch: ['**/*.test.ts'],
  transform: {
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
  },
};
