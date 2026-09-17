<template>
  <div class="profile-settings" :aria-busy="loading">
    <div v-if="loading" class="profile-loading" role="status">
      <span class="profile-loading-avatar"></span>
      <span class="profile-loading-line wide"></span>
      <span class="profile-loading-line"></span>
      <span class="profile-loading-card"></span>
    </div>

    <div v-else-if="loadError" class="profile-load-error" role="alert">
      <ExclamationCircleOutlined />
      <strong>个人资料暂时无法加载</strong>
      <span>{{ loadError }}</span>
      <button type="button" @click="loadProfile">重试</button>
    </div>

    <form v-else class="profile-form" @submit.prevent="saveProfile">
      <section class="profile-identity" aria-labelledby="profile-identity-title">
        <div class="profile-avatar-control">
          <CropperAvatar
            :key="avatarRevision"
            ref="avatarCropperRef"
            :upload-api="uploadImg"
            :show-btn="false"
            :value="avatarUrl"
            :modal-props="{ zIndex: 2400 }"
            aria-label="点击头像更换头像"
            role="button"
            tabindex="0"
            title="点击头像更换"
            width="80"
            @change="updateAvatar"
            @keydown.enter.prevent="openAvatarEditor"
            @keydown.space.prevent="openAvatarEditor"
          />
          <span class="profile-avatar-badge" aria-hidden="true"><CameraOutlined /></span>
        </div>
        <div class="profile-identity-copy">
          <h3 id="profile-identity-title">{{ profile.realname || '未命名用户' }}</h3>
          <span>{{ profile.username || '登录用户' }}</span>
          <div class="profile-sync-note">
            <CheckCircleFilled aria-hidden="true" />
            <span>头像和名称会同步为智能体创建人信息</span>
          </div>
        </div>
      </section>

      <div class="profile-sections">
        <section class="profile-section" aria-labelledby="profile-basic-title">
          <div class="profile-section-heading">
            <h3 id="profile-basic-title">基本资料</h3>
          </div>

          <div class="profile-field-grid">
            <label class="profile-field profile-field-full">
              <span>显示名称</span>
              <input v-model.trim="profile.realname" type="text" maxlength="100" autocomplete="name" placeholder="请输入名称" />
            </label>

            <label class="profile-field">
              <span>生日</span>
              <input v-model="profile.birthday" type="date" />
            </label>

            <fieldset class="profile-field profile-sex">
              <legend>性别</legend>
              <div class="profile-segmented">
                <label :class="{ active: profile.sex === '1' }">
                  <input v-model="profile.sex" type="radio" value="1" />
                  <span>男</span>
                </label>
                <label :class="{ active: profile.sex === '2' }">
                  <input v-model="profile.sex" type="radio" value="2" />
                  <span>女</span>
                </label>
                <label :class="{ active: profile.sex === '' }">
                  <input v-model="profile.sex" type="radio" value="" />
                  <span>不填写</span>
                </label>
              </div>
            </fieldset>
          </div>
        </section>
      </div>

      <div class="profile-save-bar">
        <span v-if="feedbackType === 'error'" class="profile-save-status error">{{ feedback }}</span>
        <button class="profile-save-button" type="submit" :disabled="saving || !dirty">
          <LoadingOutlined v-if="saving" spin />
          <span>{{ saving ? '保存中…' : '保存修改' }}</span>
        </button>
      </div>
    </form>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { CameraOutlined, CheckCircleFilled, ExclamationCircleOutlined, LoadingOutlined } from '@ant-design/icons-vue';
import dayjs from 'dayjs';
import { CropperAvatar } from '/@/components/Cropper';
import { uploadImg } from '/@/api/sys/upload';
import { useUserStore } from '/@/store/modules/user';
import headerImg from '/@/assets/images/header.jpg';
import { getUserData, userEdit } from './UserSetting.api';
import { getProxyStaticFileUrl } from '/@/utils/common/fileUrl';

type FeedbackType = '' | 'success' | 'error';

interface ProfileForm {
  id: string | number;
  username: string;
  realname: string;
  avatar: string;
  birthday: string;
  sex: string;
  postText: string;
  email: string;
  phone: string;
  createTime: string;
}

const emit = defineEmits<{
  (event: 'saved', value: { realname: string; avatar: string }): void;
  (event: 'error', error: unknown): void;
  (event: 'dirty-change', value: boolean): void;
}>();

const userStore = useUserStore();
const loading = ref(true);
const saving = ref(false);
const avatarRevision = ref(0);
const avatarCropperRef = ref<{ openModal?: () => void } | null>(null);
const loadError = ref('');
const feedback = ref('');
const feedbackType = ref<FeedbackType>('');
const savedSnapshot = ref('');

const profile = reactive<ProfileForm>({
  id: '',
  username: '',
  realname: '',
  avatar: '',
  birthday: '',
  sex: '',
  postText: '',
  email: '',
  phone: '',
  createTime: '',
});

const editableSnapshot = computed(() => JSON.stringify({
  realname: profile.realname.trim(),
  birthday: profile.birthday,
  sex: profile.sex,
}));
const dirty = computed(
  () => !loading.value && Boolean(savedSnapshot.value) && editableSnapshot.value !== savedSnapshot.value,
);
function isAvatarFileReference(value: string): boolean {
  const path = String(value || '').trim();
  if (!path) return false;
  return /^(https?:\/\/|data:image\/|blob:|\/)/i.test(path)
    || path.includes('/')
    || /\.(png|jpe?g|gif|webp|svg)(?:[?#].*)?$/i.test(path);
}

function getProfileAvatarUrl(value: string): string {
  const path = String(value || '').trim();
  if (!isAvatarFileReference(path)) return headerImg;

  // `/api` is the normal same-origin Java proxy. Newly uploaded avatars are
  // object paths (for example `/abc...`), not direct URLs, so rendering them
  // through the configured remote domain skips the required `/admin-api` path.
  if (/^(data:image\/|blob:)/i.test(path)) return path;
  if (path.startsWith('/api/sys/common/static/')) return path;

  const staticPathIndex = path.indexOf('/sys/common/static/');
  if (staticPathIndex !== -1) {
    return `/api${path.slice(staticPathIndex)}`;
  }
  return getProxyStaticFileUrl(path) || headerImg;
}

// Older cropper versions could persist the upload success message instead of a
// file path. Keep that bad legacy value from rendering as a broken <img>.
const avatarUrl = computed(() => {
  return getProfileAvatarUrl(profile.avatar);
});

function resetFeedback() {
  feedback.value = '';
  feedbackType.value = '';
}

function normalizeProfile(data: Record<string, any>) {
  Object.assign(profile, {
    id: data.id || userStore.getUserInfo?.id || '',
    username: data.username || userStore.getUserInfo?.username || '',
    realname: data.realname || '',
    avatar: data.avatar || '',
    birthday: data.birthday && dayjs(data.birthday).isValid() ? dayjs(data.birthday).format('YYYY-MM-DD') : '',
    sex: data.sex === 1 || data.sex === '1' ? '1' : data.sex === 2 || data.sex === '2' ? '2' : '',
    postText: data.postText || '',
    email: data.email || '',
    phone: data.phone || '',
    createTime: data.createTime || '',
  });
  savedSnapshot.value = editableSnapshot.value;
}

function updateCachedUser(patch: Record<string, unknown>) {
  userStore.setUserInfo({ ...userStore.getUserInfo, ...patch } as any);
}

async function persistProfile(patch: Record<string, unknown>) {
  const response = await userEdit({ ...patch, id: profile.id });
  if (!response?.success) {
    throw new Error(response?.message || '个人资料保存失败');
  }
  return response;
}

async function loadProfile() {
  loading.value = true;
  loadError.value = '';
  resetFeedback();
  try {
    const response = await getUserData();
    if (!response?.success || !response.result) {
      throw new Error(response?.message || '服务未返回用户资料');
    }
    normalizeProfile(response.result);
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '请稍后重试';
    emit('error', error);
  } finally {
    loading.value = false;
  }
}

async function updateAvatar(_source: string, data: string) {
  if (!data) return;
  resetFeedback();
  try {
    await persistProfile({ avatar: data });
    profile.avatar = data;
    updateCachedUser({ avatar: data });
    emit('saved', { realname: profile.realname, avatar: data });
  } catch (error) {
    feedback.value = error instanceof Error ? error.message : '头像保存失败';
    feedbackType.value = 'error';
    emit('error', error);
    avatarRevision.value += 1;
  }
}

function openAvatarEditor() {
  avatarCropperRef.value?.openModal?.();
}

async function saveProfile() {
  const realname = profile.realname.trim();
  if (!realname) {
    feedback.value = '请输入名称';
    feedbackType.value = 'error';
    return;
  }
  if (saving.value || !dirty.value) return;
  saving.value = true;
  resetFeedback();
  try {
    await persistProfile({
      realname,
      birthday: profile.birthday,
      sex: profile.sex ? Number(profile.sex) : 0,
    });
    profile.realname = realname;
    savedSnapshot.value = editableSnapshot.value;
    updateCachedUser({
      realname,
      birthday: profile.birthday,
      sex: profile.sex ? Number(profile.sex) : 0,
    });
    resetFeedback();
    emit('saved', { realname, avatar: profile.avatar });
  } catch (error) {
    feedback.value = error instanceof Error ? error.message : '个人资料保存失败';
    feedbackType.value = 'error';
    emit('error', error);
  } finally {
    saving.value = false;
  }
}

watch(dirty, (value) => emit('dirty-change', value), { immediate: true });
onMounted(loadProfile);
</script>

<style scoped lang="less">
.profile-settings {
  --profile-ink: #17191d;
  --profile-muted: #747985;
  --profile-line: #e8e9ed;
  --profile-soft: #f6f7f9;
  --profile-accent: #4f5fcd;
  color: var(--profile-ink);
}

.profile-loading { display: grid; padding: 28px 4px; gap: 14px; }
.profile-loading-avatar,
.profile-loading-line,
.profile-loading-card {
  display: block;
  border-radius: 10px;
  background: linear-gradient(90deg, #f0f1f3 20%, #f8f8f9 40%, #f0f1f3 60%);
  background-size: 240% 100%;
  animation: profile-shimmer 1.4s ease-in-out infinite;
}
.profile-loading-avatar { width: 88px; height: 88px; border-radius: 50%; }
.profile-loading-line { width: 42%; height: 14px; }
.profile-loading-line.wide { width: 68%; }
.profile-loading-card { height: 280px; margin-top: 16px; }

.profile-load-error {
  min-height: 360px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 10px;
  color: var(--profile-muted);
  text-align: center;
}
.profile-load-error > :first-child { color: #c55a4d; font-size: 24px; }
.profile-load-error strong { color: var(--profile-ink); font-size: 16px; }
.profile-load-error button {
  border: 1px solid var(--profile-line);
  border-radius: 8px;
  background: #fff;
  padding: 7px 14px;
  color: var(--profile-ink);
  cursor: pointer;
}

.profile-identity {
  display: grid;
  min-width: 0;
  grid-template-areas: 'avatar copy';
  grid-template-columns: 80px minmax(0, 1fr);
  align-items: center;
  gap: 16px;
  border-bottom: 1px solid var(--profile-line);
  padding: 0 4px 16px;
}

.profile-avatar-control { position: relative; grid-area: avatar; width: 80px; height: 80px; }
.profile-avatar-control :deep(.jeecg-cropper-avatar-image-wrapper) {
  position: relative;
  border: 3px solid #fff;
  box-shadow: 0 0 0 1px #d9dde5, 0 8px 20px rgba(24, 28, 38, 0.1);
  transition: box-shadow 0.18s ease, transform 0.18s ease;
}
.profile-avatar-control :deep(.jeecg-cropper-avatar-image-wrapper img) { width: 100%; height: 100%; object-fit: cover; }
.profile-avatar-control :deep([role='button']:focus-visible .jeecg-cropper-avatar-image-wrapper) {
  box-shadow: 0 0 0 3px rgba(79, 95, 205, 0.26), 0 8px 20px rgba(24, 28, 38, 0.1);
  outline: none;
}
.profile-avatar-control :deep(.jeecg-cropper-avatar-image-wrapper:hover) {
  transform: translateY(-1px);
}
.profile-avatar-control :deep(.jeecg-cropper-avatar-image-mask) {
  display: none !important;
}
.profile-avatar-badge {
  position: absolute;
  right: 0;
  bottom: 1px;
  display: grid;
  width: 27px;
  height: 27px;
  place-items: center;
  border: 2px solid #fff;
  border-radius: 50%;
  background: var(--profile-ink);
  color: #fff;
  box-shadow: 0 2px 7px rgba(18, 21, 27, 0.18);
  font-size: 11px;
  pointer-events: none;
}

.profile-identity-copy { display: flex; min-width: 0; grid-area: copy; align-items: flex-start; flex-direction: column; }
.profile-identity-copy h3 { max-width: 100%; margin: 0; overflow: hidden; color: var(--profile-ink); font-size: 20px; font-weight: 650; line-height: 1.3; text-overflow: ellipsis; white-space: nowrap; }
.profile-identity-copy > span { margin-top: 4px; color: var(--profile-muted); font-size: 13px; }
.profile-sync-note { display: flex; min-width: 0; align-items: center; gap: 6px; margin-top: 8px; color: #747984; font-size: 11px; line-height: 1.4; }
.profile-sync-note > :first-child { flex: 0 0 auto; color: #5968c9; }

.profile-sections { overflow: hidden; margin-top: 16px; border: 1px solid var(--profile-line); border-radius: 13px; background: #fff; }
.profile-section { padding: 15px 18px 16px; }
.profile-section + .profile-section { border-top: 1px solid var(--profile-line); background: #fafbfc; }
.profile-section-heading { margin-bottom: 12px; }
.profile-section-heading h3 { margin: 0; color: var(--profile-ink); font-size: 15px; font-weight: 650; }
.profile-section-heading span { display: block; margin-top: 2px; color: var(--profile-muted); font-size: 11px; }

.profile-field-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; }
.profile-field { display: flex; min-width: 0; flex-direction: column; gap: 6px; }
.profile-field > span,
.profile-field legend { color: #545963; font-size: 13px; font-weight: 600; }
.profile-field-full { grid-column: 1 / -1; }
.profile-field input[type='text'],
.profile-field input[type='date'] {
  width: 100%;
  height: 38px;
  border: 1px solid #dfe2e8;
  border-radius: 10px;
  outline: none;
  background: #fff;
  padding: 0 12px;
  color: var(--profile-ink);
  font: inherit;
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}
.profile-field input:focus { border-color: #7b87df; box-shadow: 0 0 0 3px rgba(79, 95, 205, 0.12); }

.profile-sex { margin: 0; border: 0; padding: 0; }
.profile-segmented { display: grid; grid-template-columns: 1fr 1fr 1.25fr; gap: 4px; min-height: 38px; border: 1px solid #dfe2e8; border-radius: 10px; background: var(--profile-soft); padding: 3px; }
.profile-segmented label { display: grid; place-items: center; border-radius: 7px; color: #666c77; font-size: 12px; cursor: pointer; }
.profile-segmented label.active { background: #fff; color: var(--profile-ink); font-weight: 600; box-shadow: 0 1px 4px rgba(20, 24, 32, 0.09); }
.profile-segmented input { position: absolute; opacity: 0; pointer-events: none; }

.profile-save-bar { position: sticky; z-index: 2; bottom: 0; display: flex; min-height: 54px; align-items: center; justify-content: flex-end; gap: 10px; background: #fff; padding: 12px 0 0; }
.profile-save-status { color: var(--profile-muted); font-size: 12px; }
.profile-save-status.error { color: #b5483d; }
.profile-save-button {
  display: inline-flex;
  min-width: 126px;
  height: 40px;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border: 1px solid var(--profile-ink);
  border-radius: 10px;
  background: var(--profile-ink);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: transform 0.18s ease, opacity 0.18s ease, background 0.18s ease;
}
.profile-save-button:hover:not(:disabled) { transform: translateY(-1px); background: #2a2d33; }
.profile-save-button:focus-visible,
.profile-load-error button:focus-visible { outline: 2px solid #6d7bd8; outline-offset: 2px; }
.profile-save-button:disabled { cursor: default; opacity: 0.38; }

@keyframes profile-shimmer {
  from { background-position: 100% 0; }
  to { background-position: -100% 0; }
}

@media (max-width: 380px) {
  .profile-identity { grid-template-columns: 80px minmax(0, 1fr); gap: 14px; }
  .profile-avatar-control { width: 80px; height: 80px; }
  .profile-field-grid { grid-template-columns: 1fr; }
  .profile-field-full { grid-column: auto; }
  .profile-section { padding: 17px; }
}

@media (prefers-reduced-motion: reduce) {
  .profile-loading-avatar,
  .profile-loading-line,
  .profile-loading-card { animation: none; }
  .profile-save-button { transition: none; }
}
</style>
