// vite-plugin-theme 生成的 app-theme-style / app-antd-dark-theme-style 文件名 hash 恒定，
// 内容却随构建变化；浏览器按旧缓存复用会让旧色值盖住新版 chunk 样式（运行时后注入、优先级更高）。
// 构建后给所有 JS/HTML 里对这两个文件的引用追加内容指纹查询参数（?v=<hash>）：
// 内容一变 URL 必变，缓存立即失效；文件本体不改名，旧 JS 引旧参数也不会 404。
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';

const dist = path.resolve(process.cwd(), 'dist');
const targets = ['app-theme-style.e3b0c442.css', 'app-antd-dark-theme-style.e3b0c442.css'];

const versions = {};
for (const name of targets) {
  const file = path.join(dist, 'assets', name);
  if (!existsSync(file)) continue;
  versions[name] = createHash('sha256').update(readFileSync(file)).digest('hex').slice(0, 8);
}

function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const p = path.join(dir, entry);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

let patched = 0;
for (const file of walk(dist)) {
  if (!/\.(js|html)$/.test(file)) continue;
  let text = readFileSync(file, 'utf8');
  let changed = false;
  for (const [name, v] of Object.entries(versions)) {
    if (text.includes(name) && !text.includes(`${name}?v=`)) {
      text = text.replaceAll(name, `${name}?v=${v}`);
      changed = true;
    }
  }
  if (changed) {
    writeFileSync(file, text);
    // 预压缩副本与改后内容不一致，删除（nginx 未开 gzip_static，本就不用）
    const gz = `${file}.gz`;
    if (existsSync(gz)) unlinkSync(gz);
    patched += 1;
  }
}
console.log(`[bust-theme-css] versions=${JSON.stringify(versions)} patchedFiles=${patched}`);
