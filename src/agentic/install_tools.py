import os
import sys
import shutil
import platform
import tarfile
import tempfile
from pathlib import Path
from rich.console import Console

console = Console()

OSS_CAD_SUITE_REQUIRED_BINS = (
    "yosys",
    "sby",
    "verilator",
    "iverilog",
    "vvp",
)

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

def _safe_extract_tar(tar: tarfile.TarFile, destination: str) -> None:
    """Extract a tarball without allowing path traversal outside destination."""
    destination_path = Path(destination).resolve()
    for member in tar.getmembers():
        member_path = (destination_path / member.name).resolve()
        if not str(member_path).startswith(str(destination_path)):
            raise RuntimeError(f"Unsafe path in archive: {member.name}")
    tar.extractall(path=destination)


def _move_tree_contents(source: str, destination: str) -> None:
    """Move extracted oss-cad-suite contents into the requested final directory."""
    os.makedirs(destination, exist_ok=True)
    for item in os.listdir(source):
        src = os.path.join(source, item)
        dst = os.path.join(destination, item)
        if os.path.isdir(dst) and os.path.isdir(src):
            shutil.rmtree(dst)
        elif os.path.exists(dst):
            os.remove(dst)
        shutil.move(src, dst)


def _missing_oss_cad_suite_bins(target_dir: str) -> list[str]:
    """Return required OSS CAD Suite binaries missing from target_dir/bin."""
    bin_dir = os.path.join(os.path.abspath(os.path.expanduser(str(target_dir))), "bin")
    missing = []
    for name in OSS_CAD_SUITE_REQUIRED_BINS:
        candidates = [os.path.join(bin_dir, name)]
        if platform.system().lower() == "windows":
            candidates.append(os.path.join(bin_dir, f"{name}.exe"))
        if not any(os.path.exists(path) for path in candidates):
            missing.append(name)
    return missing


def install_oss_cad_suite(target_dir):
    """Install OSS CAD Suite so target_dir itself is the suite root.

    GitHub archives contain a top-level ``oss-cad-suite/`` directory.  Users,
    docs, and the rest of AgentIC expect ``OSS_CAD_SUITE_HOME`` to point to the
    directory containing ``bin/yosys``.  This function normalizes that layout
    after extraction instead of leaving ``target/oss-cad-suite/bin`` behind.
    """
    os_name, arch = get_platform_info()
    if not os_name:
        console.print("[red]Unsupported platform for automatic EDA tool installation.[/red]")
        return False
        
    try:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
    except ImportError:
        console.print("[red]Missing 'requests' module. Please 'pip install requests urllib3' first.[/red]")
        return False

    session = requests.Session()
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[ 502, 503, 504 ])
    session.mount('https://', HTTPAdapter(max_retries=retries))

    api_url = "https://api.github.com/repos/YosysHQ/oss-cad-suite-build/releases/latest"
    console.print(f"[bold]Fetching latest OSS CAD Suite release info...[/bold]")
    
    try:
        resp = session.get(api_url, timeout=15)
        resp.raise_for_status()
        release_data = resp.json()
    except Exception as e:
        console.print(f"[red]Failed to fetch release info: {e}[/red]")
        return False
        
    assets = release_data.get("assets", [])
    expected_prefix = f"oss-cad-suite-{os_name}-{arch}-"
    expected_suffix = ".tgz"
    download_url = None
    filename = None
    
    for asset in assets:
        name = asset["name"]
        if (
            name.startswith(expected_prefix)
            and name.endswith(expected_suffix)
        ) or (
            os_name == "windows"
            and name.endswith(".exe")
            and "windows-x64" in name
        ):
            download_url = asset["browser_download_url"]
            filename = name
            break
            
    if not download_url:
        console.print(f"[red]Could not find a pre-compiled binary for {os_name}-{arch}[/red]")
        return False

    console.print(f"[yellow]Downloading {filename} (this may take a while)...[/yellow]")
    
    target_dir = os.path.abspath(os.path.expanduser(str(target_dir)))
    missing_before = _missing_oss_cad_suite_bins(target_dir)
    if not missing_before:
        console.print(f"[green]OSS CAD Suite already present at {target_dir}[/green]")
        return True
    if os.path.isdir(target_dir):
        console.print(
            f"[yellow]OSS CAD Suite at {target_dir} is incomplete; repairing missing tools: "
            f"{', '.join(missing_before)}[/yellow]"
        )

    ext = ".exe" if os_name == "windows" else ".tgz"
    fd, temp_path = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    extract_root = tempfile.mkdtemp(prefix="agentic_oss_cad_")
    
    try:
        from rich.progress import Progress
        with session.get(download_url, stream=True, timeout=30) as r:
            r.raise_for_status()
            total_length = int(r.headers.get("content-length", 0))
            
            with Progress() as progress:
                task = progress.add_task(f"[cyan]Downloading...", total=total_length)
                with open(temp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            progress.update(task, advance=len(chunk))
                            
        console.print("[green]Download complete. Extracting...[/green]")
        
        if ext == ".tgz":
            with tarfile.open(temp_path, "r:gz") as tar:
                _safe_extract_tar(tar, extract_root)

            extracted_suite = os.path.join(extract_root, "oss-cad-suite")
            if not os.path.isdir(extracted_suite):
                candidates = [
                    os.path.join(extract_root, name)
                    for name in os.listdir(extract_root)
                    if os.path.isdir(os.path.join(extract_root, name))
                ]
                extracted_suite = next(
                    (
                        path
                        for path in candidates
                        if os.path.exists(os.path.join(path, "bin", "yosys"))
                    ),
                    "",
                )
            if not extracted_suite:
                console.print("[red]Archive did not contain an oss-cad-suite/bin layout.[/red]")
                return False

            _move_tree_contents(extracted_suite, target_dir)
            missing_after = _missing_oss_cad_suite_bins(target_dir)
            if missing_after:
                console.print(
                    "[red]Install verification failed. Missing required OSS CAD Suite tools: "
                    + ", ".join(missing_after)
                    + "[/red]"
                )
                return False

            console.print(f"[green]Successfully installed to {target_dir}[/green]")
        else:
            console.print(f"[red]Windows self-extracting .exe needs manual run: {temp_path}[/red]")
            return False
            
    except Exception as e:
        console.print(f"[red]Download or extraction failed: {e}[/red]")
        return False
    finally:
        if os.path.exists(temp_path) and ext == ".tgz":
            os.remove(temp_path)
        shutil.rmtree(extract_root, ignore_errors=True)
            
    return True
