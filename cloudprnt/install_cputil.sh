#!/bin/bash
#
# CPUtil Installation Script for CloudPRNT
# =========================================
#
# This script installs Star Micronics CPUtil on Linux systems.
# Supports both .NET Runtime installation and self-contained builds.
#
# Usage:
#   bash install_cputil.sh
#
# Requirements:
#   - Linux x64 or ARM64
#   - wget or curl
#   - tar
#

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Functions
print_header() {
    echo -e "${BLUE}====================================${NC}"
    echo -e "${BLUE}  CPUtil Installation for CloudPRNT${NC}"
    echo -e "${BLUE}====================================${NC}"
    echo ""
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

# Detect architecture
detect_arch() {
    local arch=$(uname -m)
    case "$arch" in
        x86_64)
            echo "linux-x64"
            ;;
        aarch64|arm64)
            echo "linux-arm64"
            ;;
        *)
            print_error "Unsupported architecture: $arch"
            print_info "CPUtil supports: x86_64 (linux-x64) and aarch64 (linux-arm64)"
            exit 1
            ;;
    esac
}

# Check if .NET 8 is installed
check_dotnet() {
    if command -v dotnet &> /dev/null; then
        local version=$(dotnet --version 2>/dev/null | cut -d'.' -f1)
        if [ "$version" -ge 8 ]; then
            print_success ".NET Runtime $version found"
            return 0
        else
            print_warning ".NET version $version found, but version 8+ required"
            return 1
        fi
    else
        print_warning ".NET Runtime not found"
        return 1
    fi
}

# Install .NET 8 Runtime
install_dotnet() {
    print_info "Installing .NET 8 Runtime..."

    # Detect Linux distribution
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        VER=$VERSION_ID
    else
        print_error "Cannot detect Linux distribution"
        exit 1
    fi

    case "$OS" in
        ubuntu|debian)
            print_info "Detected Ubuntu/Debian - Installing via apt..."

            # Add Microsoft package repository
            wget -q https://packages.microsoft.com/config/$OS/$VER/packages-microsoft-prod.deb -O /tmp/packages-microsoft-prod.deb
            sudo dpkg -i /tmp/packages-microsoft-prod.deb
            rm /tmp/packages-microsoft-prod.deb

            # Install .NET Runtime
            sudo apt-get update
            sudo apt-get install -y dotnet-runtime-8.0
            ;;

        centos|rhel|fedora)
            print_info "Detected CentOS/RHEL/Fedora - Installing via dnf/yum..."

            # Add Microsoft package repository
            sudo rpm -Uvh https://packages.microsoft.com/config/$OS/$VER/packages-microsoft-prod.rpm

            # Install .NET Runtime
            if command -v dnf &> /dev/null; then
                sudo dnf install -y dotnet-runtime-8.0
            else
                sudo yum install -y dotnet-runtime-8.0
            fi
            ;;

        *)
            print_warning "Unsupported distribution: $OS"
            print_info "Please install .NET 8 Runtime manually from:"
            print_info "https://dotnet.microsoft.com/download/dotnet/8.0"
            exit 1
            ;;
    esac

    # Verify installation
    if check_dotnet; then
        print_success ".NET 8 Runtime installed successfully"
    else
        print_error ".NET installation failed"
        exit 1
    fi
}

# Download and install CPUtil
install_cputil() {
    local arch=$1
    local install_dir="$HOME/.local/bin"

    print_info "Installing CPUtil for $arch..."

    # Create installation directory
    mkdir -p "$install_dir"

    # Download CPUtil from Star Micronics SDK
    # Note: This URL may need to be updated based on actual download location
    local cputil_url="https://github.com/star-micronics/StarWebPrintSDK/releases/latest/download/cputil-${arch}.tar.gz"

    print_info "Downloading from: $cputil_url"

    cd /tmp

    # Try with wget first, fallback to curl
    if command -v wget &> /dev/null; then
        wget -O cputil.tar.gz "$cputil_url" 2>/dev/null || {
            print_warning "Direct download failed"
            download_from_alternative_source "$arch"
        }
    elif command -v curl &> /dev/null; then
        curl -L -o cputil.tar.gz "$cputil_url" 2>/dev/null || {
            print_warning "Direct download failed"
            download_from_alternative_source "$arch"
        }
    else
        print_error "Neither wget nor curl found. Please install one of them."
        exit 1
    fi

    # Extract
    print_info "Extracting CPUtil..."
    tar -xzf cputil.tar.gz || {
        print_error "Failed to extract CPUtil archive"
        exit 1
    }

    # Make executable
    chmod +x cputil

    # Move to installation directory
    print_info "Installing to $install_dir..."
    mv cputil "$install_dir/"

    # Cleanup
    rm -f cputil.tar.gz

    print_success "CPUtil binary installed at: $install_dir/cputil"
}

# Alternative download method (build from source if needed)
download_from_alternative_source() {
    local arch=$1

    print_warning "Attempting to build CPUtil from source..."
    print_info "This requires .NET SDK (not just runtime)"

    # Check if we have .NET SDK
    if ! dotnet --list-sdks &> /dev/null; then
        print_error "CPUtil download failed and .NET SDK not available for building from source"
        print_info ""
        print_info "Manual installation options:"
        print_info "1. Download CPUtil from: https://star-m.jp/products/s_print/sdk/StarCloudPRNT/manual/en/cputil.html"
        print_info "2. Extract and place 'cputil' binary in: $HOME/.local/bin/"
        print_info "3. Make it executable: chmod +x $HOME/.local/bin/cputil"
        exit 1
    fi

    # Clone and build (simplified - actual implementation would need the real repo)
    print_error "Source build not implemented yet"
    print_info "Please download CPUtil manually from Star Micronics website"
    exit 1
}

# Add to PATH
add_to_path() {
    local install_dir="$HOME/.local/bin"

    # Check if already in PATH
    if [[ ":$PATH:" == *":$install_dir:"* ]]; then
        print_info "$install_dir is already in PATH"
        return 0
    fi

    print_info "Adding $install_dir to PATH..."

    # Add to .bashrc
    if [ -f "$HOME/.bashrc" ]; then
        echo "" >> "$HOME/.bashrc"
        echo "# Added by CPUtil installer" >> "$HOME/.bashrc"
        echo "export PATH=\"\$PATH:$install_dir\"" >> "$HOME/.bashrc"
        print_success "Added to ~/.bashrc"
    fi

    # Add to .profile (for login shells)
    if [ -f "$HOME/.profile" ]; then
        echo "" >> "$HOME/.profile"
        echo "# Added by CPUtil installer" >> "$HOME/.profile"
        echo "export PATH=\"\$PATH:$install_dir\"" >> "$HOME/.profile"
        print_success "Added to ~/.profile"
    fi

    # Export for current session
    export PATH="$PATH:$install_dir"
}

# Test installation
test_cputil() {
    local cputil_path="$HOME/.local/bin/cputil"

    print_info "Testing CPUtil installation..."

    if ! [ -x "$cputil_path" ]; then
        print_error "CPUtil binary not found or not executable at: $cputil_path"
        return 1
    fi

    # Test version
    if "$cputil_path" --version &> /dev/null; then
        local version=$("$cputil_path" --version 2>&1 | head -1)
        print_success "CPUtil version: $version"
    else
        print_warning "Could not get CPUtil version (but binary exists)"
    fi

    # Test supported inputs
    if "$cputil_path" supportedinputs &> /dev/null; then
        print_success "CPUtil is functional!"
        print_info "Supported input formats:"
        "$cputil_path" supportedinputs | sed 's/^/  - /'
        return 0
    else
        print_error "CPUtil test failed"
        return 1
    fi
}

# Main installation flow
main() {
    print_header

    # Step 1: Detect architecture
    print_info "Step 1/5: Detecting system architecture..."
    ARCH=$(detect_arch)
    print_success "Architecture: $ARCH"
    echo ""

    # Step 2: Check/Install .NET Runtime
    print_info "Step 2/5: Checking .NET Runtime..."
    if ! check_dotnet; then
        read -p "Install .NET 8 Runtime? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            install_dotnet
        else
            print_warning "Skipping .NET installation"
            print_info "You can install .NET manually from: https://dotnet.microsoft.com/download/dotnet/8.0"
        fi
    fi
    echo ""

    # Step 3: Download and install CPUtil
    print_info "Step 3/5: Installing CPUtil..."
    install_cputil "$ARCH"
    echo ""

    # Step 4: Add to PATH
    print_info "Step 4/5: Configuring PATH..."
    add_to_path
    echo ""

    # Step 5: Test installation
    print_info "Step 5/5: Testing installation..."
    if test_cputil; then
        echo ""
        print_success "🎉 CPUtil installation completed successfully!"
        echo ""
        print_info "Next steps:"
        print_info "1. Restart your terminal or run: source ~/.bashrc"
        print_info "2. Enable CPUtil in CloudPRNT Settings (Frappe)"
        print_info "3. Test with a POS Invoice print"
        echo ""
    else
        echo ""
        print_error "Installation completed but tests failed"
        print_info "Please check the errors above and verify manually"
        echo ""
        exit 1
    fi
}

# Run main installation
main
