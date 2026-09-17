export type BrowserTtsConfig = {
  type?: 'none' | 'web' | string;
};

function normalizeSpeechText(text: string) {
  return String(text || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*]\([^)]+\)/g, ' ')
    .replace(/\[([^\]]+)]\([^)]+\)/g, '$1')
    .replace(/[#>*_~\-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export function stopBrowserTts() {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return;
  window.speechSynthesis.cancel();
}

export function speakBrowserTts(text: string, config?: BrowserTtsConfig | null) {
  if (config?.type !== 'web') return;
  if (typeof window === 'undefined' || !('speechSynthesis' in window) || typeof SpeechSynthesisUtterance === 'undefined') return;
  const content = normalizeSpeechText(text);
  if (!content) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(content);
  utterance.lang = navigator.language || 'zh-CN';
  window.speechSynthesis.speak(utterance);
}
