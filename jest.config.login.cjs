module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/src/router'],
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
  moduleNameMapper: {
    '^/@/(.*)$': '<rootDir>/src/$1',
  },
};
