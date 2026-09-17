import { useCallback, useMemo } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  type Node,
  type Edge,
  useNodesState,
  useEdgesState,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

interface PipelineGraphProps {
  runs: Array<{
    stage: string;
    status: string;
    duration_ms: number;
    metrics: Record<string, unknown>;
  }>;
  onStageClick?: (stage: string) => void;
}

interface StageNodeData {
  label: string;
  status: string;
  duration_ms: number | null;
  [key: string]: unknown;
}

const STATUS_ICONS: Record<string, string> = {
  PASS: '✓',
  FAIL: '✗',
  RUNNING: '⟳',
  PENDING: '○',
};

const STATUS_CLASS: Record<string, string> = {
  PASS: 'stage-pass',
  FAIL: 'stage-fail',
  RUNNING: 'stage-running',
  PENDING: 'stage-pending',
};

function StageNode({ data }: { data: StageNodeData }) {
  const status = data.status ?? 'PENDING';
  const cls = STATUS_CLASS[status] ?? 'stage-pending';
  const icon = STATUS_ICONS[status] ?? '○';
  const duration =
    data.duration_ms != null ? `${(data.duration_ms / 1000).toFixed(1)}s` : null;

  return (
    <div className={`stage-node ${cls}`}>
      <Handle type="target" position={Position.Left} />
      <div className="stage-icon">{icon}</div>
      <div className="stage-label">{data.label}</div>
      {duration && <div className="stage-duration">{duration}</div>}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { stage: StageNode };

const STAGES: Array<{ id: string; row: number; col: number }> = [
  // Row 1
  { id: 'SPEC', row: 0, col: 0 },
  { id: 'RTL_GEN', row: 0, col: 1 },
  { id: 'VERIFICATION', row: 0, col: 2 },
  { id: 'FORMAL_VERIFY', row: 0, col: 3 },
  { id: 'SDC_GEN', row: 0, col: 4 },
  // Row 2
  { id: 'SYNTHESIS', row: 1, col: 0 },
  { id: 'TIMING_ANALYSIS', row: 1, col: 1 },
  { id: 'DFT_SCAN', row: 1, col: 2 },
  // Row 3
  { id: 'HARDENING', row: 2, col: 0 },
  { id: 'PHYSICAL_VERIFY', row: 2, col: 1 },
  { id: 'POWER_ANALYSIS', row: 2, col: 2 },
  { id: 'SIGNOFF', row: 2, col: 3 },
];

const EDGE_PAIRS: Array<[string, string]> = [
  ['SPEC', 'RTL_GEN'],
  ['RTL_GEN', 'VERIFICATION'],
  ['VERIFICATION', 'FORMAL_VERIFY'],
  ['FORMAL_VERIFY', 'SDC_GEN'],
  ['SDC_GEN', 'SYNTHESIS'],
  ['SYNTHESIS', 'TIMING_ANALYSIS'],
  ['TIMING_ANALYSIS', 'DFT_SCAN'],
  ['DFT_SCAN', 'HARDENING'],
  ['HARDENING', 'PHYSICAL_VERIFY'],
  ['PHYSICAL_VERIFY', 'POWER_ANALYSIS'],
  ['POWER_ANALYSIS', 'SIGNOFF'],
];

const X_GAP = 220;
const Y_GAP = 180;

export default function PipelineGraph({ runs, onStageClick }: PipelineGraphProps) {
  const runMap = useMemo(() => {
    const map = new Map<string, (typeof runs)[number]>();
    for (const r of runs) map.set(r.stage, r);
    return map;
  }, [runs]);

  const initialNodes: Node[] = useMemo(
    () =>
      STAGES.map(({ id, row, col }) => {
        const run = runMap.get(id);
        return {
          id,
          type: 'stage',
          position: { x: col * X_GAP, y: row * Y_GAP },
          data: {
            label: id.replace(/_/g, ' '),
            status: run?.status ?? 'PENDING',
            duration_ms: run?.duration_ms ?? null,
          } satisfies StageNodeData,
        };
      }),
    [runMap],
  );

  const initialEdges: Edge[] = useMemo(
    () =>
      EDGE_PAIRS.map(([source, target]) => {
        const srcRun = runMap.get(source);
        return {
          id: `${source}-${target}`,
          source,
          target,
          animated: srcRun?.status === 'RUNNING',
          style: { stroke: '#555' },
        };
      }),
    [runMap],
  );

  const [nodes, , onNodesChange] = useNodesState(initialNodes);
  const [edges, , onEdgesChange] = useEdgesState(initialEdges);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      onStageClick?.(node.id);
    },
    [onStageClick],
  );

  return (
    <div style={{ width: '100%', height: 520, background: '#1a1a2e' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        nodeTypes={nodeTypes}
        fitView
        panOnDrag={false}
        zoomOnScroll={false}
        preventScrolling={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#334" gap={20} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
