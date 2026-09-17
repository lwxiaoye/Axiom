import base from './vite.config';
import type { UserConfig, ConfigEnv } from 'vite';

export default (env: ConfigEnv): UserConfig => {
  const cfg = typeof base === 'function' ? (base as any)(env) : (base as any);
  cfg.build = cfg.build || {};
  cfg.build.sourcemap = false;
  cfg.build.reportCompressedSize = false;
  cfg.build.cssCodeSplit = true;
  cfg.build.rollupOptions = cfg.build.rollupOptions || {};
  cfg.build.rollupOptions.maxParallelFileOps = 1;
  // lower peak memory during minify
  cfg.build.minify = 'esbuild';
  cfg.build.target = 'es2020';
  return cfg;
};
