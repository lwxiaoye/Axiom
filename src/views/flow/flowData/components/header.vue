<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <a-layout-header class="headerBox">
    <div></div>
    <!-- <a-input v-model:value="value" placeholder="搜索全部文件" class="searchBox" @input="handleSearch">
      <template #prefix>
        <SearchOutlined />
      </template>
    </a-input> -->

    <div class="userHeader">
      <div class="backHome mr-4" @click="backHome">回到个人中心</div>
      <img v-if="userInfo.avatar" :src="userAvatarUrl" class="userImg" />
      <img v-else src="@/assets/img/header.png" class="userImg" />
      <div class="userName">
        {{ userInfo.xm }}
      </div>
    </div>
  </a-layout-header>
</template>

<script lang="ts" setup>
  import { computed, ref } from 'vue';
  // import { SearchOutlined } from '@ant-design/icons-vue';

  // import { useSearchStore } from '@/store/search';

  import { getUserInfo } from '../FlowData.api';
  import { getFileAccessHttpUrl } from '/@/utils/common/compUtils';

  // 路由
  import { useRouter } from 'vue-router';
  const router = useRouter();

  // const searchStore = useSearchStore();
  // let value = ref<any>('');
  // // 搜索框变化时触发
  // function handleSearch() {
  //   searchStore.setSearchValue(value.value);
  // }
  let userInfo = ref<any>({});
  const userAvatarUrl = computed(() => getFileAccessHttpUrl(userInfo.value?.avatar || ''));
  function init() {
    getUserInfo().then((res: any) => {
      console.log(res);
      userInfo.value = res;
    });
  }

  init();

  function backHome() {
    router.push('/peopleCenter/index');
  }
</script>
<style lang="less" scoped>
  .w_100 {
    width: 100%;
    object-fit: cover;
  }
  .headerBox {
    height: 72px;
    background-color: #fff;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid rgba(235, 235, 235, 1);

    .searchBox {
      width: 36rem;
      height: 2.5rem;
      background: rgba(245, 245, 245, 1);
    }

    .userHeader {
      display: flex;
      align-items: center;

      .backHome {
        cursor: pointer;
      }

      .userImg {
        width: 2rem;
        height: 2rem;
        opacity: 1;
        background: rgba(204, 204, 204, 1);
        margin-right: 0.5rem;
        border-radius: 10px;
      }

      .userName {
        font-size: 0.88rem;
        font-weight: 400;
        color: rgba(51, 51, 51, 1);
      }
    }
  }
</style>
