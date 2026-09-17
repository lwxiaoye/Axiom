/** 独立运行页展示规则的定向单测配置。 */
module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/src/views/agent/run'],
  testMatch: ['**/*.test.ts'],
  transform: {
    '^.+\\.ts$': [
      'ts-jest',
      {
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
