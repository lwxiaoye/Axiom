import campusPreview from './presets/campus-welcome-v1/assets/preview.png';
import { CAMPUS_WELCOME_PRESENTATION_PRESET } from './catalog';

const PREVIEW_BY_KEY: Record<string, string> = {
  [CAMPUS_WELCOME_PRESENTATION_PRESET]: campusPreview,
};

export function getRunPresentationPreview(key: string) {
  return PREVIEW_BY_KEY[key] || '';
}
