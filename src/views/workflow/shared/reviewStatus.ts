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

export type WorkflowNextActionKey = 'run' | 'configure' | 'waiting' | 'resubmit' | 'publish';

export type WorkflowNextAction = {
  key: WorkflowNextActionKey;
  label: string;
  /** 只是状态说明、不可点击（例如等待审核） */
  passive?: boolean;
};

/**
 * 卡片上「下一步」按钮：每个状态都要让用户知道接下来该做什么，而不是只有已发布才有「运行」。
 * - 已发布 → 运行；
 * - 待审核 → 等待审核（被动提示，撤回在更多操作里）；
 * - 已驳回 → 查看原因并重新提交；
 * - 已下架 → 重新发布；
 * - 草稿 → 去配置 / 去编排。
 */
export function getWorkflowNextAction(item: WorkflowReviewItem & { aiAppType?: string }): WorkflowNextAction {
  const presentation = getWorkflowReviewPresentation(item);
  const review = item.reviewSummary;
  const lifecycle = item.status === 'pending_review' ? 'draft' : String(item.status || 'draft');

  if (review?.status === 'pending_review') {
    return { key: 'waiting', label: '等待审核', passive: true };
  }
  if (lifecycle === 'published') {
    return { key: 'run', label: '运行' };
  }
  if (review?.status === 'rejected') {
    return { key: 'resubmit', label: presentation.canSubmit ? '查看原因并重新提交' : '查看驳回原因' };
  }
  if (lifecycle === 'unpublished') {
    return { key: 'publish', label: '重新发布' };
  }
  const chatAgent = ['chatAgent', 'simple', 'agent'].includes(String(item.aiAppType || 'chatAgent'));
  return { key: 'configure', label: chatAgent ? '去配置' : '去编排' };
}
