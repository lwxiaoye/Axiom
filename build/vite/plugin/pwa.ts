/**
 * PWA 插件配置 - 适配按需加载
 */
import { VitePWA } from 'vite-plugin-pwa';
import type { VitePWAOptions } from 'vite-plugin-pwa';
import type { PluginOption } from 'vite';

export function configPwaPlugin(isBuild: boolean): PluginOption | PluginOption[] {
  if (!isBuild) {
    console.log('非生产环境不启用 PWA 插件!');
    return [];
  }

  const pwaOptions: Partial<VitePWAOptions> = {
    // 自毁式 Service Worker：注销已注册的旧 SW 并清空全部缓存，然后自动刷新页面。
    // 之前 SW 会 cache-first 预缓存 index.html + 入口资源，导致每次重新部署后用户
    // 仍看到旧版本（必须硬刷新）。本项目为频繁迭代的内网工具，离线能力价值极低，
    // 故彻底移除 SW 缓存；老客户端下次访问会自愈。如日后需要 PWA 再改回。
    selfDestroying: true,
    registerType: 'manual',
    injectRegister: 'inline', // 内联注册脚本，确保浏览器能及时取到自毁 SW
    includeAssets: ['axiom-mark.svg'],
    manifest: {
      name: 'AXIOM Campus Agents',
      short_name: 'AXIOM',
      theme_color: '#ffffff',
      icons: [
        {
          src: '/axiom-mark.svg',
          sizes: 'any',
          type: 'image/svg+xml',
        },
        {
          src: '/axiom-mark.svg',
          sizes: 'any',
          type: 'image/svg+xml',
        },
      ],
    },
    workbox: {
      maximumFileSizeToCacheInBytes: 10 * 1024 * 1024, // 10MB
      cleanupOutdatedCaches: true,
      
      // 预缓存：只缓存关键资源，不预缓存路由组件 CSS/JS（避免登录页加载全部资源）
      globPatterns: [
        'index.html', // 必须预缓存（避免 non-precached-url 错误）
        'manifest.webmanifest',
        'assets/index-*.css', // 仅入口 CSS
        'axiom-mark.svg',
        'js/index-*.js',
        'js/*-vendor-*.js',
      ],
      
      // 不使用导航回退功能
      navigateFallback: undefined,
      
      // 运行时缓存：按需加载的资源
      runtimeCaching: [
        {
          urlPattern: /\/js\/.*\.js$/i,
          handler: 'NetworkFirst',
          options: {
            cacheName: 'js-chunks-cache',
            networkTimeoutSeconds: 3,
            expiration: {
              maxEntries: 100,
              maxAgeSeconds: 60 * 60 * 24 * 7, // 7天
            },
            cacheableResponse: {
              statuses: [0, 200],
            },
          },
        },
        {
          urlPattern: /\/assets\/.*\.css$/i,
          handler: 'CacheFirst',
          options: {
            cacheName: 'css-cache',
            expiration: {
              maxEntries: 50,
              maxAgeSeconds: 60 * 60 * 24 * 30, // 30天
            },
            cacheableResponse: {
              statuses: [0, 200],
            },
          },
        },
        // Google Fonts
        {
          urlPattern: /^https:\/\/fonts\.googleapis\.com\/.*/i,
          handler: 'CacheFirst',
          options: {
            cacheName: 'google-fonts-cache',
            expiration: {
              maxEntries: 10,
              maxAgeSeconds: 60 * 60 * 24 * 365,
            },
            cacheableResponse: {
              statuses: [0, 200],
            },
          },
        },
        // 图片资源
        {
          urlPattern: /\.(?:png|jpg|jpeg|svg|gif|webp)$/,
          handler: 'CacheFirst',
          options: {
            cacheName: 'image-cache',
            expiration: {
              maxEntries: 100,
              maxAgeSeconds: 60 * 60 * 24 * 30,
            },
          },
        },
        // API 请求
        {
          urlPattern: /\/api\/.*/i,
          handler: 'NetworkFirst',
          options: {
            cacheName: 'api-cache',
            networkTimeoutSeconds: 10,
            expiration: {
              maxEntries: 50,
              maxAgeSeconds: 60 * 5,
            },
            cacheableResponse: {
              statuses: [0, 200],
            },
          },
        },
      ],
      // 启用立即更新：新 SW 立即激活并接管页面
      skipWaiting: true,
      clientsClaim: true,
    },
    devOptions: {
      enabled: false,
    },
  };

  return VitePWA(pwaOptions);
}
