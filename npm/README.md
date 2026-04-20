# agentic-ic

> AgentIC — Autonomous AI-Driven Chip Design: Natural Language to GDSII

An npm wrapper for the Python AgentIC package. This provides a convenient CLI interface for npm users while leveraging the full power of the Python-based chip design pipeline.

## Prerequisites

- **Python 3.10+**
- **pip** (Python package manager)

The npm package will check for Python and pip on installation, and attempt to install the `agentic-ic` Python package if not found.

## Installation

### Local Development

```bash
cd npm
npm install
```

### Global Installation

```bash
npm install -g agentic-ic
```

### From GitHub (Not Published Yet)

```bash
npm install Vickyrrrrrr/AgentIC
```

## Usage

After installation, the `agentic` command will be available:

```bash
# Build a chip from natural language
agentic build --name counter --desc "8-bit counter with enable and reset"

# Check environment setup
agentic doctor

# Configure LLM credentials
agentic login
agentic configure

# Run synthesis
agentic synth --design counter

# Run static timing analysis
agentic sta --design counter

# View cache stats
agentic cache stats
```

## What It Does

This npm package is a thin wrapper that:
1. Checks for Python 3.10+ and pip
2. Automatically installs the `agentic-ic` Python package if needed
3. Passes all commands to the Python CLI

All actual chip design logic happens in Python - this npm package just makes it accessible to npm users.

## Requirements

- Node.js 18+
- Python 3.10+
- pip
- (Python packages installed by this wrapper)

## Publishing to npm

```bash
cd npm
npm login
npm publish
```

## License

Proprietary - AgentIC Team