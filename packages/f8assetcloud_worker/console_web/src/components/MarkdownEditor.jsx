import { useDeferredValue, useRef } from 'react';
import { Eye, Heading1, Link2, List, PenSquare, Sparkles } from 'lucide-react';

import { MarkdownContent } from './MarkdownContent.jsx';
import { Button } from './ui/button.jsx';

const toolbarActions = [
  { key: 'heading', label: 'Heading', icon: Heading1 },
  { key: 'bold', label: 'Bold', icon: Sparkles },
  { key: 'list', label: 'List', icon: List },
  { key: 'link', label: 'Link', icon: Link2 },
];

function nextWrappedSelection({ textarea, prefix, suffix, placeholder }) {
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const selectedText = textarea.value.slice(start, end);
  const innerText = selectedText || placeholder;
  const replacement = `${prefix}${innerText}${suffix}`;
  textarea.setRangeText(replacement, start, end, 'end');
  return textarea.value;
}

function nextPrefixedLines({ textarea, prefix, placeholder }) {
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const selectedText = textarea.value.slice(start, end);
  const baseText = selectedText || placeholder;
  const replacement = baseText
    .split('\n')
    .map((line) => {
      const trimmedLine = line.trim();
      return trimmedLine ? `${prefix}${trimmedLine}` : prefix.trimEnd();
    })
    .join('\n');
  textarea.setRangeText(replacement, start, end, 'end');
  return textarea.value;
}

export function MarkdownEditor({ value, onChange }) {
  const textareaRef = useRef(null);
  const deferredValue = useDeferredValue(value);

  function handleToolbarClick(actionKey) {
    const textarea = textareaRef.current;
    if (!textarea) {
      return;
    }
    let nextValue = textarea.value;
    if (actionKey === 'heading') {
      nextValue = nextPrefixedLines({ textarea, prefix: '# ', placeholder: 'Heading' });
    } else if (actionKey === 'bold') {
      nextValue = nextWrappedSelection({ textarea, prefix: '**', suffix: '**', placeholder: 'Bold text' });
    } else if (actionKey === 'list') {
      nextValue = nextPrefixedLines({ textarea, prefix: '- ', placeholder: 'List item' });
    } else if (actionKey === 'link') {
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const selectedText = textarea.value.slice(start, end);
      const label = selectedText || 'Link text';
      textarea.setRangeText(`[${label}](https://example.com)`, start, end, 'end');
      nextValue = textarea.value;
    }
    onChange(nextValue);
    textarea.focus();
  }

  return (
    <div className="space-y-4 rounded-[1.5rem] border border-white/10 bg-slate-950/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-white">Description</p>
          <p className="mt-1 text-xs leading-5 text-slate-400">
            Supports headings, lists, links, code blocks, and tables.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {toolbarActions.map((action) => {
            const Icon = action.icon;
            return (
              <Button
                key={action.key}
                type="button"
                variant="outline"
                size="sm"
                className="border-white/12 bg-white/5 text-slate-200 hover:bg-white/10"
                onClick={() => handleToolbarClick(action.key)}
              >
                <Icon className="size-3.5" />
                {action.label}
              </Button>
            );
          })}
        </div>
      </div>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
        <section className="space-y-2">
          <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.24em] text-cyan-200/80">
            <PenSquare className="size-3.5" />
            Write
          </div>
          <textarea
            ref={textareaRef}
            className="min-h-64 w-full rounded-[1.25rem] border border-white/12 bg-white/5 px-4 py-3 text-sm leading-7 text-white focus:border-cyan-300/40 focus:outline-none"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder="Describe this asset with Markdown..."
          />
        </section>
        <section className="space-y-2">
          <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.24em] text-cyan-200/80">
            <Eye className="size-3.5" />
            Preview
          </div>
          <div className="min-h-64 rounded-[1.25rem] border border-white/10 bg-white/5 p-4">
            <MarkdownContent
              source={deferredValue}
              placeholder="Your Markdown preview will appear here."
            />
          </div>
        </section>
      </div>
    </div>
  );
}
