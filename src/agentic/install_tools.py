import os
import sys
import platform
import tarfile
import urllib.request
from pathlib import Path
from rich.console import Console

console = Console()

def get_platform_info():
    sys_os = platform.system().lower()
    machine = platform.machine().lower()
    
    if sys_os == "darwin":
        os_name = "darwin"
        arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    elif sys_os == "linux":
        os_name = "linux"
        if machine in ("x86_64", "amd64"):
            arch = "x64"
        elif machine in ("arm64", "aarch64"):
            arch = "arm64"
        elif "riscv64" in machine:
            arch = "rv64"
        else:
            arch = "x64"
    elif sys_os == "windows":
        os_name = "windows"
        arch = "x64"
    else:
        return None, None
        
    return os_name, arch

def install_oss_cad_suite(target_dir):
    os_name, arch = get_platform_info()
    if not os_name:
        console.print("[red]Unsupported platform for automatic EDA tool installation.[/red]")
        return False
        
    try:
        import requests
    except ImportError:
        console.print("[red]Missing 'requests' module. Please 'pip install requests' first.[/red]")
        return False

    api_url = "https://api.github.com/repos/YosysHQ/oss-cad-suite-build/releases/latest"
    console.print(f"[bold]Fetching latest OSS CAD Suite release info...[/bold]")
    
    try:
        resp = requests.get(api_url)
        resp.raise_for_status()
        release_data = resp.json()
    except Exception as e:
        console.print(f"[red]Failed to fetch release info: {e}[/red]")
        return False
        
    assets = release_data.get("assets", [])
    expected_suffix = f"{os_name}-{arch}.tgz"
    download_url = None
    
    # E.g. oss-cad-suite-darwin-arm64-XXXXXXXX.tgz
    for asset in assets:
        name = asset["name"]
        if name.endswith(expected_suffix) or (os_name == "windows" and name.endswith(".exe") and "windows-x64" in name):
            download_url = asset["browser_download_url"]
            filename = name
            break
            
    if not download_url:
        console.print(f"[red]Could not find a pre-compiled binary for {os_name}-{arch}[/red]")
        return False

    console.print(f"[yellow]Downloading {filename} (this may take a while)...[/yellow]")
    
    # Download the file
    import tempfile
    ext = ".exe" if os_name == "windows" else ".tgz"
    fd, temp_path = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    
    try:
        urllib.request.urlretrieve(download_url, temp_path)
        console.print("[green]Download complete. Extracting...[/green]")
        
        os.makedirs(target_dir, exist_ok=True)
        if ext == ".tgz":
            with tarfile.open(temp_path, "r:gz") as tar:
                tar.extractall(path=target_dir)
            console.print(f"[green]Successfully installed to {target_dir}[/green]")
        else:
            console.print(f"[red]Windows self-extracting .exe needs manual run: {temp_path}[/red]")
            return False
            
    except Exception as e:
        console.print(f"[red]Extraction failed: {e}[/red]")
        return False
    finally:
        if os.path.exists(temp_path) and ext == ".tgz":
            os.remove(temp_path)
            
    return True
