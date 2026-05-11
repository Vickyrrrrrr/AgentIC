# VLSI-RAG: Building an Open-Source AI Chip Design Assistant
### A Step-by-Step Blueprint Using Public Books, Papers & Open PDKs

> **Project Vision:** Instead of trying to fine-tune an LLM on proprietary foundry data (TSMC N2, Samsung 3GAE, Intel 18A — all NDA-locked), we build a RAG (Retrieval-Augmented Generation) pipeline over the entire universe of *public* VLSI knowledge: textbooks, IEEE papers, and open-source PDKs. Just like a human engineer learns from Weste & Harris before stepping inside a fab, this system reasons from first principles — and hits the same wall humans do: it knows *why* but not the exact proprietary DRC rules. That wall is acceptable. The system is still enormously useful.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Knowledge Sources — Books](#2-knowledge-sources--books)
3. [Knowledge Sources — Open PDKs](#3-knowledge-sources--open-pdks)
4. [Knowledge Sources — Papers & Roadmaps](#4-knowledge-sources--papers--roadmaps)
5. [Step 1: Data Collection & Ingestion](#5-step-1-data-collection--ingestion)
6. [Step 2: Chunking Strategy](#6-step-2-chunking-strategy)
7. [Step 3: Embedding & Vector Store](#7-step-3-embedding--vector-store)
8. [Step 4: Retrieval Agent Design](#8-step-4-retrieval-agent-design)
9. [Step 5: Reasoning Layer (LLM Selection)](#9-step-5-reasoning-layer-llm-selection)
10. [Step 6: Anti-Hallucination Guardrails](#10-step-6-anti-hallucination-guardrails)
11. [Step 7: AgentIC Integration](#11-step-7-agentic-integration)
12. [Open PDK Reference Table](#12-open-pdk-reference-table)
13. [What This System Can & Cannot Do](#13-what-this-system-can--cannot-do)
14. [Roadmap & Next Steps](#14-roadmap--next-steps)

---

## 1. Architecture Overview

```
                        ┌─────────────────────────────────┐
                        │         USER QUERY               │
                        │  "How does DIBL affect Vth       │
                        │   at sub-5nm FinFET?"            │
                        └────────────────┬────────────────┘
                                         │
                        ┌────────────────▼────────────────┐
                        │        RETRIEVAL AGENTS          │
                        │  ┌─────────┐  ┌──────────────┐  │
                        │  │ Books   │  │  PDK Docs    │  │
                        │  │ Agent   │  │  Agent       │  │
                        │  └────┬────┘  └──────┬───────┘  │
                        │       │              │           │
                        │  ┌────▼──────────────▼───────┐  │
                        │  │     Vector Store (FAISS/  │  │
                        │  │     Qdrant) — Top-K chunks │  │
                        │  └────────────┬──────────────┘  │
                        └───────────────┼─────────────────┘
                                        │
                        ┌───────────────▼─────────────────┐
                        │       REASONING AGENT            │
                        │  GPT-5.4 Thinking / Kimi K2.6   │
                        │  Synthesizes retrieved chunks    │
                        │  Cites source for every claim    │
                        └───────────────┬─────────────────┘
                                        │
                        ┌───────────────▼─────────────────┐
                        │       GROUNDING CHECK            │
                        │  Flag answers with no citation   │
                        │  Refuse if confidence < threshold│
                        └─────────────────────────────────┘
```

**Core principle:** Every answer the system gives must trace back to a retrieved chunk. If no chunk supports the answer, the system says so rather than hallucinating fab-specific data.

---

## 2. Knowledge Sources — Books

### 🧬 Tier 1: Device Physics (Sub-10nm Reasoning)

| Book | Authors | Why It Matters for RAG | Find It |
|------|---------|----------------------|---------|
| *Fundamentals of Modern VLSI Devices* (2nd Ed.) | Yuan Taur & Tak Ning | Best source for FinFET physics, DIBL, velocity saturation, short-channel effects at sub-10nm | [Cambridge University Press](https://www.cambridge.org/core/books/fundamentals-of-modern-vlsi-devices/4C4B5F96F8B5BB26DACE98B87C13FDA6) |
| *Semiconductor Physics and Devices* (4th Ed.) | Donald Neamen | MOS theory, bandgap, doping, p-n junctions — the bedrock layer | [McGraw-Hill](https://www.mheducation.com/highered/product/semiconductor-physics-devices-neamen/M9780073529585.html) |
| *Fundamentals of Semiconductor Fabrication* | S.M. Sze & May | Lithography, etching, ion implant, deposition — process-level knowledge | [Wiley](https://www.wiley.com/en-us/Fundamentals+of+Semiconductor+Fabrication-p-9780471232797) |
| *Physics of Semiconductors and Their Heterostructures* | Morkoc | Advanced — III-V materials, GaN, future node materials | [McGraw-Hill](https://www.mheducation.com/highered/product/0070430977.html) |
| *Solid State Electronic Devices* (7th Ed.) | Streetman & Banerjee | Standard MOS physics, widely cited in academia | [Pearson](https://www.pearson.com/store/p/solid-state-electronic-devices/P100000614571) |

---

### ⚡ Tier 2: CMOS & Digital Design (The Core RTL→Layout Layer)

| Book | Authors | Why It Matters for RAG | Find It |
|------|---------|----------------------|---------|
| *CMOS VLSI Design: A Circuits & Systems Perspective* (4th Ed.) | Weste & Harris | The single most important VLSI book — full stack from transistor to chip | [Pearson](https://www.pearson.com/store/p/cmos-vlsi-design/P100000613210) |
| *Digital Integrated Circuits* (2nd Ed.) | Rabaey, Chandrakasan, Nikolic | Called the "Bhagavad Gita of VLSI" — timing, power, noise margin | [Pearson](https://www.pearson.com/store/p/digital-integrated-circuits/P100000609680) |
| *CMOS Digital Integrated Circuits* (3rd Ed.) | Sung-Mo Kang | Strong timing analysis, logic family comparisons, power | [McGraw-Hill](https://www.mheducation.com/highered/product/cmos-digital-integrated-circuits-analysis-design-kang-leblebici/M9780072460537.html) |
| *Introduction to VLSI Circuits and Systems* | John Uyemura | Clear layout-to-circuit explanations, excellent for beginners | [Wiley](https://www.wiley.com/en-us/Introduction+to+VLSI+Circuits+and+Systems-p-9780471127772) |
| *Basic VLSI Design* (3rd Ed.) | Pucknell & Eshraghian | Classic beginner text — nMOS, CMOS, stick diagrams | [Pearson](https://www.pearson.com/store/p/basic-vlsi-design/P100000612345) |

---

### 📐 Tier 3: Analog IC Design

| Book | Authors | Why It Matters for RAG | Find It |
|------|---------|----------------------|---------|
| *Design of Analog CMOS Integrated Circuits* (2nd Ed.) | Behzad Razavi | Gold standard — MOSFETs, op-amps, OTAs, PLLs, ADCs | [McGraw-Hill](https://www.mheducation.com/highered/product/design-analog-cmos-integrated-circuits-razavi/M9780072524932.html) |
| *Analysis and Design of Analog Integrated Circuits* (5th Ed.) | Gray, Hurst, Lewis, Meyer | Noise, feedback, stability — industry veteran reference | [Wiley](https://www.wiley.com/en-us/Analysis+and+Design+of+Analog+Integrated+Circuits%2C+5th+Edition-p-9780470245996) |
| *CMOS Analog Circuit Design* (3rd Ed.) | Allen & Holberg | CMOS-specific analog depth, noise analysis, bias circuits | [Oxford University Press](https://global.oup.com/academic/product/cmos-analog-circuit-design-9780199339136) |
| *Microelectronic Circuits* (8th Ed.) | Sedra & Smith | Nearly universal undergrad text — analog + digital fundamentals | [Oxford University Press](https://global.oup.com/academic/product/microelectronic-circuits-9780190853464) |
| *Analog Design Essentials* | Willy Sansen | Low-noise design, systematic methodology — European school | [Springer](https://link.springer.com/book/10.1007/b135984) |

---

### 🏗️ Tier 4: Physical Design & Timing Closure

| Book | Authors | Why It Matters for RAG | Find It |
|------|---------|----------------------|---------|
| *VLSI Physical Design: From Graph Partitioning to Timing Closure* | Kahng, Lienig, Markov, Hu | Best PD book — floorplan, placement, routing, STA | [Springer](https://link.springer.com/book/10.1007/978-3-030-96415-3) |
| *Static Timing Analysis for Nanometer Designs* | J. Bhaskar & Rakesh Chadha | Deep STA — setup/hold, OCV, CPPR, clock gating | [Springer](https://link.springer.com/book/10.1007/978-0-387-93820-2) |
| *Physical Design Essentials* | Khosrow Golshan | Practical PnR guidance, standard cell aware | [Springer](https://link.springer.com/book/10.1007/978-0-387-36716-1) |
| *Algorithms for VLSI Physical Design Automation* (3rd Ed.) | Naveed Sherwani | Classical algorithms — partitioning, placement, routing | [Springer](https://link.springer.com/book/10.1007/978-1-4615-6371-4) |
| *The Art of Timing Closure* | Khosrow Golshan | Practical fixes for real timing violations | [Springer](https://link.springer.com/book/10.1007/978-0-387-32093-7) |

---

### 🔤 Tier 5: HDL, RTL & Verification

| Book | Authors | Why It Matters for RAG | Find It |
|------|---------|----------------------|---------|
| *Verilog HDL* (2nd Ed.) | Samir Palnitkar | Standard Verilog reference — perfect for RTL code generation tasks | [Pearson](https://www.pearson.com/store/p/verilog-hdl/P100000611111) |
| *SystemVerilog for Verification* (3rd Ed.) | Chris Spear & Greg Tumbush | Best SV verification book — classes, interfaces, assertions | [Springer](https://link.springer.com/book/10.1007/978-1-4614-0715-7) |
| *Writing Testbenches using SystemVerilog* | Janick Bergeron | Most cited verification book | [Springer](https://link.springer.com/book/10.1007/0-387-29221-0) |
| *Formal Verification: An Essential Toolkit for Modern VLSI* | Seligman, Sturton, Beer | Formal property checking, model checking, assertion synthesis | [Morgan Kaufmann](https://www.sciencedirect.com/book/9780123982650/formal-verification) |
| *SystemVerilog Assertions Handbook* (4th Ed.) | Cohen, Venkataramanan, Kumari | SVA deep dive — perfect for assertion RAG | [VhdlCohen Publishing](http://www.systemverilog.us/vf/SVA_Handbook4th_Edition.pdf) |
| *The UVM Primer* | Ray Salemi | Best intro-to-UVM for beginners, practical examples | [Boot Camp Press](https://www.uvmprimer.com/) |

---

### 🔋 Tier 6: Low Power & Advanced Topics

| Book | Authors | Why It Matters for RAG | Find It |
|------|---------|----------------------|---------|
| *Low Power VLSI Design* | Ajit Pal | Comprehensive coverage — gate, architectural, system-level power | [PHI Learning](https://www.phindia.com/Books/BookDetail/9788120338197/low-power-vlsi-design) |
| *Advanced Low Power Digital Circuit Design* | Kaushik Roy & Sharat Prasad | Gate-level and architectural power optimization techniques | [Cambridge University Press](https://www.cambridge.org/core/books/low-power-cmos-vlsi-circuit-design/DE9019D0E65B4AA875CE12A73F18BD28) |
| *Machine Learning Techniques for VLSI Chip Design* | Editors: Multiple | ML-for-EDA — connects your AI work to chip design directly | [Wiley/IEEE Press](https://www.oreilly.com/library/view/machine-learning-techniques/9781119910398/) |

---

## 3. Knowledge Sources — Open PDKs

These are fully open-source, legally usable without NDA, and contain real or predictive fab data — ideal RAG sources for DRC rules, SPICE models, cell libraries, and technology files.

### SkyWater SKY130 — 130nm (Real, Manufacturable)

- **GitHub:** [https://github.com/google/skywater-pdk](https://github.com/google/skywater-pdk)
- **Raw Data:** [https://github.com/google/skywater-pdk-sky130-raw-data](https://github.com/google/skywater-pdk-sky130-raw-data)
- **Documentation:** [https://skywater-pdk.readthedocs.io/](https://skywater-pdk.readthedocs.io/)
- **License:** Apache 2.0
- **What to ingest:** DRC rules, LVS decks, SPICE models, cell characterization data, layer stack, process specs
- **Key RAG value:** Only fully open PDK for real, tapeout-proven silicon. Google has sponsored 500+ open-source chips on this node. DRC rule documentation is gold for a RAG.

```bash
git clone https://github.com/google/skywater-pdk.git
```

---

### GlobalFoundries GF180MCU — 180nm (Real, Manufacturable)

- **GitHub:** [https://github.com/google/gf180mcu-pdk](https://github.com/google/gf180mcu-pdk)
- **Documentation:** [https://gf180mcu-pdk.readthedocs.io/](https://gf180mcu-pdk.readthedocs.io/)
- **License:** Apache 2.0
- **What to ingest:** 3.3V/6V process specs, standard cell libraries (7T and 9T), IO cells, analog models
- **Key RAG value:** Covers mixed-signal and power design at 180nm — useful for reasoning about legacy node behavior, IoT chips, analog IPs

```bash
git clone https://github.com/google/gf180mcu-pdk.git
```

---

### ASAP7 — 7nm Predictive (Academic FinFET)

- **GitHub:** [https://github.com/The-OpenROAD-Project/asap7](https://github.com/The-OpenROAD-Project/asap7)
- **Homepage:** [https://asap.asu.edu](https://asap.asu.edu)
- **License:** BSD-3
- **Developed by:** ASU + ARM Research
- **What to ingest:** FinFET SPICE models (BSIM-CMG), DRC manual (`asap7_drm_201207a.pdf`), 7.5T and 6T standard cell libraries, technology files for Cadence Virtuoso
- **Key RAG value:** The ONLY public 7nm PDK with FinFET models. The DRM (Design Rule Manual) is a critical RAG document for sub-10nm reasoning. Predictive, not real fab, but physically motivated.

```bash
git clone https://github.com/The-OpenROAD-Project/asap7.git
# DRM PDF is at: asap7/asap7_pdk_r1p7/docs/asap7_drm_201207a.pdf
```

---

### FreePDK45 — 45nm Predictive

- **Homepage:** [https://eda.ncsu.edu/freepdk/](https://eda.ncsu.edu/freepdk/)
- **GitHub Mirror:** [https://github.com/mflowgen/freepdk-45nm](https://github.com/mflowgen/freepdk-45nm)
- **Wiki:** [https://www.eda.ncsu.edu/wiki/FreePDK45:Contents](https://www.eda.ncsu.edu/wiki/FreePDK45:Contents)
- **License:** Apache 2.0
- **Developed by:** NC State University (NCSU)
- **What to ingest:** Predictive Technology Model (PTM) SPICE models, design rules, NanGate Open Cell Library, Liberty timing files (.lib)
- **Key RAG value:** Bridge node — physically between real 130nm and predictive 7nm. Widely used in academic ASIC courses. Liberty files are excellent RAG material for timing queries.

```bash
git clone https://github.com/mflowgen/freepdk-45nm.git
```

---

### Open PDKs Installer (Sky130 + GF180 combined)

- **GitHub:** [https://github.com/RTimothyEdwards/open_pdks](https://github.com/RTimothyEdwards/open_pdks)
- Installs both SKY130 and GF180MCU in Open-Access format for tools like Magic, KLayout, Xschem

```bash
git clone https://github.com/RTimothyEdwards/open_pdks.git
```

---

## 4. Knowledge Sources — Papers & Roadmaps

### IEEE IRDS (International Roadmap for Devices and Systems)
- **URL:** [https://irds.ieee.org/editions/2023](https://irds.ieee.org/editions/2023)
- Freely downloadable PDFs projecting 2nm, 1nm, angstrom-scale node characteristics
- **Ingest:** More Moore chapter, Beyond CMOS chapter, lithography chapter

### arXiv cs.AR — Hardware Architecture Papers
- **URL:** [https://arxiv.org/list/cs.AR/recent](https://arxiv.org/list/cs.AR/recent)
- Hundreds of free papers on FinFET, GAA, 3D ICs, chiplets, ML-for-EDA
- Use the arXiv API to bulk-download by topic

### IEDM (IEEE International Electron Devices Meeting)
- **URL:** [https://www.ieee-iedm.org/](https://www.ieee-iedm.org/)
- Gold standard for new process node announcements (IBM 2nm, Intel 18A papers)
- Many older papers are open access via IEEE Xplore

### ACM/IEEE DAC, ICCAD, DATE Conference Papers
- **URL:** [https://dl.acm.org/conference/dac](https://dl.acm.org/conference/dac)
- EDA-focused papers — placement, routing, timing, power optimization

### OpenROAD Project Documentation
- **URL:** [https://openroad.readthedocs.io/](https://openroad.readthedocs.io/)
- **GitHub:** [https://github.com/The-OpenROAD-Project/OpenROAD](https://github.com/The-OpenROAD-Project/OpenROAD)
- Full RTL-to-GDS flow documentation — excellent for EDA tool usage RAG

### NPTEL IIT VLSI Lecture Notes (Free)
- **URL:** [https://nptel.ac.in/courses/117105080](https://nptel.ac.in/courses/117105080)
- Free IIT professor course notes — device physics through digital design

### MIT OpenCourseWare 6.004 — Computation Structures
- **URL:** [https://ocw.mit.edu/courses/6-004-computation-structures-spring-2017/](https://ocw.mit.edu/courses/6-004-computation-structures-spring-2017/)

---

## 5. Step 1: Data Collection & Ingestion

### 5.1 PDF Books Ingestion

```python
# Install dependencies
pip install pypdf2 pdfplumber langchain sentence-transformers qdrant-client

# Parse PDFs
import pdfplumber

def extract_pdf_text(pdf_path: str) -> list[dict]:
    chunks = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text and len(text.strip()) > 50:
                chunks.append({
                    "text": text,
                    "source": pdf_path,
                    "page": page_num + 1,
                    "type": "book"
                })
    return chunks
```

### 5.2 PDK Documentation Ingestion

PDKs contain a mix of:
- **PDF docs** (DRM, user guides) → parse with pdfplumber
- **Markdown/RST files** (readthedocs) → parse directly
- **SPICE model files** (.lib, .sp) → treat as structured text, chunk by subcircuit
- **Liberty timing files** (.lib) → chunk by cell name
- **Verilog cell models** (.v) → chunk by module

```python
import os
import glob

def ingest_pdk_directory(pdk_root: str) -> list[dict]:
    chunks = []
    
    # Markdown/RST docs
    for f in glob.glob(f"{pdk_root}/**/*.md", recursive=True):
        with open(f) as fh:
            chunks.append({
                "text": fh.read(), "source": f,
                "type": "pdk_doc", "pdk": os.path.basename(pdk_root)
            })
    
    # SPICE models
    for f in glob.glob(f"{pdk_root}/**/*.spice", recursive=True):
        with open(f) as fh:
            chunks.append({
                "text": fh.read(), "source": f,
                "type": "pdk_spice", "pdk": os.path.basename(pdk_root)
            })
    
    # Liberty files
    for f in glob.glob(f"{pdk_root}/**/*.lib", recursive=True):
        with open(f) as fh:
            chunks.append({
                "text": fh.read(), "source": f,
                "type": "pdk_liberty", "pdk": os.path.basename(pdk_root)
            })
    
    return chunks
```

### 5.3 arXiv Paper Ingestion

```python
import arxiv

def fetch_vlsi_papers(max_results: int = 500) -> list[dict]:
    queries = [
        "FinFET VLSI design", "GAA transistor 2nm",
        "static timing analysis", "physical design automation",
        "RTL synthesis EDA", "low power VLSI"
    ]
    client = arxiv.Client()
    papers = []
    for query in queries:
        search = arxiv.Search(
            query=query, max_results=max_results,
            categories=["cs.AR", "cs.ET", "eess.SP"]
        )
        for result in client.results(search):
            papers.append({
                "text": result.summary,
                "title": result.title,
                "source": result.entry_id,
                "type": "paper",
                "year": result.published.year
            })
    return papers
```

---

## 6. Step 2: Chunking Strategy

**Critical:** Do NOT chunk by fixed token count alone. VLSI knowledge has natural boundaries.

```python
from langchain.text_splitter import RecursiveCharacterTextSplitter

# Different chunk sizes per content type
CHUNK_CONFIG = {
    "book":         {"chunk_size": 600,  "overlap": 100},  # Paragraph-level
    "pdk_doc":      {"chunk_size": 400,  "overlap": 80},   # Rule-level
    "pdk_spice":    {"chunk_size": 800,  "overlap": 0},    # Subcircuit intact
    "pdk_liberty":  {"chunk_size": 500,  "overlap": 0},    # Cell intact
    "paper":        {"chunk_size": 500,  "overlap": 100},  # Abstract-level
}

def smart_chunk(doc: dict) -> list[dict]:
    cfg = CHUNK_CONFIG.get(doc["type"], {"chunk_size": 500, "overlap": 100})
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=cfg["chunk_size"],
        chunk_overlap=cfg["overlap"],
        separators=["\n\n", "\n", ".", " "]
    )
    splits = splitter.split_text(doc["text"])
    return [
        {
            "text": split,
            "metadata": {
                "source": doc["source"],
                "type": doc["type"],
                "domain": classify_domain(split),   # See below
                "node_applicability": classify_node(split),
                "page": doc.get("page"),
                "pdk": doc.get("pdk"),
            }
        }
        for split in splits
    ]

def classify_domain(text: str) -> str:
    """Tag each chunk with its VLSI domain for filtered retrieval."""
    keywords = {
        "device_physics": ["FinFET", "MOSFET", "threshold voltage", "DIBL", "leakage", "bandgap", "BSIM"],
        "timing": ["setup time", "hold time", "slack", "STA", "clock skew", "OCV", "CPPR"],
        "power": ["dynamic power", "leakage power", "clock gating", "DVFS", "power gating"],
        "physical_design": ["floorplan", "placement", "routing", "DRC", "LVS", "CTS"],
        "rtl": ["always", "module", "assign", "SystemVerilog", "Verilog", "RTL"],
        "verification": ["UVM", "assertion", "SVA", "testbench", "coverage", "simulation"],
        "analog": ["op-amp", "OTA", "ADC", "DAC", "PLL", "noise", "gain margin"],
    }
    text_lower = text.lower()
    for domain, kws in keywords.items():
        if any(kw.lower() in text_lower for kw in kws):
            return domain
    return "general"

def classify_node(text: str) -> str:
    """Tag applicable process node for filtered retrieval."""
    for node in ["2nm", "3nm", "5nm", "7nm", "10nm", "14nm", "28nm", "45nm", "130nm", "180nm"]:
        if node in text:
            return node
    return "general"
```

---

## 7. Step 3: Embedding & Vector Store

### Embedding Model Selection

| Model | Strengths | Best For |
|-------|-----------|---------|
| `text-embedding-3-large` (OpenAI) | Best semantic quality | Production, high accuracy |
| `nomic-embed-text` | Open-source, fast | Budget, local deployment |
| `BAAI/bge-large-en-v1.5` | Strong technical text | HuggingFace, free |
| `sentence-transformers/all-MiniLM-L6-v2` | Lightweight | Quick prototyping |

**Recommended for VLSI RAG:** `text-embedding-3-large` for production; `BAAI/bge-large-en-v1.5` for open-source deployment.

```python
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from openai import OpenAI
import uuid

client = QdrantClient(path="./vlsi_rag_db")  # Local disk storage
openai_client = OpenAI()

# Create collection with metadata payload
client.create_collection(
    collection_name="vlsi_knowledge",
    vectors_config=VectorParams(size=3072, distance=Distance.COSINE),
)

def embed_and_store(chunks: list[dict]):
    for chunk in chunks:
        response = openai_client.embeddings.create(
            input=chunk["text"],
            model="text-embedding-3-large"
        )
        embedding = response.data[0].embedding
        
        client.upsert(
            collection_name="vlsi_knowledge",
            points=[PointStruct(
                id=str(uuid.uuid4()),
                vector=embedding,
                payload={
                    "text": chunk["text"],
                    **chunk["metadata"]
                }
            )]
        )
```

---

## 8. Step 4: Retrieval Agent Design

Use **domain-filtered retrieval** — don't search the whole database for every query. Route to the right sub-domain first.

```python
from qdrant_client.models import Filter, FieldCondition, MatchValue

def retrieve(query: str, top_k: int = 8, domain_filter: str = None) -> list[dict]:
    # Embed the query
    response = openai_client.embeddings.create(
        input=query, model="text-embedding-3-large"
    )
    query_vec = response.data[0].embedding
    
    # Build optional domain filter
    search_filter = None
    if domain_filter:
        search_filter = Filter(
            must=[FieldCondition(
                key="domain",
                match=MatchValue(value=domain_filter)
            )]
        )
    
    # Retrieve top-K chunks
    results = client.search(
        collection_name="vlsi_knowledge",
        query_vector=query_vec,
        limit=top_k,
        query_filter=search_filter,
        with_payload=True
    )
    
    return [
        {
            "text": r.payload["text"],
            "source": r.payload["source"],
            "type": r.payload["type"],
            "score": r.score,
            "domain": r.payload.get("domain"),
        }
        for r in results
    ]

# Domain router — classify query before retrieval
def route_query(query: str) -> str:
    routing_map = {
        "timing": ["setup", "hold", "slack", "STA", "clock", "timing"],
        "device_physics": ["FinFET", "GAA", "threshold", "DIBL", "leakage", "transistor"],
        "physical_design": ["floorplan", "placement", "routing", "DRC", "via", "metal"],
        "rtl": ["Verilog", "SystemVerilog", "RTL", "always", "module", "synthesize"],
        "verification": ["UVM", "testbench", "assertion", "SVA", "coverage"],
        "analog": ["op-amp", "ADC", "PLL", "noise", "bandwidth", "gain"],
        "power": ["power", "leakage", "clock gating", "DVFS", "energy"],
    }
    query_lower = query.lower()
    for domain, keywords in routing_map.items():
        if any(kw.lower() in query_lower for kw in keywords):
            return domain
    return None  # No filter — search everything
```

---

## 9. Step 5: Reasoning Layer (LLM Selection)

### Model Comparison for VLSI RAG

| Model | Intelligence Score | Context Window | Cost (Input/Output) | Best For |
|-------|------------------|----------------|---------------------|---------|
| GPT-5.4 (xhigh) | 57/60 | 1,000,000 tokens | $2.50 / $15.00 per 1M | Complex multi-document reasoning, large context |
| GPT-5.4 Thinking | 57/60 | 1,000,000 tokens | $2.50 / $15.00 per 1M | Deep synthesis across many VLSI papers |
| Kimi K2.6 | 54/60 | 128,000 tokens | $0.47 / $2.00 per 1M | High volume queries, cost-sensitive batch runs |
| Claude Opus 4.7 | 57/60 | 200,000 tokens | $15.00 / $75.00 per 1M | Long document analysis, nuanced reasoning |

**Recommended strategy:**
- Use **Kimi K2.6** for first-pass retrieval scoring and chunk ranking (cheap, fast)
- Use **GPT-5.4 Thinking** for final synthesis when the query is complex or multi-domain

```python
from openai import OpenAI

def synthesize(query: str, retrieved_chunks: list[dict]) -> str:
    # Build context from retrieved chunks
    context = "\n\n---\n\n".join([
        f"[Source: {c['source']}, Domain: {c['domain']}]\n{c['text']}"
        for c in retrieved_chunks
    ])
    
    system_prompt = """You are a VLSI chip design expert assistant. 
    Answer questions using ONLY the provided context from VLSI textbooks, 
    research papers, and open PDK documentation.
    
    Rules:
    1. Every claim must cite its source chunk (use [Source: ...])
    2. If the context does not support an answer, say: 
       "This information is not in the available public documentation. 
        It may require proprietary foundry data (NDA-protected)."
    3. Do NOT hallucinate fab-specific values not present in the context.
    4. Distinguish between predictive/academic PDK data (ASAP7, FreePDK45) 
       and real manufacturable data (SKY130, GF180).
    """
    
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-5.4",  # or "moonshotai/kimi-k2.6" via OpenRouter
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"}
        ],
        reasoning_effort="high"  # "high" for complex queries, "low" for simple
    )
    return response.choices[0].message.content
```

---

## 10. Step 6: Anti-Hallucination Guardrails

This is the most critical layer — VLSI misinformation can waste months of design time.

```python
def grounded_answer(query: str) -> dict:
    # Step 1: Route and retrieve
    domain = route_query(query)
    chunks = retrieve(query, top_k=8, domain_filter=domain)
    
    # Step 2: Check if we have enough evidence
    if not chunks or max(c["score"] for c in chunks) < 0.70:
        return {
            "answer": "⚠️ Insufficient evidence in the knowledge base. "
                      "This query may require proprietary foundry data.",
            "sources": [],
            "confidence": "low"
        }
    
    # Step 3: Synthesize with LLM
    answer = synthesize(query, chunks)
    
    # Step 4: Check if answer cites sources (basic hallucination check)
    has_citations = "[Source:" in answer
    
    return {
        "answer": answer,
        "sources": [c["source"] for c in chunks],
        "confidence": "high" if has_citations else "medium",
        "domain": domain,
        "node_filters": list(set(c.get("node_applicability", "general") for c in chunks))
    }
```

### Guardrail Categories

| Query Type | System Behavior |
|-----------|-----------------|
| Covered by books/papers | Full answer with citations |
| Covered by open PDK | Answer + note if predictive vs. real fab |
| Requires proprietary PDK | Explicit "NDA-protected — not available" response |
| Ambiguous node | Ask user to specify: SKY130 / GF180 / ASAP7 / FreePDK45 |
| Fabrication-specific DRC | Point to open PDK DRM; note limitations for real nodes |

---

## 11. Step 7: AgentIC Integration

Your existing `src/agentic` architecture maps naturally to this system.

### Agent Roles in AgentIC

```
src/agentic/
├── agents/
│   ├── vlsi_retrieval_agent.py    ← Query vector store, apply domain filters
│   ├── vlsi_synthesis_agent.py    ← Call GPT-5.4/Kimi, enforce citations
│   ├── pdk_agent.py               ← Specialized PDK rules queries
│   └── paper_search_agent.py      ← arXiv + IEEE search for latest papers
├── tools/
│   ├── qdrant_tool.py             ← Vector store search tool
│   ├── arxiv_tool.py              ← Live paper fetching tool
│   └── pdk_parser_tool.py         ← Parse PDK files on demand
└── orchestrator.py                ← Route queries, manage agent handoffs
```

### Orchestrator Logic

```python
# In orchestrator.py — add VLSI routing logic
VLSI_DOMAINS = ["device_physics", "timing", "power", "rtl", "verification", "analog", "physical_design"]

def handle_vlsi_query(query: str):
    domain = route_query(query)
    
    if domain == "rtl":
        # Use retrieval_agent + synthesis_agent
        return pipeline(vlsi_retrieval_agent, vlsi_synthesis_agent, query)
    
    elif domain == "device_physics":
        # Use book-focused retrieval (ASAP7 DRM + Taur & Ning)
        return pipeline(pdk_agent, vlsi_synthesis_agent, query)
    
    elif "latest" in query.lower() or "2024" in query.lower() or "2025" in query.lower():
        # Fetch fresh papers from arXiv first
        return pipeline(paper_search_agent, vlsi_synthesis_agent, query)
    
    else:
        # General VLSI — full database search
        return grounded_answer(query)
```

---

## 12. Open PDK Reference Table

| PDK | Node | Type | License | Manufacturable | GitHub |
|-----|------|------|---------|---------------|--------|
| SKY130 | 130nm | Bulk CMOS | Apache 2.0 | ✅ Yes (SkyWater) | [google/skywater-pdk](https://github.com/google/skywater-pdk) |
| GF180MCU | 180nm | Bulk CMOS | Apache 2.0 | ✅ Yes (GlobalFoundries) | [google/gf180mcu-pdk](https://github.com/google/gf180mcu-pdk) |
| ASAP7 | 7nm | FinFET (Predictive) | BSD-3 | ❌ Academic only | [The-OpenROAD-Project/asap7](https://github.com/The-OpenROAD-Project/asap7) |
| FreePDK45 | 45nm | Bulk CMOS (Predictive) | Apache 2.0 | ❌ Academic only | [mflowgen/freepdk-45nm](https://github.com/mflowgen/freepdk-45nm) |
| Open PDKs installer | 130+180nm | Both | Apache 2.0 | ✅ (via Sky130/GF180) | [RTimothyEdwards/open_pdks](https://github.com/RTimothyEdwards/open_pdks) |

---

## 13. What This System Can & Cannot Do

### ✅ Can Do (Public Knowledge Layer)
- Explain FinFET device physics — DIBL, short-channel effects, velocity saturation
- Generate Verilog/SystemVerilog RTL for standard components
- Answer timing questions (setup/hold, OCV, multi-cycle paths)
- Explain DRC rules for SKY130 and GF180 (open PDK data)
- Discuss ASAP7 7nm design rules and SPICE models (predictive)
- Explain standard cell design, power grid planning, clock tree synthesis theory
- Write UVM testbenches and SVA assertions
- Compare architectures (RISC-V, ARM Cortex-M) at RTL level

### ❌ Cannot Do (Proprietary Wall — Same as a Human Without NDA)
- TSMC N2/N3/N5 exact DRC rules (proprietary)
- Samsung 3GAE/4nm metal stack specifics (proprietary)
- Intel 18A/20A RibbonFET exact device parameters (proprietary)
- Any fab's real Process Design Kits requiring NDA
- Calibre DRC/LVS deck specifics for production fabs
- Exact yield optimization parameters for production nodes

**This is not a bug — it is the honest boundary, the same boundary any human engineer has before foundry access.**

---

## 14. Roadmap & Next Steps

### Phase 1: Foundation (Weeks 1–4)
- [ ] Clone all 4 open PDKs (SKY130, GF180, ASAP7, FreePDK45)
- [ ] Collect PDF versions of Tier 1 and Tier 2 books
- [ ] Build PDF ingestion pipeline with pdfplumber
- [ ] Set up Qdrant local instance and embed first 10,000 chunks
- [ ] Test basic retrieval on 20 VLSI questions

### Phase 2: Domain Routing (Weeks 5–8)
- [ ] Implement domain classifier (`classify_domain`)
- [ ] Add node-aware filtering (`classify_node`)
- [ ] Build domain-specific retrieval agents in AgentIC
- [ ] Integrate arXiv live paper fetching tool
- [ ] Evaluate retrieval quality with VLSI expert review

### Phase 3: Synthesis & Guardrails (Weeks 9–12)
- [ ] Integrate GPT-5.4 Thinking for synthesis
- [ ] Implement citation enforcement in prompts
- [ ] Build confidence scoring and proprietary-boundary detector
- [ ] Add Kimi K2.6 as cost-efficient first-pass model
- [ ] A/B test GPT-5.4 vs Kimi on 100 VLSI benchmark questions

### Phase 4: AgentIC Full Integration (Weeks 13–16)
- [ ] Wire all agents through AgentIC orchestrator
- [ ] Add CLI interface for VLSI queries
- [ ] Build web interface (optional)
- [ ] Open-source the pipeline on GitHub
- [ ] Submit a paper to arXiv cs.AR

---

*Built on the insight that LLMs can learn from public VLSI books and open PDKs the same way human engineers do — reaching the same knowledge boundary as an engineer before fab NDA access, which is still enormously valuable.*

