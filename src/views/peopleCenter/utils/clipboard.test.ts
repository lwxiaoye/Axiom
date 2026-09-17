/**
 * @jest-environment jsdom
 */
import { copyText, copyTextLegacy } from './clipboard';

describe('clipboard', () => {
  let execCommand: jest.Mock;

  beforeEach(() => {
    execCommand = jest.fn().mockReturnValue(true);
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      writable: true,
      value: execCommand,
    });
  });

  afterEach(() => {
    document.body.innerHTML = '';
    jest.restoreAllMocks();
  });

  it('returns false for empty text', async () => {
    await expect(copyText('')).resolves.toBe(false);
    expect(copyTextLegacy('')).toBe(false);
    expect(execCommand).not.toHaveBeenCalled();
  });

  it('uses Clipboard API when secure context is available', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true });
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });

    await expect(copyText('hello world')).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith('hello world');
    expect(execCommand).not.toHaveBeenCalled();
  });

  it('falls back to execCommand when Clipboard API rejects', async () => {
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true });
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: jest.fn().mockRejectedValue(new Error('denied')) },
    });

    await expect(copyText('fallback path')).resolves.toBe(true);
    expect(execCommand).toHaveBeenCalledWith('copy');
    expect(document.querySelector('[data-copy-helper]')).toBeNull();
  });

  it('legacy path selects text and cleans helper node', () => {
    expect(copyTextLegacy('plain')).toBe(true);
    expect(execCommand).toHaveBeenCalledWith('copy');
    expect(document.querySelector('[data-copy-helper]')).toBeNull();
  });

  it('returns false when execCommand reports failure', async () => {
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false });
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: undefined,
    });
    execCommand.mockReturnValue(false);
    await expect(copyText('nope')).resolves.toBe(false);
  });
});
