import { getWorkflowReviewPresentation } from './reviewStatus';

describe('workflow review status presentation', () => {
  it('keeps the published lifecycle while a replacement version is under review', () => {
    expect(
      getWorkflowReviewPresentation({
        status: 'published',
        reviewSummary: { status: 'pending_review', versionId: 'v2', versionNo: 2 },
      })
    ).toEqual({
      statusClass: 'pending',
      text: '已发布 · 更新审核中',
      canSubmit: false,
      canWithdraw: true,
    });
  });

  it('surfaces a rejected republish reason without treating the app as live', () => {
    expect(
      getWorkflowReviewPresentation({
        status: 'unpublished',
        reviewSummary: {
          status: 'rejected',
          versionId: 'v3',
          versionNo: 3,
          reviewComment: '请补充工具权限说明',
        },
      })
    ).toEqual({
      statusClass: 'failed',
      text: '已下架 · 重新发布被驳回',
      canSubmit: true,
      canWithdraw: false,
      reviewComment: '请补充工具权限说明',
    });
  });

  it('allows an unpublished app without a pending version to submit for review', () => {
    expect(getWorkflowReviewPresentation({ status: 'unpublished' })).toEqual({
      statusClass: 'unpublished',
      text: '已下架',
      canSubmit: true,
      canWithdraw: false,
    });
  });

  it('does not expose the legacy enabled flag for a published app', () => {
    expect(getWorkflowReviewPresentation({ status: 'published' })).toEqual({
      statusClass: 'published',
      text: '已发布',
      canSubmit: false,
      canWithdraw: false,
    });
  });

  it('allows a published app with saved draft changes to submit an update', () => {
    expect(getWorkflowReviewPresentation({ status: 'published', hasUnpublishedChanges: true })).toEqual({
      statusClass: 'published',
      text: '已发布 · 有未发布修改',
      canSubmit: true,
      canWithdraw: false,
    });
  });

  it('does not allow a rejected update to be resubmitted after the draft matches the live version', () => {
    expect(
      getWorkflowReviewPresentation({
        status: 'published',
        hasUnpublishedChanges: false,
        reviewSummary: { status: 'rejected', versionId: 'v2', versionNo: 2 },
      })
    ).toEqual({
      statusClass: 'failed',
      text: '已发布 · 更新被驳回',
      canSubmit: false,
      canWithdraw: false,
    });
  });
});
