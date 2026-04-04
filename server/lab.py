import os
import tempfile
import asyncio
from fastapi import APIRouter, HTTPException, Depends
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
        from litellm import completion
        
        system_prompt = """
You are an expert IC verification engineer. The user will provide a Verilog design module.
Your job is to write a comprehensive testbench for it (`tb_<modulename>`), including clock generation (if applicable) and a reasonable set of stimulus/vectors to verify functionality.
IMPORTANT:
- Ensure the testbench includes `$dumpfile("dump.vcd");` and `$dumpvars(0, <tb_module_name>);` so GTKWave simulation viewing works.
- Output ONLY the new testbench Verilog code, without markdown blocks, without explanations, and DO NOT repeat the original code. Just the `module tb_...` block.
"""
        user_prompt = f"Here is my Verilog design:\n\n```verilog\n{req.code}\n```\n\nPlease write just the testbench module for it."

        # Grab user API keys dynamically mapped by auth middleware
        from server.auth import get_llm_key_for_user
        llm_api_key = request.headers.get("X-LLM-API-Key") or get_llm_key_for_user(profile)
        
        # If the BYOK is JSON grouped, unpack group2 (coding agents)
        import json
        try:
            bk = json.loads(llm_api_key)
            llm_api_key = bk.get("group2", {}).get("api_key", llm_api_key)
        except:
            pass

        llm_model = "gpt-4o" # default model if BYOK doesn't specify one, wait, let's just use env defaults
        
        # Fallback to defaults
        if not llm_api_key:
            import os
            llm_model = os.environ.get("LLM_MODEL", "gpt-4o")
            llm_api_key = (
                os.environ.get("LLM_API_KEY") 
                or os.environ.get("OPENAI_API_KEY") 
                or os.environ.get("GROQ_API_KEY")
                or os.environ.get("NVIDIA_API_KEY")
            )
            if os.environ.get("GROQ_API_KEY"):
                llm_model = os.environ.get("GROQ_MODEL", "groq/llama-3.3-70b-versatile")
            elif os.environ.get("NVIDIA_API_KEY"):
                llm_model = os.environ.get("NVIDIA_MODEL", "openai/meta/llama-3.3-70b-instruct")

        if not llm_api_key:
            return {"success": False, "error": "No LLM API key configured for Workspace."}

        response = completion(
            model=llm_model,
            api_key=llm_api_key,
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
        from litellm import completion
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

        # Re-read API keys live from environment at request time.
        # get_role_llm_config() is evaluated at startup and may have cached an
        # empty string if HF Secrets weren't mounted yet. Reading os.environ
        header_byok = request.headers.get("X-LLM-API-Key")
        if header_byok:
            import json
            try:
                byok_config = json.loads(header_byok)
            except:
                byok_config = {"group3": {"api_key": header_byok}}
            g = byok_config.get("group3", {})
            api_key = g.get("api_key", header_byok)
            if getattr(g, "model", None) or g.get("model"):
                cfg["model"] = g.get("model")
            if getattr(g, "base_url", None) or g.get("base_url"):
                cfg["base_url"] = g.get("base_url")

        elif profile and profile.get("plan") == "byok":
            byok_config = get_byok_config_for_user(profile)
            if byok_config and "group3" in byok_config:
                g = byok_config["group3"]
                api_key = g.get("api_key", "")
                if getattr(g, "model", None) or g.get("model"):
                    cfg["model"] = g.get("model")
                _KNOWN = ("openai/", "groq/", "ollama/", "anthropic/", "nvidia_nim/", "azure/", "huggingface/", "together_ai/", "mistral/")
                if g.get("base_url") and not any(cfg["model"].startswith(p) for p in _KNOWN):
                    cfg["model"] = f"openai/{cfg['model']}"
                if g.get("base_url"):
                    cfg["base_url"] = g.get("base_url")
            else:
                api_key = ""
        else:
            # Try Groq first (preferred for fixer), then NVIDIA, then GLM, then generic LLM
            api_key = (
                os.environ.get("GROQ_API_KEY")
                or os.environ.get("NVIDIA_API_KEY")
                or os.environ.get("GLM_API_KEY")
                or os.environ.get("LLM_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
                or cfg.get("api_key")
            )
            # Pick the matching model for whichever key we found
            if os.environ.get("GROQ_API_KEY"):
                cfg = {
                    "model": os.environ.get("GROQ_MODEL", "groq/llama-3.3-70b-versatile"),
                    "api_key": os.environ["GROQ_API_KEY"],
                    "base_url": "",
                }
            elif os.environ.get("NVIDIA_API_KEY"):
                cfg = {
                    "model": os.environ.get("NVIDIA_MODEL", "openai/meta/llama-3.3-70b-instruct"),
                    "api_key": os.environ["NVIDIA_API_KEY"],
                    "base_url": os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
                }
            elif os.environ.get("GLM_API_KEY"):
                cfg = {
                    "model": f"openai/{os.environ.get('GLM_MODEL', 'glm-4-plus')}",
                    "api_key": os.environ["GLM_API_KEY"],
                    "base_url": os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"),
                }
            elif os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"):
                cfg = {
                    "model": os.environ.get("LLM_MODEL", "gpt-4o"),
                    "api_key": os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"),
                    "base_url": os.environ.get("LLM_BASE_URL", ""),
                }

        if not api_key:
            return {
                "success": False,
                "response": "❌ No LLM API key found. Please add OPENAI_API_KEY, GROQ_API_KEY, NVIDIA_API_KEY, or GLM_API_KEY to your environment variables.",
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
        
        response = completion(
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
