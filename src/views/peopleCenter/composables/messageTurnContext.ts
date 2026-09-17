import type { SkillItem } from '../agentApi';

export type MessageContextAttachment = {
  filename?: string;
  kind?: string;
  referenceId?: string;
};

function uniqueSkills(skills: SkillItem[]): SkillItem[] {
  const seen = new Set<string>();
  return skills.filter((skill) => {
    const id = String(skill?.id || '').trim();
    if (!id || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

/**
 * Restore the Skill selection owned by one historical user message.
 *
 * New messages persist a reference id on the Skill attachment. Older messages only have the
 * visible name, so they can be restored only when the current catalog has one unambiguous match.
 * The returned id is still revalidated by the server when the edited turn is submitted.
 */
export function messageTurnSkills(
  explicit: SkillItem[] | undefined,
  attachments: MessageContextAttachment[] | undefined,
  catalog: SkillItem[] = [],
): SkillItem[] {
  const owned = uniqueSkills([...(explicit || [])]);
  if (owned.length) return owned;

  const byId = new Map(catalog.map((skill) => [String(skill.id), skill]));
  const restored: SkillItem[] = [];
  for (const attachment of attachments || []) {
    if (attachment.kind !== 'skill') continue;
    const referenceId = String(attachment.referenceId || '').trim();
    if (referenceId) {
      restored.push(byId.get(referenceId) || {
        id: referenceId,
        name: String(attachment.filename || '').trim(),
      });
      continue;
    }

    const name = String(attachment.filename || '').trim();
    if (!name) continue;
    const matches = catalog.filter((skill) => String(skill.name || '').trim() === name);
    if (matches.length === 1) restored.push(matches[0]);
  }
  return uniqueSkills(restored);
}

export function hasSkillReference(attachments: MessageContextAttachment[] | undefined): boolean {
  return Boolean(attachments?.some((attachment) => attachment.kind === 'skill'));
}
