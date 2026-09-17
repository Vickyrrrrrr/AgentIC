/*
# Create chip designs and pipeline runs tables (single-tenant, no auth)

1. New Tables
  - `designs`
    - `id` (uuid, primary key)
    - `name` (text, design name e.g. "uart_tx")
    - `description` (text, natural language description)
    - `pdk` (text, target PDK e.g. "sky130")
    - `strategy` (text, build strategy)
    - `clock_mhz` (numeric, target frequency)
    - `status` (text, current pipeline state)
    - `rtl_code` (text, generated Verilog/SV)
    - `testbench_code` (text, generated testbench)
    - `sdc_code` (text, generated constraints)
    - `spec_json` (jsonb, hardware spec)
    - `created_at` (timestamptz)
    - `updated_at` (timestamptz)
  - `pipeline_runs`
    - `id` (uuid, primary key)
    - `design_id` (uuid, FK to designs)
    - `stage` (text, pipeline stage name)
    - `status` (text, PASS/FAIL/RETRY/SKIP/ERROR)
    - `failure_class` (text, optional)
    - `diagnostics` (text[], array of messages)
    - `metrics` (jsonb, stage-specific metrics)
    - `artifacts` (jsonb, file paths and results)
    - `duration_ms` (integer)
    - `created_at` (timestamptz)
  - `design_artifacts`
    - `id` (uuid, primary key)
    - `design_id` (uuid, FK to designs)
    - `artifact_type` (text, e.g. "netlist", "waveform", "report")
    - `name` (text)
    - `content` (text, file content or JSON)
    - `metadata` (jsonb)
    - `created_at` (timestamptz)
2. Security
  - Enable RLS on all tables.
  - Allow anon + authenticated full CRUD (single-tenant, no auth).
*/

-- Designs table
CREATE TABLE IF NOT EXISTS designs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  description text NOT NULL DEFAULT '',
  pdk text NOT NULL DEFAULT 'sky130',
  strategy text NOT NULL DEFAULT 'SV_MODULAR',
  clock_mhz numeric NOT NULL DEFAULT 50,
  status text NOT NULL DEFAULT 'INIT',
  rtl_code text,
  testbench_code text,
  sdc_code text,
  spec_json jsonb,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

ALTER TABLE designs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_designs" ON designs;
CREATE POLICY "anon_select_designs" ON designs FOR SELECT
  TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "anon_insert_designs" ON designs;
CREATE POLICY "anon_insert_designs" ON designs FOR INSERT
  TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_designs" ON designs;
CREATE POLICY "anon_update_designs" ON designs FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_delete_designs" ON designs;
CREATE POLICY "anon_delete_designs" ON designs FOR DELETE
  TO anon, authenticated USING (true);

-- Pipeline runs table
CREATE TABLE IF NOT EXISTS pipeline_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  design_id uuid NOT NULL REFERENCES designs(id) ON DELETE CASCADE,
  stage text NOT NULL,
  status text NOT NULL DEFAULT 'PENDING',
  failure_class text,
  diagnostics text[] DEFAULT '{}',
  metrics jsonb DEFAULT '{}',
  artifacts jsonb DEFAULT '{}',
  duration_ms integer DEFAULT 0,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE pipeline_runs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_runs" ON pipeline_runs;
CREATE POLICY "anon_select_runs" ON pipeline_runs FOR SELECT
  TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "anon_insert_runs" ON pipeline_runs;
CREATE POLICY "anon_insert_runs" ON pipeline_runs FOR INSERT
  TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_runs" ON pipeline_runs;
CREATE POLICY "anon_update_runs" ON pipeline_runs FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_delete_runs" ON pipeline_runs;
CREATE POLICY "anon_delete_runs" ON pipeline_runs FOR DELETE
  TO anon, authenticated USING (true);

-- Design artifacts table
CREATE TABLE IF NOT EXISTS design_artifacts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  design_id uuid NOT NULL REFERENCES designs(id) ON DELETE CASCADE,
  artifact_type text NOT NULL,
  name text NOT NULL,
  content text,
  metadata jsonb DEFAULT '{}',
  created_at timestamptz DEFAULT now()
);

ALTER TABLE design_artifacts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_artifacts" ON design_artifacts;
CREATE POLICY "anon_select_artifacts" ON design_artifacts FOR SELECT
  TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "anon_insert_artifacts" ON design_artifacts;
CREATE POLICY "anon_insert_artifacts" ON design_artifacts FOR INSERT
  TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_artifacts" ON design_artifacts;
CREATE POLICY "anon_update_artifacts" ON design_artifacts FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_delete_artifacts" ON design_artifacts;
CREATE POLICY "anon_delete_artifacts" ON design_artifacts FOR DELETE
  TO anon, authenticated USING (true);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_runs_design_id ON pipeline_runs(design_id);
CREATE INDEX IF NOT EXISTS idx_runs_stage ON pipeline_runs(stage);
CREATE INDEX IF NOT EXISTS idx_artifacts_design_id ON design_artifacts(design_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_type ON design_artifacts(artifact_type);
CREATE INDEX IF NOT EXISTS idx_designs_status ON designs(status);
