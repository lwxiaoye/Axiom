import type { UserInfo, LoginInfo } from '/#/store';
import type { ErrorMessageMode } from '/#/axios';
import { defineStore } from 'pinia';
import { store } from '/@/store';
import { RoleEnum } from '/@/enums/roleEnum';
import { PageEnum } from '/@/enums/pageEnum';
import { ROLES_KEY, TOKEN_KEY, USER_INFO_KEY, LOGIN_INFO_KEY, DB_DICT_DATA_KEY, TENANT_ID, OAUTH2_THIRD_LOGIN_TENANT_ID } from '/@/enums/cacheEnum';
import { getAuthCache, setAuthCache, removeAuthCache } from '/@/utils/auth';
import { GetUserInfoModel, LoginParams, ThirdLoginParams } from '/@/api/sys/model/userModel';
import { doLogout, getUserInfo, loginApi, phoneLoginApi, thirdLogin } from '/@/api/sys/user';
import { useI18n } from '/@/hooks/web/useI18n';
import { useMessage } from '/@/hooks/web/useMessage';
import { router } from '/@/router';
import { isArray } from '/@/utils/is';
import { useGlobSetting } from '/@/hooks/setting';
import { JDragConfigEnum } from '/@/enums/jeecgEnum';
import { useSso } from '/@/hooks/web/useSso';
import { isOAuth2AppEnv } from "/@/views/sys/login/useLogin";
import { recordAuditEvent } from '/@/api/audit/audit.api';
import { getUrlParam } from "@/utils";
import { loginRedirectQuery, resolvePostLoginPath } from '/@/router/postLoginRedirect';
import { clearUserScopedStorage, registerStorageUserIdGetter } from '/@/views/peopleCenter/utils/userScopedStorage';
interface dictType {
  [key: string]: any;
}
interface UserState {
  userInfo: Nullable<UserInfo>;
  token?: string;
  roleList: RoleEnum[];
  dictItems?: dictType | null;
  sessionTimeout?: boolean;
  lastUpdateTime: number;
  tenantid?: string | number;
  shareTenantId?: Nullable<string | number>;
  loginInfo?: Nullable<LoginInfo>;

}

export const useUserStore = defineStore({
  id: 'app-user',
  state: (): UserState => ({
    // 用户信息
    userInfo: null,
    // token
    token: undefined,
    // 角色列表
    roleList: [],
    // 字典
    dictItems: null,
    // session过期时间
    sessionTimeout: false,
    // Last fetch time
    lastUpdateTime: 0,
    //租户id
    tenantid: '',
    // 分享租户ID
    // 用于分享页面所属租户与当前用户登录租户不一致的情况
    shareTenantId: null,
    //登录返回信息
    loginInfo: null,

  }),
  getters: {
    getUserInfo(): UserInfo {
      if(this.userInfo == null){
        this.userInfo = getAuthCache<UserInfo>(USER_INFO_KEY)!=null ? getAuthCache<UserInfo>(USER_INFO_KEY) : null;
      }
      return this.userInfo || getAuthCache<UserInfo>(USER_INFO_KEY) || {};
    },
    getLoginInfo(): LoginInfo {
      return this.loginInfo || getAuthCache<LoginInfo>(LOGIN_INFO_KEY) || {};
    },
    getToken(): string {
      return this.token || getAuthCache<string>(TOKEN_KEY);
    },
    getAllDictItems(): [] {
      return this.dictItems || getAuthCache(DB_DICT_DATA_KEY);
    },
    getRoleList(): RoleEnum[] {
      return this.roleList.length > 0 ? this.roleList : getAuthCache<RoleEnum[]>(ROLES_KEY);
    },
    getSessionTimeout(): boolean {
      return !!this.sessionTimeout;
    },
    getLastUpdateTime(): number {
      return this.lastUpdateTime;
    },
    getTenant(): string | number {
      return this.tenantid || getAuthCache<string | number>(TENANT_ID);
    },
    // 是否有分享租户id
    hasShareTenantId(): boolean {
      return this.shareTenantId != null && this.shareTenantId !== '';
    },
  },
  actions: {
    setToken(info: string | undefined) {
      this.token = info ? info : ''; // for null or undefined value
      setAuthCache(TOKEN_KEY, info);
    },

    setRoleList(roleList: RoleEnum[]) {
      this.roleList = roleList;
      setAuthCache(ROLES_KEY, roleList);
    },
    setUserInfo(info: UserInfo | null) {
      this.userInfo = info;
      this.lastUpdateTime = new Date().getTime();
      setAuthCache(USER_INFO_KEY, info);
    },
    setLoginInfo(info: LoginInfo | null) {
      this.loginInfo = info;
      setAuthCache(LOGIN_INFO_KEY, info);
    },
    setAllDictItems(dictItems) {
      this.dictItems = dictItems;
      setAuthCache(DB_DICT_DATA_KEY, dictItems);
    },
    setAllDictItemsByLocal() {
      // 代码逻辑说明: 【QQYUN-8572】表格行选择卡顿问题（customRender中字典引起的）
      if (!this.dictItems) {
        const allDictItems = getAuthCache(DB_DICT_DATA_KEY);
        if (allDictItems) {
          this.dictItems = allDictItems;
        }
      }
    },
    setTenant(id) {
      this.tenantid = id;
      setAuthCache(TENANT_ID, id);
    },
    setShareTenantId(id: NonNullable<typeof this.shareTenantId>) {
      this.shareTenantId = id;
    },
    setSessionTimeout(flag: boolean) {
      this.sessionTimeout = flag;
    },
    // 重置用户状态
    resetState() {
      this.userInfo = null;
      this.dictItems = null;
      this.token = '';
      this.roleList = [];
      this.sessionTimeout = false;
    },
    /**
     * 登录事件
     */
    async login(
      params: LoginParams & {
        goHome?: boolean;
        mode?: ErrorMessageMode;
      }
    ): Promise<GetUserInfoModel | null> {
      try {
        const { goHome = true, mode, ...loginParams } = params;
        const data = await loginApi(loginParams, mode);
        const { token, userInfo } = data || {};
        if (!token) {
          throw new Error('登录未返回凭证');
        }
        this.setToken(token);
        if (userInfo) {
          this.setTenant(userInfo.loginTenantId);
          this.setUserInfo(userInfo);
        }
        return this.afterLoginAction(goHome, data);
      } catch (error) {
        return Promise.reject(error);
      }
    },
    /**
     * 扫码登录事件
     */
    async qrCodeLogin(token): Promise<GetUserInfoModel | null> {
      try {
        // save token
        this.setToken(token);
        return this.afterLoginAction(true, {});
      } catch (error) {
        return Promise.reject(error);
      }
    },
    /**
     * 登录完成处理
     * @param goHome
     */
    async afterLoginAction(goHome?: boolean, data?: any): Promise<any | null> {
      const token = this.token || data?.token;
      if (token) this.setToken(token);
      if (data?.userInfo) this.setUserInfo(data.userInfo);
      if (!this.getToken) return data ?? null;
      let userInfo = this.userInfo || data?.userInfo;
      try {
        userInfo = (await this.getUserInfoAction()) || userInfo;
      } catch {
        if (!userInfo) throw new Error('获取用户信息失败');
      }
      recordAuditEvent({ category: 'login', action: '登录成功', resource: 'Web 控制台' });
      const sessionTimeout = this.sessionTimeout;
      if (sessionTimeout) {
        this.setSessionTimeout(false);
      } else {
        await this.setLoginInfo({ ...data, isLogin: true });
        localStorage.setItem(JDragConfigEnum.DRAG_BASE_URL, useGlobSetting().domainUrl);

        const target = resolvePostLoginPath(
          router.currentRoute.value?.query?.redirect,
          userInfo?.homePath,
        );
        if (!goHome) {
          /* session timeout overlay keeps the current page */
        } else if (getUrlParam('ticket')) {
          window.location.replace(target);
        } else {
          await router.replace(target);
          if (router.currentRoute.value.path === PageEnum.BASE_LOGIN) {
            window.location.replace(target);
          }
        }
      }
      return data;
    },
    /**
     * 手机号登录
     * @param params
     */
    async phoneLogin(
      params: LoginParams & {
        goHome?: boolean;
        mode?: ErrorMessageMode;
      }
    ): Promise<GetUserInfoModel | null> {
      try {
        const { goHome = true, mode, ...loginParams } = params;
        const data = await phoneLoginApi(loginParams, mode);
        // 代码逻辑说明: 【issues/7488】手机号码登录，在请求头中无法获取租户id---
        const { token , userInfo } = data;
        this.setTenant(userInfo!.loginTenantId);
        // save token
        this.setToken(token);
        return this.afterLoginAction(goHome, data);
      } catch (error) {
        return Promise.reject(error);
      }
    },
    /**
     * 获取用户信息
     */
    async getUserInfoAction(): Promise<UserInfo | null> {
      if (!this.getToken) {
        return null;
      }
      const payload = await getUserInfo();
      if (!payload?.userInfo) {
        throw new Error('获取用户信息失败');
      }
      const { userInfo, sysAllDictItems } = payload;
      if (userInfo) {
        console.log(userInfo)

        const { roles = [] } = userInfo;
        if (isArray(roles)) {
          const roleList = roles.map((item) => item.value) as RoleEnum[];
          this.setRoleList(roleList);
        } else {
          userInfo.roles = [];
          this.setRoleList([]);
        }
        this.setUserInfo(userInfo);
      }
      /**
       * 添加字典信息到缓存
       * @updateBy:lsq
       * @updateDate:2021-09-08
       */
      if (sysAllDictItems) {
        this.setAllDictItems(sysAllDictItems);
      }

      return userInfo;
    },
    /**
     * 退出登录
     * @param goLogin 是否跳到登录页
     * @param userInitiated 是否用户主动点「退出登录」。主动退出不带 redirect 回登录页：
     *   校园公用机上下一个登录的往往是别人，把 A 最后停留的会话/助手页带给 B 会让 B 被
     *   自动拽进 A 的会话（然后 404）。token 失效被踢（userInitiated=false）仍带回跳路径，
     *   但只保留 pathname——query 里可能有别人的 thread id。
     */
    async logout(goLogin = false, userInitiated = false) {
      if (this.getToken) {
        try {
          await doLogout();
        } catch {
          console.log('注销Token失败');
        }
      }

      // 客户端会话隔离（2026-09-19）：在 userInfo 被置空之前取 id，把这个账号写在
      // localStorage / sessionStorage / IndexedDB 里的业务数据（默认模型、面试冲突提示、
      // 输入草稿，以及已下线功能遗留的子智能体运行变量、工作流本地备份）一并清掉。token 失效走的也是这里。
      clearUserScopedStorage(resolveStorageUserId(this.getUserInfo));

      this.setToken('');
      setAuthCache(TOKEN_KEY, null);
      this.setSessionTimeout(false);
      this.setUserInfo(null);
      this.setLoginInfo(null);
      this.setTenant(null);
      // 代码逻辑说明: 【TV360X-23】退出登录后会提示「Token时效，请重新登录」
      setTimeout(() => {
        this.setAllDictItems(null);
      }, 1e3);
      // 代码逻辑说明: 退出登录后清除拖拽模块的接口前缀
      localStorage.removeItem(JDragConfigEnum.DRAG_BASE_URL);

      //如果开启单点登录,则跳转到单点统一登录中心
      const openSso = useGlobSetting().openSso;
      if (openSso == 'true') {
        await useSso().ssoLoginOut();
      }
      //退出登录的时候需要用的应用id
      if(isOAuth2AppEnv()){
        const tenantId = getAuthCache(OAUTH2_THIRD_LOGIN_TENANT_ID);
        removeAuthCache(OAUTH2_THIRD_LOGIN_TENANT_ID);
        goLogin && await router.push({ name:"Login",query:{ tenantId:tenantId }})
      }else{
        // 代码逻辑说明: 修复登录成功后，没有正确重定向的问题
        if (goLogin && router.currentRoute.value.path !== PageEnum.BASE_LOGIN) {
          await router.push({
            path: PageEnum.BASE_LOGIN,
            // 主动退出不带 redirect；被踢只带 pathname（见 logout 注释）
            query: userInitiated ? {} : loginRedirectQuery(router.currentRoute.value.path),
          });
        }

      }
    },
    /**
     * 登录事件
     */
    async ThirdLogin(
      params: ThirdLoginParams & {
        goHome?: boolean;
        mode?: ErrorMessageMode;
      }
    ): Promise<any | null> {
      try {
        const { goHome = true, mode, ...ThirdLoginParams } = params;
        const data = await thirdLogin(ThirdLoginParams, mode);
        // 代码逻辑说明: 【issues/6652】开启租户数据隔离，接入钉钉后登录默认租户为0了---
        const { token, userInfo } = data;
        this.setTenant(userInfo?.loginTenantId);
        // save token
        this.setToken(token);
        return this.afterLoginAction(goHome, data);
      } catch (error) {
        return Promise.reject(error);
      }
    },
    /**
     * 退出询问
     */
    confirmLoginOut() {
      const { createConfirm } = useMessage();
      const { t } = useI18n();
      createConfirm({
        iconType: 'warning',
        title: t('sys.app.logoutTip'),
        content: t('sys.app.logoutMessage'),
        onOk: async () => {
          await this.logout(true, true);
        },
      });
    },
  },
});

// Need to be used outside the setup
export function useUserStoreWithOut() {
  return useUserStore(store);
}

/** 本机存储作用域用的用户 id：与 chatDrafts.currentDraftUserId 同口径（id → username），未登录为空串。 */
function resolveStorageUserId(info: Partial<UserInfo> | null | undefined): string {
  const anyInfo = (info || {}) as { id?: unknown; username?: unknown };
  return String(anyInfo.id || anyInfo.username || '').trim();
}

// 把「当前用户 id」注入 userScopedStorage（该工具刻意不 import Pinia/路由，见其文件头）。
// 本模块在路由守卫初始化时就已加载，早于任何业务页读写存储。
registerStorageUserIdGetter(() => {
  try {
    return resolveStorageUserId(useUserStoreWithOut().getUserInfo);
  } catch {
    return ''; // Pinia 尚未安装（极早期）：按未登录处理，不读不写
  }
});
