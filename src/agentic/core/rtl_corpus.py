"""
RTL Corpus Builder
==================

Creates JSONL training/evaluation corpora from local AgentIC artifacts, golden
templates, checkpoints, and design directories.  This is the data plumbing for
domain-specific RTL models without pretending a fine-tuned model already exists.
"""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ..config import WORKSPACE_ROOT, OPENLANE_ROOT


@dataclass
class CorpusRecord:
    """A single RTL corpus record."""

    task: str
    design_name: str
    source: str
    prompt: str
    rtl: str
    testbench: str = ""
    metadata: Dict[str, object] = None


class RTLCorpusBuilder:
    """Build JSONL corpora for domain-specific RTL training/evaluation."""

    def __init__(self, roots: Optional[List[str]] = None):
        self.roots = [Path(os.path.expanduser(r)) for r in roots] if roots else self._default_roots()

    @staticmethod
    def _default_roots() -> List[Path]:
        return [
            Path(WORKSPACE_ROOT) / "src" / "agentic" / "golden_lib" / "templates",
            Path(OPENLANE_ROOT) / "designs",
            Path(WORKSPACE_ROOT) / "checkpoints",
        ]

    def collect(self, limit: int = 0) -> List[CorpusRecord]:
        records: List[CorpusRecord] = []
        seen = set()
        for root in self.roots:
            if not root.exists():
                continue
            for rtl_path in self._rtl_files(root):
                key = str(rtl_path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                record = self._record_from_rtl(rtl_path)
                if record:
                    records.append(record)
                    if limit and len(records) >= limit:
                        return records
        return records

    @staticmethod
    def _rtl_files(root: Path) -> Iterable[Path]:
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() in {".v", ".sv"} and not path.name.endswith("_tb.v"):
                yield path

    def _record_from_rtl(self, rtl_path: Path) -> Optional[CorpusRecord]:
        try:
            rtl = rtl_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        if "module " not in rtl or len(rtl.strip()) < 40:
            return None

        design_name = rtl_path.stem
        tb = self._find_testbench(rtl_path)
        prompt = self._infer_prompt(rtl_path, design_name)
        metadata = {
            "path": str(rtl_path),
            "bytes": len(rtl),
            "has_testbench": bool(tb),
            "module_count": rtl.count("module "),
        }
        return CorpusRecord(
            task="spec_to_rtl",
            design_name=design_name,
            source=str(rtl_path),
            prompt=prompt,
            rtl=rtl,
            testbench=tb,
            metadata=metadata,
        )

    @staticmethod
    def _find_testbench(rtl_path: Path) -> str:
        candidates = [
            rtl_path.with_name(f"{rtl_path.stem}_tb{rtl_path.suffix}"),
            rtl_path.with_name(f"{rtl_path.stem}_tb.v"),
            rtl_path.with_name("tb.v"),
        ]
        for candidate in candidates:
            if candidate.exists():
                try:
                    return candidate.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    return ""
        return ""

    @staticmethod
    def _infer_prompt(rtl_path: Path, design_name: str) -> str:
        checkpoint_spec = ""
        for parent in [rtl_path.parent, *rtl_path.parents]:
            latest = parent / "latest.json"
            if latest.exists():
                try:
                    data = json.loads(latest.read_text(encoding="utf-8"))
                    checkpoint_spec = str(data.get("spec") or data.get("description") or "")
                    break
                except (OSError, ValueError):
                    pass
        if checkpoint_spec:
            return checkpoint_spec[:4000]
        readable = design_name.replace("_", " ")
        return f"Implement synthesizable Verilog RTL for {readable}."

    def export_jsonl(self, output_path: str, limit: int = 0) -> Dict[str, object]:
        output = Path(os.path.expanduser(output_path))
        output.parent.mkdir(parents=True, exist_ok=True)
        records = self.collect(limit=limit)
        with output.open("w", encoding="utf-8") as f:
            for record in records:
                payload = asdict(record)
                if payload.get("metadata") is None:
                    payload["metadata"] = {}
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return {
            "output": str(output),
            "records": len(records),
            "roots": [str(r) for r in self.roots],
        }
