<template>
  <section class="research-team" :data-details-open="open" aria-label="本次研究团队">
    <div class="team-heading-row">
      <div class="team-heading">
        <button
          class="team-avatars entering"
          type="button"
          :aria-label="open ? '隐藏详细协作过程' : '查看详细协作过程'"
          :aria-expanded="open"
          @click="open = !open"
        >
          <ResearchOrb role="leader" :active="!settled" />
          <ResearchOrb v-for="member in team.members" :key="member.id" :role="member.role" :active="memberActive(member.id)" />
        </button>
        <span>{{ heading }}</span>
        <span v-if="elapsed && !settled" class="team-elapsed">{{ elapsed }}</span>
        <button
          v-if="!settled"
          class="team-toggle"
          type="button"
          :aria-expanded="expanded"
          :aria-label="expanded ? '收起协作过程' : '展开协作过程'"
          @click="expanded = !expanded"
        >
          <PremiumChevron class="team-chevron" :direction="expanded ? 'down' : 'right'" :size="12" />
        </button>
      </div>
    </div>
    <Transition name="team-finish">
      <div v-if="!settled" class="team-activity-wrap">
        <TransitionGroup name="team-feed" tag="div" class="team-activity">
          <div v-for="item in visibleActivity" :key="item.id" class="activity-row">
            <ResearchActivityItem
              :item="item" :name="owner(item.memberId).name" :role="owner(item.memberId).role"
              :active="memberActive(item.memberId)" :compact="!expanded" :settled="settled" />
          </div>
        </TransitionGroup>
      </div>
    </Transition>
    <Drawer
      v-model:open="open"
      placement="right"
      :width="460"
      :z-index="2300"
      :keyboard="true"
      :mask="false"
      root-class-name="research-team-drawer"
    >
      <template #title><span class="drawer-title"><span :key="String(open)" class="team-avatars entering"><ResearchOrb role="leader" :active="!settled" /><ResearchOrb v-for="member in team.members" :key="member.id" :role="member.role" :active="memberActive(member.id)" /></span>研究团队</span></template>
      <section v-for="group in activityGroups" :key="group.id" class="detail-group">
        <button class="group-heading" type="button" :aria-expanded="!collapsedGroups.has(group.id)" @click="toggleGroup(group.id)">
          <ResearchOrb :role="owner(group.memberId).role" :active="memberActive(group.memberId)" />
          <strong>{{ owner(group.memberId).name }}</strong>
          <span v-if="owner(group.memberId).role === 'leader'">统筹</span>
          <PremiumChevron :direction="collapsedGroups.has(group.id) ? 'right' : 'down'" :size="11" />
        </button>
        <div class="group-clip" :class="{ open: !collapsedGroups.has(group.id) }">
          <div class="group-clip-inner">
            <div class="group-activities">
              <div v-for="item in group.items" :key="item.id" class="detail-activity" :class="{ 'search-step': item.kind === 'search' }">
                <ResearchActivityItem :item="item" :name="owner(item.memberId).name" :role="owner(item.memberId).role" :active="memberActive(item.memberId)" :settled="settled" detail hide-owner />
              </div>
            </div>
          </div>
        </div>
      </section>
      <p v-for="member in failedMembers" :key="member.id" class="member-error">{{ member.name }}：{{ member.error }}</p>
      <p v-for="member in partialMembers" :key="member.id" class="member-note">{{ member.name }}：{{ memberContribution(member) }}</p>
    </Drawer>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Drawer } from 'ant-design-vue';
import PremiumChevron from './PremiumChevron.vue';
import ResearchOrb from './ResearchOrb.vue';
import ResearchActivityItem from './ResearchActivityItem.vue';
import { memberContribution, researchActivities, type ResearchTeamSnapshot } from '../utils/researchTeam';
const props = defineProps<{ team: ResearchTeamSnapshot; settled: boolean; cancelled?: boolean; failed?: boolean; elapsed?: string }>();
const open = ref(false);
const expanded = ref(false);
const collapsedGroups = ref(new Set<string>());
watch(() => props.team.id, () => { expanded.value = false; open.value = false; collapsedGroups.value.clear(); });
const activity = computed(() => researchActivities(props.team));
// Group only adjacent activity, preserving the actual conversation order.
const activityGroups = computed(() => {
  const groups: { id: string; memberId: string; items: typeof activity.value }[] = [];
  for (const item of activity.value) {
    const previous = groups[groups.length - 1];
    if (previous?.memberId === item.memberId) previous.items.push(item);
    else groups.push({ id: item.id, memberId: item.memberId, items: [item] });
  }
  return groups;
});
function toggleGroup(id: string) {
  if (collapsedGroups.value.has(id)) collapsedGroups.value.delete(id);
  else collapsedGroups.value.add(id);
}
const visibleActivity = computed(() => expanded.value ? activity.value : activity.value.slice(-3));
const failedMembers = computed(() => props.team.members.filter(member => member.error));
const partialMembers = computed(() => props.team.members.filter(member => member.status === 'partial'));
const heading = computed(() => {
  if (props.cancelled) return '研究已停止';
  if (props.failed) return '研究未完成';
  if (props.settled) return '研究团队';
  if (props.team.stage === 'planning') return '正在制定研究计划';
  if (props.team.stage === 'synthesizing') return '正在整合研究报告';
  return '团队正在研究';
});
function owner(id: string) {
  return props.team.members.find(member => member.id === id) || props.team.leader || { name: '主智能体', role: 'leader' as const };
}
function memberActive(id: string) {
  if (props.settled || props.team.stage === 'synthesizing') return false;
  const member = props.team.members.find(member => member.id === id);
  return member ? ['researching', 'reviewing'].includes(member.status) : true;
}
</script>
<style scoped lang="less">
.research-team { min-width: 0; max-width: 100%; margin: 8px 0 16px; color: #666; font-size: 13px; }
.team-heading-row { display: flex; align-items: center; gap: 10px; }
.team-heading { display: flex; align-items: center; gap: 7px; min-height: 32px; max-width: 100%; flex-wrap: wrap; }
.team-avatars, .team-toggle { border: 0; padding: 0; background: none; color: inherit; font: inherit; cursor: pointer; }
.team-avatars:focus-visible, .team-toggle:focus-visible { outline: 2px solid #576ed8; outline-offset: 3px; }
.team-toggle { display: inline-flex; align-items: center; justify-content: center; padding: 4px; margin-left: -2px; border-radius: 5px; color: #999; }
.team-avatars { display: inline-flex; align-items: center; gap: 2px; flex: none; padding: 0 2px; border-radius: 999px; }
.team-avatars > .research-orb { width: 26px; height: 26px; margin: 0; }
.team-avatars.entering > .research-orb { animation: team-join .36s cubic-bezier(.22,1,.36,1) both; }
.team-avatars.entering > .research-orb:nth-child(2) { animation-delay: .24s; }.team-avatars.entering > .research-orb:nth-child(3) { animation-delay: .48s; }.team-avatars.entering > .research-orb:nth-child(4) { animation-delay: .72s; }
.team-elapsed { color: #8a8a8a; }.team-chevron { color: #999; }
.team-activity-wrap { display: grid; grid-template-rows: 1fr; min-width: 0; max-width: 100%; margin-top: 5px; }
.team-activity { min-width: 0; min-height: 0; max-width: 100%; overflow: hidden; }
.activity-row { display: grid; grid-template-rows: 1fr; width: 100%; min-width: 0; max-width: 100%; }
.activity-row > * { min-width: 0; min-height: 0; }
.team-feed-enter-active, .team-feed-leave-active {
  transition: grid-template-rows .24s cubic-bezier(.22,1,.36,1), opacity .2s ease;
}
.team-feed-enter-active > *, .team-feed-leave-active > * { overflow: hidden; }
.team-feed-enter-from, .team-feed-leave-to { grid-template-rows: 0fr; opacity: 0; }
.team-finish-leave-active { display: grid; overflow: hidden; transition: grid-template-rows .28s cubic-bezier(.22,1,.36,1), opacity .2s ease; }
.team-finish-leave-from { grid-template-rows: 1fr; }
.team-finish-leave-to { grid-template-rows: 0fr; opacity: 0; }
.drawer-title { display: flex; align-items: center; gap: 9px; font-size: 14px; }
.detail-group { margin-bottom: 22px; }
.group-heading { display: flex; align-items: center; gap: 6px; padding: 0; margin-bottom: 5px; border: 0; background: none; cursor: pointer; color: #777; font: inherit; font-size: 13px; }
.group-heading strong { color: #171717; font-weight: 600; }
.group-heading:focus-visible { outline: 2px solid #576ed8; outline-offset: 3px; border-radius: 4px; }
.group-clip {
  display: grid;
  grid-template-rows: 1fr;
  min-width: 0;
  opacity: 1;
  transition: grid-template-rows .24s cubic-bezier(.22,1,.36,1), opacity .2s ease;
}
.group-clip:not(.open) { grid-template-rows: 0fr; opacity: 0; pointer-events: none; }
.group-clip-inner { min-width: 0; min-height: 0; overflow: hidden; }
.detail-activity { position: relative; min-width: 0; margin-bottom: 6px; }
.search-step::before { content: ''; position: absolute; top: 18px; bottom: -8px; left: 6px; width: 1px; background: #ededeb; }
.search-step:last-child::before { display: none; }
.member-error, .member-note { color: #888; font-size: 12px; }
@keyframes team-join { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) {
  .team-avatars.entering > .research-orb { animation: none; }
  .team-feed-enter-active, .team-feed-leave-active, .team-finish-leave-active, .team-chevron, .group-clip { transition: none; }
}
</style>
<style lang="less">
.research-team-drawer.ant-drawer { pointer-events: none; }
.research-team-drawer .ant-drawer-header { padding: 18px 16px; border-bottom: 0; }
.research-team-drawer .ant-drawer-header-title { flex-direction: row-reverse; gap: 12px; }
.research-team-drawer .ant-drawer-close { display: inline-flex; align-items: center; justify-content: center; flex: none; width: 40px; height: 40px; margin: 0; padding: 0; border-radius: 50%; color: #171717; transition: background-color .15s ease, transform .1s ease; }
.research-team-drawer .ant-drawer-close:hover { background: #f2f2f0; }
.research-team-drawer .ant-drawer-close:active { background: #e9e9e7; transform: scale(.95); }
.research-team-drawer .ant-drawer-close:focus-visible { outline: 2px solid #576ed8; outline-offset: 2px; }
.research-team-drawer .ant-drawer-body { padding: 8px 16px 28px; scrollbar-gutter: stable; }
.research-team-drawer .ant-drawer-content-wrapper {
  pointer-events: auto;
  // 无遮罩 Drawer 关闭时会移除行内 width，退场期间仍须保持面板宽度。
  width: 460px;
  max-width: 100vw;
  transition: transform .24s cubic-bezier(.4, 0, .2, 1);
}
.research-team-drawer .ant-drawer-panel-motion-right-enter-active,
.research-team-drawer .ant-drawer-panel-motion-right-appear-active,
.research-team-drawer .ant-drawer-panel-motion-right-leave-active {
  transition: transform .24s cubic-bezier(.4, 0, .2, 1);
}
.research-team-drawer:not(.ant-drawer-open) .ant-drawer-content-wrapper { pointer-events: none; }
@media (min-width: 1100px) {
  .tox-center:has(.research-team) .workspace {
    transition: padding-right .24s cubic-bezier(.4, 0, .2, 1);
  }
  .tox-center:has(.research-team[data-details-open="true"]) .workspace {
    padding-right: 460px;
  }
  .chat-home:not(.empty-state):has(.research-team) .composer-dock {
    transition: left .24s cubic-bezier(.4, 0, .2, 1), width .24s cubic-bezier(.4, 0, .2, 1);
  }
  .chat-home:not(.empty-state):has(.research-team[data-details-open="true"]) .composer-dock {
    left: calc((100% + var(--center-nav-width, 280px) - 460px) / 2);
    width: min(820px, calc(100vw - var(--center-nav-width, 280px) - 540px));
  }
}
@media (prefers-reduced-motion: reduce) {
  .research-team-drawer .ant-drawer-close,
  .research-team-drawer .ant-drawer-content-wrapper,
  .research-team-drawer .ant-drawer-panel-motion-right-enter-active,
  .research-team-drawer .ant-drawer-panel-motion-right-appear-active,
  .research-team-drawer .ant-drawer-panel-motion-right-leave-active,
  .tox-center:has(.research-team) .workspace,
  .chat-home:not(.empty-state):has(.research-team) .composer-dock {
    transition: none;
  }
}
</style>
