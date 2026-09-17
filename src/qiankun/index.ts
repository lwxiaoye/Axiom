import { registerMicroApps, start, addGlobalUncaughtErrorHandler } from 'qiankun';
import { getMicroApps } from './apps';
import { getFastGptQiankunProps } from './props';

declare global {
  interface Window {
    qiankunStarted?: boolean;
  }
}

function refreshFastGptProps(app: any) {
  if (app?.name === 'fastgpt') {
    app.props = getFastGptQiankunProps();
  }
  return app;
}

export default function registerApps() {
  if (window.qiankunStarted) return;

  const apps = getMicroApps();
  if (!apps.length) return;

  window.qiankunStarted = true;

  registerMicroApps(apps, {
    beforeLoad: [refreshFastGptProps],
    beforeMount: [refreshFastGptProps],
  });

  addGlobalUncaughtErrorHandler((event) => {
    console.error('[qiankun] micro app error', event);
  });

  start({
    prefetch: false,
    sandbox: {
      experimentalStyleIsolation: true,
    },
  });
}
