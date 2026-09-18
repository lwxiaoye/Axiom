import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const read = (relative: string) => readFileSync(resolve(__dirname, relative), 'utf8');

const tab = read('../tabs/MyAgentsTab.vue');
const page = read('../pages/MyAgentsPage.vue');
const panel = read('AgentsPanel.vue');
const reviewPanel = read('ReviewPanel.vue');
const adminConsole = read('../pages/AdminConsolePage.vue');

describe('publish flow: create → configure → publish → review → run', () => {
  it('checks publish readiness before opening the publish dialog and routes 400s to the config page', () => {
    expect(page).toContain('getPublishReadiness(item, definition)');
    expect(page).toContain('offerConfiguration(item, \'还不能发布\'');
    expect(page).toContain('describeSubmitReviewFailure(error)');
    expect(page).toContain('failure.needsConfiguration');
    expect(page).toContain('onOk: () => openAiAppDesigner(item)');
    expect(page).toContain('result?.autoApproved');
  });

  it('shows a next step on every card instead of only a run button for published apps', () => {
    expect(panel).toContain("nextAction(item).key === 'run'");
    expect(panel).toContain('nextAction(item).passive');
    expect(panel).toContain('@click="runNextAction(item)"');
    expect(panel).toContain("if (action.key === 'configure') emit('openDesigner', item);");
    expect(panel).toContain("else if (action.key === 'publish') emit('publish', item);");
    expect(panel).toContain("okText: '重新提交发布'");
  });

  it('mounts the review queue inside My Agents for reviewers only, never in the admin console', () => {
    expect(tab).toContain("import ReviewPanel from '../workbench/ReviewPanel.vue';");
    expect(tab).toContain('v-else-if="activePanel === \'review\' && isReviewer"');
    expect(tab).toContain("label: '待审核'");
    expect(tab).toContain('isReviewer.value = Boolean(caps?.isReviewer);');
    expect(tab).toContain("@changed=\"emit('reviewChanged')\"");
    expect(page).toContain('@review-changed="handleReviewChanged"');
    expect(adminConsole).not.toContain('ReviewPanel');
    expect(adminConsole).not.toContain('queryReviewPage');
  });

  it('lets the reviewer approve or reject with a mandatory rejection reason', () => {
    expect(reviewPanel).toContain('approveReview(reviewModal.record.id, comment || undefined)');
    expect(reviewPanel).toContain('rejectReview(reviewModal.record.id, comment)');
    expect(reviewPanel).toContain("message.warning('驳回必须填写原因')");
    expect(reviewPanel).toContain("emit('changed')");
    expect(reviewPanel).toContain('previewVersionId: record.id');
  });
});
