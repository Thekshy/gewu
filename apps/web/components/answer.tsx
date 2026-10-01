"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** LLM 回答的 markdown 渲染（GFM：表格/任务列表/删除线）；排版走 typography 插件。 */
export default function Answer({ text }: { text: string }) {
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none prose-p:my-1.5 prose-p:leading-relaxed prose-headings:mb-1.5 prose-headings:mt-3 prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0 prose-pre:my-2 prose-code:rounded-md prose-code:bg-muted prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.85em] prose-code:before:content-none prose-code:after:content-none">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}
