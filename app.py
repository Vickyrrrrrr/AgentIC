import streamlit as st
import os
import pandas as pd
import subprocess
import glob

# Configuration
OPENLANE_ROOT = os.environ.get("OPENLANE_ROOT", os.path.expanduser("~/OpenLane"))
DESIGNS_DIR = os.path.join(OPENLANE_ROOT, "designs")

st.set_page_config(page_title="AgentIC Dashboard", page_icon="チップ", layout="wide")

st.title("🤖 AgentIC: AI Chip Design Studio")
st.markdown("Monitor your AI-generated silicon designs and metrics.")

# --- Sidebar: Project Selection ---
if not os.path.exists(DESIGNS_DIR):
    st.error(f"OpenLane Design Directory not found at: {DESIGNS_DIR}")
    st.stop()

designs = [d for d in os.listdir(DESIGNS_DIR) if os.path.isdir(os.path.join(DESIGNS_DIR, d))]
# Filter out standard openlane designs if needed, keeping it simple for now
selected_design = st.sidebar.selectbox("Select Design", designs)

# --- Main Content ---

if selected_design:
    design_path = os.path.join(DESIGNS_DIR, selected_design)
    
    # 1. Source Code Viewer
    st.header(f"📦 Design: {selected_design}")
    
    verilog_path = os.path.join(design_path, "src", f"{selected_design}.v")
    if os.path.exists(verilog_path):
        with open(verilog_path, "r") as f:
            code = f.read()
        with st.expander("Show Verilog Source", expanded=False):
            st.code(code, language="verilog")
    else:
        st.warning("No Verilog source found.")

    # 2. Build Status & Metrics
    st.subheader("🏭 Fabrication Status")
    
    # Check for GDS
    # Note: AgentIC uses tag "agentrun", but users might use others.
    # We look for the most recent run or 'agentrun'
    runs_dir = os.path.join(design_path, "runs")
    gds_path = None
    report_path = None
    
    if os.path.exists(runs_dir):
        # Prefer 'agentrun' if it exists, else latest
        if os.path.exists(os.path.join(runs_dir, "agentrun")):
            run_name = "agentrun"
        else:
            # find latest run
            all_runs = sorted(glob.glob(os.path.join(runs_dir, "*")), key=os.path.getmtime, reverse=True)
            run_name = os.path.basename(all_runs[0]) if all_runs else None
            
        if run_name:
            st.info(f"Viewing Run: **{run_name}**")
            
            # GDS Path
            possible_gds = os.path.join(runs_dir, run_name, "results", "final", "gds", f"{selected_design}.gds")
            if os.path.exists(possible_gds):
                gds_path = possible_gds
                st.success("✅ GDSII Layout Generated")
                
                # Download Button
                with open(gds_path, "rb") as f:
                    st.download_button(
                        label="⬇️ Download GDSII File",
                        data=f,
                        file_name=f"{selected_design}.gds",
                        mime="application/octet-stream"
                    )
            else:
                st.warning("⚠️ No GDSII file found in this run.")

            # Metrics (CSV)
            # OpenLane typically stores metrics in reports/metrics.csv
            possible_csv = os.path.join(runs_dir, run_name, "reports", "metrics.csv")
            if os.path.exists(possible_csv):
                try:
                    df = pd.read_csv(possible_csv)
                    # Transpose for better reading if it's a single row
                    st.dataframe(df.T)
                except Exception as e:
                    st.error(f"Could not read metrics CSV: {e}")
            else:
                 st.info("No metrics.csv report found.")
        else:
             st.info("No runs found for this design.")
    else:
        st.info("No runs directory found.")

# --- New Design Creator ---
st.markdown("---")
st.subheader("✨ Create New Design")

with st.form("new_design_form"):
    new_name = st.text_input("Design Name (no spaces)", "my_new_chip")
    new_desc = st.text_area("Description", "A 8-bit shift register with enable signal.")
    submitted = st.form_submit_button("🚀 Launch AI Designer")

    if submitted:
        if " " in new_name:
            st.error("Design name cannot contain spaces.")
        else:
            cmd = ["python3", "main.py", "build", "--name", new_name, "--desc", new_desc]
            
            with st.spinner("AI is thinking and building... (This generally takes 5-10 mins)"):
                # We run this from the AgentIC root
                process = subprocess.run(
                    cmd, 
                    cwd=os.path.dirname(os.path.abspath(__file__)),
                    capture_output=True, 
                    text=True
                )
                
            if process.returncode == 0:
                st.success(f"Design '{new_name}' created successfully!")
                st.text_area("Build Output", process.stdout, height=200)
                st.rerun()
            else:
                st.error("Build failed.")
                st.text_area("Error Log", process.stderr, height=200)
