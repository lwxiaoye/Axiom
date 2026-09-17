<template>
  <span class="thumb">
    <Image v-if="fileUrl" :src="thumbUrl" :width="104" />
  </span>
</template>
<script lang="ts">
  import { computed, defineComponent } from 'vue';
  import { propTypes } from '/@/utils/propTypes';
  import { Image } from 'ant-design-vue';
  import { getFileAccessHttpUrl } from '/@/utils/common/compUtils';

  export default defineComponent({
    components: { Image },
    props: {
      fileUrl: propTypes.string.def(''),
      fileName: propTypes.string.def(''),
    },
    setup(props) {
      const thumbUrl = computed(() => getFileAccessHttpUrl(props.fileUrl));
      return { thumbUrl };
    },
  });
</script>
<style lang="less">
  .thumb {
    img {
      position: static;
      display: block;
      cursor: zoom-in;
      border-radius: 4px;
      object-fit: cover;
    }
  }
</style>
