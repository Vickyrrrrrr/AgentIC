import React from 'react';

type JobSummary = {
  job_id: string;
  design_name: string;
  status: string;
  current_state: string;
  created_at: number;
  event_count: number;
};

interface BuildHistoryProps {
  jobs: JobSummary[];
  selectedDesign: string;
  onSelectDesign: (designName: string) => void;
  onOpenPage: (page: string) => void;
}

function statusColor(status: string): string {
  if (status === 'done') return 'var(--success)';
  if (status === 'failed') return 'var(--fail)';
  if (status === 'running' || status === 'queued' || status === 'cancelling') return 'var(--accent)';
  return 'var(--text-dim)';
}

function formatTs(ts: number): string {
  if (!ts) return 'Unknown';
  return new Date(ts * 1000).toLocaleString();
}

export const BuildHistory: React.FC<BuildHistoryProps> = ({
  jobs,
  selectedDesign,
  onSelectDesign,
  onOpenPage,
}) => {
  const sortedJobs = [...jobs].sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  const filteredJobs = selectedDesign
    ? sortedJobs.filter((job) => job.design_name === selectedDesign)
    : sortedJobs;

  const doneCount = sortedJobs.filter((job) => job.status === 'done').length;
  const runningCount = sortedJobs.filter(
    (job) => job.status === 'running' || job.status === 'queued' || job.status === 'cancelling'
  ).length;
  const failedCount = sortedJobs.filter((job) => job.status === 'failed').length;

  return (
    <div className="page-container" style={{ padding: '1.5rem', maxWidth: '1180px' }}>
      <div className="grid-4" style={{ marginBottom: '1rem' }}>
        <div className="sci-fi-card">
          <div className="section-heading">Total Jobs</div>
          <div style={{ fontSize: '1.6rem', fontWeight: 700 }}>{sortedJobs.length}</div>
        </div>
        <div className="sci-fi-card">
          <div className="section-heading">Running</div>
          <div style={{ fontSize: '1.6rem', fontWeight: 700, color: 'var(--accent)' }}>{runningCount}</div>
        </div>
        <div className="sci-fi-card">
          <div className="section-heading">Succeeded</div>
          <div style={{ fontSize: '1.6rem', fontWeight: 700, color: 'var(--success)' }}>{doneCount}</div>
        </div>
        <div className="sci-fi-card">
          <div className="section-heading">Failed</div>
          <div style={{ fontSize: '1.6rem', fontWeight: 700, color: 'var(--fail)' }}>{failedCount}</div>
        </div>
      </div>

      <div className="sci-fi-card" style={{ padding: '1.1rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.8rem' }}>
          <h3 style={{ margin: 0, fontWeight: 700 }}>
            Build Timeline
            {selectedDesign ? ` · ${selectedDesign}` : ''}
          </h3>
          <button className="btn-primary" onClick={() => onOpenPage('Design Studio')}>
            Start New Build
          </button>
        </div>

        {filteredJobs.length === 0 ? (
          <div
            style={{
              border: '1px dashed var(--border-mid)',
              borderRadius: 'var(--radius)',
              padding: '1.2rem',
              color: 'var(--text-mid)',
              fontSize: '0.9rem',
            }}
          >
            No jobs available for this design context yet.
          </div>
        ) : (
          <table className="enterprise-table">
            <thead>
              <tr>
                <th>Job ID</th>
                <th>Design</th>
                <th>Status</th>
                <th>Stage</th>
                <th>Events</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredJobs.map((job) => (
                <tr key={job.job_id}>
                  <td style={{ fontFamily: 'Fira Code, monospace', fontSize: '0.78rem' }}>
                    {job.job_id.slice(0, 8)}...
                  </td>
                  <td>{job.design_name || '-'}</td>
                  <td>
                    <span style={{ color: statusColor(job.status), fontWeight: 650 }}>{job.status}</span>
                  </td>
                  <td>{job.current_state || '-'}</td>
                  <td>{job.event_count || 0}</td>
                  <td>{formatTs(job.created_at)}</td>
                  <td>
                    <button
                      className="top-nav-btn"
                      style={{ width: 'auto', height: '30px', padding: '0 0.55rem', fontSize: '0.75rem' }}
                      onClick={() => {
                        onSelectDesign(job.design_name);
                        onOpenPage('Dashboard');
                      }}
                    >
                      Open Design
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};

