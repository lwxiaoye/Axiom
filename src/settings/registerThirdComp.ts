import type { App } from 'vue';
// SLIM-BUILD: JVxeTable 会急加载 vxe-pc-ui + vxe-table + vxe-table-plugin-antd，
// 仅被 system / peopleCenter 等后台页面使用，agent 使用界面不依赖。
// import { registerJVxeTable } from '/@/components/jeecg/JVxeTable';
// import { registerJVxeCustom } from '/@/components/JVxeCustom';

// 注册全局dayjs
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import customParseFormat from 'dayjs/plugin/customParseFormat';
import { createAsyncComponent } from '/@/utils/factory/createAsyncComponent';

export async function registerThirdComp(app: App) {
  //---------------------------------------------------------------------
  // SLIM-BUILD: 见文件顶部说明
  // registerJVxeTable(app);
  // await registerJVxeCustom();
  //---------------------------------------------------------------------
  // 注册全局聊天表情包
  // 代码逻辑说明: 【QQYUN-8241】emoji-mart-vue-fast库异步加载
  app.component(
    'Picker',
    createAsyncComponent(() => {
      return new Promise((resolve, rejected) => {
        import('emoji-mart-vue-fast/src')
          .then((res) => {
            const { Picker } = res;
            resolve(Picker);
          })
          .catch((err) => {
            rejected(err);
          });
      });
    })
  );
  // update-end--author:liaozhiyang---date:20240308---for：【QQYUN-8241】emoji-mart-vue-fast库异步加载
  //---------------------------------------------------------------------
  // 注册全局dayjs
  dayjs.locale('zh-cn');
  dayjs.extend(relativeTime);
  dayjs.extend(customParseFormat);
  app.config.globalProperties.$dayjs = dayjs
  app.provide('$dayjs', dayjs)
  //---------------------------------------------------------------------
}
