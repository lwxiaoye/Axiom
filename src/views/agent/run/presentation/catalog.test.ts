import { createHash } from 'crypto';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import {
  CAMPUS_WELCOME_PRESENTATION_PRESET,
  DEFAULT_RUN_PRESENTATION_PRESET,
  RUN_PRESENTATION_PRESETS,
  normalizeRunPresentationPreset,
} from './catalog';

describe('run presentation catalog', () => {
  it('registers the campus welcome preset', () => {
    expect(RUN_PRESENTATION_PRESETS.map((item) => item.key)).toContain(CAMPUS_WELCOME_PRESENTATION_PRESET);
  });

  it('falls back to the standard appearance for absent or unknown keys', () => {
    expect(normalizeRunPresentationPreset(undefined)).toBe(DEFAULT_RUN_PRESENTATION_PRESET);
    expect(normalizeRunPresentationPreset('future-unregistered-theme')).toBe(DEFAULT_RUN_PRESENTATION_PRESET);
  });

  it('校园迎新素材使用真透明抠图，且对话开始后仍保留', () => {
    const presetRoot = resolve(__dirname, 'presets/campus-welcome-v1');
    const composerSource = readFileSync(resolve(presetRoot, 'CampusWelcomeComposerDecorations.vue'), 'utf8');
    const expectedHashes = {
      'backpack-cutout.png': '5ae12ffe682761093fa29ff90392195434c5580af337e2c5d46e33c73102a977',
      'student-group-cutout.png': 'b575b77ef16f32dc9ed09c626a350c40802c17df52c187270651a40691f76c2b',
    };

    expect(composerSource).toContain("./assets/backpack-cutout.png");
    expect(composerSource).toContain("./assets/student-group-cutout.png");
    expect(composerSource).toContain("'is-conversation': !emptyState");
    expect(composerSource).toContain('`is-device-${device}`');
    expect(composerSource).toContain('.campus-composer-decorations.is-device-tablet');
    expect(composerSource).toContain('.campus-composer-decorations.is-device-mobile');
    for (const [filename, expectedHash] of Object.entries(expectedHashes)) {
      const digest = createHash('sha256').update(readFileSync(resolve(presetRoot, 'assets', filename))).digest('hex');
      expect(digest).toBe(expectedHash);
    }
  });
});
