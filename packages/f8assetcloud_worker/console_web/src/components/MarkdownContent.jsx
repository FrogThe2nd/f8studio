import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { cn } from '../lib/cn.js';

function Paragraph({ className, ...props }) {
  return <p className={cn('mt-4 text-sm leading-7 text-slate-200 first:mt-0', className)} {...props} />;
}

function HeadingOne({ className, ...props }) {
  return <h1 className={cn('mt-6 text-3xl font-semibold tracking-tight text-white first:mt-0', className)} {...props} />;
}

function HeadingTwo({ className, ...props }) {
  return <h2 className={cn('mt-6 text-2xl font-semibold tracking-tight text-white first:mt-0', className)} {...props} />;
}

function HeadingThree({ className, ...props }) {
  return <h3 className={cn('mt-5 text-xl font-semibold text-white first:mt-0', className)} {...props} />;
}

function UnorderedList({ className, ...props }) {
  return <ul className={cn('mt-4 list-disc space-y-2 pl-6 text-sm leading-7 text-slate-200 first:mt-0', className)} {...props} />;
}

function OrderedList({ className, ...props }) {
  return <ol className={cn('mt-4 list-decimal space-y-2 pl-6 text-sm leading-7 text-slate-200 first:mt-0', className)} {...props} />;
}

function ListItem({ className, ...props }) {
  return <li className={cn('marker:text-cyan-200', className)} {...props} />;
}

function InlineCode({ className, ...props }) {
  return <code className={cn('rounded bg-slate-900/80 px-1.5 py-0.5 font-mono text-[0.92em] text-cyan-100', className)} {...props} />;
}

function CodeBlock({ className, children, ...props }) {
  return (
    <pre className={cn('mt-4 overflow-x-auto rounded-2xl border border-white/10 bg-slate-950/80 p-4 text-xs leading-6 text-cyan-100 first:mt-0', className)}>
      <code {...props}>{children}</code>
    </pre>
  );
}

function Blockquote({ className, ...props }) {
  return <blockquote className={cn('mt-4 border-l-2 border-cyan-300/50 pl-4 text-sm italic leading-7 text-slate-300 first:mt-0', className)} {...props} />;
}

function LinkRenderer({ className, href, ...props }) {
  return (
    <a
      {...props}
      href={href}
      className={cn('font-medium text-cyan-200 underline decoration-cyan-300/50 underline-offset-4 hover:text-white', className)}
      rel="noreferrer"
      target="_blank"
    />
  );
}

function Table({ className, ...props }) {
  return (
    <div className="mt-4 overflow-x-auto first:mt-0">
      <table className={cn('min-w-full border-collapse text-left text-sm text-slate-200', className)} {...props} />
    </div>
  );
}

function TableHeadCell({ className, ...props }) {
  return <th className={cn('border-b border-white/10 px-3 py-2 font-semibold text-white', className)} {...props} />;
}

function TableCell({ className, ...props }) {
  return <td className={cn('border-b border-white/5 px-3 py-2 align-top', className)} {...props} />;
}

export function MarkdownContent({ source, placeholder = 'Nothing to preview yet.', className }) {
  const text = String(source || '').trim();
  if (!text) {
    return <p className={cn('text-sm leading-7 text-slate-400', className)}>{placeholder}</p>;
  }
  return (
    <div className={cn('markdown-content', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: Paragraph,
          h1: HeadingOne,
          h2: HeadingTwo,
          h3: HeadingThree,
          ul: UnorderedList,
          ol: OrderedList,
          li: ListItem,
          blockquote: Blockquote,
          a: LinkRenderer,
          table: Table,
          th: TableHeadCell,
          td: TableCell,
          code({ inline, className: childClassName, children, ...props }) {
            if (inline) {
              return <InlineCode className={childClassName} {...props}>{children}</InlineCode>;
            }
            return <CodeBlock className={childClassName} {...props}>{children}</CodeBlock>;
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
