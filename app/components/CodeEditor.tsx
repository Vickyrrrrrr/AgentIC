import { Editor } from '@monaco-editor/react';

interface CodeEditorProps {
  code: string;
  language?: string;
  onChange?: (val: string) => void;
  readOnly?: boolean;
  height?: string;
}

function LoadingSkeleton({ height }: { height: string }) {
  return (
    <div
      className="animate-pulse rounded-xl bg-slate-800"
      style={{ height }}
    />
  );
}

export default function CodeEditor({
  code,
  language = 'systemverilog',
  onChange,
  readOnly = false,
  height = '400px',
}: CodeEditorProps) {
  return (
    <div className="rounded-xl overflow-hidden border border-slate-700/50">
      <Editor
        height={height}
        language={language}
        value={code}
        theme="vs-dark"
        loading={<LoadingSkeleton height={height} />}
        onChange={(value) => onChange?.(value ?? '')}
        options={{
          readOnly,
          minimap: { enabled: false },
          fontSize: 13,
          fontFamily: '"JetBrains Mono", monospace',
          wordWrap: 'on',
          scrollBeyondLastLine: false,
          padding: { top: 12, bottom: 12 },
          lineNumbersMinChars: 3,
          renderLineHighlight: 'gutter',
          smoothScrolling: true,
          cursorBlinking: 'smooth',
          automaticLayout: true,
        }}
      />
    </div>
  );
}
