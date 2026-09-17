import { nextTick, watch } from 'vue';
import { createDebugMessage } from './debugMessage';

describe('debug message', () => {
  it('notifies the preview when streamed content changes', async () => {
    const message = createDebugMessage();
    const contents: string[] = [];
    const stop = watch(
      () => message.content,
      (content) => contents.push(content),
    );

    message.content += '你';
    await nextTick();
    message.content += '好';
    await nextTick();
    stop();

    expect(contents).toEqual(['你', '你好']);
  });
});
