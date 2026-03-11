"""
CDC Analyzer — Phase 4 of the Spec Pipeline
=============================================

Receives a feasibility-checked hardware specification and identifies every
signal that crosses a clock domain boundary.  For each crossing it assigns
an exact synchronization strategy and generates CDC sub-module specifications
that the RTL generator must instantiate.

A missed CDC is the hardest class of silicon bug to debug — it may pass
simulation and fail only on real silicon under specific timing conditions.

Pipeline Steps:
  1. IDENTIFY CLOCK DOMAINS  — Extract all distinct clock domains
  2. IDENTIFY CROSSING SIGNALS — Enumerate every cross-domain signal
  3. ASSIGN SYNCHRONIZATION STRATEGY — One strategy per crossing
  4. GENERATE CDC SUBMODULES — Submodule specs for the RTL generator
  5. OUTPUT — Enriched spec with CDC annotations
"""

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ─── Synchronization Strategy Constants ──────────────────────────────

SYNC_SINGLE_BIT = "SINGLE_BIT_SYNC"
SYNC_PULSE = "PULSE_SYNC"
SYNC_ASYNC_FIFO = "ASYNC_FIFO"
SYNC_HANDSHAKE = "HANDSHAKE"
SYNC_RESET = "RESET_SYNC"
SYNC_QUASI_STATIC = "QUASI_STATIC"
SYNC_UNRESOLVED = "CDC_UNRESOLVED"

# Minimum FIFO depth for async FIFOs
MIN_FIFO_DEPTH = 4
DEFAULT_FIFO_DEPTH = 8

# Synchronizer depth thresholds
DEFAULT_SYNC_DEPTH = 2
HIGH_FREQ_RATIO_THRESHOLD = 4  # use 3-flop sync when ratio > 4:1


# ─── Signal Type Detection Patterns ─────────────────────────────────

_RESET_PATTERNS = re.compile(
    r"\brst|reset|rstn|rst_n|arst|areset\b", re.IGNORECASE
)
_HANDSHAKE_PATTERNS = re.compile(
    r"\breq|ack|valid|ready|grant|request|acknowledge\b", re.IGNORECASE
)
_BUS_WIDTH_PATTERN = re.compile(
    r"\[(\d+)\s*:\s*(\d+)\]"
)
_DATA_BUS_PATTERNS = re.compile(
    r"\bdata|wdata|rdata|din|dout|payload|fifo_data|wr_data|rd_data\b",
    re.IGNORECASE,
)
_CLOCK_PATTERNS = re.compile(
    r"\bclk|clock|pclk|hclk|fclk|aclk|sclk|mclk|bclk|clk_\w+\b",
    re.IGNORECASE,
)
_ENABLE_FLAG_PATTERNS = re.compile(
    r"\ben|enable|flag|strobe|pulse|irq|interrupt|trigger|start|done|busy|"
    r"empty|full|overflow|underflow|valid|error\b",
    re.IGNORECASE,
)
_CONFIG_PATTERNS = re.compile(
    r"\bcfg|config|mode|ctrl|control|param|setting|threshold\b",
    re.IGNORECASE,
)

# Clock source keywords for domain identification
_CLK_DIVIDER_PATTERNS = re.compile(
    r"divid|prescal|div_by|divided|half_clk|clk_div", re.IGNORECASE
)
_CLK_GATE_PATTERNS = re.compile(
    r"gate|gated|clock_gate|clk_gate|cg_|icg", re.IGNORECASE
)


# ─── Output Dataclasses ─────────────────────────────────────────────

@dataclass
class ClockDomain:
    """A distinct clock domain in the design."""
    domain_name: str
    source_clock_signal: str
    nominal_frequency_mhz: float = 0.0
    is_derived: bool = False          # True if divided/gated from another
    parent_domain: str = ""           # Name of parent domain if derived
    derivation_type: str = ""         # "divided" | "gated" | ""
    submodules: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CrossingSignal:
    """A signal that crosses between two clock domains."""
    signal_name: str
    source_domain: str
    destination_domain: str
    signal_type: str      # "single_bit_control" | "multi_bit_data" | "bus" |
                          # "handshake" | "reset"
    bit_width: int = 1
    direction: str = "unidirectional"  # "unidirectional" | "bidirectional"
    sync_strategy: str = ""
    sync_details: Dict[str, Any] = field(default_factory=dict)
    unresolved_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CDCSubmodule:
    """A synchronization submodule that must be instantiated in the RTL."""
    module_name: str
    strategy: str
    ports: List[Dict[str, str]] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    behavior: str = ""
    source_domain: str = ""
    destination_domain: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CDCResult:
    """Output of the CDCAnalyzer."""
    cdc_status: str  # "SINGLE_DOMAIN" | "MULTI_DOMAIN" | "UNRESOLVED"
    clock_domains: List[ClockDomain] = field(default_factory=list)
    crossing_signals: List[CrossingSignal] = field(default_factory=list)
    cdc_submodules_added: List[CDCSubmodule] = field(default_factory=list)
    cdc_warnings: List[str] = field(default_factory=list)
    cdc_unresolved: List[str] = field(default_factory=list)
    domain_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cdc_status": self.cdc_status,
            "clock_domains": [d.to_dict() for d in self.clock_domains],
            "crossing_signals": [c.to_dict() for c in self.crossing_signals],
            "cdc_submodules_added": [s.to_dict() for s in self.cdc_submodules_added],
            "cdc_warnings": list(self.cdc_warnings),
            "cdc_unresolved": list(self.cdc_unresolved),
            "domain_count": self.domain_count,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


# ─── Main Class ──────────────────────────────────────────────────────

class CDCAnalyzer:
    """
    Identifies every clock domain crossing in a hardware specification and
    assigns synchronization strategies before RTL generation.

    Input:  HardwareSpec dict (+ optional hierarchy/feasibility data)
    Output: CDCResult with domains, crossings, sync submodules, and warnings
    """

    def analyze(
        self,
        hw_spec_dict: Dict[str, Any],
        hierarchy_result_dict: Optional[Dict[str, Any]] = None,
    ) -> CDCResult:
        """
        Run full CDC analysis on the spec.

        Args:
            hw_spec_dict: HardwareSpec.to_dict() output.
            hierarchy_result_dict: Optional HierarchyResult.to_dict() for
                expanded submodule clock information.

        Returns:
            CDCResult with full CDC analysis.
        """
        # Step 1: Identify all clock domains
        domains = self._identify_clock_domains(hw_spec_dict, hierarchy_result_dict)

        if len(domains) <= 1:
            return CDCResult(
                cdc_status="SINGLE_DOMAIN",
                clock_domains=domains,
                domain_count=len(domains),
                cdc_warnings=["No CDC analysis required — single clock domain."]
                if domains else [],
            )

        # Build domain relationship map
        domain_map = {d.domain_name: d for d in domains}
        async_pairs = self._find_async_domain_pairs(domains)

        # Step 2: Identify all crossing signals
        crossings = self._identify_crossing_signals(
            hw_spec_dict, hierarchy_result_dict, domains, async_pairs
        )

        if not crossings:
            return CDCResult(
                cdc_status="MULTI_DOMAIN",
                clock_domains=domains,
                domain_count=len(domains),
                cdc_warnings=[
                    f"Design has {len(domains)} clock domains but no "
                    f"cross-domain signals detected. Verify domain isolation."
                ],
            )

        # Step 3: Assign synchronization strategy to each crossing
        warnings: List[str] = []
        unresolved: List[str] = []
        for crossing in crossings:
            self._assign_sync_strategy(crossing, domain_map, warnings, unresolved)

        # Step 4: Generate CDC submodules
        submodules = self._generate_cdc_submodules(crossings)

        # Determine overall status
        if unresolved:
            status = "UNRESOLVED"
            warnings.append(
                f"CDC analysis has {len(unresolved)} unresolved crossing(s). "
                f"RTL generation should not proceed until these are resolved."
            )
        else:
            status = "MULTI_DOMAIN"

        return CDCResult(
            cdc_status=status,
            clock_domains=domains,
            crossing_signals=crossings,
            cdc_submodules_added=submodules,
            cdc_warnings=warnings,
            cdc_unresolved=unresolved,
            domain_count=len(domains),
        )

    # ── Step 1: Identify Clock Domains ───────────────────────────────

    def _identify_clock_domains(
        self,
        hw_spec_dict: Dict[str, Any],
        hierarchy_result_dict: Optional[Dict[str, Any]],
    ) -> List[ClockDomain]:
        """Extract every distinct clock domain from the spec."""
        domains: List[ClockDomain] = []
        seen_clocks: Set[str] = set()

        target_freq = hw_spec_dict.get("target_frequency_mhz", 50) or 50

        # 1a. Scan top-level ports for clock signals
        top_ports = hw_spec_dict.get("ports", [])
        for port in top_ports:
            pname = port.get("name", "")
            if _CLOCK_PATTERNS.search(pname) and pname.lower() not in seen_clocks:
                seen_clocks.add(pname.lower())
                domains.append(ClockDomain(
                    domain_name=self._clock_to_domain_name(pname),
                    source_clock_signal=pname,
                    nominal_frequency_mhz=target_freq,
                ))

        # 1b. Scan submodule ports for additional clock signals
        all_submodules = self._collect_all_submodules(
            hw_spec_dict, hierarchy_result_dict
        )
        for sm in all_submodules:
            sm_ports = sm.get("ports", [])
            sm_name = sm.get("name", "")
            sm_desc = f"{sm_name} {sm.get('description', '')}".lower()
            for port in sm_ports:
                pname = port.get("name", "")
                if _CLOCK_PATTERNS.search(pname) and pname.lower() not in seen_clocks:
                    seen_clocks.add(pname.lower())
                    # Try to determine if it's a derived clock
                    is_derived = False
                    parent = ""
                    derivation = ""
                    freq = target_freq

                    if _CLK_DIVIDER_PATTERNS.search(pname) or \
                       _CLK_DIVIDER_PATTERNS.search(sm_desc):
                        is_derived = True
                        derivation = "divided"
                        # Try to extract division factor
                        div_match = re.search(r"div(?:ide)?(?:_by)?_?(\d+)", 
                                              pname + " " + sm_desc, re.IGNORECASE)
                        if div_match:
                            divisor = int(div_match.group(1))
                            freq = target_freq / max(divisor, 1)
                        else:
                            freq = target_freq / 2  # assume /2 if not specified
                        parent = domains[0].domain_name if domains else ""

                    elif _CLK_GATE_PATTERNS.search(pname) or \
                         _CLK_GATE_PATTERNS.search(sm_desc):
                        is_derived = True
                        derivation = "gated"
                        parent = domains[0].domain_name if domains else ""

                    domains.append(ClockDomain(
                        domain_name=self._clock_to_domain_name(pname),
                        source_clock_signal=pname,
                        nominal_frequency_mhz=freq,
                        is_derived=is_derived,
                        parent_domain=parent,
                        derivation_type=derivation,
                    ))

        # 1c. Check spec-level clock_domains field (if the spec generator
        #     explicitly listed them)
        spec_clock_domains = hw_spec_dict.get("clock_domains", [])
        if isinstance(spec_clock_domains, list):
            for cd in spec_clock_domains:
                if isinstance(cd, dict):
                    cd_name = cd.get("name", cd.get("domain", ""))
                    cd_clk = cd.get("clock", cd.get("signal", cd_name))
                    cd_freq = cd.get("frequency_mhz", cd.get("freq", target_freq))
                elif isinstance(cd, str):
                    cd_name = cd
                    cd_clk = cd
                    cd_freq = target_freq
                else:
                    continue
                if cd_clk.lower() not in seen_clocks:
                    seen_clocks.add(cd_clk.lower())
                    domains.append(ClockDomain(
                        domain_name=self._clock_to_domain_name(cd_name),
                        source_clock_signal=cd_clk,
                        nominal_frequency_mhz=cd_freq,
                    ))

        # 1d. Check mandatory_fields_status for clock_domains info
        mfs = hw_spec_dict.get("mandatory_fields_status", {})
        cd_info = mfs.get("clock_domains", {})
        if isinstance(cd_info, dict):
            val = cd_info.get("value", cd_info.get("inferred_value", ""))
            if isinstance(val, str) and val:
                # Try parsing "single domain" or "dual clock" etc.
                multi_match = re.search(r"(\d+)\s*(?:clock|domain)", val, re.IGNORECASE)
                if multi_match and int(multi_match.group(1)) > 1 and len(domains) < 2:
                    # The spec says multiple clocks but we only found one in ports
                    # Add a placeholder second domain
                    domains.append(ClockDomain(
                        domain_name="domain_secondary",
                        source_clock_signal="clk_secondary",
                        nominal_frequency_mhz=target_freq,
                    ))

        # 1e. Assign submodules to domains
        self._assign_submodules_to_domains(domains, all_submodules)

        # If no clock was found at all, assume single domain
        if not domains:
            domains.append(ClockDomain(
                domain_name="domain_clk",
                source_clock_signal="clk",
                nominal_frequency_mhz=target_freq,
            ))

        return domains

    def _clock_to_domain_name(self, clock_signal: str) -> str:
        """Convert a clock signal name to a domain name."""
        name = clock_signal.lower().strip()
        name = re.sub(r"[^a-z0-9_]", "_", name)
        if not name.startswith("domain_"):
            name = f"domain_{name}"
        return name

    def _assign_submodules_to_domains(
        self,
        domains: List[ClockDomain],
        submodules: List[Dict[str, Any]],
    ) -> None:
        """Assign each submodule to its clock domain based on port connections."""
        if not domains:
            return

        primary_domain = domains[0]
        domain_clk_map: Dict[str, ClockDomain] = {}
        for d in domains:
            domain_clk_map[d.source_clock_signal.lower()] = d

        for sm in submodules:
            sm_name = sm.get("name", "")
            sm_ports = sm.get("ports", [])
            assigned = False
            for port in sm_ports:
                pname = port.get("name", "").lower()
                if pname in domain_clk_map:
                    domain_clk_map[pname].submodules.append(sm_name)
                    assigned = True
                    break
            if not assigned:
                # Default: assign to primary domain
                primary_domain.submodules.append(sm_name)

    def _find_async_domain_pairs(
        self, domains: List[ClockDomain]
    ) -> Set[Tuple[str, str]]:
        """Find all pairs of clock domains that are asynchronous to each other."""
        async_pairs: Set[Tuple[str, str]] = set()

        for i, d1 in enumerate(domains):
            for d2 in domains[i + 1:]:
                # Same source = synchronous
                if d1.source_clock_signal == d2.source_clock_signal:
                    continue

                # Divided from same parent = synchronous (but flag gated clocks)
                if (d1.parent_domain and d1.parent_domain == d2.domain_name and
                        d1.derivation_type == "divided"):
                    continue
                if (d2.parent_domain and d2.parent_domain == d1.domain_name and
                        d2.derivation_type == "divided"):
                    continue

                # Gated clock from same source: potentially same domain but
                # may have enable timing issues — flag it
                if (d1.parent_domain == d2.domain_name and
                        d1.derivation_type == "gated"):
                    # Not truly async, but needs care
                    continue
                if (d2.parent_domain == d1.domain_name and
                        d2.derivation_type == "gated"):
                    continue

                # All other pairs are asynchronous
                async_pairs.add((d1.domain_name, d2.domain_name))
                async_pairs.add((d2.domain_name, d1.domain_name))

        return async_pairs

    # ── Step 2: Identify Crossing Signals ────────────────────────────

    def _identify_crossing_signals(
        self,
        hw_spec_dict: Dict[str, Any],
        hierarchy_result_dict: Optional[Dict[str, Any]],
        domains: List[ClockDomain],
        async_pairs: Set[Tuple[str, str]],
    ) -> List[CrossingSignal]:
        """Find all signals that cross between asynchronous clock domains."""
        crossings: List[CrossingSignal] = []
        seen: Set[str] = set()

        if not async_pairs:
            return crossings

        # Build submodule → domain map
        sm_domain_map: Dict[str, str] = {}
        for domain in domains:
            for sm_name in domain.submodules:
                sm_domain_map[sm_name] = domain.domain_name

        all_submodules = self._collect_all_submodules(
            hw_spec_dict, hierarchy_result_dict
        )

        # For each submodule, check if any of its ports connect to a
        # submodule in a different (async) domain
        sm_by_name: Dict[str, Dict[str, Any]] = {}
        for sm in all_submodules:
            sm_by_name[sm.get("name", "")] = sm

        # Strategy: Look for signals that appear in ports of submodules
        # belonging to different async domains.  Also check top-level
        # ports that fan out to multiple domains.
        port_consumers: Dict[str, List[Tuple[str, str]]] = {}
        # port_consumers[signal_name] = [(submodule, domain), ...]

        for sm in all_submodules:
            sm_name = sm.get("name", "")
            sm_domain = sm_domain_map.get(sm_name, domains[0].domain_name)
            for port in sm.get("ports", []):
                pname = port.get("name", "")
                if _CLOCK_PATTERNS.search(pname):
                    continue  # Skip clock signals themselves
                if pname not in port_consumers:
                    port_consumers[pname] = []
                port_consumers[pname].append((sm_name, sm_domain))

        # Find signals consumed by submodules in different async domains
        for sig_name, consumers in port_consumers.items():
            consumer_domains = {dom for _, dom in consumers}
            if len(consumer_domains) < 2:
                continue

            # Check each pair of consuming domains
            domain_list = sorted(consumer_domains)
            for i, d1 in enumerate(domain_list):
                for d2 in domain_list[i + 1:]:
                    if (d1, d2) in async_pairs:
                        key = f"{sig_name}:{d1}->{d2}"
                        if key in seen:
                            continue
                        seen.add(key)
                        seen.add(f"{sig_name}:{d2}->{d1}")

                        # Determine signal type and width
                        sig_type, bit_width = self._classify_signal(
                            sig_name, port_consumers[sig_name], sm_by_name
                        )

                        crossings.append(CrossingSignal(
                            signal_name=sig_name,
                            source_domain=d1,
                            destination_domain=d2,
                            signal_type=sig_type,
                            bit_width=bit_width,
                            direction="unidirectional",
                        ))

        # Also check for reset signals crossing domains
        self._check_reset_crossings(
            domains, async_pairs, hw_spec_dict, crossings, seen
        )

        # Check for inter-domain handshake pairs
        self._check_handshake_crossings(
            domains, async_pairs, all_submodules, sm_domain_map, crossings, seen
        )

        return crossings

    def _classify_signal(
        self,
        signal_name: str,
        consumers: List[Tuple[str, str]],
        sm_by_name: Dict[str, Dict[str, Any]],
    ) -> Tuple[str, int]:
        """Classify a signal as single_bit_control, multi_bit_data, bus, handshake, or reset."""
        name_lower = signal_name.lower()

        # Reset?
        if _RESET_PATTERNS.search(name_lower):
            return "reset", 1

        # Check port data type for width
        bit_width = 1
        for sm_name, _ in consumers:
            sm = sm_by_name.get(sm_name, {})
            for port in sm.get("ports", []):
                if port.get("name", "") == signal_name:
                    dtype = port.get("data_type", "")
                    width_match = _BUS_WIDTH_PATTERN.search(dtype)
                    if width_match:
                        hi = int(width_match.group(1))
                        lo = int(width_match.group(2))
                        bit_width = abs(hi - lo) + 1
                    break
            if bit_width > 1:
                break

        # Handshake?
        if _HANDSHAKE_PATTERNS.search(name_lower):
            if bit_width == 1:
                return "handshake", 1
            return "handshake", bit_width

        # Multi-bit data or bus?
        if bit_width > 1:
            if _DATA_BUS_PATTERNS.search(name_lower):
                return "multi_bit_data", bit_width
            return "bus", bit_width

        # Single-bit control/flag/enable
        if _ENABLE_FLAG_PATTERNS.search(name_lower):
            return "single_bit_control", 1

        # Default: single-bit control
        return "single_bit_control", 1

    def _check_reset_crossings(
        self,
        domains: List[ClockDomain],
        async_pairs: Set[Tuple[str, str]],
        hw_spec_dict: Dict[str, Any],
        crossings: List[CrossingSignal],
        seen: Set[str],
    ) -> None:
        """Ensure every reset signal crossing a domain boundary is captured."""
        top_ports = hw_spec_dict.get("ports", [])
        reset_signals = [
            p.get("name", "") for p in top_ports
            if _RESET_PATTERNS.search(p.get("name", ""))
        ]

        if not reset_signals:
            return

        # Every reset that enters the chip must be synchronized to each
        # async domain it serves
        primary_domain = domains[0].domain_name if domains else ""
        for rst_sig in reset_signals:
            for domain in domains:
                if domain.domain_name == primary_domain:
                    continue
                if (primary_domain, domain.domain_name) not in async_pairs:
                    continue
                key = f"{rst_sig}:{primary_domain}->{domain.domain_name}"
                if key in seen:
                    continue
                seen.add(key)
                crossings.append(CrossingSignal(
                    signal_name=rst_sig,
                    source_domain=primary_domain,
                    destination_domain=domain.domain_name,
                    signal_type="reset",
                    bit_width=1,
                    direction="unidirectional",
                ))

    def _check_handshake_crossings(
        self,
        domains: List[ClockDomain],
        async_pairs: Set[Tuple[str, str]],
        submodules: List[Dict[str, Any]],
        sm_domain_map: Dict[str, str],
        crossings: List[CrossingSignal],
        seen: Set[str],
    ) -> None:
        """Detect req/ack handshake pairs that span domains."""
        # Look for paired req/ack or valid/ready signals
        req_ack_pairs = []
        for sm in submodules:
            sm_name = sm.get("name", "")
            ports = sm.get("ports", [])
            port_names = [p.get("name", "") for p in ports]
            for pn in port_names:
                pn_lower = pn.lower()
                # Find req→ack pairs
                if "req" in pn_lower:
                    ack_name = pn_lower.replace("req", "ack")
                    for pn2 in port_names:
                        if pn2.lower() == ack_name:
                            req_ack_pairs.append((pn, pn2, sm_name))
                # Find valid→ready pairs
                if "valid" in pn_lower:
                    ready_name = pn_lower.replace("valid", "ready")
                    for pn2 in port_names:
                        if pn2.lower() == ready_name:
                            req_ack_pairs.append((pn, pn2, sm_name))

        # Mark bidirectional if req/ack span domains
        for req_sig, ack_sig, sm_name in req_ack_pairs:
            sm_dom = sm_domain_map.get(sm_name, "")
            for domain in domains:
                if domain.domain_name == sm_dom:
                    continue
                if (sm_dom, domain.domain_name) in async_pairs:
                    key = f"{req_sig}:{sm_dom}->{domain.domain_name}"
                    if key not in seen:
                        seen.add(key)
                        crossings.append(CrossingSignal(
                            signal_name=f"{req_sig}/{ack_sig}",
                            source_domain=sm_dom,
                            destination_domain=domain.domain_name,
                            signal_type="handshake",
                            bit_width=1,
                            direction="bidirectional",
                        ))

    # ── Step 3: Assign Synchronization Strategy ──────────────────────

    def _assign_sync_strategy(
        self,
        crossing: CrossingSignal,
        domain_map: Dict[str, ClockDomain],
        warnings: List[str],
        unresolved: List[str],
    ) -> None:
        """Assign the correct synchronization strategy to a crossing signal."""
        src_domain = domain_map.get(crossing.source_domain)
        dst_domain = domain_map.get(crossing.destination_domain)

        # Calculate frequency ratio for synchronizer depth decisions
        src_freq = src_domain.nominal_frequency_mhz if src_domain else 50
        dst_freq = dst_domain.nominal_frequency_mhz if dst_domain else 50
        freq_ratio = max(src_freq, dst_freq) / max(min(src_freq, dst_freq), 1)
        sync_depth = 3 if freq_ratio > HIGH_FREQ_RATIO_THRESHOLD else 2

        sig_type = crossing.signal_type
        bit_width = crossing.bit_width
        sig_name = crossing.signal_name

        # ── RESET signals: ALWAYS use RESET_SYNC ────────────────────
        if sig_type == "reset":
            crossing.sync_strategy = SYNC_RESET
            crossing.sync_details = {
                "origin_domain": crossing.source_domain,
                "target_domain": crossing.destination_domain,
                "behavior": "Asynchronous assert, synchronous deassert",
                "sync_depth": sync_depth,
            }
            return

        # ── MULTI-BIT data/bus: NEVER use 2-flop sync ───────────────
        if bit_width > 1 and sig_type in ("multi_bit_data", "bus"):
            crossing.sync_strategy = SYNC_ASYNC_FIFO
            fifo_depth = DEFAULT_FIFO_DEPTH
            if freq_ratio > 4:
                fifo_depth = 16  # deeper FIFO for large frequency ratios
            ptr_width = self._gray_pointer_width(fifo_depth)
            crossing.sync_details = {
                "data_width": bit_width,
                "fifo_depth": fifo_depth,
                "gray_pointer_width": ptr_width,
                "behavior": (
                    "Gray-coded read/write pointers, pointer sync via "
                    f"{sync_depth}-flop synchronizers, no combinational "
                    "paths between clock domains"
                ),
            }
            warnings.append(
                f"CDC: Signal '{sig_name}' ({bit_width}-bit) crosses from "
                f"{crossing.source_domain} to {crossing.destination_domain}. "
                f"Assigned ASYNC_FIFO (depth={fifo_depth})."
            )
            return

        # ── HANDSHAKE signals ────────────────────────────────────────
        if sig_type == "handshake":
            if bit_width > 1:
                # Multi-bit handshake: use HANDSHAKE protocol
                crossing.sync_strategy = SYNC_HANDSHAKE
                crossing.sync_details = {
                    "req_signal": f"{sig_name}_req",
                    "ack_signal": f"{sig_name}_ack",
                    "data_width": bit_width,
                    "behavior": (
                        "4-phase handshake: req asserted with stable data, "
                        "ack confirms receipt, req deasserted, ack deasserted"
                    ),
                }
                return
            else:
                # Single-bit handshake control: 2-flop sync is sufficient
                # if it's a level signal (req/ack/valid/ready)
                crossing.sync_strategy = SYNC_SINGLE_BIT
                crossing.sync_details = {
                    "sync_depth": sync_depth,
                    "dont_touch": True,
                    "behavior": (
                        f"{sync_depth}-stage synchronizer, both flops have "
                        "dont_touch attribute for synthesis"
                    ),
                }
                return

        # ── Single-bit control/enable/flag ───────────────────────────
        if bit_width == 1 and sig_type == "single_bit_control":
            # Check if this might be a pulse shorter than dst clock period
            is_likely_pulse = any(
                kw in sig_name.lower()
                for kw in ["pulse", "strobe", "trigger", "irq", "interrupt"]
            )

            if is_likely_pulse and src_freq > dst_freq:
                # Source is faster: pulse may be shorter than dst period
                crossing.sync_strategy = SYNC_PULSE
                crossing.sync_details = {
                    "behavior": (
                        "Toggle flip-flop in source domain, "
                        f"{sync_depth}-flop synchronizer in destination domain, "
                        "edge detector to regenerate pulse"
                    ),
                    "sync_depth": sync_depth,
                }
                warnings.append(
                    f"CDC: Signal '{sig_name}' is a likely pulse crossing from "
                    f"fast ({src_freq} MHz) to slow ({dst_freq} MHz) domain. "
                    f"Using PULSE_SYNC to avoid missing it."
                )
                return
            else:
                crossing.sync_strategy = SYNC_SINGLE_BIT
                crossing.sync_details = {
                    "sync_depth": sync_depth,
                    "dont_touch": True,
                    "behavior": (
                        f"{sync_depth}-stage synchronizer, both flops have "
                        "dont_touch attribute for synthesis"
                    ),
                }
                return

        # ── Config/quasi-static signals ──────────────────────────────
        if _CONFIG_PATTERNS.search(sig_name):
            if bit_width == 1:
                crossing.sync_strategy = SYNC_SINGLE_BIT
                crossing.sync_details = {
                    "sync_depth": sync_depth,
                    "dont_touch": True,
                    "behavior": (
                        f"{sync_depth}-stage synchronizer with dont_touch"
                    ),
                }
                warnings.append(
                    f"CDC: Config signal '{sig_name}' crosses domains. "
                    f"If it only changes during configuration/reset, consider "
                    f"QUASI_STATIC with documented guarantee."
                )
                return
            else:
                # Multi-bit config: use handshake since it's low bandwidth
                crossing.sync_strategy = SYNC_HANDSHAKE
                crossing.sync_details = {
                    "req_signal": f"{sig_name}_cfg_req",
                    "ack_signal": f"{sig_name}_cfg_ack",
                    "data_width": bit_width,
                    "behavior": (
                        "4-phase handshake for configuration register update"
                    ),
                }
                warnings.append(
                    f"CDC: Multi-bit config signal '{sig_name}' ({bit_width}-bit) "
                    f"crosses domains. Using HANDSHAKE. If only set during reset, "
                    f"consider QUASI_STATIC."
                )
                return

        # ── Fallback: unresolved ─────────────────────────────────────
        crossing.sync_strategy = SYNC_UNRESOLVED
        crossing.unresolved_reason = (
            f"Cannot confidently classify signal '{sig_name}' "
            f"(type={sig_type}, width={bit_width}) for automatic sync assignment."
        )
        unresolved.append(
            f"CDC_UNRESOLVED: {sig_name} crossing from "
            f"{crossing.source_domain} to {crossing.destination_domain}. "
            f"Reason: {crossing.unresolved_reason}"
        )

    def _gray_pointer_width(self, fifo_depth: int) -> int:
        """Calculate Gray code pointer width for a given FIFO depth."""
        import math
        return max(2, int(math.ceil(math.log2(max(fifo_depth, 2)))) + 1)

    # ── Step 4: Generate CDC Submodules ──────────────────────────────

    def _generate_cdc_submodules(
        self, crossings: List[CrossingSignal]
    ) -> List[CDCSubmodule]:
        """Generate synchronization submodule specs for the RTL generator."""
        submodules: List[CDCSubmodule] = []
        seen_modules: Set[str] = set()

        for crossing in crossings:
            if crossing.sync_strategy == SYNC_UNRESOLVED:
                continue

            strategy = crossing.sync_strategy
            sig_name = self._sanitize_name(crossing.signal_name)

            if strategy == SYNC_SINGLE_BIT:
                mod_name = f"cdc_sync_{sig_name}"
                if mod_name in seen_modules:
                    continue
                seen_modules.add(mod_name)
                depth = crossing.sync_details.get("sync_depth", 2)
                submodules.append(CDCSubmodule(
                    module_name=mod_name,
                    strategy=strategy,
                    ports=[
                        {"name": "clk_dst", "direction": "input",
                         "data_type": "logic", "description": "Destination clock"},
                        {"name": "rst_n_dst", "direction": "input",
                         "data_type": "logic", "description": "Destination reset (active-low)"},
                        {"name": "sig_src", "direction": "input",
                         "data_type": "logic", "description": "Source domain signal"},
                        {"name": "sig_dst_synced", "direction": "output",
                         "data_type": "logic", "description": "Synchronized signal in destination domain"},
                    ],
                    parameters={"SYNC_DEPTH": depth},
                    behavior=(
                        f"{depth}-stage synchronizer chain. All flops have "
                        "(* dont_touch = \"true\" *) attribute to prevent "
                        "synthesis optimization. Reset clears all stages."
                    ),
                    source_domain=crossing.source_domain,
                    destination_domain=crossing.destination_domain,
                ))

            elif strategy == SYNC_PULSE:
                mod_name = f"cdc_pulse_sync_{sig_name}"
                if mod_name in seen_modules:
                    continue
                seen_modules.add(mod_name)
                depth = crossing.sync_details.get("sync_depth", 2)
                submodules.append(CDCSubmodule(
                    module_name=mod_name,
                    strategy=strategy,
                    ports=[
                        {"name": "clk_src", "direction": "input",
                         "data_type": "logic", "description": "Source clock"},
                        {"name": "rst_n_src", "direction": "input",
                         "data_type": "logic", "description": "Source reset (active-low)"},
                        {"name": "clk_dst", "direction": "input",
                         "data_type": "logic", "description": "Destination clock"},
                        {"name": "rst_n_dst", "direction": "input",
                         "data_type": "logic", "description": "Destination reset (active-low)"},
                        {"name": "pulse_src", "direction": "input",
                         "data_type": "logic", "description": "Single-cycle pulse in source domain"},
                        {"name": "pulse_dst", "direction": "output",
                         "data_type": "logic", "description": "Regenerated pulse in destination domain"},
                    ],
                    parameters={"SYNC_DEPTH": depth},
                    behavior=(
                        "Toggle flip-flop captures pulse in source domain. "
                        f"{depth}-flop synchronizer transfers toggle to "
                        "destination domain. XOR edge detector regenerates "
                        "single-cycle pulse in destination domain."
                    ),
                    source_domain=crossing.source_domain,
                    destination_domain=crossing.destination_domain,
                ))

            elif strategy == SYNC_ASYNC_FIFO:
                data_width = crossing.sync_details.get("data_width", 8)
                fifo_depth = crossing.sync_details.get("fifo_depth", DEFAULT_FIFO_DEPTH)
                ptr_width = crossing.sync_details.get("gray_pointer_width", 4)
                mod_name = f"cdc_fifo_{sig_name}"
                if mod_name in seen_modules:
                    continue
                seen_modules.add(mod_name)
                submodules.append(CDCSubmodule(
                    module_name=mod_name,
                    strategy=strategy,
                    ports=[
                        {"name": "wr_clk", "direction": "input",
                         "data_type": "logic", "description": "Write clock"},
                        {"name": "wr_rst_n", "direction": "input",
                         "data_type": "logic", "description": "Write reset (active-low)"},
                        {"name": "wr_en", "direction": "input",
                         "data_type": "logic", "description": "Write enable"},
                        {"name": "wr_data", "direction": "input",
                         "data_type": f"logic [{data_width - 1}:0]",
                         "description": "Write data"},
                        {"name": "wr_full", "direction": "output",
                         "data_type": "logic", "description": "FIFO full flag"},
                        {"name": "rd_clk", "direction": "input",
                         "data_type": "logic", "description": "Read clock"},
                        {"name": "rd_rst_n", "direction": "input",
                         "data_type": "logic", "description": "Read reset (active-low)"},
                        {"name": "rd_en", "direction": "input",
                         "data_type": "logic", "description": "Read enable"},
                        {"name": "rd_data", "direction": "output",
                         "data_type": f"logic [{data_width - 1}:0]",
                         "description": "Read data"},
                        {"name": "rd_empty", "direction": "output",
                         "data_type": "logic", "description": "FIFO empty flag"},
                    ],
                    parameters={
                        "DATA_WIDTH": data_width,
                        "FIFO_DEPTH": fifo_depth,
                        "PTR_WIDTH": ptr_width,
                    },
                    behavior=(
                        f"Asynchronous FIFO with {fifo_depth}-deep buffer. "
                        f"Gray-coded {ptr_width}-bit read and write pointers. "
                        "Pointer synchronization via 2-flop synchronizers. "
                        "No combinational paths between write and read clock "
                        "domains. Full/empty generation from synchronized "
                        "Gray pointers."
                    ),
                    source_domain=crossing.source_domain,
                    destination_domain=crossing.destination_domain,
                ))

            elif strategy == SYNC_HANDSHAKE:
                data_width = crossing.sync_details.get("data_width", 1)
                req_sig = crossing.sync_details.get("req_signal", f"{sig_name}_req")
                ack_sig = crossing.sync_details.get("ack_signal", f"{sig_name}_ack")
                mod_name = f"cdc_handshake_{sig_name}"
                if mod_name in seen_modules:
                    continue
                seen_modules.add(mod_name)
                ports = [
                    {"name": "clk_src", "direction": "input",
                     "data_type": "logic", "description": "Source clock"},
                    {"name": "rst_n_src", "direction": "input",
                     "data_type": "logic", "description": "Source reset"},
                    {"name": "clk_dst", "direction": "input",
                     "data_type": "logic", "description": "Destination clock"},
                    {"name": "rst_n_dst", "direction": "input",
                     "data_type": "logic", "description": "Destination reset"},
                    {"name": req_sig, "direction": "output",
                     "data_type": "logic", "description": "Request signal"},
                    {"name": ack_sig, "direction": "input",
                     "data_type": "logic", "description": "Acknowledge signal"},
                ]
                if data_width > 1:
                    ports.extend([
                        {"name": "data_src", "direction": "input",
                         "data_type": f"logic [{data_width - 1}:0]",
                         "description": "Source data"},
                        {"name": "data_dst", "direction": "output",
                         "data_type": f"logic [{data_width - 1}:0]",
                         "description": "Destination data (valid when ack)"},
                    ])
                submodules.append(CDCSubmodule(
                    module_name=mod_name,
                    strategy=strategy,
                    ports=ports,
                    parameters={"DATA_WIDTH": data_width},
                    behavior=(
                        "4-phase handshake protocol: (1) source asserts req "
                        "with stable data, (2) destination synchronizes req "
                        "and asserts ack, (3) source deasserts req, "
                        "(4) destination deasserts ack. Data must remain "
                        "stable from req assert to ack assert."
                    ),
                    source_domain=crossing.source_domain,
                    destination_domain=crossing.destination_domain,
                ))

            elif strategy == SYNC_RESET:
                mod_name = f"cdc_reset_sync_{sig_name}"
                if mod_name in seen_modules:
                    continue
                seen_modules.add(mod_name)
                depth = crossing.sync_details.get("sync_depth", 2)
                submodules.append(CDCSubmodule(
                    module_name=mod_name,
                    strategy=strategy,
                    ports=[
                        {"name": "clk_dst", "direction": "input",
                         "data_type": "logic", "description": "Destination clock"},
                        {"name": "rst_async_n", "direction": "input",
                         "data_type": "logic",
                         "description": "Asynchronous reset input (active-low)"},
                        {"name": "rst_sync_n", "direction": "output",
                         "data_type": "logic",
                         "description": "Synchronized reset output (active-low)"},
                    ],
                    parameters={"SYNC_DEPTH": depth},
                    behavior=(
                        "Asynchronous assert, synchronous deassert reset "
                        f"synchronizer. {depth}-flop chain clocked by "
                        "destination clock. Reset assertion is immediate "
                        "(async), deassertion is synchronized to destination "
                        "clock to prevent metastability."
                    ),
                    source_domain=crossing.source_domain,
                    destination_domain=crossing.destination_domain,
                ))

        return submodules

    # ── Utility Methods ──────────────────────────────────────────────

    def _collect_all_submodules(
        self,
        hw_spec_dict: Dict[str, Any],
        hierarchy_result_dict: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Flatten all submodules from both spec and hierarchy result."""
        all_subs: List[Dict[str, Any]] = []

        # From spec
        for sm in hw_spec_dict.get("submodules", []):
            if isinstance(sm, dict):
                all_subs.append(sm)

        # From hierarchy result (may have nested specs)
        if hierarchy_result_dict:
            for sm in hierarchy_result_dict.get("submodules", []):
                if isinstance(sm, dict):
                    all_subs.append(sm)
                    # Recurse into nested_spec
                    nested = sm.get("nested_spec")
                    if isinstance(nested, dict):
                        for nsm in nested.get("submodules", []):
                            if isinstance(nsm, dict):
                                all_subs.append(nsm)

        return all_subs

    def _sanitize_name(self, name: str) -> str:
        """Convert a signal name to a valid Verilog identifier fragment."""
        # Remove slash-separated compound names
        name = name.replace("/", "_")
        name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        name = re.sub(r"_+", "_", name).strip("_")
        return name.lower()
