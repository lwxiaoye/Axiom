export type WorkflowReviewSummary = {
  versionId: string;
  versionNo: number;
  status: 'pending_review' | 'rejected';
  submittedAt?: string | null;
  reviewedAt?: string | null;
  reviewedByName?: string;
  reviewComment?: string;
};

type WorkflowReviewItem = {
  status?: string;
  hasUnpublishedChanges?: boolean;
  reviewSummary?: WorkflowReviewSummary;
};

export type WorkflowReviewPresentation = {
  statusClass: 'published' | 'pending' | 'unpublished' | 'failed' | 'draft';
  text: string;
  canSubmit: boolean;
  canWithdraw: boolean;
  reviewComment?: string;
};

export function getWorkflowReviewPresentation(item: WorkflowReviewItem): WorkflowReviewPresentation {
  const lifecycle = item.status === 'pending_review' ? 'draft' : String(item.status || 'draft');
  const review = item.reviewSummary;

  if (review?.status === 'pending_review') {
    const text = lifecycle === 'published'
      ? '已发布 · 更新审核中'
      : lifecycle === 'unpublished'
        ? '已下架 · 重新发布审核中'
        : '首次发布审核中';
    return { statusClass: 'pending', text, canSubmit: false, canWithdraw: true };
  }

  if (review?.status === 'rejected') {
    const text = lifecycle === 'published'
      ? '已发布 · 更新被驳回'
      : lifecycle === 'unpublished'
        ? '已下架 · 重新发布被驳回'
        : '已驳回';
    return {
      statusClass: 'failed',
      text,
      canSubmit: lifecycle !== 'published' || Boolean(item.hasUnpublishedChanges),
      canWithdraw: false,
      ...(review.reviewComment ? { reviewComment: review.reviewComment } : {}),
    };
  }

  if (lifecycle === 'published') {
    if (item.hasUnpublishedChanges) {
      return { statusClass: 'published', text: '已发布 · 有未发布修改', canSubmit: true, canWithdraw: false };
    }
    return { statusClass: 'published', text: '已发布', canSubmit: false, canWithdraw: false };
  }
  if (lifecycle === 'unpublished') {
    return { statusClass: 'unpublished', text: '已下架', canSubmit: true, canWithdraw: false };
  }
  return { statusClass: 'draft', text: '草稿', canSubmit: true, canWithdraw: false };
}
