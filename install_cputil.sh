#!/bin/bash
#
# CPUtil Installation Script for CloudPRNT
# =========================================
#
# This script downloads and installs Star Micronics CPUtil on Linux x64
# CPUtil is a developer support tool for CloudPRNT servers
#
# Usage:
#   sudo bash install_cputil.sh
#   or
#   bash install_cputil.sh --user-install
#
# Documentation: https://star-m.jp/products/s_print/sdk/StarCloudPRNT/manual/en/cputil.html

set -e  # Exit on error

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
CPUTIL_VERSION="2.0.0"
CPUTIL_URL="https://www.star-m.jp/dl/cloudprnt/cputil-2.0.0-linux-x64.tar.gz"
CPUTIL_ARCHIVE="cputil-linux-x64.tar.gz"
SYSTEM_INSTALL_DIR="/opt/star/cputil"
USER_INSTALL_DIR="$HOME/.local/bin/cputil"
SYMLINK_DIR="/usr/local/bin"

# Parse arguments
USER_INSTALL=false
if [ "$1" == "--user-install" ]; then
    USER_INSTALL=true
fi

# Functions
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_os() {
    print_info "Checking operating system..."

    if [ "$(uname -s)" != "Linux" ]; then
        print_error "This script is for Linux x64 only"
        print_info "For other platforms, download from: https://www.star-m.jp/prjump/000199.html"
        exit 1
    fi

    if [ "$(uname -m)" != "x86_64" ]; then
        print_error "This script requires x86_64 architecture"
        exit 1
    fi

    print_info "OS: Linux x64 ✓"
}

check_dependencies() {
    print_info "Checking dependencies..."

    # Check for wget or curl
    if ! command -v wget &> /dev/null && ! command -v curl &> /dev/null; then
        print_error "wget or curl is required but not installed"
        print_info "Install with: sudo apt-get install wget"
        exit 1
    fi

    # Check for tar
    if ! command -v tar &> /dev/null; then
        print_error "tar is required but not installed"
        print_info "Install with: sudo apt-get install tar"
        exit 1
    fi

    print_info "Dependencies ✓"
}

download_cputil() {
    print_info "Downloading CPUtil ${CPUTIL_VERSION}..."

    local temp_dir=$(mktemp -d)
    cd "$temp_dir"

    if command -v wget &> /dev/null; then
        wget -O "$CPUTIL_ARCHIVE" "$CPUTIL_URL" || {
            print_error "Failed to download CPUtil"
            print_warn "URL might have changed. Please check: https://www.star-m.jp/prjump/000199.html"
            exit 1
        }
    else
        curl -L -o "$CPUTIL_ARCHIVE" "$CPUTIL_URL" || {
            print_error "Failed to download CPUtil"
            print_warn "URL might have changed. Please check: https://www.star-m.jp/prjump/000199.html"
            exit 1
        }
    fi

    print_info "Download complete ✓"
    echo "$temp_dir"
}

extract_cputil() {
    local temp_dir=$1
    print_info "Extracting CPUtil archive..."

    cd "$temp_dir"
    tar -xzf "$CPUTIL_ARCHIVE" || {
        print_error "Failed to extract archive"
        exit 1
    }

    # Find the extracted directory (usually cputil or similar)
    local extracted_dir=$(find . -maxdepth 1 -type d ! -name "." | head -n 1)

    if [ -z "$extracted_dir" ]; then
        print_error "Could not find extracted CPUtil directory"
        exit 1
    fi

    print_info "Extraction complete ✓"
    echo "$extracted_dir"
}

install_system_wide() {
    print_info "Installing CPUtil system-wide to ${SYSTEM_INSTALL_DIR}..."

    # Check if running as root
    if [ "$EUID" -ne 0 ]; then
        print_error "System-wide installation requires sudo/root privileges"
        print_info "Run with: sudo bash install_cputil.sh"
        print_info "Or use: bash install_cputil.sh --user-install"
        exit 1
    fi

    local temp_dir=$1
    local extracted_dir=$2

    # Create installation directory
    mkdir -p "$SYSTEM_INSTALL_DIR"

    # Copy files
    cp -r "$temp_dir/$extracted_dir"/* "$SYSTEM_INSTALL_DIR/" || {
        print_error "Failed to copy files to ${SYSTEM_INSTALL_DIR}"
        exit 1
    }

    # Make cputil executable
    chmod +x "$SYSTEM_INSTALL_DIR/cputil" || {
        print_error "Failed to make cputil executable"
        exit 1
    }

    # Create symbolic link
    if [ -d "$SYMLINK_DIR" ]; then
        ln -sf "$SYSTEM_INSTALL_DIR/cputil" "$SYMLINK_DIR/cputil" || {
            print_warn "Failed to create symlink in ${SYMLINK_DIR}"
            print_info "You may need to add ${SYSTEM_INSTALL_DIR} to your PATH manually"
        }
        print_info "Symbolic link created in ${SYMLINK_DIR} ✓"
    fi

    print_info "System-wide installation complete ✓"
    print_info "CPUtil installed to: ${SYSTEM_INSTALL_DIR}"
}

install_user() {
    print_info "Installing CPUtil for current user to ${USER_INSTALL_DIR}..."

    local temp_dir=$1
    local extracted_dir=$2

    # Create installation directory
    mkdir -p "$USER_INSTALL_DIR"

    # Copy files
    cp -r "$temp_dir/$extracted_dir"/* "$USER_INSTALL_DIR/" || {
        print_error "Failed to copy files to ${USER_INSTALL_DIR}"
        exit 1
    }

    # Make cputil executable
    chmod +x "$USER_INSTALL_DIR/cputil" || {
        print_error "Failed to make cputil executable"
        exit 1
    }

    # Add to PATH if not already there
    local shell_rc=""
    if [ -n "$BASH_VERSION" ]; then
        shell_rc="$HOME/.bashrc"
    elif [ -n "$ZSH_VERSION" ]; then
        shell_rc="$HOME/.zshrc"
    fi

    if [ -n "$shell_rc" ]; then
        if ! grep -q "$USER_INSTALL_DIR" "$shell_rc" 2>/dev/null; then
            echo "" >> "$shell_rc"
            echo "# CPUtil path (added by install_cputil.sh)" >> "$shell_rc"
            echo "export PATH=\"$USER_INSTALL_DIR:\$PATH\"" >> "$shell_rc"
            print_info "Added CPUtil to PATH in ${shell_rc}"
            print_warn "Run 'source ${shell_rc}' or restart your terminal to use cputil"
        fi
    fi

    print_info "User installation complete ✓"
    print_info "CPUtil installed to: ${USER_INSTALL_DIR}"
}

verify_installation() {
    print_info "Verifying installation..."

    # Test cputil command
    if command -v cputil &> /dev/null; then
        local version_output=$(cputil supportedinputs 2>&1 || echo "error")
        if [[ "$version_output" == *"error"* ]]; then
            print_warn "CPUtil installed but may not be working correctly"
            return 1
        else
            print_info "CPUtil is working correctly ✓"
            print_info "Supported input formats:"
            cputil supportedinputs
            return 0
        fi
    else
        print_warn "CPUtil command not found in PATH"
        if [ "$USER_INSTALL" = true ]; then
            print_info "Run: export PATH=\"${USER_INSTALL_DIR}:\$PATH\""
            print_info "Or restart your terminal"
        fi
        return 1
    fi
}

cleanup() {
    local temp_dir=$1
    print_info "Cleaning up temporary files..."
    rm -rf "$temp_dir"
    print_info "Cleanup complete ✓"
}

show_usage() {
    cat << EOF

${GREEN}CPUtil Installation Summary${NC}
================================

CPUtil has been successfully installed!

${GREEN}Usage Examples:${NC}

1. Check supported input formats:
   cputil supportedinputs

2. Get media types for Star Markup:
   cputil mediatypes-mime text/vnd.star.markup

3. Convert Star Markup to StarPRNT commands:
   cputil thermal3 decode application/vnd.star.starprnt input.stm output.bin

4. Decode printer status:
   cputil jsonstatus "23 86 00 00 00 00 00 00 00 00 00"

${GREEN}Common Options:${NC}

  thermal2/thermal58     2inch/58mm printer (384 dots) - mC-Print2
  thermal3/thermal80     3inch/80mm printer (576 dots) - mC-Print3, TSP650II
  thermal4/thermal112    4inch/112mm printer (832 dots) - TSP800II

  dither                 Apply dithering to images
  scale-to-fit          Scale image to fit print area
  drawer-end            Open cash drawer at end of job
  buzzer-end X          Sound buzzer X times at end of job

${GREEN}Documentation:${NC}
  https://star-m.jp/products/s_print/sdk/StarCloudPRNT/manual/en/cputil.html

${GREEN}Python Integration Example:${NC}
  import subprocess

  # Get media types
  result = subprocess.run(['cputil', 'mediatypes-mime', 'text/vnd.star.markup'],
                         capture_output=True, text=True)
  media_types = json.loads(result.stdout)

  # Convert markup to StarPRNT
  subprocess.run(['cputil', 'thermal3', 'decode',
                 'application/vnd.star.starprnt', 'input.stm', 'output.bin'])

EOF
}

# Main installation flow
main() {
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║                                                            ║"
    echo "║          CPUtil Installation Script for CloudPRNT          ║"
    echo "║                  Star Micronics CPUtil                     ║"
    echo "║                    Version ${CPUTIL_VERSION}                        ║"
    echo "║                                                            ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""

    check_os
    check_dependencies

    local temp_dir=$(download_cputil)
    local extracted_dir=$(extract_cputil "$temp_dir")

    if [ "$USER_INSTALL" = true ]; then
        install_user "$temp_dir" "$extracted_dir"
    else
        install_system_wide "$temp_dir" "$extracted_dir"
    fi

    cleanup "$temp_dir"

    echo ""
    verify_installation || true

    echo ""
    show_usage

    print_info "Installation complete! ✓"
    echo ""
}

# Run main function
main
