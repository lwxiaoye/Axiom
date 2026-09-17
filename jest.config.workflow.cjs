/**
 * 定向测试配置：只跑 src/views/workflow 下的纯 TS 单测（协议/编译器/工具函数，
 * 全部相对导入，不含 Vue 组件与 /@/ 别名）。运行：pnpm test:workflow
 */
module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/src/views/workflow'],
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
