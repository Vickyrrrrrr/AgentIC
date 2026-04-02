import re
with open("src/agentic/cli.py", "r") as f:
    text = f.read()

# Replace console = Console()
theme_code = """from rich.theme import Theme
claude_theme = Theme({
    "info": "dim white",
    "accent": "#d97757",
    "success": "#32997b",
    "warning": "#e0b04a",
    "error": "#d45851",
    "heading": "bold #e5e1d8",
    "border": "#8f8a80",
    "spinner": "#d97757"
})
console = Console(theme=claude_theme)
"""
text = text.replace("console = Console()", theme_code)

color_map = {
    "bold green": "success",
    "bold cyan": "accent",
    "bold yellow": "warning",
    "bold red": "error",
    "bold magenta": "accent",
    "bold blue": "info",
    "cyan": "accent",
    "green": "success",
    "yellow": "warning",
    "red": "error",
    "magenta": "accent",
    "blue": "info"
}

for old, new in color_map.items():
    text = text.replace(f"[{old}]", f"[{new}]")
    text = text.replace(f"[/{old}]", f"[/{new}]")
    
# Check for status
text = re.sub(r'(with console\.status\([^)]+)\):', r'\1, spinner="dots12", spinner_style="spinner"):', text)

with open("src/agentic/cli.py", "w") as f:
    f.write(text)

print("Done cli")
