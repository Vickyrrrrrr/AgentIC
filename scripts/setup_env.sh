#!/bin/bash
# Setup environment for AgentIC and OSS CAD Suite

export OSS_CAD_SUITE_HOME="$HOME/AgentIC/oss-cad-suite"
if [ -d "$OSS_CAD_SUITE_HOME" ]; then
    export PATH="$OSS_CAD_SUITE_HOME/bin:$PATH"
    echo "✅ OSS CAD Suite added to PATH"
else
    echo "⚠ OSS CAD Suite not found at $OSS_CAD_SUITE_HOME"
    echo "   Please wait for the installation to finish."
fi

# Verify tools
if command -v sby &> /dev/null; then
    echo "✅ SymbiYosys (sby) is available."
else
    echo "❌ SymbiYosys (sby) NOT found."
fi
