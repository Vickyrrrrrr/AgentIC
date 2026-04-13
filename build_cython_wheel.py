import os
import shutil
import subprocess
import glob
import sys

def build_secure_wheel():
    print("🚀 Starting secure Cython build for PyPI...")
    build_dir = "cython_build"
    
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

    # 3. Patch pyproject.toml to use setuptools + Cython
    with open(f"{build_dir}/pyproject.toml", "r") as f:
        toml_content = f.read()
    
    # Replace hatchling with setuptools
    toml_content = toml_content.replace(
        'requires = ["hatchling"]',
        'requires = ["setuptools>=61.0", "wheel", "Cython>=3.0.0"]'
    ).replace(
        'build-backend = "hatchling.build"',
        'build-backend = "setuptools.build_meta"'
    )
    # Remove hatch specific stuff to avoid warnings
    if "[tool.hatch" in toml_content:
        toml_content = toml_content.split("[tool.hatch")[0]
        
    with open(f"{build_dir}/pyproject.toml", "w") as f:
        f.write(toml_content)

    # 4. Create setup.py for Cython compilation
    setup_py = """
from setuptools import setup, Extension, find_packages
from Cython.Build import cythonize
from setuptools.command.build_py import build_py
import glob
import os

class ExcludeSourceBuildPy(build_py):
    # This prevents setuptools from packaging the raw .py AND the intermediate .c files
    def build_module(self, module, module_file, package):
        if os.path.basename(module_file) == "__init__.py":
            # We keep __init__.py files as raw python to maintain the package structure
            return super().build_module(module, module_file, package)
        # Skip all other .py files, they will be compiled into .so extensions instead
        pass

py_files = glob.glob("src/agentic/**/*.py", recursive=True)

ext_modules = []
for f in py_files:
    if os.path.basename(f) == "__init__.py":
        continue
    
    # Convert path to module name: src/agentic/cli.py -> agentic.cli
    module_name = f.replace("src/", "").replace(".py", "").replace(os.sep, ".")
    ext_modules.append(Extension(module_name, [f]))

setup(
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    ext_modules=cythonize(ext_modules, compiler_directives={'language_level': "3"}, force=True),
    include_package_data=True,
    # Tell setuptools not to include Cython's intermediate C files or Pyx in the final wheel
    exclude_package_data={"": ["*.c", "*.pyx"]},
    cmdclass={'build_py': ExcludeSourceBuildPy},
)
"""
    with open(f"{build_dir}/setup.py", "w") as f:
        f.write(setup_py)

    # Add MANIFEST.in to ensure golden_lib templates are included!
    manifest = "recursive-include src/agentic/golden_lib/templates *.v\n"
    with open(f"{build_dir}/MANIFEST.in", "w") as f:
        f.write(manifest)

    # 5. Build the wheel
    print("⚙️ Compiling with Cython and building wheel (this may take a few minutes)...")
    os.chdir(build_dir)
    try:
        subprocess.run([sys.executable, "-m", "build", "--wheel"], check=True)
    except subprocess.CalledProcessError:
        print("\n❌ Build failed.")
        sys.exit(1)
    os.chdir("..")

    # 6. Check output
    os.makedirs("dist", exist_ok=True)
    wheels = glob.glob(f"{build_dir}/dist/*.whl")
    for dist_file in wheels:
        shutil.copy(dist_file, "dist/")
        print(f"\n✅ Created compiled wheel: dist/{os.path.basename(dist_file)}")
        
    print("\nNote: This wheel is platform-specific (e.g. linux_x86_64).")
    print("Because it is now compiled C binary code, you must build it separately on a Mac and a Windows machine if you want users on those platforms to install it.")

if __name__ == "__main__":
    build_secure_wheel()
