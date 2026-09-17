/**
 * Used to parse the .env.development proxy configuration
 */
import http from 'node:http';
import os from 'node:os';
import type { ProxyOptions } from 'vite';

type ProxyItem = [string, string];

type ProxyList = ProxyItem[];

type ProxyTargetList = Record<string, ProxyOptions>;

const httpsRE = /^https:\/\//;

function inodeLocalAddress(): string | undefined {
  for (const [name, addrs] of Object.entries(os.networkInterfaces())) {
    if (!name.startsWith('utun') || !addrs) continue;
    for (const addr of addrs) {
      const family = String(addr.family);
      if (
        (family === 'IPv4' || family === '4') &&
        addr.address.startsWith('10.255.136.')
      ) {
        return addr.address;
      }
    }
  }
  return undefined;
}

/**
 * Generate proxy
 * @param list
 */
export function createProxy(list: ProxyList = []) {
  const ret: ProxyTargetList = {};
  for (const [prefix, target] of list) {
    const isHttps = httpsRE.test(target);
    const extra: ProxyOptions = {};
    try {
      const host = new URL(target).hostname;
      if (/^(10\.253\.|10\.255\.)/.test(host)) {
        const localAddress = inodeLocalAddress();
        if (localAddress) {
          extra.agent = new http.Agent({ localAddress, keepAlive: true });
        }
      }
    } catch {
      // keep the stock proxy when the target is not a URL
    }

    // https://github.com/http-party/node-http-proxy#options
    ret[prefix] = {
      target: target,
      changeOrigin: true,
      // Preserve the browser peer only for the local Agent API's trusted Uvicorn
      // proxy-header parsing; do not change what unrelated Java proxies receive.
      ...(prefix === '/agent-api' ? { xfwd: true } : {}),
      ws: true,
      rewrite: (path) => path.replace(new RegExp(`^${prefix}`), ''),
      // https is require secure=false
      ...(isHttps ? { secure: false } : {}),
      ...extra,
    };
  }
  return ret;
}
