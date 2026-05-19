"""Generic hard-macro/IP manifest support for physical implementation flows."""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


def _as_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if str(v).strip()]
    return [str(value)]


def _normalize_paths(value: Any, base_dir: str) -> List[str]:
    paths = []
    for raw_path in _as_list(value):
        path = os.path.expandvars(os.path.expanduser(raw_path.strip()))
        if not path:
            continue
        if not os.path.isabs(path):
            path = os.path.abspath(os.path.join(base_dir, path))
        paths.append(path)
    return paths


def _collect_paths(data: Dict[str, Any], base_dir: str, *keys: str) -> List[str]:
    paths: List[str] = []
    files = data.get("files") if isinstance(data.get("files"), dict) else {}
    for key in keys:
        paths.extend(_normalize_paths(data.get(key), base_dir))
        paths.extend(_normalize_paths(files.get(key), base_dir))
    return _dedupe(paths)


def _dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


@dataclass
class MacroPlacement:
    x: float
    y: float
    orient: str = "N"

    @classmethod
    def from_dict(cls, data: Any) -> Optional["MacroPlacement"]:
        if not isinstance(data, dict):
            return None
        if "x" not in data or "y" not in data:
            return None
        try:
            return cls(
                x=float(data["x"]),
                y=float(data["y"]),
                orient=str(data.get("orient", "N")).upper(),
            )
        except (TypeError, ValueError):
            return None

    def to_dict(self) -> Dict[str, Any]:
        return {"x": self.x, "y": self.y, "orient": self.orient}


@dataclass
class MacroCollateral:
    name: str
    module: str = ""
    instance: str = ""
    kind: str = "custom"
    lefs: List[str] = field(default_factory=list)
    gds: List[str] = field(default_factory=list)
    libs: List[str] = field(default_factory=list)
    blackbox_verilog: List[str] = field(default_factory=list)
    spice: List[str] = field(default_factory=list)
    cdl: List[str] = field(default_factory=list)
    placement: Optional[MacroPlacement] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], base_dir: str) -> "MacroCollateral":
        name = str(data.get("name") or data.get("id") or data.get("module") or "").strip()
        module = str(data.get("module") or data.get("cell") or data.get("blackbox") or "").strip()
        instance = str(data.get("instance") or data.get("inst") or "").strip()
        kind = str(data.get("kind") or data.get("type") or data.get("category") or "custom").strip()

        return cls(
            name=name,
            module=module,
            instance=instance,
            kind=kind or "custom",
            lefs=_collect_paths(data, base_dir, "lef", "lefs"),
            gds=_collect_paths(data, base_dir, "gds", "gds_files"),
            libs=_collect_paths(data, base_dir, "lib", "libs", "liberty", "liberties"),
            blackbox_verilog=_collect_paths(
                data,
                base_dir,
                "verilog",
                "verilogs",
                "blackbox_verilog",
                "blackbox_verilogs",
            ),
            spice=_collect_paths(data, base_dir, "spice", "spices", "spice_model", "spice_models"),
            cdl=_collect_paths(data, base_dir, "cdl", "cdls"),
            placement=MacroPlacement.from_dict(data.get("placement")),
            metadata={
                k: v
                for k, v in data.items()
                if k
                not in {
                    "name",
                    "id",
                    "module",
                    "cell",
                    "blackbox",
                    "instance",
                    "inst",
                    "kind",
                    "type",
                    "category",
                    "files",
                    "lef",
                    "lefs",
                    "gds",
                    "gds_files",
                    "lib",
                    "libs",
                    "liberty",
                    "liberties",
                    "verilog",
                    "verilogs",
                    "blackbox_verilog",
                    "blackbox_verilogs",
                    "spice",
                    "spices",
                    "spice_model",
                    "spice_models",
                    "cdl",
                    "cdls",
                    "placement",
                }
            },
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "module": self.module,
            "instance": self.instance,
            "kind": self.kind,
            "lefs": list(self.lefs),
            "gds": list(self.gds),
            "libs": list(self.libs),
            "blackbox_verilog": list(self.blackbox_verilog),
            "spice": list(self.spice),
            "cdl": list(self.cdl),
            "placement": self.placement.to_dict() if self.placement else None,
            "metadata": dict(self.metadata),
        }


@dataclass
class MacroManifest:
    macros: List[MacroCollateral] = field(default_factory=list)
    manifest_path: str = ""
    base_dir: str = ""

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        base_dir: str = "",
        manifest_path: str = "",
    ) -> "MacroManifest":
        if not isinstance(data, dict):
            data = {}
        root = os.path.abspath(os.path.expanduser(base_dir or os.getcwd()))
        raw_macros = data.get("macros", [])
        if isinstance(raw_macros, dict):
            raw_macros = [
                {"name": macro_name, **macro_data}
                if isinstance(macro_data, dict)
                else {"name": macro_name, "module": str(macro_data)}
                for macro_name, macro_data in raw_macros.items()
            ]
        macros = [
            MacroCollateral.from_dict(macro, root)
            for macro in raw_macros
            if isinstance(macro, dict)
        ]
        return cls(macros=macros, manifest_path=manifest_path, base_dir=root)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest_path": self.manifest_path,
            "base_dir": self.base_dir,
            "macros": [macro.to_dict() for macro in self.macros],
        }

    def has_physical_collateral(self) -> bool:
        return any(macro.lefs or macro.gds or macro.libs for macro in self.macros)

    def has_placements(self) -> bool:
        return any(macro.placement for macro in self.macros)


def load_macro_manifest(path: str) -> MacroManifest:
    manifest_path = os.path.abspath(os.path.expanduser(os.path.expandvars(path)))
    base_dir = os.path.dirname(manifest_path)
    with open(manifest_path, "r", encoding="utf-8") as f:
        if manifest_path.endswith((".yaml", ".yml")):
            try:
                import yaml  # type: ignore
            except Exception as exc:
                raise RuntimeError("YAML macro manifests require PyYAML") from exc
            data = yaml.safe_load(f) or {}
        else:
            data = json.load(f)
    return MacroManifest.from_dict(data, base_dir=base_dir, manifest_path=manifest_path)


def discover_macro_manifest(design_root: str = "", explicit_path: str = "") -> MacroManifest:
    if explicit_path:
        return load_macro_manifest(explicit_path)

    primary_root = os.path.abspath(os.path.expanduser(design_root or os.getcwd()))
    cwd_root = os.path.abspath(os.getcwd())
    search_roots = _dedupe([primary_root, cwd_root])
    candidates = [
        "macro_manifest.json",
        "macros.json",
        "macro_manifest.yaml",
        "macro_manifest.yml",
        "macros.yaml",
        "macros.yml",
    ]
    for search_root in search_roots:
        for filename in candidates:
            path = os.path.join(search_root, filename)
            if os.path.exists(path):
                return load_macro_manifest(path)
    return MacroManifest(base_dir=primary_root)


def validate_macro_manifest(manifest: MacroManifest) -> List[str]:
    warnings: List[str] = []
    for macro in manifest.macros:
        label = macro.name or macro.module or macro.instance or "<unnamed>"
        if not macro.name:
            warnings.append(f"Macro {label} is missing a stable name.")
        if not macro.module:
            warnings.append(f"Macro {label} is missing a black-box module/cell name.")
        if not macro.lefs:
            warnings.append(f"Macro {label} has no LEF; placement/routing cannot see its blockage.")
        if not macro.gds:
            warnings.append(f"Macro {label} has no GDS; final stream-out will need this added later.")
        if not macro.libs:
            warnings.append(f"Macro {label} has no Liberty .lib; timing for its pins will be incomplete.")
        if not macro.blackbox_verilog:
            warnings.append(f"Macro {label} has no black-box Verilog model; RTL compile may fail.")

        for path in macro.lefs + macro.gds + macro.libs + macro.blackbox_verilog + macro.spice + macro.cdl:
            if not os.path.exists(path):
                warnings.append(f"Macro {label} references missing file: {path}")
    return warnings


def macro_openlane_tcl(manifest: MacroManifest) -> str:
    lefs = _dedupe(path for macro in manifest.macros for path in macro.lefs)
    gds = _dedupe(path for macro in manifest.macros for path in macro.gds)
    libs = _dedupe(path for macro in manifest.macros for path in macro.libs)
    blackboxes = _dedupe(path for macro in manifest.macros for path in macro.blackbox_verilog)

    lines = ["# Generic hard-macro/IP collateral"]
    if lefs:
        lines.append(f'set ::env(EXTRA_LEFS) "{" ".join(lefs)}"')
    if gds:
        lines.append(f'set ::env(EXTRA_GDS_FILES) "{" ".join(gds)}"')
    if libs:
        lines.append(f'set ::env(EXTRA_LIBS) "{" ".join(libs)}"')
    if blackboxes:
        lines.append(f'set ::env(VERILOG_FILES_BLACKBOX) "{" ".join(blackboxes)}"')
    if manifest.has_placements():
        lines.append('set ::env(MACRO_PLACEMENT_CFG) "$::env(DESIGN_DIR)/src/macro_placement.cfg"')

    spice = _dedupe(path for macro in manifest.macros for path in macro.spice)
    cdl = _dedupe(path for macro in manifest.macros for path in macro.cdl)
    if spice:
        lines.append(f'# SPICE macro models available for LVS/signoff: {" ".join(spice)}')
    if cdl:
        lines.append(f'# CDL macro models available for LVS/signoff: {" ".join(cdl)}')

    return "\n".join(lines) + "\n" if len(lines) > 1 else ""


def macro_placement_cfg(manifest: MacroManifest) -> str:
    lines = []
    for macro in manifest.macros:
        if not macro.placement:
            continue
        inst = macro.instance or macro.name
        lines.append(f"{inst} {macro.placement.x:g} {macro.placement.y:g} {macro.placement.orient}")
    return "\n".join(lines) + ("\n" if lines else "")


def macro_prompt_guidance(manifest: MacroManifest) -> str:
    if not manifest.macros:
        return ""

    lines = [
        "AVAILABLE HARD MACROS / DESIGN PLUGINS:",
        "Instantiate these as black boxes. Do not reimplement their internal transistor/analog/memory/PLL/custom logic in synthesizable RTL.",
    ]
    for macro in manifest.macros:
        module = macro.module or "<module-name-missing>"
        instance = macro.instance or "<choose-instance-name>"
        lines.append(f"- {macro.name or module} ({macro.kind}): module `{module}`, instance `{instance}`")
    return "\n".join(lines)
