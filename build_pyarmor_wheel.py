import os
import shutil
import subprocess
import glob
import sys

def build_secure_wheel():
    print("🚀 Starting secure Pyarmor build for PyPI...")
    build_dir = "pyarmor_build"
    
    # 1. Clean previous build
    if os.path.exists(build_dir):
        shutil.rmtree(build_dir)
    os.makedirs(build_dir)
    
    # 2. Copy necessary files
    for item in ["src", "pyproject.toml", "README.md", "requirements.txt"]:
        if os.path.exists(item):
            if os.path.isdir(item):
                shutil.copytree(item, f"{build_dir}/{item}")
            else:
                shutil.copy(item, f"{build_dir}/{item}")

    # 3. Run PyArmor to obfuscate
    print("🔐 Obfuscating Python code with Pyarmor...")
    try:
        subprocess.run([
            sys.executable, "-m", "pyarmor.cli.pyarmor", "gen", 
            "-O", f"{build_dir}/obfuscated", 
            "-r", f"{build_dir}/src/agentic"
        ], check=True)
    except subprocess.CalledProcessError:
        print("\n❌ Pyarmor obfuscation failed.")
        print("Note: PyArmor Trial limits files to ~32KB. You need a full Pyarmor license to obfuscate orchestrator.py (250KB).")
        sys.exit(1)

    # 4. Replace source with obfuscated code
    print("📦 Packing obfuscated files...")
    shutil.rmtree(f"{build_dir}/src/agentic")
    shutil.move(f"{build_dir}/obfuscated/agentic", f"{build_dir}/src/agentic")
    
    # 5. Restore non-python files (Pyarmor skips them by default)
    # Golden templates must be copied back over
    os.makedirs(f"{build_dir}/src/agentic/golden_lib/templates", exist_ok=True)
    for v_file in glob.glob("src/agentic/golden_lib/templates/*.v"):
        shutil.copy(v_file, f"{build_dir}/src/agentic/golden_lib/templates/")

    # 6. Move the Pyarmor runtime package into src so it gets built
    runtime_dirs = glob.glob(f"{build_dir}/obfuscated/pyarmor_runtime_*")
    runtime_pkg = ""
    if runtime_dirs:
        runtime_pkg = os.path.basename(runtime_dirs[0])
        shutil.move(runtime_dirs[0], f"{build_dir}/src/{runtime_pkg}")
        
        # Inject the runtime into the pyproject.toml hatch configuration
        with open(f"{build_dir}/pyproject.toml", "r") as f:
            toml_content = f.read()
        
        # Update packages = ["src/agentic"] to include the runtime
        toml_content = toml_content.replace(
            'packages = ["src/agentic"]',
            f'packages = ["src/agentic", "src/{runtime_pkg}"]'
        )
        
        with open(f"{build_dir}/pyproject.toml", "w") as f:
            f.write(toml_content)

    # 7. Build the wheel
    print("⚙️ Building the secure .whl and .tar.gz...")
    os.chdir(build_dir)
    subprocess.run([sys.executable, "-m", "build"], check=True)
    os.chdir("..")

    # 8. Move built packages to root dist/
    os.makedirs("dist", exist_ok=True)
    for dist_file in glob.glob(f"{build_dir}/dist/*"):
        shutil.copy(dist_file, "dist/")
        
    print(f"\n✅ Success! Your encrypted package is in the dist/ folder.")
    print("You can now test it with: pip install dist/agentic_ic-...-py3-none-any.whl")
    print("Then upload to PyPI: twine upload dist/*")

if __name__ == "__main__":
    build_secure_wheel()
