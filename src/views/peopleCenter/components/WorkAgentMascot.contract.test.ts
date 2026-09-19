import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(__dirname, '..');
const componentSource = readFileSync(resolve(__dirname, 'WorkAgentMascot.vue'), 'utf8');
const chatTabSource = readFileSync(resolve(root, 'tabs/ChatTab.vue'), 'utf8');
const presentationSource = readFileSync(resolve(root, 'builtinAssistants/presentation/ui.ts'), 'utf8');
const masterSource = readFileSync(resolve(root, '../../../public/agent-icons/work-agent-orb.svg'), 'utf8');
const campusOrbOverlaySource = readFileSync(resolve(root, '../../../public/agent-icons/builtin/campus-services-orb-overlay-v3.svg'), 'utf8');
const presentationMascotSource = readFileSync(resolve(root, '../../../public/agent-icons/builtin/presentation-assistant-mascot-v5.svg'), 'utf8');
const presentationStaticSource = readFileSync(resolve(root, '../../../public/agent-icons/builtin/presentation-assistant-v5.svg'), 'utf8');

const canonicalBodyPath = 'M26.8 1.2C38.1 1.2 46.5 10.1 46.5 21.1C46.5 32 38.1 40.7 26.8 40.7C15.5 40.7 7.2 32 7.2 21.1C7.2 10.1 15.6 1.2 26.8 1.2Z';
const canonicalLeftEye = 'cx="26.2" cy="19.5" rx="2.55" ry="4.65"';
const canonicalRightEye = 'cx="35.55" cy="19.05" rx="2.2" ry="4.55"';

type OrbLayout = { left: number; top: number; width: number };
type OrbGeometry = { viewSize: number; cx: number; cy: number; diameter: number };

function projectOrb(layout: OrbLayout, geometry: OrbGeometry) {
  const scale = layout.width / geometry.viewSize;
  return {
    cx: layout.left + geometry.cx * scale,
    cy: layout.top + geometry.cy * scale,
    diameter: geometry.diameter * scale,
  };
}

function expectOrbAligned(reference: ReturnType<typeof projectOrb>, candidate: ReturnType<typeof projectOrb>) {
  expect(Math.abs(candidate.cx - reference.cx)).toBeLessThan(0.03);
  expect(Math.abs(candidate.cy - reference.cy)).toBeLessThan(0.03);
  expect(Math.abs(candidate.diameter - reference.diameter)).toBeLessThan(0.03);
}

describe('AXIOM Agent mascot identity contract', () => {
  it('keeps the animated component and HD master on the same canonical face geometry', () => {
    for (const source of [componentSource, masterSource]) {
      expect(source).toContain('viewBox="0 0 57 44"');
      expect(source).toContain(canonicalBodyPath);
      expect(source).toContain(canonicalLeftEye);
      expect(source).toContain(canonicalRightEye);
    }
  });

  it('keeps every mascot at the welcome composer perch and hides it after sending', () => {
    expect(chatTabSource).toContain('<WorkAgentMascot');
    expect(chatTabSource).toContain('v-if="!uiPolicy?.showAvatar && !uiPolicy?.hideComposerMascot && chatMessages.length === 0"');
    expect(chatTabSource).not.toContain('chatMessages.length === 0 || campusMode');
    expect(chatTabSource).toContain(':state="workAgentMascotState"');
    expect(chatTabSource).not.toContain(':draggable=');
    expect(chatTabSource).not.toContain(':position-key=');
    expect(componentSource).not.toContain('sessionStorage');
    expect(componentSource).not.toContain('@pointerdown=');
    expect(chatTabSource).toMatch(/\.composer-agent-mascot\s*\{[\s\S]{0,180}top:\s*-56px;[\s\S]{0,120}left:\s*84px;[\s\S]{0,120}width:\s*76px;/);
  });

  it('aligns all three visible orbs to the same composer-relative center and diameter', () => {
    expect(presentationSource).toContain('showAvatar: false');
    expect(presentationSource).toContain('hideComposerMascot: false');
    expect(chatTabSource).toMatch(/\.composer-agent-mascot\.variant-campus\s*\{[\s\S]{0,220}top:\s*-83\.69px;[\s\S]{0,80}left:\s*73\.24px;[\s\S]{0,80}width:\s*103\.19px;/);
    expect(chatTabSource).toMatch(/\.composer-agent-mascot\.variant-presentation\s*\{[\s\S]{0,220}top:\s*-86\.1px;[\s\S]{0,80}left:\s*88\.29px;[\s\S]{0,80}width:\s*84\.9px;/);
    expect(chatTabSource).toMatch(/@media \(max-width: 720px\)[\s\S]{0,800}\.composer-agent-mascot\.variant-campus\s*\{[\s\S]{0,120}top:\s*-70\.32px;[\s\S]{0,80}left:\s*46\.94px;[\s\S]{0,80}width:\s*86\.89px;/);
    expect(chatTabSource).toMatch(/@media \(max-width: 720px\)[\s\S]{0,1000}\.composer-agent-mascot\.variant-presentation\s*\{[\s\S]{0,120}top:\s*-72\.35px;[\s\S]{0,80}left:\s*59\.62px;[\s\S]{0,80}width:\s*71\.5px;/);

    const mainGeometry = { viewSize: 57, cx: 26.85, cy: 20.95, diameter: 39.3 };
    const campusGeometry = { viewSize: 512, cx: 231, cy: 276, diameter: 260 };
    const presentationGeometry = { viewSize: 512, cx: 190, cy: 350, diameter: 316 };
    const desktopMain = projectOrb({ left: 84, top: -56, width: 76 }, mainGeometry);
    const mobileMain = projectOrb({ left: 56, top: -47, width: 64 }, mainGeometry);

    expectOrbAligned(desktopMain, projectOrb({ left: 73.24, top: -83.69, width: 103.19 }, campusGeometry));
    expectOrbAligned(desktopMain, projectOrb({ left: 88.29, top: -86.1, width: 84.9 }, presentationGeometry));
    expectOrbAligned(mobileMain, projectOrb({ left: 46.94, top: -70.32, width: 86.89 }, campusGeometry));
    expectOrbAligned(mobileMain, projectOrb({ left: 59.62, top: -72.35, width: 71.5 }, presentationGeometry));
  });

  it('uses production themed bases with app-rendered eyes for both builtin assistants', () => {
    expect(componentSource).toContain("src: '/agent-icons/builtin/campus-services-mascot-v3.png'");
    expect(componentSource).toContain("src: '/agent-icons/builtin/presentation-assistant-mascot-v5.svg'");
    expect(componentSource).toContain('class="mascot-themed-eye-layer"');
    expect(componentSource).toContain("type WorkAgentMascotVariant = 'main' | 'campus' | 'presentation'");
    expect(existsSync(resolve(root, '../../../public/agent-icons/builtin/campus-services-mascot-v3.png'))).toBe(true);
    expect(existsSync(resolve(root, '../../../public/agent-icons/builtin/presentation-assistant-mascot-v5.svg'))).toBe(true);
  });

  it('keeps the campus orb on the same canonical blue palette as the main agent', () => {
    for (const color of ['#1a79ff', '#0b64f8', '#0758ee', '#0349d9']) {
      expect(masterSource.toLowerCase()).toContain(color);
      expect(campusOrbOverlaySource.toLowerCase()).toContain(color);
    }
    expect(campusOrbOverlaySource).toContain('<circle cx="231" cy="276" r="130"');
    expect(existsSync(resolve(root, '../../../public/agent-icons/builtin/campus-services-static-v2.png'))).toBe(true);
  });

  it('keeps the presentation identity simple, circular, and faithful to the approved slide reference', () => {
    for (const source of [presentationMascotSource, presentationStaticSource]) {
      expect(source).toContain('viewBox="0 0 512 512"');
      expect(source).toContain('<circle cx="190" cy="350" r="158"');
      expect(source).toContain('M138 174H306L428 270V458H138V174Z');
      expect(source).toContain('#1A79FF');
      expect(source).toContain('#0349D9');
      expect(source).not.toContain('<text');
    }
    expect(presentationMascotSource).not.toContain('<ellipse');
    expect(presentationStaticSource).toContain('<ellipse cx="185" cy="334" rx="19" ry="35"');
    expect(presentationStaticSource).toContain('<ellipse cx="258" cy="331" rx="17" ry="34"');
  });

  it('changes expression through transforms while retaining the same body color and silhouette', () => {
    expect(componentSource).toContain('.is-searching .mascot-eyes');
    expect(componentSource).toContain('.is-discover .mascot-discovery');
    expect(componentSource).toContain('@media (prefers-reduced-motion: reduce)');
    expect(componentSource).not.toMatch(/class="[^"]*(mouth|eyebrow)/i);
  });

  it('tracks left, center, and right across the composer without dragging', () => {
    expect(componentSource).toContain("trackingHost?.addEventListener('pointermove', onTrackingPointerMove");
    expect(componentSource).toContain("trackingHost?.addEventListener('pointerdown', onTrackingPointerDown");
    expect(componentSource).toContain("closest<HTMLElement>('.composer')");
    expect(componentSource).toContain('deltaX / horizontalRange');
    expect(componentSource).toContain('clamp(deltaX / horizontalRange, -1, 1) * 1.75');
    expect(componentSource).toContain('.mascot-themed-eye-layer {');
    expect(componentSource).toContain('translate3d(var(--mascot-look-x), var(--mascot-look-y), 0)');
    expect(componentSource).toContain('@click="greet"');
    expect(componentSource).toContain('@keydown.enter.prevent="greet"');
    expect(componentSource).toContain('@keydown.space.prevent="greet"');
  });

  it('follows coarse pointers so mobile gaze tracks the finger', () => {
    expect(componentSource).not.toContain("event.pointerType === 'touch' || motionQuery?.matches");
    expect(componentSource).toContain("event.pointerType === 'touch' || event.pointerType === 'pen'");
    expect(componentSource).toContain('onTrackingPointerDown');
    expect(componentSource).toContain('onCoarsePointerMove');
    expect(componentSource).toContain("window.addEventListener('pointermove', onCoarsePointerMove");
    expect(componentSource).toContain("window.addEventListener('pointerup', onCoarsePointerUp");
  });

  it('uses a different click reaction for each of the three personas', () => {
    expect(componentSource).toContain('mascot-greet-main');
    expect(componentSource).toContain('mascot-greet-campus');
    expect(componentSource).toContain('mascot-greet-presentation');
    expect(componentSource).toContain('mascot-eyes-campus');
    expect(componentSource).toContain('mascot-eyes-presentation');
    expect(componentSource).toContain('.is-poked.variant-main .mascot-main-art');
    expect(componentSource).toContain('mascot-greet-main-squash');
    expect(componentSource).toContain('.is-poked.variant-campus .mascot-themed-art');
    expect(componentSource).toContain('.is-poked.variant-presentation .mascot-themed-art');
    expect(componentSource).not.toMatch(/\.is-poked\s+\.mascot-orb\s*\{/);
  });

  it('keeps the jumping head fully painted instead of clipping it to the view box', () => {
    expect(componentSource).toContain('overflow="visible"');
    expect(componentSource).toMatch(/\.work-agent-mascot\s*\{[\s\S]{0,180}overflow:\s*visible;/);
    expect(componentSource).toContain('@keyframes mascot-greet-main-squash');
    expect(chatTabSource).toMatch(/\.composer-agent-mascot\s*\{[\s\S]{0,220}overflow:\s*visible;/);
  });

  it('lets + and @ panels cover only their overlapping mascot area', () => {
    expect(chatTabSource).toMatch(/\.composer-agent-mascot\s*\{[\s\S]{0,160}z-index:\s*4;/);
    expect(chatTabSource).toMatch(/\.mention-picker\s*\{[\s\S]{0,500}z-index:\s*30;/);
    expect(chatTabSource).toMatch(/\.plus-menu\s*\{[\s\S]{0,500}z-index:\s*100;/);
    expect(chatTabSource).not.toMatch(/v-if="[^\"]*(plusOpen|mentionOpen)[^\"]*"[^>]*class="composer-agent-mascot"/);
  });
});
