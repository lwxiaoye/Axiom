/** Detect a short assistant reply that is asking the user to choose direction.
 *  Deep Research often asks before searching; that is not a failed run. */

const CLARIFY_CHOICE = /还是|要不要|哪种|哪一种|请确认|想确认/;

export type UserClarificationTarget = {
  content?: string;
  error?: string;
  generatedFiles?: Array<{ filename?: string }>;
};

export function isUserClarificationText(text: string): boolean {
  const body = String(text || '').trim();
  if (body.length < 16 || body.length > 1600) return false;
  const tail = body.slice(-280);
  if (!/[？?]/.test(tail)) return false;
  if (!CLARIFY_CHOICE.test(body)) return false;
  const lead = body.length > 120 ? body.slice(0, -120) : '';
  if ((lead.match(/[。.]/g) || []).length >= 4) return false;
  return true;
}

export function isUserClarificationMessage(message: UserClarificationTarget | null | undefined): boolean {
  if (!message) return false;
  const files = message.generatedFiles || [];
  const hasProduct = files.some((file) => {
    const name = String(file?.filename || '').trim();
    if (!name) return false;
    return !/\.research\.md$/i.test(name);
  });
  if (hasProduct) return false;
  return isUserClarificationText(message.content || '');
}

export function absorbFailedRunIfUserClarification(
  target: UserClarificationTarget,
  complete: (target: UserClarificationTarget) => void,
): boolean {
  if (!isUserClarificationMessage(target)) return false;
  target.error = undefined;
  complete(target);
  return true;
}
