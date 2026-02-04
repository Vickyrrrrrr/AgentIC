# Research Poster Content: AgentIC & Defense Challenges

## Title
**AgentIC: Autonomous Sovereign Silicon Design Framework for Defense Applications**

## Abstract
Modern defense systems are critically dependent on advanced semiconductors. However, reliance on foreign EDA tools and fabrication supply chains introduces severe risks, including Hardware Trojans, backdoors, and strategic denial of technology. Validating the "Trust" in hardware is expensive and time-consuming. **AgentIC** is a novel, AI-driven "Text-to-Silicon" framework designed to democratize and accelerate the design of secure, sovereign silicon. By leveraging specialized coding Large Language Models (LLMs) like **Qwen Coder** and open-source EDA tools (OpenLane), AgentIC automates the RTL-to-GDSII flow, ensuring design secrecy and reducing the "Time-to-Tactical-Edge" for custom hardware solutions.

---

## 1. Problem Statement: The Defense Silicon Gap

### A. Supply Chain Vulnerability
*   **Dependency:** Over 90% of advanced chips and EDA tools are controlled by non-domestic entities.
*   **Risk:** In conflict scenarios, access to critical components can be cut off (Sanctions/Blockades).

### B. Hardware Security Threats
*   **Trojans:** Malicious logic inserted during design or fabrication (e.g., "Kill Switches" or data exfiltration backdoors).
*   **Opaque IP:** Using closed-source "Black Box" IP cores makes it impossible to verify full security coverage.

### C. Obsolescence & Agility
*   **Legacy Systems:** Maintaining aging military hardware requires obsolete chips that are no longer manufactured.
*   **Slow Development:** Traditional ASIC design cycles take 18-24 months, too slow for evolving asymmetric threats.

---

## 2. Proposed Solution: The AgentIC Framework

AgentIC serves as a **Sovereign Design Companion**, allowing defense engineers to generate verified, physically layout-ready silicon from high-level natural language specifications.

### Key Pillars:
1.  **AI-Agent Crew:** Specialized AI agents act as the Design Team (Architect, RTL Coder, Verification Engineer).
2.  **Self-Correction:** Closed-loop feedback mechanism where agents fix their own compilation and simulation errors.
3.  **Local & Resilient:** Support for on-premises LLM deployment (e.g., Qwen Coder on air-gapped servers) with **Robust Fallback** (Cloud → Local) to ensure operational continuity.
4.  **Open Source Flow:** output is compatible with the OpenLane/SkyWater 130nm PDK, ensuring a fully auditable toolchain.

---

## 3. Methodology & Architecture

### The Workflow
1.  **Prompt:** User inputs specs (e.g., *"Design a secure AES-256 accelerator with side-channel attack masking"*).
2.  **Design Agent:** Generates SystemVerilog RTL.
3.  **Verification Agent:** Generates a self-checking testbench (randomized stimuli + assertion checking).
4.  **Simulation & Loop:** 
    *   Runs `iverilog` simulation.
    *   If fail: Agents read logs -> Patch Code -> Re-run.
    *   If pass: Proceed to hardening.
5.  **Physical Design:** Automates OpenLane scripts to generate GDSII.

### Sovereign Tech Stack
*   **Logic:** Custom AI Agents powered by **Qwen Coder** (Primary) / Llama 3 (Backup)
*   **Simulation:** Icarus Verilog (Open Source)
*   **Layout:** OpenLane (Open Source)
*   **PDK:** SkyWater 130nm (Open Google/SkyWater)

---

## 4. Addressing Defense Challenges (Analysis)

| Challenge | AgentIC Solution |
| :--- | :--- |
| **IP Theft / Secrecy** | **Local Inference:** Prompts run on secure, air-gapped servers. No cloud APIs required. |
| **Trojan Insertion** | **Auditable Code:** AI generates human-readable SystemVerilog, not binary blobs. Easier to review. |
| **Rapid Field Deployment** | **Speed:** Reduces design-to-layout time from months to days for auxiliary chips. |
| **Talent Shortage** | **Force Multiplier:** Allows non-expert systems engineers to create functional hardware blocks. |

---

## 5. Case Study: Secure Processor Recovery

**Experiment:** 
We tasked AgentIC to design a "Secure Lockout Mechanism" for a processor (a common need for tamper-proof hardware).

*   **Input:** "Create a state machine that locks the system after 3 failed 4-digit PIN attempts."
*   **Result:**
    1.  Agent generated an FSM (Finite State Machine) correctly handling transitions.
    2.  Initial bug: Reset logic was inverted.
    3.  **Auto-Fix:** Verification agent caught the timeout error, and the Designer agent corrected the polarity in `always_ff`.
    4.  **Final Output:** Clean, LVS-clean GDSII layout ready for fabrication.

---

## 6. Conclusion & Future Work

AgentIC demonstrates that Generative AI can bridge the gap between secure requirements and physical silicon. By keeping the "Brain" of the design process local and using open tools, India can achieve true **Atmanirbhar** status in the strategic semiconductor sector. 

**Future Roadmap:**
*   Integration with formal verification tools for mathematical security proofs.
*   Support for FPGA bitstream generation for rapid field updates.

---

## References
1.  *The OpenLane Project Documentation*
2.  *Qwen Coder / Llama 3 Technical Reports*
3.  *Defense Advanced Research Projects Agency (DARPA) POSH Program*
