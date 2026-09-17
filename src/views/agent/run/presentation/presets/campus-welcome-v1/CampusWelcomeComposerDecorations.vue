<template>
  <span
    :class="[
      'campus-composer-decorations',
      `is-device-${device}`,
      { 'is-conversation': !emptyState },
    ]"
    aria-hidden="true"
  >
    <img class="campus-backpack" :src="backpack" alt="" />
    <img class="campus-students" :src="studentGroup" alt="" />
  </span>
</template>

<script setup lang="ts">
import backpack from './assets/backpack-cutout.png';
import studentGroup from './assets/student-group-cutout.png';

withDefaults(
  defineProps<{
    emptyState?: boolean;
    device?: 'desktop' | 'tablet' | 'mobile';
  }>(),
  {
    emptyState: true,
    device: 'desktop',
  },
);
</script>

<style scoped lang="less">
.campus-composer-decorations {
  position: absolute;
  z-index: 1;
  right: 0;
  bottom: 100%;
  left: 0;
  height: 86px;
  pointer-events: none;
}

.campus-composer-decorations img {
  position: absolute;
  bottom: -4px;
  display: block;
  height: auto;
  filter: drop-shadow(0 7px 8px rgba(44, 89, 128, 0.08));
  user-select: none;
}

.campus-backpack {
  left: 28px;
  width: 72px;
}

.campus-students {
  right: 28px;
  width: 178px;
}

.campus-composer-decorations.is-conversation {
  height: 64px;
}

.campus-composer-decorations.is-conversation .campus-backpack {
  left: 22px;
  width: 54px;
}

.campus-composer-decorations.is-conversation .campus-students {
  right: 22px;
  width: 132px;
}

.campus-composer-decorations.is-device-tablet {
  height: 72px;
}

.campus-composer-decorations.is-device-tablet .campus-backpack {
  left: 20px;
  width: 58px;
}

.campus-composer-decorations.is-device-tablet .campus-students {
  right: 20px;
  width: 138px;
}

.campus-composer-decorations.is-device-tablet.is-conversation .campus-backpack {
  left: 18px;
  width: 50px;
}

.campus-composer-decorations.is-device-tablet.is-conversation .campus-students {
  right: 18px;
  width: 124px;
}

.campus-composer-decorations.is-device-mobile { height: 64px; }
.campus-composer-decorations.is-device-mobile .campus-backpack { left: 16px; width: 50px; }
.campus-composer-decorations.is-device-mobile .campus-students { right: 14px; width: 120px; }
.campus-composer-decorations.is-device-mobile.is-conversation .campus-backpack { left: 14px; width: 44px; }
.campus-composer-decorations.is-device-mobile.is-conversation .campus-students { right: 12px; width: 108px; }
</style>
