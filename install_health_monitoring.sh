#!/bin/bash
# Installation script for Hitron CODA cable modem health monitoring
# Run as: sudo ./install-health-monitoring.sh

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   error "This script must be run as root (use sudo)"
   exit 1
fi

# Check if Netdata is installed
if ! command -v netdata &> /dev/null; then
    error "Netdata is not installed. Please install Netdata first."
    exit 1
fi

log "Installing Hitron CODA health monitoring for Netdata..."

# Detect Netdata installation type and paths
detect_netdata_installation() {
    log "Detecting Netdata installation type and paths..."
    
    # Initialize variables
    NETDATA_CONFIG_DIR=""
    NETDATA_HEALTH_DIR=""
    NETDATA_PLUGINS_DIR=""
    NETDATA_NOTIFY_DIR=""
    INSTALL_TYPE=""
    
    # Check for Snap installation
    if command -v snap >/dev/null 2>&1 && snap list 2>/dev/null | grep -q netdata; then
        INSTALL_TYPE="snap"
        NETDATA_CONFIG_DIR="/var/snap/netdata/current/etc/netdata"
        NETDATA_HEALTH_DIR="${NETDATA_CONFIG_DIR}/health.d"
        NETDATA_PLUGINS_DIR="/snap/netdata/current/usr/libexec/netdata/python.d"
        NETDATA_NOTIFY_DIR="/snap/netdata/current/usr/libexec/netdata/plugins.d"
        
    # Check for Docker installation
    elif docker ps 2>/dev/null | grep -q netdata; then
        INSTALL_TYPE="docker"
        warn "Docker installation detected. Manual installation required."
        warn "Use: docker cp <files> netdata_container:/path/to/destination"
        return 1
        
    # Check for standard package installation
    elif [ -d "/usr/libexec/netdata" ] && [ -d "/etc/netdata" ]; then
        INSTALL_TYPE="package"
        NETDATA_CONFIG_DIR="/etc/netdata"
        NETDATA_HEALTH_DIR="${NETDATA_CONFIG_DIR}/health.d"
        NETDATA_PLUGINS_DIR="/usr/libexec/netdata/python.d"
        NETDATA_NOTIFY_DIR="/usr/libexec/netdata/plugins.d"
        
    # Check for alternative package paths
    elif [ -d "/usr/lib/netdata" ] && [ -d "/etc/netdata" ]; then
        INSTALL_TYPE="package-alt"
        NETDATA_CONFIG_DIR="/etc/netdata"
        NETDATA_HEALTH_DIR="${NETDATA_CONFIG_DIR}/health.d"
        NETDATA_PLUGINS_DIR="/usr/lib/netdata/python.d"
        NETDATA_NOTIFY_DIR="/usr/lib/netdata/plugins.d"
        
    # Check for opt installation (static builds)
    elif [ -d "/opt/netdata" ]; then
        INSTALL_TYPE="static"
        NETDATA_CONFIG_DIR="/opt/netdata/etc/netdata"
        NETDATA_HEALTH_DIR="${NETDATA_CONFIG_DIR}/health.d"
        NETDATA_PLUGINS_DIR="/opt/netdata/usr/libexec/netdata/python.d"
        NETDATA_NOTIFY_DIR="/opt/netdata/usr/libexec/netdata/plugins.d"
        
    # Auto-detect using find commands
    else
        INSTALL_TYPE="auto-detect"
        log "Auto-detecting Netdata paths..."
        
        # Find config directory
        NETDATA_CONFIG_DIR=$(find /etc /var /opt -name "netdata" -type d 2>/dev/null | head -1)
        if [ -z "$NETDATA_CONFIG_DIR" ]; then
            error "Could not find Netdata config directory"
            return 1
        fi
        
        # Find plugins directory
        NETDATA_PLUGINS_DIR=$(find /usr /opt /snap -path "*/netdata/python.d" -type d 2>/dev/null | head -1)
        if [ -z "$NETDATA_PLUGINS_DIR" ]; then
            error "Could not find Netdata python.d plugins directory"
            return 1
        fi
        
        # Find notify directory
        NETDATA_NOTIFY_DIR=$(find /usr /opt /snap -path "*/netdata/plugins.d" -type d 2>/dev/null | head -1)
        if [ -z "$NETDATA_NOTIFY_DIR" ]; then
            warn "Could not find Netdata plugins.d directory (notifications will be skipped)"
        fi
        
        NETDATA_HEALTH_DIR="${NETDATA_CONFIG_DIR}/health.d"
    fi
    
    # Verify directories exist
    if [ ! -d "$NETDATA_CONFIG_DIR" ]; then
        error "Config directory does not exist: $NETDATA_CONFIG_DIR"
        return 1
    fi
    
    if [ ! -d "$NETDATA_PLUGINS_DIR" ]; then
        error "Plugins directory does not exist: $NETDATA_PLUGINS_DIR"
        return 1
    fi
    
    success "Detected Netdata installation type: $INSTALL_TYPE"
    log "Config directory: $NETDATA_CONFIG_DIR"
    log "Health directory: $NETDATA_HEALTH_DIR"  
    log "Plugins directory: $NETDATA_PLUGINS_DIR"
    if [ -n "$NETDATA_NOTIFY_DIR" ]; then
        log "Notify directory: $NETDATA_NOTIFY_DIR"
    fi
    
    return 0
}

# Detect Netdata installation
if ! detect_netdata_installation; then
    error "Failed to detect Netdata installation. Please install manually."
    exit 1
fi

# Create directories if they don't exist
mkdir -p "$NETDATA_HEALTH_DIR"

# Check if the main plugin exists
PLUGIN_FILE="$NETDATA_PLUGINS_DIR/hitron_coda.chart.py"
if [ ! -f "$PLUGIN_FILE" ]; then
    warn "Main plugin file not found at $PLUGIN_FILE"
    warn "Please install the hitron_coda.chart.py plugin first"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Install health configuration
HEALTH_CONFIG="$NETDATA_HEALTH_DIR/hitron_coda.conf"
log "Installing health configuration to $HEALTH_CONFIG"

cat > "$HEALTH_CONFIG" << 'EOF'
# Netdata health configuration for Hitron CODA cable modem
# Generated by install-health-monitoring.sh

# Downstream Power Level Monitoring
template: hitron_coda_downstream_power_low
      on: hitron_coda.downstream_power
   class: Latency
    type: Cable Modem
component: Network
    calc: $this
   units: dBmV
   every: 10s
    warn: $this < -15
    crit: $this < -20
   delay: down 5m multiplier 1.5 max 1h
    info: downstream channel power level is too low
      to: webmaster

template: hitron_coda_downstream_power_high
      on: hitron_coda.downstream_power
   class: Latency
    type: Cable Modem
component: Network
    calc: $this
   units: dBmV
   every: 10s
    warn: $this > 15
    crit: $this > 20
