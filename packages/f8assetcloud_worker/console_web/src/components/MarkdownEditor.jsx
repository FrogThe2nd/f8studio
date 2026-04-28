import { useEffect, useMemo, useRef, useState } from 'react';
import {
  BlockTypeSelect,
  BoldItalicUnderlineToggles,
  CreateLink,
  DiffSourceToggleWrapper,
  InsertCodeBlock,
  InsertTable,
  ListsToggle,
  MDXEditor,
  Separator,
  UndoRedo,
  codeBlockPlugin,
  codeMirrorPlugin,
  diffSourcePlugin,
  headingsPlugin,
  linkDialogPlugin,
  linkPlugin,
  listsPlugin,
  markdownShortcutPlugin,
  quotePlugin,
  tablePlugin,
  toolbarPlugin,
} from '@mdxeditor/editor';
import '@mdxeditor/editor/style.css';
import { createPortal } from 'react-dom';
import { Maximize2, Minimize2 } from 'lucide-react';

import { cn } from '../lib/cn.js';

const codeBlockLanguages = {
  bash: 'Bash',
  css: 'CSS',
  html: 'HTML',
  js: 'JavaScript',
  json: 'JSON',
  jsx: 'JSX',
  md: 'Markdown',
  py: 'Python',
  sql: 'SQL',
  ts: 'TypeScript',
  tsx: 'TSX',
  txt: 'Plain text',
};

function MarkdownToolbar() {
  return (
    <DiffSourceToggleWrapper options={['rich-text', 'source']}>
      <UndoRedo />
      <Separator />
      <BlockTypeSelect />
      <Separator />
      <BoldItalicUnderlineToggles options={['Bold', 'Italic']} />
      <ListsToggle options={['bullet', 'number']} />
      <CreateLink />
      <Separator />
      <InsertTable />
      <InsertCodeBlock />
    </DiffSourceToggleWrapper>
  );
}

export function MarkdownEditor({
  value,
  onChange,
  label = 'Description',
  description = 'Supports headings, lists, links, quotes, tables, code blocks, and source mode.',
  placeholder = 'Write with Markdown...',
  minHeightClassName = 'min-h-64',
}) {
  const editorRef = useRef(null);
  const latestValueRef = useRef(String(value || ''));
  const initialMarkdown = useMemo(() => String(value || ''), []);
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => {
    const nextValue = String(value || '');
    latestValueRef.current = nextValue;

    const editor = editorRef.current;
    if (!editor) {
      return;
    }
    if (editor.getMarkdown() !== nextValue) {
      editor.setMarkdown(nextValue);
    }
  }, [value]);

  useEffect(() => {
    if (!fullscreen) {
      return undefined;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    function handleKeyDown(event) {
      if (event.key === 'Escape') {
        setFullscreen(false);
      }
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [fullscreen]);

  const plugins = useMemo(() => [
    headingsPlugin(),
    listsPlugin(),
    quotePlugin(),
    linkPlugin(),
    linkDialogPlugin(),
    tablePlugin(),
    codeBlockPlugin({ defaultCodeBlockLanguage: 'txt' }),
    codeMirrorPlugin({ autoLoadLanguageSupport: false, codeBlockLanguages }),
    markdownShortcutPlugin(),
    diffSourcePlugin({ viewMode: 'rich-text' }),
    toolbarPlugin({ toolbarContents: MarkdownToolbar }),
  ], []);

  function handleChange(nextMarkdown) {
    if (nextMarkdown === latestValueRef.current) {
      return;
    }
    latestValueRef.current = nextMarkdown;
    onChange(nextMarkdown);
  }

  const editorContent = (
    <section className={cn(
      'space-y-3',
      fullscreen
        ? 'fixed inset-0 z-[120] overflow-y-auto bg-slate-950 p-6'
        : '',
    )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-white">{label}</p>
          {description ? (
            <p className="mt-1 text-xs leading-5 text-slate-400">{description}</p>
          ) : null}
        </div>
        <button
          type="button"
          className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-white/12 bg-white/5 px-3 text-xs font-medium text-slate-200 transition hover:bg-white/10 hover:text-white"
          onClick={() => setFullscreen((current) => !current)}
        >
          {fullscreen ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
          {fullscreen ? 'Exit Fullscreen' : 'Fullscreen'}
        </button>
      </div>
      <MDXEditor
        ref={editorRef}
        className="f8-markdown-editor dark-theme"
        contentEditableClassName={cn(
          'f8-markdown-editor-content',
          fullscreen ? 'min-h-[calc(100vh-11rem)]' : minHeightClassName,
        )}
        markdown={initialMarkdown}
        onChange={handleChange}
        placeholder={placeholder}
        plugins={plugins}
        suppressHtmlProcessing
        trim={false}
      />
    </section>
  );

  if (fullscreen) {
    return createPortal(editorContent, document.body);
  }
  return editorContent;
}
