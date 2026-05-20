import os, glob

files_to_update = [
    '/home/vickynishad/AgentIC/server/api.py',
    '/home/vickynishad/AgentIC/server/lab.py',
    '/home/vickynishad/AgentIC/src/agentic/cli.py',
    '/home/vickynishad/AgentIC/src/agentic/config.py',
    '/home/vickynishad/AgentIC/src/agentic/core/cache_manager.py',
    '/home/vickynishad/AgentIC/src/agentic/core/context_manager.py',
    '/home/vickynishad/AgentIC/src/agentic/core/provider_manager.py',
    '/home/vickynishad/AgentIC/src/agentic/core/usage_tracker.py',
    '/home/vickynishad/AgentIC/src/agentic/core/spec_generator.py',
    '/home/vickynishad/AgentIC/src/agentic/orchestrator.py',
    '/home/vickynishad/AgentIC/src/agentic/tools/api_manager.py',
    '/home/vickynishad/AgentIC/src/agentic/tools/rate_limiter.py',
    '/home/vickynishad/AgentIC/README.md'
]

for fp in files_to_update:
    if os.path.exists(fp):
        with open(fp, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Replace specific model defaults and provider names
        content = content.replace('gpt-4o', 'infinity')
        content = content.replace('azure', 'infinity')
        content = content.replace('AZURE', 'INFINITY')
        
        with open(fp, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f'Updated {fp}')
    else:
        print(f'File not found: {fp}')
