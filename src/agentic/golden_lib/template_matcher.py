"""Template Matcher for Golden Reference Library.

Matches user descriptions to pre-verified RTL templates using keyword analysis.
Returns the best matching template for the LLM to customize rather than
generating from scratch.
"""

import os
import re
import json
from typing import Optional, Tuple, Dict, List

# Template registry: maps IP type to metadata
TEMPLATE_REGISTRY = {
    "counter": {
        "keywords": [
            "counter",
            "count",
            "increment",
            "decrement",
            "up counter",
            "down counter",
            "timer count",
        ],
        "file": "counter.v",
        "tb_file": "counter_tb.v",
        "description": "Parameterizable N-bit up/down counter with enable and load",
        "parameters": {"WIDTH": 8},
        "complexity": "simple",
    },
    "fifo": {
        "keywords": [
            "fifo",
            "queue",
            "buffer",
            "first in first out",
            "circular buffer",
            "data buffer",
        ],
        "file": "fifo.v",
        "tb_file": "fifo_tb.v",
        "description": "Synchronous FIFO with parameterizable width and depth",
        "parameters": {"DATA_WIDTH": 8, "DEPTH": 16},
        "complexity": "medium",
    },
    "uart_tx": {
        "keywords": ["uart", "serial", "transmitter", "rs232", "tx", "baud"],
        "file": "uart_tx.v",
        "tb_file": "uart_tx_tb.v",
        "description": "UART Transmitter with configurable baud rate",
        "parameters": {"CLK_FREQ": 50000000, "BAUD_RATE": 115200},
        "complexity": "medium",
    },
    "spi_master": {
        "keywords": ["spi", "serial peripheral", "master", "mosi", "miso", "sclk"],
        "file": "spi_master.v",
        "tb_file": "spi_master_tb.v",
        "description": "SPI Master with configurable clock polarity and phase",
        "parameters": {"CLK_DIV": 4},
        "complexity": "medium",
    },
    "fsm": {
        "keywords": [
            "fsm",
            "state machine",
            "finite state",
            "controller",
            "sequencer",
            "control unit",
        ],
        "file": "fsm.v",
        "tb_file": "fsm_tb.v",
        "description": "Generic FSM template with configurable states",
        "parameters": {"NUM_STATES": 4},
        "complexity": "simple",
    },
    "pwm": {
        "keywords": [
            "pwm",
            "pulse width",
            "modulation",
            "duty cycle",
            "motor control",
            "led dimm",
        ],
        "file": "pwm.v",
        "tb_file": "pwm_tb.v",
        "description": "PWM Generator with configurable resolution and frequency",
        "parameters": {"RESOLUTION": 8},
        "complexity": "simple",
    },
    "timer": {
        "keywords": ["timer", "watchdog", "timeout", "countdown", "alarm", "periodic"],
        "file": "timer.v",
        "tb_file": "timer_tb.v",
        "description": "General-purpose timer with prescaler and interrupt",
        "parameters": {"WIDTH": 32},
        "complexity": "medium",
    },
    "shift_register": {
        "keywords": [
            "shift register",
            "shift",
            "serial to parallel",
            "parallel to serial",
            "lfsr",
            "shifter",
        ],
        "file": "shift_register.v",
        "tb_file": "shift_register_tb.v",
        "description": "Parameterizable shift register with serial/parallel IO",
        "parameters": {"WIDTH": 8},
        "complexity": "simple",
    },
    "picorv32_wrapper": {
        "keywords": [
            "picorv32",
            "riscv",
            "risc-v",
            "cpu",
            "processor",
            "microcontroller",
            "core",
        ],
        "file": "picorv32_wrapper.v",
        "tb_file": "",
        "description": "PicoRV32 RISC-V CPU Core Wrapper with memory-mapped IO",
        "parameters": {},
        "complexity": "high",
        "requires_download": "https://raw.githubusercontent.com/YosysHQ/picorv32/master/picorv32.v",
    },
    "sram_macro_wrapper": {
        "keywords": [
            "sram",
            "macro",
            "memory block",
            "physical memory",
            "ram",
            "2kbyte",
        ],
        "file": "sram_macro_wrapper.v",
        "tb_file": "",
        "description": "Sky130 SRAM 2KB Physical Macro Wrapper",
        "parameters": {},
        "complexity": "high",
        "requires_macro": "sky130_sram_2kbyte_1rw1r_32x512_8",
    },
}

# Base directory for template files
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


class TemplateMatcher:
    """Matches user design descriptions to golden reference templates."""

    def __init__(self):
        self.registry = TEMPLATE_REGISTRY

    # Keywords that indicate the design is too complex/specialized for a basic template.
    # When these appear, the LLM should generate from scratch instead of using a template.
    COMPLEXITY_INDICATORS = [
        "tmr",
        "triple modular",
        "redundancy",
        "radiation",
        "hardened",
        "hardening",
        "fault tolerant",
        "majority voting",
        "lockstep",
        r"\becc\b",
        "error correct",
        r"\baxi\b",
        r"\bahb\b",
        r"\bapb\b",
        "wishbone",
        "avalon",  # bus protocols
        "pipeline",
        "pipelined",
        "superscalar",
        "out of order",
        r"\bdma\b",
        "cache",
        r"\bmmu\b",
        "arbiter",
        "crossbar",
        "encryption",
        r"\baes\b",
        r"\bsha\b",
        r"\brsa\b",
        "crypto",
        "neural",
        "accelerator",
        "tensor",
        "convolution",
        "multi.?channel",
        "multi.?port",
        "dual.?port",
        "custom protocol",
        "proprietary",
    ]

    def match(self, description: str, design_name: str = "") -> Optional[Dict]:
        """Find the best matching template for a given description.

        Args:
            description: Natural language description of the design
            design_name: Name of the design (also checked for keyword matches)

        Returns:
            dict with template info if match found, None otherwise.
            Keys: 'ip_type', 'score', 'template_code', 'tb_code',
                  'description', 'parameters', 'customize_prompt'
        """
        text = f"{design_name} {description}".lower()

        # Reject if description has complexity indicators — design is too
        # specialized for any basic template, LLM should generate from scratch.
        for indicator in self.COMPLEXITY_INDICATORS:
            if re.search(indicator, text):
                return None

        best_match = None
        best_score = 0

        for ip_type, meta in self.registry.items():
            score = self._score_match(text, meta["keywords"])
            if score > best_score:
                best_score = score
                best_match = ip_type

        # Raised threshold to avoid loose matches
        if best_score < 3:
            return None

        meta = self.registry[best_match]

        # Load template file contents
        template_code = self._load_template(meta["file"])
        tb_code = self._load_template(meta.get("tb_file", ""))

        if not template_code:
            return None

        return {
            "ip_type": best_match,
            "score": best_score,
            "template_code": template_code,
            "tb_code": tb_code,
            "description": meta["description"],
            "parameters": meta["parameters"],
            "complexity": meta["complexity"],
            "customize_prompt": self._build_customize_prompt(
                best_match, meta, template_code, design_name, description
            ),
        }

    def _score_match(self, text: str, keywords: List[str]) -> int:
        """Score how well text matches a set of keywords."""
        score = 0
        for kw in keywords:
            if kw in text:
                # Longer keyword matches are worth more
                score += len(kw.split())
        return score

    def _load_template(self, filename: str) -> str:
        """Load a template file from the templates directory."""
        if not filename:
            return ""
        filepath = os.path.join(TEMPLATES_DIR, filename)
        try:
            with open(filepath, "r") as f:
                return f.read()
        except OSError as e:
            return ""

    def _build_customize_prompt(
        self,
        ip_type: str,
        meta: Dict,
        template_code: str,
        design_name: str,
        description: str,
    ) -> str:
        """Build the prompt for LLM to customize the template."""
        params_str = "\n".join(
            [f"  - {k} = {v}" for k, v in meta["parameters"].items()]
        )

        return f"""You are customizing a PRE-VERIFIED {ip_type.upper()} template.

GOLDEN REFERENCE (proven, working RTL):
```verilog
{template_code}
```

YOUR TASK:
1. Rename the module to "{design_name}"
2. Customize parameters based on this requirement: {description}
3. Add any extra features requested that aren't in the template
4. Keep the PROVEN structure — do NOT rewrite from scratch

CUSTOMIZABLE PARAMETERS:
{params_str}

RULES:
- Keep the reset logic EXACTLY as in the template (async active-low rst_n)
- Keep the clock/reset port names: clk, rst_n
- You may add new ports but DO NOT remove existing ones
- You may add new states/logic but DO NOT restructure the FSM
- Module name MUST be "{design_name}"
- Return the complete customized module in ```verilog fences
"""

    def list_available(self) -> List[Dict]:
        """List all available templates."""
        result = []
        for ip_type, meta in self.registry.items():
            result.append(
                {
                    "ip_type": ip_type,
                    "description": meta["description"],
                    "complexity": meta["complexity"],
                    "parameters": meta["parameters"],
                    "has_template": os.path.exists(
                        os.path.join(TEMPLATES_DIR, meta["file"])
                    ),
                    "has_testbench": os.path.exists(
                        os.path.join(TEMPLATES_DIR, meta.get("tb_file", ""))
                    ),
                }
            )
        return result


def get_best_template(description: str, design_name: str = "") -> Optional[Dict]:
    """Convenience function to get the best matching template.

    Args:
        description: Natural language design description
        design_name: Name of the design

    Returns:
        Template dict or None if no match
    """
    matcher = TemplateMatcher()
    return matcher.match(description, design_name)
