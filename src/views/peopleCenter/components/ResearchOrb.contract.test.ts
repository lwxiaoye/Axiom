import fs from 'node:fs';
import path from 'node:path';

const source = fs.readFileSync(path.resolve(__dirname, 'ResearchOrb.vue'), 'utf8');

describe('ResearchOrb motion contract', () => {
  it('uses eyeless role art with a shared pair of pure-white animated eyes', () => {
    expect(source).toContain('/agent-icons/research/leader-eyeless-v2.svg');
    expect(source.match(/white-eye-v1\.svg/g)).toHaveLength(2);
    expect(source).not.toMatch(/pupil|eyeball/);
    expect(source).toContain('background: transparent');
  });

  it('gives every active role a different, desynchronised gesture', () => {
    expect(source).toContain('leader-listen 7.4s');
    expect(source).toContain('researcher-scan 6.1s');
    expect(source).toContain('analyst-note 5.3s');
    expect(source).toContain('verifier-check 8.2s');
    expect(source.match(/animation-delay/g)).toBeNull();
    expect(source.match(/-[\d.]+s infinite/g)).toHaveLength(16);
  });

  it('gives all four roles distinct eye routes, blink rhythms and starting phases', () => {
    expect(source).toContain('leader-gaze 8.7s');
    expect(source).toContain('researcher-gaze 6.4s');
    expect(source).toContain('analyst-gaze 7.1s');
    expect(source).toContain('verifier-gaze 5.2s');
    expect(source).toContain('leader-blink 7.9s');
    expect(source).toContain('researcher-blink 9.3s');
    expect(source).toContain('analyst-double-blink 5.9s');
    expect(source).toContain('verifier-blink 8.6s');
    expect(source.match(/\.orb-gaze \{ animation:/g)).toHaveLength(4);
    expect(source.match(/\.orb-eye \{ animation:/g)).toHaveLength(4);
  });

  it('animates role tools independently with restrained, purposeful gestures', () => {
    expect(source).toContain('<SearchOutlined />');
    expect(source).toContain('<FileTextFilled class="paper-glyph" />');
    expect(source).toContain('/agent-icons/research/pencil-simple-fill.svg');
    expect(source).not.toContain('EditFilled');
    expect(source).toContain('<CheckCircleFilled />');
    expect(source).not.toContain('hand-grabbing');
    expect(source).not.toContain('tool-glasses');
    expect(source).toContain('researcher-magnify 6.4s');
    expect(source).toContain('analyst-paper 6.8s');
    expect(source).toContain('analyst-write 6.8s');
    expect(source).toContain('verifier-stamp 5.2s');
  });

  it('only moves active orbs and respects reduced-motion', () => {
    expect(source.match(/\.research-orb\.active\[data-role='[^']+'\] \.orb-motion/g)).toHaveLength(4);
    expect(source).toContain('@media (prefers-reduced-motion: reduce)');
    expect(source).toContain('.research-orb.active .orb-motion { animation: none; transform: none; }');
    expect(source).toContain('.research-orb.active .orb-gaze,');
  });
});
