import { PipelineClient } from "./pipeline-client";

export const dynamic = "force-dynamic";

export default function PipelinePage() {
  return (
    <div className="flex flex-col h-[calc(100vh-3rem)]">
      <div className="px-4 py-3 bg-white border-b border-slate-200 shrink-0">
        <h1 className="text-base font-semibold text-slate-800">Pipeline</h1>
        <p className="text-xs text-slate-500 mt-0.5">Companies by review status — use the dropdown on each card to move</p>
      </div>
      <PipelineClient />
    </div>
  );
}
