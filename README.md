# OpenEasySync

Open-source, cross-platform clean-room re-implementation of the EasySync application for the Ours Technology Inc. (OTi / UR Technology Inc.) OTi-2108 USB Host-to-Host Data Bridge (`0ea0:2108`).

<https://gitlab.com/brlin/openeasysync>  
[![The GitLab CI pipeline status badge of the project's `main` branch](https://gitlab.com/brlin/openeasysync/badges/main/pipeline.svg?ignore_skipped=true "Click here to check out the comprehensive status of the GitLab CI pipelines")](https://gitlab.com/brlin/openeasysync/-/pipelines) [![GitHub Actions workflow status badge](https://github.com/brlin-tw/openeasysync/actions/workflows/check-potential-problems.yml/badge.svg "GitHub Actions workflow status")](https://github.com/brlin-tw/openeasysync/actions/workflows/check-potential-problems.yml) [![pre-commit enabled badge](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white "This project uses pre-commit to check potential problems")](https://pre-commit.com/) [![REUSE Specification compliance badge](https://api.reuse.software/badge/gitlab.com/brlin/openeasysync "This project complies to the REUSE specification to decrease software licensing costs")](https://api.reuse.software/info/gitlab.com/brlin/openeasysync)

## Features

- **Direct USB Bulk-Only Transport (BOT):** Native user-space access via PyUSB (`libusb`) without needing custom kernel drivers or OS drive letters.
- **SCSI Pass-Through Fallbacks:** Supports Linux `SG_IO` (`/dev/sg*`) and Windows `SPTI` (`\\.\X:`).
- **Transfer Line Mode Protocol (`0x007F`):** Full 64 KB (`0x10000`) block framing, packet splitter, packet reassembler, and link polling state machine.
- **552-Byte Directory & Drive Layout:** Cross-platform Unix $\leftrightarrow$ Win32 path translation and 64-bit `FILETIME` timestamp preservation.
- **File Transfer Operations:**
  - Single file upload (`0x09`) and download (`0x0A`).
  - Recursive batch file upload (`0x0B`) and download (`0x0C`).
  - Overwrite pre-checking (`0x0F`, `0x14`, `0x15`).
  - Mid-transfer cancellation (`0x0D`).
  - `<OTIMSG>` XML handshakes (`CONNECT`, `DISCONNECT`).
- **Offline Dual-Host Mock Transport:** `MockLoopbackTransport` allows automated end-to-end integration testing in unit test runners without physical hardware.
- **Dual Frontends:**
  - Headless CLI and peer daemon (`openeasysync`).
  - Native Dual-Pane Graphical Interface (`openeasysync-gui` using PySide6).

## Installation

```bash
# Clone the repository
git clone https://gitlab.com/brlin/openeasysync.git
cd openeasysync

# Create a virtual environment and install OpenEasySync
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[gui,dev]
```

## Usage

### Command-Line Interface (CLI)

```bash
# Query connected OTi-2108 hardware information and link speeds
openeasysync info

# Run background daemon to service file transfer requests from remote peer
openeasysync daemon

# List files on remote peer
openeasysync ls
openeasysync ls "C:\Windows"

# Upload a file or directory to remote peer
openeasysync put ./local_file.txt "C:\local_file.txt"
openeasysync put ./my_folder "C:\my_folder"

# Download a file or directory from remote peer
openeasysync get "C:\remote_file.txt" ./downloaded_file.txt
```

### Graphical User Interface (GUI)

```bash
# Launch the dual-pane file manager
openeasysync-gui
```

### Running Tests

```bash
# Run complete test suite
pytest tests/ -v

# Run with test coverage report
pytest tests/ --cov=openeasysync --cov-report=term-missing
```

## Licensing

Unless otherwise noted([comment headers](https://reuse.software/spec-3.3/#comment-headers)/[REUSE.toml](https://reuse.software/spec-3.3/#reusetoml)), this product is licensed under [the 3.0 version of the GNU Affero General Public License license](https://www.gnu.org/licenses/agpl.html), or any of its more recent versions of your preference.

This work complies to [the REUSE Specification](https://reuse.software/spec/), refer to the [REUSE - Make licensing easy for everyone](https://reuse.software/) website for info regarding the licensing of this product.
