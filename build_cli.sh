#!/bin/bash
set -e

echo "🚀 Building AgentIC standalone binary..."

# We explicitly use /usr/bin/python3 because your default `python3`
# points to the OSS-CAD-Suite python, which has a broken OpenSSL library
# and causes `_ssl.cpython.so undefined symbol` crashes in the final executable.
SYS_PYTHON="/usr/bin/python3"

# Ensure pyinstaller is installed on the system python
if ! $SYS_PYTHON -m pip show pyinstaller &> /dev/null; then
    echo "📦 PyInstaller not found. Installing..."
    $SYS_PYTHON -m pip install pyinstaller
fi

# Run PyInstaller
# --name: the output binary name
# --onefile: bundle everything into a single file
# --clean: clear pyinstaller cache before building
# --collect-data: Typer and CrewAI sometimes need their data files bundled
# --exclude-module: Skip huge AI libraries that LangChain mistakenly asks for
echo "🔨 Compiling main.py into a standalone binary..."
$SYS_PYTHON -m PyInstaller \
    --name agentic \
    --onefile \
    --clean \
    --collect-data typer \
    --collect-data loguru \
    --collect-data pydantic \
    --collect-data langchain_openai \
    --collect-all crewai \
    --hidden-import crewai \
    --hidden-import langchain_openai \
    --paths src \
    --exclude-module torch \
    --exclude-module torchvision \
    --exclude-module transformers \
    --exclude-module tensorflow \
    --exclude-module sentence-transformers \
    --exclude-module huggingface_hub \
    main.py

echo "✅ Build complete! Your standalone binary is located at ./dist/agentic"
echo "You can test it by running: ./dist/agentic --help"
