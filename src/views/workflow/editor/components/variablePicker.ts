export type VariablePickerGroup = {
  label: string;
  options: { label: string; detail: string; value: string }[];
};

export function flattenVariableGroups(groups: VariablePickerGroup[]): VariablePickerGroup['options'] {
  return groups.flatMap((group) => group.options);
}

export function nextVariablePickerIndex(
  currentIndex: number,
  itemCount: number,
  direction: 'up' | 'down',
): number {
  if (!itemCount) return -1;
  const current = currentIndex < 0 ? 0 : currentIndex;
  return direction === 'down' ? (current + 1) % itemCount : (current - 1 + itemCount) % itemCount;
}

export function formatVariableTemplate(value: string): string {
  const trimmed = value.trim();
  return /^\{\{.*\}\}$/.test(trimmed) ? trimmed : `{{${trimmed}}}`;
}
