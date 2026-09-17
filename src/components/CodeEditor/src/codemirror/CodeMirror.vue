<template>
  <div class="relative !h-full w-full overflow-hidden" ref="el"> </div>
</template>

<script lang="ts" setup>
  import { ref, onMounted, onUnmounted, watchEffect, watch, unref, nextTick } from 'vue';
  import { useDebounceFn } from '@vueuse/core';
  import { useAppStore } from '/@/store/modules/app';
  import { useWindowSizeFn } from '/@/hooks/event/useWindowSizeFn';
  import CodeMirror from 'codemirror';
  // css
  import './codemirror.css';
  import 'codemirror/theme/idea.css';
  import 'codemirror/theme/material-palenight.css';
  import 'codemirror/addon/hint/show-hint.css';
  // modes
  import 'codemirror/mode/javascript/javascript';
  import 'codemirror/mode/css/css';
  import 'codemirror/mode/htmlmixed/htmlmixed';
  import 'codemirror/mode/markdown/markdown';
  import 'codemirror/mode/python/python';
  import 'codemirror/mode/xml/xml';
  import 'codemirror/mode/yaml/yaml';
  // addons
  import 'codemirror/addon/hint/show-hint';
  import 'codemirror/addon/edit/closebrackets';
  import 'codemirror/addon/edit/closetag';

  const props = defineProps({
    mode: { type: String, default: 'application/json' },
    value: { type: String, default: '' },
    readonly: { type: Boolean, default: false },
  });

  const emit = defineEmits(['change', 'update:value', 'save']);

  const el = ref();
  let editor: Nullable<CodeMirror.Editor>;
  const codeMirror = CodeMirror as typeof CodeMirror & {
    registerHelper?: (type: string, name: string, helper: Function) => void;
    Pos?: (line: number, ch: number) => CodeMirror.Position;
    __axiomPythonHintRegistered?: boolean;
  };

  const PYTHON_HINTS = [
    'False',
    'None',
    'True',
    'and',
    'as',
    'assert',
    'async',
    'await',
    'break',
    'class',
    'continue',
    'def',
    'del',
    'elif',
    'else',
    'except',
    'finally',
    'for',
    'from',
    'global',
    'if',
    'import',
    'in',
    'is',
    'lambda',
    'nonlocal',
    'not',
    'or',
    'pass',
    'raise',
    'return',
    'try',
    'while',
    'with',
    'yield',
    'abs',
    'all',
    'any',
    'bool',
    'dict',
    'enumerate',
    'filter',
    'float',
    'format',
    'input',
    'int',
    'isinstance',
    'len',
    'list',
    'map',
    'max',
    'min',
    'open',
    'print',
    'range',
    'set',
    'sorted',
    'str',
    'sum',
    'tuple',
    'type',
    'zip',
    'json',
    'os',
    're',
    'requests',
    'sys',
    'time',
  ];

  const debounceRefresh = useDebounceFn(refresh, 100);
  const appStore = useAppStore();

  watch(
    () => props.value,
    async (value) => {
      await nextTick();
      const oldValue = editor?.getValue();
      if (value !== oldValue) {
        setEditorValue(value);
        debounceRefresh();
      }
    },
    { flush: 'post' }
  );

  watchEffect(() => {
    editor?.setOption('mode', props.mode);
  });

  watch(
    () => appStore.getDarkMode,
    async () => {
      setTheme();
    },
    {
      immediate: true,
    }
  );

  function setTheme() {
    unref(editor)?.setOption('theme', appStore.getDarkMode === 'light' ? 'idea' : 'material-palenight');
  }

  function refresh() {
    editor?.refresh();
  }

  function setEditorValue(value?: string) {
    editor?.setValue(value ? value : '');
    editor?.clearHistory();
  }

  function isPythonMode(mode?: string) {
    return ['python', 'text/x-python', 'application/x-python-code'].includes(String(mode || '').toLowerCase());
  }

  function getPythonCompletionToken(instance: CodeMirror.Editor) {
    const cursor = instance.getCursor();
    const token = instance.getTokenAt(cursor);
    const tokenString = token.string || '';
    const prefixLength = cursor.ch - token.start;
    const prefix = tokenString.slice(0, prefixLength);

    if (!/^[A-Za-z_][\w_]*$/.test(prefix)) {
      return { cursor, fromCh: cursor.ch, toCh: cursor.ch, prefix: '' };
    }

    return { cursor, fromCh: token.start, toCh: cursor.ch, prefix };
  }

  function registerPythonHint() {
    if (codeMirror.__axiomPythonHintRegistered || !codeMirror.registerHelper || !codeMirror.Pos) return;
    codeMirror.__axiomPythonHintRegistered = true;

    codeMirror.registerHelper('hint', 'python', (instance: CodeMirror.Editor) => {
      const { cursor, fromCh, toCh, prefix } = getPythonCompletionToken(instance);
      const words = new Set(PYTHON_HINTS);

      instance.getValue().replace(/[A-Za-z_][\w_]*/g, (word) => {
        words.add(word);
        return word;
      });

      const lowerPrefix = prefix.toLowerCase();
      const list = Array.from(words)
        .filter((word) => !lowerPrefix || word.toLowerCase().startsWith(lowerPrefix))
        .sort((a, b) => a.localeCompare(b));

      return {
        list,
        from: codeMirror.Pos!(cursor.line, fromCh),
        to: codeMirror.Pos!(cursor.line, toCh),
      };
    });
  }

  function showPythonHint(instance: CodeMirror.Editor, change: CodeMirror.EditorChange) {
    if (!isPythonMode(props.mode) || props.readonly) return;
    const typedText = change.text?.join('') || '';
    if (!/^[\w.]$/.test(typedText)) return;

    setTimeout(() => {
      const hintEditor = instance as CodeMirror.Editor & {
        state?: { completionActive?: unknown };
        showHint?: (options?: { completeSingle?: boolean }) => void;
      };

      if (!hintEditor.state?.completionActive) {
        hintEditor.showHint?.({ completeSingle: false });
      }
    }, 0);
  }

  async function init() {
    registerPythonHint();
    const addonOptions = {
      autoCloseBrackets: true,
      autoCloseTags: true,
      foldGutter: true,
      gutters: ['CodeMirror-linenumbers'],
      extraKeys: {
        'Ctrl-Space': 'autocomplete',
        'Ctrl-S': (instance: CodeMirror.Editor) => {
          emit('save', instance.getValue());
        },
        'Cmd-S': (instance: CodeMirror.Editor) => {
          emit('save', instance.getValue());
        },
      },
    };

    editor = CodeMirror(el.value!, {
      value: '',
      mode: props.mode,
      readOnly: props.readonly,
      tabSize: 2,
      theme: 'material-palenight',
      lineWrapping: true,
      lineNumbers: true,
      ...addonOptions,
    });
    setEditorValue(props.value);
    setTheme();
    editor?.on('change', (instance) => {
      const value = instance.getValue();
      emit('update:value', value);
      emit('change', value);
    });
    editor?.on('inputRead', showPythonHint);
  }

  onMounted(async () => {
    await nextTick();
    init();
    useWindowSizeFn(debounceRefresh);
  });

  onUnmounted(() => {
    editor = null;
  });
</script>
