import type { ProjectConfig } from '/#/config';
import { MenuTypeEnum, MenuModeEnum, TriggerEnum, MixSidebarTriggerEnum } from '/@/enums/menuEnum';
import { CacheTypeEnum } from '/@/enums/cacheEnum';
import {
  ContentEnum,
  PermissionModeEnum,
  ThemeEnum,
  RouterTransitionEnum,
  SettingButtonPositionEnum,
  SessionTimeoutProcessingEnum,
  TabsThemeEnum,
} from '/@/enums/appEnum';
import { darkMode } from '/@/settings/designSetting';
import { getConfigByMenuType } from '../utils/getConfigByMenuType';
// 修改此属性，实现默认的四个系统主题快速切换
const menuType = MenuTypeEnum.SIDEBAR;

// update-begin--author:liaozhiyang---date:20251201---for【QQYUN-14176】修改一个配置就能切换默认四个主题，不需要额外修改颜色等
const { themeColor, headerBgColor, sideBgColor, split, mode } = getConfigByMenuType(menuType);
// update-end--author:liaozhiyang---date:20251201---for【QQYUN-14176】修改一个配置就能切换默认四个主题，不需要额外修改颜色等
// ! 改动后需要清空浏览器缓存
const setting: ProjectConfig = {
  "showSettingButton": false,
  "showDarkModeToggle": false,
  "settingButtonPosition": "auto",
  "permissionMode": "BACK",
  "permissionCacheType": 1,
  "sessionTimeoutProcessing": 0,
  "themeColor": "#111827",
  "themeMode": "light",
  "grayMode": false,
  "colorWeak": false,
  "fullContent": false,
  "contentMode": "full",
  "showLogo": true,
  "showFooter": false,
  "aiIconShow": false,
  "headerSetting": {
    "bgColor": "#ffffff",
    "fixed": true,
    "show": true,
    "theme": "light",
    "useLockPage": false,
    "showFullScreen": false,
    "showDoc": false,
    "showNotice": true,
    "showSearch": true
  },
  "menuSetting": {
    "bgColor": "#f7f8fa",
    "fixed": true,
    "collapsed": false,
    "collapsedShowTitle": false,
    "canDrag": false,
    "show": true,
    "hidden": false,
    "menuWidth": 224,
    "mode": "inline",
    "type": "sidebar",
    "theme": "light",
    "isThemeBright": false,
    "split": false,
    "topMenuAlign": "center",
    "trigger": "FOOTER",
    "accordion": true,
    "closeMixSidebarOnChange": false,
    "mixSideTrigger": "click",
    "mixSideFixed": false
  },
  "multiTabsSetting": {
    "cache": false,
    "show": true,
    "canDrag": true,
    "showQuick": true,
    "showRedo": true,
    "showFold": true,
    "theme": "card"
  },
  "transitionSetting": {
    "enable": true,
    "basicTransition": "fade-slide",
    "openPageLoading": true,
    "openNProgress": true
  },
  "openKeepAlive": true,
  "lockTime": 0,
  "showBreadCrumb": false,
  "showBreadCrumbIcon": true,
  "useErrorHandle": false,
  "useOpenBackTop": true,
  "canEmbedIFramePage": true,
  "closeMessageOnSwitch": true,
  "removeAllHttpPending": false
}

export default setting;
