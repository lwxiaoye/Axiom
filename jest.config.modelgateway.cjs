module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/src/views/modelGateway'],
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
          paths: {
            '/@/*': ['src/*'],
            '@/*': ['src/*'],
            '/#/*': ['types/*'],
          },
        },
        isolatedModules: true,
      },
    ],
  },
  moduleNameMapper: {
    '^/@/(.*)$': '<rootDir>/src/$1',
    '^@/(.*)$': '<rootDir>/src/$1',
    '^/#/(.*)$': '<rootDir>/types/$1',
  },
};
