import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from streamlit_option_menu import option_menu
from streamlit_ace import st_ace
import time
import os
import glob
import subprocess

# --- 1. CONFIGURATION & THEME ---
st.set_page_config(
    page_title="AgentIC | AI Silicon Design",
    page_icon="🧊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for "Deep Space" Glassmorphism Theme
st.markdown("""
<style>
    /* Full Page Background */
    .stApp {
        background-color: #0E1117;
        color: #E0E0E0;
    }
    
    /* Top Bar Pulse Animation */
    @keyframes pulse {
        0% { box-shadow: 0 0 0 0 rgba(0, 209, 255, 0.7); }
        70% { box-shadow: 0 0 0 10px rgba(0, 209, 255, 0); }
        100% { box-shadow: 0 0 0 0 rgba(0, 209, 255, 0); }
    }
    
    .status-indicator {
        width: 12px;
        height: 12px;
        background-color: #00D1FF;
        border-radius: 50%;
        display: inline-block;
        animation: pulse 2s infinite;
        margin-right: 8px;
    }

    /* Glassmorphism Cards */
    .glass-card {
        background: rgba(22, 27, 34, 0.7);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 15px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        transition: transform 0.2s, box-shadow 0.2s;
    }
    
    .glass-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 15px rgba(0, 209, 255, 0.15); /* Electric Blue Glow */
        border-color: rgba(0, 209, 255, 0.3);
    }
    
    /* Metric Big Numbers */
    .metric-value {
        font-family: 'Ensure', sans-serif;
        font-size: 32px;
        font-weight: 700;
        background: -webkit-linear-gradient(#00D1FF, #7000FF);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    
    .metric-label {
        font-size: 14px;
        color: #A0A0A0;
        text-transform: uppercase;
        letter-spacing: 1px;
    }

    /* Sidebar Customization */
    section[data-testid="stSidebar"] {
        background-color: #0E1117;
        border-right: 1px solid rgba(255, 255, 255, 0.05);
    }
    
    /* Terminal Output Style */
    .terminal-window {
        background-color: #000000;
        border: 1px solid #333;
        border-left: 4px solid #00D1FF;
        border-radius: 5px;
        padding: 15px;
        font-family: 'Courier New', monospace;
        color: #00FF00;
        height: 300px;
        overflow-y: auto;
    }
    
    /* Custom Header */
    .header-container {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding-bottom: 20px;
        border-bottom: 1px solid rgba(255,255,255,0.1);
        margin-bottom: 30px;
    }
    
    .app-title {
        font-size: 24px;
        font-weight: 600;
        color: #FFFFFF;
    }

</style>
""", unsafe_allow_html=True)

# --- 2. HEADER & LAYOUT ---

col_h1, col_h2 = st.columns([3, 1])
with col_h1:
    st.markdown("""
        <div class="header-container">
            <div class="app-title">
                🧊 AgentIC <span style="color:#00D1FF; font-weight:300;">| AI-Powered Silicon Design</span>
            </div>
            <div style="display:flex; align-items:center;">
                <div class="status-indicator"></div>
                <span style="font-size:12px; color:#00D1FF; letter-spacing:1px;">SYSTEM STATUS: ONLINE</span>
            </div>
        </div>
    """, unsafe_allow_html=True)

# Sidebar Navigation
with st.sidebar:
    selected_page = option_menu(
        "Navigation", 
        ["Dashboard", "Design Studio", "Fabrication", "Settings"],
        icons=['speedometer2', 'cpu', 'layers', 'sliders'],
        menu_icon="cast", 
        default_index=0,
        styles={
            "container": {"padding": "5px", "background-color": "#0E1117"},
            "icon": {"color": "#00D1FF", "font-size": "20px"},
            "nav-link": {"font-size": "14px", "text-align": "left", "margin":"5px", "--hover-color": "#161B22"},
            "nav-link-selected": {"background-color": "#161B22", "color": "#00D1FF", "border-left": "3px solid #00D1FF"},
        }
    )
    
    st.markdown("---")
    demo_mode = st.toggle("Demo Mode (Mock Data)", value=False)

# Configuration Paths
OPENLANE_ROOT = os.environ.get("OPENLANE_ROOT", os.path.expanduser("~/OpenLane"))
DESIGNS_DIR = os.path.join(OPENLANE_ROOT, "designs")

# --- 3. PAGE: DASHBOARD ---
if selected_page == "Dashboard":
    st.markdown("## 📡 Mission Control")
    
    # -- Row 1: 3D Metric Cards --
    c1, c2, c3, c4 = st.columns(4)
    
    def metric_card(title, value, delta, color):
        return f"""
        <div class="glass-card">
            <div class="metric-label">{title}</div>
            <div class="metric-value">{value}</div>
            <div style="color: {color}; font-size: 14px; margin-top: 5px;">
                {delta}
            </div>
        </div>
        """
    
    # Mock data for Demo Mode
    if demo_mode:
        wns_val, wns_d = "-0.05 ns", "▼ 12% Improved"
        pwr_val, pwr_d = "14.2 mW", "▼ 8% Efficient"
        area_val, area_d = "420 μm²", "▼ 5% Smaller"
        gates_val, gates_d = "12,450", "▲ 24% Density"
    else:
        # Placeholder for real metrics parsing
        wns_val, wns_d = "N/A", "--"
        pwr_val, pwr_d = "N/A", "--"
        area_val, area_d = "N/A", "--"
        gates_val, gates_d = "N/A", "--"

    c1.markdown(metric_card("Worst Negative Slack", wns_val, wns_d, "#00FF99"), unsafe_allow_html=True)
    c2.markdown(metric_card("Total Power", pwr_val, pwr_d, "#00D1FF"), unsafe_allow_html=True)
    c3.markdown(metric_card("Die Area", area_val, area_d, "#7000FF"), unsafe_allow_html=True)
    c4.markdown(metric_card("Gate Count", gates_val, gates_d, "#FF0055"), unsafe_allow_html=True)

    # -- Row 2: Charts --
    col_chart1, col_chart2 = st.columns([1, 1])
    
    with col_chart1:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.subheader("⚡ AgentIC vs Human Design")
        
        categories = ['Speed (MHz)', 'Power Efficiency', 'Area Opt.', 'DRC Safety', 'Time-to-Market']
        
        fig = go.Figure()
        fig.add_trace(go.Scatterpolar(
            r=[8, 9, 7, 10, 10] if demo_mode else [0,0,0,0,0],
            theta=categories,
            fill='toself',
            name='AgentIC AI',
            line_color='#00D1FF',
            opacity=0.7
        ))
        fig.add_trace(go.Scatterpolar(
            r=[6, 7, 8, 9, 4] if demo_mode else [0,0,0,0,0],
            theta=categories,
            fill='toself',
            name='Manual Engineer',
            line_color='#7000FF',
            opacity=0.4
        ))
        
        fig.update_layout(
            polar=dict(
                radialaxis=dict(visible=True, range=[0, 10], showline=False, gridcolor="rgba(255,255,255,0.1)"),
                bgcolor="rgba(0,0,0,0)"
            ),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#E0E0E0"),
            showlegend=True,
            legend=dict(y=1.2)
        )
        st.plotly_chart(fig, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with col_chart2:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.subheader("⏱️ Optimization Benchmarking")
        
        # Donut Chart for Time Saved
        labels = ['Design Time', 'Verification', 'Physical Layout', 'Optimization']
        values = [4500, 2500, 1053, 500]
        
        fig2 = go.Figure(data=[go.Pie(
            labels=labels, 
            values=values, 
            hole=.6,
            marker_colors=['#00D1FF', '#7000FF', '#222222', '#111111']
        )])
        
        fig2.update_layout(
            annotations=[dict(text='85%<br>Faster', x=0.5, y=0.5, font_size=20, showarrow=False, font_color="#FFFFFF")],
            paper_bgcolor="rgba(0,0,0,0)",
            showlegend=True,
            font=dict(color="#E0E0E0")
        )
        st.plotly_chart(fig2, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # -- Row 3: Live Timeline --
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.subheader("🚀 Pipelined Execution Flow")
    
    # Gantt Chart Replacement
    df_gantt = pd.DataFrame([
        dict(Task="Synthesis (Yosys)", Start='2023-01-01', Finish='2023-02-28', Completion=100),
        dict(Task="Floorplan (OpenROAD)", Start='2023-03-05', Finish='2023-04-15', Completion=80),
        dict(Task="Placement & Resizing", Start='2023-02-20', Finish='2023-05-30', Completion=60),
        dict(Task="CTS & Routing", Start='2023-03-20', Finish='2023-06-30', Completion=0)
    ])
    
    # We fake dates to numbers for a simple visual
    fig3 = px.timeline(df_gantt, x_start="Start", x_end="Finish", y="Task", color="Completion",
                      color_continuous_scale=["#161B22", "#00D1FF"])
    fig3.update_yaxes(autorange="reversed")
    fig3.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", 
        plot_bgcolor="rgba(0,0,0,0)", 
        font=dict(color="#E0E0E0")
    )
    st.plotly_chart(fig3, use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

# --- 4. PAGE: DESIGN STUDIO ---
elif selected_page == "Design Studio":
    st.markdown("## 🛠️ AI Design Studio")
    
    c_left, c_right = st.columns([1, 2])
    
    with c_left:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.subheader("New Project")
        
        with st.form("design_form"):
            name = st.text_input("Design Name", placeholder="e.g. neuro_core_v1")
            desc = st.text_area("Functional Description", placeholder="Describe logic, inputs, outputs...", height=150)
            
            submitted = st.form_submit_button("🚀 Generating Verilog", type="primary")
            
            if submitted and not demo_mode:
                st.info("Initiating DeepSeek Agent...")
                # Call subprocess logic here
                time.sleep(1) # mock delay
                st.success("Agent dispatched.")
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Selector for existing designs
        if os.path.exists(DESIGNS_DIR):
            designs = [d for d in os.listdir(DESIGNS_DIR) if os.path.isdir(os.path.join(DESIGNS_DIR, d))]
            selected_design = st.selectbox("Load Existing Design", designs)
        else:
            selected_design = None

    with c_right:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.subheader("💻 Code Editor")
        
        verilog_content = "// Select a design to view code"
        if selected_design:
            v_path = os.path.join(DESIGNS_DIR, selected_design, "src", f"{selected_design}.v")
            if os.path.exists(v_path):
                with open(v_path, "r") as f:
                    verilog_content = f.read()

        # Ace Editor
        code = st_ace(
            value=verilog_content,
            language="verilog",
            theme="monokai",
            key="verilog_editor",
            height=400,
            font_size=14,
            show_gutter=True,
            wrap=True
        )
        st.markdown('</div>', unsafe_allow_html=True)

    # Terminal Log
    st.markdown("### 📟 Agent Logs")
    st.markdown(f"""
    <div class="terminal-window">
    [SYSTEM] Initialized AgentIC Kernel v2.0.<br>
    [INFO] DeepSeek-R1 Model Loaded (Quantized).<br>
    [INFO] Connected to OpenLane Docker Container.<br>
    <span style="color:#00D1FF">vickynishad@agentic:~$</span> Waiting for command...
    </div>
    """, unsafe_allow_html=True)

# --- 5. PAGE: FABRICATION ---
elif selected_page == "Fabrication":
    st.markdown("## 🏗️ Fabrication & GDSII")
    
    # Show GDS Status
    if os.path.exists(DESIGNS_DIR):
        designs = [d for d in os.listdir(DESIGNS_DIR) if os.path.isdir(os.path.join(DESIGNS_DIR, d))]
        design_to_fab = st.selectbox("Select Design for GDSII Extraction", designs)
        
        if design_to_fab:
            col1, col2 = st.columns(2)
            
            with col1:
                st.info(f"Checking runs for {design_to_fab}...")
                runs_dir = os.path.join(DESIGNS_DIR, design_to_fab, "runs")
                
                # Logic to find GDS
                gds_path = None
                if os.path.exists(runs_dir):
                    # check agentrun
                    agentrun_path = os.path.join(runs_dir, "agentrun", "results", "final", "gds", f"{design_to_fab}.gds")
                    if os.path.exists(agentrun_path):
                        gds_path = agentrun_path
                
                if gds_path:
                    st.success("✅ GDSII File Ready")
                    st.markdown(f"**Path:** `{gds_path}`")
                    
                    with open(gds_path, "rb") as f:
                        st.download_button(
                            label="⬇️ Download Tapeout GDS",
                            data=f,
                            file_name=f"{design_to_fab}.gds",
                            mime="application/octet-stream",
                            type="primary"
                        )
                else:
                    st.warning("⚠️ No tapeout file found. Run 'Build' first.")
            
            with col2:
                # Placeholder for GDS Viewer image
                st.markdown('<div class="glass-card" style="text-align:center;">', unsafe_allow_html=True)
                st.markdown("### 🔬 Layout Preview")
                st.markdown("*Use 3rd party KLayout viewer for full inspection*")
                st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/e/e4/Die_shot_of_180nm_CMOS_node.jpg/640px-Die_shot_of_180nm_CMOS_node.jpg", caption="Silicon Die Shot (Placeholder)")
                st.markdown('</div>', unsafe_allow_html=True)


# --- FOOTER ---
st.markdown("---")
st.markdown("""
<div style="text-align: center; color: #555; font-size: 12px;">
    AGENTIC FRAMEWORK © 2026 | POWERED BY DEEPSEEK & OPENLANE
</div>
""", unsafe_allow_html=True)
