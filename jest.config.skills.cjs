module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/src/views/skills'],
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

