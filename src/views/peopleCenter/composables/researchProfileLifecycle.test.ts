import { researchRunDelivered } from './researchProfileLifecycle';

describe('research profile lifecycle', () => {
  it.each(['completed', 'partial'] as const)(
    'exits the one-shot profile after a %s delivery',
    (outcome) => {
      expect(researchRunDelivered(outcome)).toBe(true);
    },
  );

  it.each(['failed', 'cancelled', null] as const)(
    'keeps the profile available after %s',
    (outcome) => {
      expect(researchRunDelivered(outcome)).toBe(false);
    },
  );
});

