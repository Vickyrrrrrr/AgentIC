import os
import tempfile
import asyncio
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from server.auth import get_current_user
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

@router.post("/ai-assist")
async def ai_assist(req: AIFixPayload, profile: dict = Depends(get_current_user)):
    """A direct, lightweight LLM call to assist the user in the Lab ide, separate from the build queue."""
    try:
        from litellm import completion
        
        # Pull the primary Config (assuming user wants fast code fixes, Groq is excellent here)
        cfg = get_role_llm_config("fixer") # Defaults to GROQ or user's env
        
        # Securely swap if the cloud user has BYOK
        api_key = profile.get("llm_api_key") if profile and profile.get("plan") == "byok" else cfg.get("api_key")
        
        messages = [
            {"role": "system", "content": "You are an expert Verilog/VLSI engineer helping a user debug code in a web IDE. You MUST output the fully fixed code inside exactly one ```verilog codeblock, followed by a concise textual explanation of the bugs you fixed."},
            {"role": "user", "content": f"{req.query}\n\nCODE:\n```verilog\n{req.code}\n```"}
        ]
        
        response = completion(
            model=cfg["model"],
            messages=messages,
            api_base=cfg.get("base_url"),
            api_key=api_key,
            temperature=0.1
        )
        
        return {"success": True, "response": response.choices[0].message.content}
        
    except Exception as e:
        return {"success": False, "response": f"AI Assistance failed to connect: {str(e)}"}

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
