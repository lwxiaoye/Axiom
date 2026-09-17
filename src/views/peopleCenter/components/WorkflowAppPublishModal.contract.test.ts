import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const modal = readFileSync(resolve(__dirname, 'WorkflowAppPublishModal.vue'), 'utf8');
const page = readFileSync(resolve(__dirname, '../pages/MyAgentsPage.vue'), 'utf8');

describe('WorkflowAppPublishModal update visibility', () => {
  it('preserves the live visibility configuration during an update release', () => {
    expect(modal).toContain('void prefillPublishedSettings(item)');
    expect(modal).toContain('form.visibleRoleIds = live.visibleRoleIds || []');
    expect(modal).toContain('form.visibleDeptIds = live.visibleDeptIds || []');
    expect(modal).toContain('isUpdate.value && !roleVisibilityReady && !roleVisibilityTouched ? undefined');
    expect(modal).toContain('isUpdate.value && !deptVisibilityReady && !deptVisibilityTouched ? undefined');
    expect(page).toContain('visibleRoleIds?: string[] | string;');
    expect(page).toContain('visibleDeptIds?: string[] | string;');
  });

  it('keeps publishing focused on the marketplace', () => {
    expect(modal).not.toContain('form.publishChannels');
    expect(modal).not.toContain('发布为 API');
    expect(page).not.toContain('publishChannels: payload.publishChannels');
    expect(page).not.toContain('embedOrigins: payload.embedOrigins');
  });
});
