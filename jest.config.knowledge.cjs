/** Pure knowledge-domain TypeScript tests, without Vue or Vite aliases. */
module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/src/views/knowledge'],
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
        },
        isolatedModules: true,
      },
    ],
  },
};
