import os
import json
import tempfile
import asyncio
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel

from server.auth import get_current_user, get_byok_config_for_user
from agentic.config import get_role_llm_config

router = APIRouter(prefix="/lab", tags=["Manual EDA Lab"])

class CodePayload(BaseModel):
    code: str
    top_module: str = "top"

class SimPayload(BaseModel):
    code: str
    top_module: str = "top"

class AIFixPayload(BaseModel):
    code: str
    query: str = "Please review this Verilog code."

class VcdPayload(BaseModel):
    vcd_data: str

_KNOWN_MODEL_PREFIXES = (
    "openai/",
    "groq/",
    "ollama/",
    "anthropic/",
    "nvidia_nim/",
    "azure/",
    "huggingface/",
    "together_ai/",
    "mistral/",
)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


ALLOW_BACKEND_LLM_FALLBACK = _env_flag("ALLOW_BACKEND_LLM_FALLBACK", False)


def _has_real_api_key(value: str) -> bool:
    return bool(value and value.strip() and value.strip() not in ("NA", "mock-key"))


def _normalize_model(model: str, base_url: str) -> str:
    model = (model or "").strip()
    base_url = (base_url or "").strip()
    if base_url and model and not any(model.startswith(prefix) for prefix in _KNOWN_MODEL_PREFIXES):
        return f"openai/{model}"
    return model


def _load_request_byok(request: Request, profile: dict | None) -> dict | None:
    header = request.headers.get("X-LLM-API-Key")
    if header:
        try:
            parsed = json.loads(header)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {
                "group1": {"api_key": header},
                "group2": {"api_key": header},
                "group3": {"api_key": header},
            }
    return get_byok_config_for_user(profile)


def _resolve_byok_group(
    byok_config: dict | None,
    group_order: tuple[str, ...],
    fallback_cfg: dict[str, str],
) -> dict[str, str] | None:
    if not byok_config:
        return None

    for group_name in group_order:
        group = byok_config.get(group_name) or {}
        api_key = (group.get("api_key") or "").strip()
        if not _has_real_api_key(api_key):
            continue
        model = _normalize_model(group.get("model") or fallback_cfg.get("model", ""), group.get("base_url") or fallback_cfg.get("base_url", ""))
        return {
            "group": group_name,
            "api_key": api_key,
            "model": model or fallback_cfg.get("model", ""),
            "base_url": (group.get("base_url") or fallback_cfg.get("base_url") or "").strip(),
        }
    return None

@router.post("/syntax-check")
async def check_syntax(req: CodePayload, profile: dict = Depends(get_current_user)):
    """Runs a lightning-fast Verilator syntax check on user-provided code."""
    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = os.path.join(temp_dir, f"{req.top_module}.v")
        with open(file_path, "w") as f:
            f.write(req.code)

        # Run Verilator in lint-only mode (industry standard for strict checking)
        cmd = f"verilator --lint-only -Wall {file_path}"
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        output = (stdout.decode() + "\n" + stderr.decode()).strip()
        success = process.returncode == 0
        
        return {
            "success": success,
            "logs": output if output else ("✅ Verilator Syntax Check Passed (Industry Standard)!" if success else "Failed with no output")
        }

@router.post("/synthesize")
async def synthesize_code(req: CodePayload, profile: dict = Depends(get_current_user)):
    """Validates if code is actual valid hardware logic via Open-Source standard Yosys."""
    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = os.path.join(temp_dir, f"{req.top_module}.v")
        with open(file_path, "w") as f:
            f.write(req.code)
            
        script_path = os.path.join(temp_dir, "synth.ys")
        with open(script_path, "w") as f:
            f.write(f"read_verilog {file_path}\n")
            f.write(f"hierarchy -top {req.top_module}\n")
            f.write("synth\n")
            f.write("stat\n")
        
        cmd = f"yosys -q {script_path}"
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        output = (stdout.decode() + "\n" + stderr.decode()).strip()
        
        return {
            "success": process.returncode == 0,
            "logs": output if output else "Synthesis Success with Yosys"
        }

@router.post("/simulate")
async def simulate_code(req: SimPayload, profile: dict = Depends(get_current_user)):
    """Compiles and runs a simulation using Icarus Verilog, capturing stdout."""
    with tempfile.TemporaryDirectory() as temp_dir:
        code_path = os.path.join(temp_dir, f"{req.top_module}.v")
        out_path = os.path.join(temp_dir, "sim.vvp")
        
        with open(code_path, "w") as f: f.write(req.code)

        # Compile with Icarus
        cmd_compile = f"iverilog -o {out_path} {code_path}"
        compile_proc = await asyncio.create_subprocess_shell(
            cmd_compile,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        c_stdout, c_stderr = await compile_proc.communicate()
        
        if compile_proc.returncode != 0:
            return {"success": False, "stage": "compile", "logs": c_stderr.decode()}

        # Run with VVP
        cmd_run = f"vvp {out_path}"
        run_proc = await asyncio.create_subprocess_shell(
            cmd_run,
            cwd=temp_dir,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        r_stdout, r_stderr = await run_proc.communicate()
        
        output = r_stdout.decode() + "\n" + r_stderr.decode()
        
        vcd_data = None
        vcd_path = os.path.join(temp_dir, "dump.vcd")
        if os.path.exists(vcd_path):
            with open(vcd_path, "r", encoding="utf-8", errors="ignore") as vf:
                vcd_data = vf.read()

        return {
            "success": run_proc.returncode == 0, 
            "stage": "simulate", 
            "logs": output.strip(),
            "vcd": vcd_data
        }
class TestbenchPayload(BaseModel):
    code: str

@router.post("/generate-testbench")
async def generate_testbench(req: TestbenchPayload, request: Request, profile: dict = Depends(get_current_user)):
    """Uses LLM to automatically append a testbench to the provided Verilog code."""
    try:
        from litellm import acompletion
        
        system_prompt = """
You are an expert IC verification engineer. The user will provide a Verilog design module.
Your job is to write a comprehensive testbench for it (`tb_<modulename>`), including clock generation (if applicable) and a reasonable set of stimulus/vectors to verify functionality.
IMPORTANT:
- Ensure the testbench includes `$dumpfile("dump.vcd");` and `$dumpvars(0, <tb_module_name>);` so GTKWave simulation viewing works.
- Output ONLY the new testbench Verilog code, without markdown blocks, without explanations, and DO NOT repeat the original code. Just the `module tb_...` block.
"""
        user_prompt = f"Here is my Verilog design:\n\n```verilog\n{req.code}\n```\n\nPlease write just the testbench module for it."

        cfg = get_role_llm_config("testbench_designer")
        byok_config = _load_request_byok(request, profile)
        resolved = _resolve_byok_group(byok_config, ("group2", "group1", "group3"), cfg)

        llm_model = cfg.get("model", "gpt-4o")
        llm_api_key = cfg.get("api_key", "") if ALLOW_BACKEND_LLM_FALLBACK else ""
        llm_api_base = cfg.get("base_url", "") if ALLOW_BACKEND_LLM_FALLBACK else ""

        if resolved:
            llm_model = resolved["model"]
            llm_api_key = resolved["api_key"]
            llm_api_base = resolved["base_url"]

        if not _has_real_api_key(llm_api_key):
            return {
                "success": False,
                "error": (
                    "This deployment requires BYOK for AI lab actions. Configure Workspace BYOK "
                    "or send X-LLM-API-Key with the request."
                ),
            }

        response = await acompletion(
            model=llm_model,
            api_key=llm_api_key,
            api_base=llm_api_base or None,
            messages=[
                {"role": "system", "content": system_prompt.strip()},
                {"role": "user", "content": user_prompt.strip()}
            ],
            temperature=0.2,
            max_tokens=2048
        )
        
        tb_code = response.choices[0].message.content.strip()
        
        # Clean up markdown if the LLM leaked them
        if tb_code.startswith("```verilog"):
            tb_code = tb_code.split("```verilog", 1)[1]
            if tb_code.endswith("```"):
                tb_code = tb_code.rsplit("```", 1)[0]
        
        return {"success": True, "testbench": tb_code.strip()}
        
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/ai-assist")
async def ai_assist(req: AIFixPayload, request: Request, profile: dict = Depends(get_current_user)):
    """A direct, lightweight LLM call to assist the user in the Lab IDE.
    
    Enhanced flow:
    1. Run syntax check first to gather error context
    2. Send code + errors to LLM for comprehensive fix (syntax + logic + synthesizability)
    3. Compute line-by-line diff between original and fixed code
    4. Return structured response with diffs
    """
    try:
        from litellm import acompletion
        import difflib
        
        # ── Step 1: Run syntax check to gather error context ──
        error_context = ""
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "check.v")
            with open(file_path, "w") as f:
                f.write(req.code)
            
            cmd = f"verilator --lint-only -Wall {file_path}"
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            raw_output = (stdout.decode() + "\n" + stderr.decode()).strip()
            
            if process.returncode != 0:
                # Clean up file paths from error messages for readability
                error_context = raw_output.replace(file_path, "<code>")
            else:
                error_context = "No syntax errors detected by Verilator."
        
        # ── Step 2: LLM call with error context ──
        cfg = get_role_llm_config("fixer")
        byok_config = _load_request_byok(request, profile)
        # Legacy fallback keeps Lab usable for users who filled the old UI group3 bucket.
        resolved = _resolve_byok_group(byok_config, ("group1", "group3", "group2"), cfg)

        api_key = (resolved or {}).get("api_key", cfg.get("api_key", "") if ALLOW_BACKEND_LLM_FALLBACK else "")
        cfg["model"] = (resolved or {}).get("model", cfg.get("model", ""))
        cfg["base_url"] = (resolved or {}).get("base_url", cfg.get("base_url", "") if ALLOW_BACKEND_LLM_FALLBACK else "")

        if not _has_real_api_key(api_key):
            return {
                "success": False,
                "response": (
                    "BYOK is required on this deployment for AI lab actions. Configure Group 1 "
                    "for Fixer/Debugger in Workspace BYOK, or enable backend fallback only for private local usage."
                ),
                "fixed_code": None,
                "line_changes": [],
                "explanation": "",
            }
        
        messages = [
            {"role": "system", "content": """You are an expert Verilog/SystemVerilog engineer. Your job is to fix ALL issues in the provided code and produce FULLY SYNTHESIZABLE, error-free Verilog.

You MUST fix:
1. All syntax errors reported by the linter
2. All logical bugs (wrong reset values, missing cases, off-by-one, width mismatches)
3. All synthesizability issues (latches from incomplete case/if, unsupported constructs)
4. Ensure all outputs are driven in all code paths
5. Ensure all registers have proper reset values
6. You MUST return the ENTIRE file contents (including any modules, testbenches, or comments you did not change). Do not truncate or omit any parts of the provided code!

Output format: Place the COMPLETE fixed Verilog code inside exactly one ```verilog codeblock.
After the code block, provide a concise explanation of every change you made, referencing line numbers from the ORIGINAL code.
Format each change as: "Line N: <what was wrong> → <what you fixed>"."""},
            {"role": "user", "content": f"""{req.query}

LINTER ERRORS/WARNINGS:
{error_context}

ORIGINAL CODE:
```verilog
{req.code}
```"""}
        ]
        
        response = await acompletion(
            model=cfg["model"],
            messages=messages,
            api_base=cfg.get("base_url"),
            api_key=api_key,
            temperature=0.1
        )
        
        response_text = response.choices[0].message.content
        
        # ── Step 3: Extract fixed code and compute line-by-line diff ──
        code_match = __import__('re').search(r'```(?:verilog|systemverilog|sv)\n([\s\S]*?)```', response_text)
        
        result = {
            "success": True,
            "response": response_text,
            "fixed_code": None,
            "line_changes": [],
            "explanation": "",
        }
        
        if code_match and code_match.group(1):
            fixed_code = code_match.group(1).strip()
            result["fixed_code"] = fixed_code
            
            # Extract explanation (everything after the code block)
            explanation = response_text[code_match.end():].strip()
            # Also check for text before the code block
            preamble = response_text[:code_match.start()].strip()
            if preamble:
                explanation = preamble + "\n\n" + explanation
            result["explanation"] = explanation
            
            # Compute line-by-line diff
            original_lines = req.code.splitlines()
            fixed_lines = fixed_code.splitlines()
            
            changes = []
            differ = difflib.unified_diff(
                original_lines, fixed_lines,
                fromfile="original", tofile="fixed",
                lineterm=""
            )
            
            # Parse unified diff to extract individual line changes
            old_line_num = 0
            new_line_num = 0
            for line in differ:
                if line.startswith("@@"):
                    # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
                    import re
                    hunk = re.match(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
                    if hunk:
                        old_line_num = int(hunk.group(1)) - 1
                        new_line_num = int(hunk.group(2)) - 1
                elif line.startswith("-") and not line.startswith("---"):
                    old_line_num += 1
                    changes.append({
                        "type": "removed",
                        "line": old_line_num,
                        "old": line[1:],  # strip the leading '-'
                        "new": None
                    })
                elif line.startswith("+") and not line.startswith("+++"):
                    new_line_num += 1
                    # Try to pair with the most recent 'removed' entry
                    paired = False
                    for c in reversed(changes):
                        if c["type"] == "removed" and c["new"] is None:
                            c["type"] = "modified"
                            c["new"] = line[1:]  # strip the leading '+'
                            paired = True
                            break
                    if not paired:
                        changes.append({
                            "type": "added",
                            "line": new_line_num,
                            "old": None,
                            "new": line[1:]
                        })
                else:
                    old_line_num += 1
                    new_line_num += 1
            
            result["line_changes"] = changes
        
        return result
        
    except Exception as e:
        return {"success": False, "response": f"AI Assistance failed to connect: {str(e)}", "fixed_code": None, "line_changes": [], "explanation": ""}

@router.post("/gtkwave")
async def launch_gtkwave(req: VcdPayload, profile: dict = Depends(get_current_user)):
    """Instantly launches GTKWave on the server/local desktop containing this rendered VCD."""
    import subprocess
    import tempfile
    
    # We must keep the temp file alive so gtkwave can read it
    fd, path = tempfile.mkstemp(suffix=".vcd")
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(req.vcd_data)
        
    try:
        # Spawn GTKWave asynchronously so it doesn't block the API
        subprocess.Popen(["gtkwave", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"success": True, "message": "GTKWave launched locally on server display."}
    except Exception as e:
        return {"success": False, "message": f"Failed to launch GTKWave: {str(e)}"}
