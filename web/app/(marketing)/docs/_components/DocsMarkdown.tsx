import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

/**
 * Q96: render a docs/{slug}.md file. The styling is intentionally
 * inline (not a global .prose stylesheet) so the marketing surface
 * stays decoupled from the console's typography.
 *
 * react-markdown + remark-gfm covers the markdown surface we author:
 * GitHub-flavored tables, fenced code blocks, autolinks, task lists.
 * No syntax highlighting (would require a heavyweight client bundle);
 * code blocks use a tinted background and the system mono font.
 *
 * The component is a server component because react-markdown 9.x can
 * render on the server with no DOM dependencies.
 */
export function DocsMarkdown({ source }: { source: string }) {
  return (
    <div className="docs-md max-w-3xl">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: (props) => (
            <h1
              className="mt-0 mb-4 text-3xl font-semibold tracking-tight text-[var(--color-text-primary)]"
              {...props}
            />
          ),
          h2: (props) => (
            <h2
              className="mt-10 mb-3 text-xl font-semibold text-[var(--color-text-primary)]"
              {...props}
            />
          ),
          h3: (props) => (
            <h3
              className="mt-6 mb-2 text-base font-semibold text-[var(--color-text-primary)]"
              {...props}
            />
          ),
          p: (props) => (
            <p
              className="my-4 text-[15px] leading-7 text-[var(--color-text-secondary)]"
              {...props}
            />
          ),
          a: ({ href, ...props }) => (
            <a
              href={href}
              className="text-[var(--color-accent)] underline-offset-2 hover:underline"
              {...props}
            />
          ),
          ul: (props) => (
            <ul className="my-4 list-disc pl-5 space-y-1.5 text-[15px] leading-7 text-[var(--color-text-secondary)]" {...props} />
          ),
          ol: (props) => (
            <ol className="my-4 list-decimal pl-5 space-y-1.5 text-[15px] leading-7 text-[var(--color-text-secondary)]" {...props} />
          ),
          li: (props) => <li {...props} />,
          code: ({ className, children, ...props }) => {
            const isBlock = typeof className === "string" && className.startsWith("language-")
            if (isBlock) {
              return (
                <code className={`${className} block`} {...props}>
                  {children}
                </code>
              )
            }
            return (
              <code
                className="rounded bg-[var(--color-surface-overlay)] px-1.5 py-0.5 font-mono text-[12.5px] text-[var(--color-text-primary)]"
                {...props}
              >
                {children}
              </code>
            )
          },
          pre: (props) => (
            <pre
              className="my-5 overflow-x-auto rounded-lg border border-[var(--color-border-subtle)] bg-[#0B0F19] p-4 text-[12.5px] leading-6 text-[#E2E8F0]"
              {...props}
            />
          ),
          table: (props) => (
            <div className="my-5 overflow-x-auto">
              <table className="w-full border-collapse text-[14px]" {...props} />
            </div>
          ),
          thead: (props) => <thead className="text-left text-[var(--color-text-tertiary)]" {...props} />,
          th: (props) => (
            <th className="border-b border-[var(--color-border-subtle)] px-3 py-2 font-semibold uppercase tracking-wide text-[12px]" {...props} />
          ),
          td: (props) => (
            <td className="border-b border-[var(--color-border-subtle)] px-3 py-2 align-top text-[var(--color-text-secondary)]" {...props} />
          ),
          blockquote: (props) => (
            <blockquote
              className="my-4 border-l-4 border-[var(--color-accent)] bg-[var(--color-surface-overlay)] px-4 py-2 text-[var(--color-text-secondary)] italic"
              {...props}
            />
          ),
          hr: () => <hr className="my-8 border-[var(--color-border-subtle)]" />,
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}
