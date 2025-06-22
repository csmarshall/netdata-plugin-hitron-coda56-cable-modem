# Netdata Hitron CODA Cable Modem Plugin

A comprehensive Netdata plugin for monitoring Hitron CODA cable modems with **31 downstream channels**, **5 upstream channels**, **DOCSIS 3.1 OFDM support**, and **advanced collection modes** for optimal performance and reliability.

## 🚀 Enhanced Features (v1.2.0)

- **50+ Metrics**: Complete visibility into all cable modem channels and performance
- **DOCSIS 3.1 Support**: Monitor next-generation OFDM channels for gigabit+ speeds
- **Dual Collection Modes**: Serial (safe, default) and Parallel (fast) collection modes
- **Smart Timeout Management**: Two-tier timeout system with auto-calculated retries
- **Built-in Health Monitoring**: Track plugin reliability, response times, and success rates
- **Ultra-Conservative Mode**: Guaranteed one-request-at-a-time for sensitive modems

## 📊 Charts Generated (15 Total)

### Plugin Monitoring
- **Plugin Health**: Success rates, response times, collection times, consecutive failures, active endpoints

### Signal Quality  
- **Downstream Power** (31 channels): -15 to +15 dBmV optimal range
- **Downstream SNR** (31 channels): >30 dB good, >35 dB excellent
- **Upstream Power** (5 channels): 35-50 dBmV optimal range
- **OFDM Power & SNR**: DOCSIS 3.1 high-speed channel monitoring

### Performance & Throughput
- **Downstream Throughput**: Real-time data transfer per channel
- **OFDM Throughput**: High-speed DOCSIS 3.1 data rates
- **Frequencies**: Channel assignments for downstream (531-711 MHz) and upstream (16-40 MHz)
- **Bandwidth**: Upstream channel widths (3.2 or 6.4 MHz)

### Error Monitoring
- **Corrected Errors**: Fixed transmission problems per channel
- **Uncorrected Errors**: Lost data causing slowdowns and timeouts

### System Status
- **Uptime**: Time since last modem reboot (in minutes for better granularity)
- **Link Speed**: Negotiated connection speed with ISP

## 🔧 Collection Modes Explained

The plugin offers two collection modes with different performance and safety characteristics:

### 🛡️ Serial Collection (Default - Recommended)

**How it works**: Queries endpoints one at a time in sequence

```
Timeline (Serial Mode):
0s     ├─ Query dsinfo.asp (downstream QAM)
4s     ├─ Query dsofdminfo.asp (downstream OFDM)  
8s     ├─ Query usinfo.asp (upstream QAM)
12s    ├─ Query usofdminfo.asp (upstream OFDM)
16s    ├─ Query getSysInfo.asp (system info)
20s    ├─ Query getLinkStatus.asp (link speed)
24s    └─ Collection Complete
```

**Advantages:**
- ✅ **Maximum Compatibility**: Works with all modems, even very old ones
- ✅ **Absolute Safety**: Only one request at a time, no concurrent load
- ✅ **Predictable Timing**: Fixed, sequential processing order
- ✅ **Minimal Resource Usage**: Low memory and CPU overhead
- ✅ **Troubleshooting Friendly**: Easy to debug and understand

**Disadvantages:**
- ⏱️ **Slower Collection**: 20-40 seconds total (vs 4-6 seconds parallel)
- 📈 **Higher Latency**: Longer detection time for issues

**Best for:**
- Legacy or sensitive modems
- Conservative environments
- First-time setup and testing
- Problematic network conditions

### ⚡ Parallel Collection (Optional - High Performance)

**How it works**: Queries all endpoints simultaneously

```
Timeline (Parallel Mode):
0s     ├─ Query ALL endpoints simultaneously:
       │  ├─ dsinfo.asp
       │  ├─ dsofdminfo.asp  
       │  ├─ usinfo.asp
       │  ├─ usofdminfo.asp
       │  ├─ getSysInfo.asp
       │  └─ getLinkStatus.asp
4-6s   └─ All Collections Complete
```

**Advantages:**
- ⚡ **6x Faster**: 4-6 seconds total collection time
- 📊 **Better Responsiveness**: Faster issue detection
- 🎯 **Efficient Resource Use**: Better utilization of network/CPU
- 📈 **Scalable**: Better for monitoring multiple modems

**Disadvantages:**
- ⚠️ **Higher Modem Load**: May overwhelm sensitive modems
- 🔧 **More Complex**: Additional async processing overhead
- 🐛 **Harder Debugging**: Concurrent operations harder to trace

**Best for:**
- Modern, robust modems
- High-frequency monitoring (every 30-60 seconds)
- Performance-critical environments
- Multiple modem deployments

## ⚙️ Configuration Reference

### 🔴 Required Parameters

Only **2 parameters** are actually required - everything else is auto-calculated:

```yaml
localhost:
  name: 'hitron_coda'                   # REQUIRED: Plugin instance name
  host: 'https://192.168.100.1'        # REQUIRED: Your modem's IP address
  # Everything else below is optional with smart defaults
```

### 🟢 Auto-Calculated Parameters

The plugin automatically calculates optimal values based on your configuration:

| Parameter | Auto-Calculation | Example |
|-----------|------------------|---------|
| `collection_timeout` | `update_every × 0.9` | `60s × 0.9 = 54s` |
| `max_retries` | `collection_timeout ÷ endpoint_timeout` | `54s ÷ 6s = 9 retries` |

### 🔧 Smart Configuration Examples

#### Minimal Configuration (Recommended Starting Point)
```yaml
localhost:
  name: 'hitron_coda'
  host: 'https://192.168.100.1'
  device_name: 'My Cable Modem'         # Optional but recommended
  # Plugin auto-calculates:
  # - update_every: 60s (default)
  # - endpoint_timeout: 6s (default)
  # - collection_timeout: 54s (60 × 0.9)
  # - max_retries: 9 (54 ÷ 6)
  # - parallel_collection: false (serial mode)
  # - inter_request_delay: 0s (no delays)
```

#### Custom Frequency with Auto-Optimization
```yaml
localhost:
  name: 'hitron_coda'
  host: 'https://192.168.100.1'
  update_every: 30                      # Only specify what you want to change
  # Plugin auto-adjusts everything else:
  # - collection_timeout: 27s (30 × 0.9)
  # - max_retries: 4 (27 ÷ 6)
  # - Serial collection: 6 endpoints × 6s = 36s max (fits in 27s timeout with retries)
```

### Advanced Configuration Options

```yaml
localhost:
  # === BASIC SETTINGS ===
  name: 'hitron_coda'
  host: 'https://192.168.100.1'
  device_name: 'My Cable Modem'
  update_every: 60                      # How often to collect data (seconds)
  
  # === COLLECTION MODE ===
  parallel_collection: false            # Default: false (SERIAL mode - safer)
                                        # true = PARALLEL mode (6x faster but more load)
  
  # === TIMEOUT CONFIGURATION ===
  endpoint_timeout: 6                   # Timeout per individual endpoint (seconds)
                                        # Range: 3-20s, Default: 6s
  
  collection_timeout: 54                # Timeout for entire collection cycle (seconds)  
                                        # Default: auto-calculated (90% of update_every)
                                        # Must be < update_every to prevent overlaps
  
  # === RETRY CONFIGURATION ===
  max_retries: 9                        # Maximum retry attempts per endpoint
                                        # Default: auto-calculated (collection_timeout ÷ endpoint_timeout)
                                        # Manual override: 1-20 retries
  
  # === SERIAL MODE OPTIONS ===
  inter_request_delay: 0                # Pause between endpoints in serial mode (seconds)
                                        # Default: 0 (no delay)
                                        # Range: 0-10s for ultra-conservative operation
```

### 🧮 Auto-Calculation Examples

The plugin uses smart calculations based on **6 fixed endpoints** and your configuration:

#### Example 1: Conservative Setup
```yaml
# Input:
update_every: 120
endpoint_timeout: 8

# Auto-calculated:
collection_timeout: 108               # 120 × 0.9
max_retries: 13                      # 108 ÷ 8 = 13.5 → 13

# Performance:
# Serial mode: 6 × 8s = 48s typical
# With retries: up to 108s maximum
```

#### Example 2: High-Performance Setup
```yaml
# Input:
update_every: 30
parallel_collection: true

# Auto-calculated:
collection_timeout: 27               # 30 × 0.9
max_retries: 4                      # 27 ÷ 6 = 4.5 → 4

# Performance:
# Parallel mode: ~6s typical
# Very fast monitoring every 30 seconds
```

#### Example 3: Ultra-Safe Setup
```yaml
# Input:
update_every: 300
endpoint_timeout: 15
inter_request_delay: 5

# Auto-calculated:
collection_timeout: 270              # 300 × 0.9
max_retries: 18                     # 270 ÷ 15 = 18

# Performance:
# Serial + delays: 6 × (15s + 5s) = 120s typical
# Very safe with lots of retry budget
```

## 📈 Configuration Examples by Use Case

### 🛡️ Ultra-Conservative (Maximum Safety)

**Use case**: Very old modems, sensitive equipment, debugging

```yaml
ultra_safe:
  name: 'hitron_ultra_safe'
  host: 'https://192.168.100.1'
  device_name: 'Legacy Cable Modem'
  update_every: 300                   # 5-minute intervals
  parallel_collection: false          # SERIAL mode only
  endpoint_timeout: 15                # Very patient timeout
  collection_timeout: 250             # Plenty of time
  inter_request_delay: 3              # 3-second pause between endpoints
  max_retries: 1                      # Minimal retries
```

**Performance**: ~108 seconds collection time (6 × (15s + 3s))
**Safety**: Maximum - guaranteed one request at a time with pauses

### 🎯 Balanced Production (Recommended)

**Use case**: Most production environments, modern modems

```yaml
production:
  name: 'hitron_production'
  host: 'https://192.168.100.1'
  device_name: 'Production Internet Gateway'
  update_every: 60                    # 1-minute intervals
  parallel_collection: false          # SERIAL mode (safe default)
  endpoint_timeout: 6                 # Reasonable timeout
  collection_timeout: 54              # 90% of update_every
  inter_request_delay: 0              # No delays needed
  max_retries: 9                      # Auto-calculated: 54/6 = 9
```

**Performance**: ~36 seconds collection time (6 × 6s)
**Safety**: High - sequential requests with good timeout margins

### ⚡ High-Performance (Fast Monitoring)

**Use case**: High-frequency monitoring, robust modems, multiple modems

```yaml
high_performance:
  name: 'hitron_fast'
  host: 'https://192.168.100.1'
  device_name: 'High-Speed Cable Modem'
  update_every: 30                    # 30-second intervals
  parallel_collection: true           # PARALLEL mode for speed
  endpoint_timeout: 4                 # Standard timeout
  collection_timeout: 25              # 90% of 30s
  max_retries: 6                      # Auto-calculated: 25/4 = 6
```

**Performance**: ~4-6 seconds collection time (all endpoints parallel)
**Safety**: Moderate - concurrent requests may stress some modems

### 🔬 Development/Testing

**Use case**: Plugin development, testing, troubleshooting

```yaml
development:
  name: 'hitron_test'
  host: 'https://192.168.100.1'
  device_name: 'Test Lab Modem'
  update_every: 20                    # Frequent testing
  parallel_collection: false          # SERIAL for easier debugging
  endpoint_timeout: 3                 # Quick failure detection
  collection_timeout: 18              # 90% of 20s
  inter_request_delay: 1              # 1-second delays for observation
  max_retries: 3                      # Limited retries for faster feedback
```

**Performance**: ~24 seconds collection time (6 × (3s + 1s))
**Safety**: High - easy to debug with visible request timing

## 🚨 Configuration Guidelines

### Timeout Relationships

```
CRITICAL RULE: collection_timeout < update_every
```

**Why**: Prevents overlapping collection cycles which cause resource contention.

```yaml
# ✅ GOOD - Collection finishes before next cycle
update_every: 60
collection_timeout: 54               # 90% of update_every

# ❌ BAD - Collections will overlap
update_every: 60  
collection_timeout: 65               # Longer than update_every!
```

### Retry Calculation

The plugin auto-calculates retries to fit within the collection window:

```
max_retries = floor(collection_timeout / endpoint_timeout)
```

**Examples**:
- `collection_timeout: 54s`, `endpoint_timeout: 4s` → `max_retries: 13`
- `collection_timeout: 27s`, `endpoint_timeout: 3s` → `max_retries: 9`
- `collection_timeout: 108s`, `endpoint_timeout: 8s` → `max_retries: 13`

You can manually override this calculation if needed.

### Serial Mode Timing

For serial mode, estimate total collection time:

```
Estimated Time = endpoints × (endpoint_timeout + inter_request_delay)
                = 6 × (endpoint_timeout + inter_request_delay)
```

**Examples**:
- `endpoint_timeout: 4s`, `inter_request_delay: 0s` → ~24s total
- `endpoint_timeout: 6s`, `inter_request_delay: 1s` → ~42s total  
- `endpoint_timeout: 15s`, `inter_request_delay: 3s` → ~108s total

Ensure this is less than `collection_timeout`.

## ⚡ Quick Installation

```bash
# Download and install the enhanced plugin
sudo cp hitron_coda.chart.py /usr/libexec/netdata/python.d/
sudo cp hitron_coda.conf /etc/netdata/python.d/
sudo cp health/hitron_coda.conf /etc/netdata/health.d/

# Restart Netdata
sudo systemctl restart netdata
```

## 🩺 Health Monitoring

The enhanced plugin includes comprehensive health monitoring:

### Plugin Health Chart Metrics

- **Success Rate**: >95% excellent, >90% good, <85% needs attention
- **Response Time**: <1000ms excellent, <2000ms good, >3000ms slow  
- **Collection Time**: Should be well under collection_timeout
- **Consecutive Failures**: >10 indicates persistent issues
- **Active Endpoints**: Should be 5-6 for healthy modem

### Key Alert Thresholds

```yaml
# Recommended health alert thresholds
success_rate: 
  warn: < 90%
  crit: < 75%

response_time:
  warn: > 2000ms  
  crit: > 5000ms

collection_time:
  warn: > 80% of collection_timeout
  crit: > 95% of collection_timeout

consecutive_failures:
  warn: > 5
  crit: > 15
```

## 🔍 Troubleshooting Guide

### Common Issues and Solutions

| Issue | Symptoms | Solution |
|-------|----------|----------|
| **Low Success Rate** | Success rate <90%, frequent failures | Increase `endpoint_timeout`, check network connectivity |
| **Collection Timeouts** | Collections exceed time limit | Increase `collection_timeout` or reduce `max_retries` |
| **Overlapping Collections** | Log warnings about overlaps | Ensure `collection_timeout < update_every` |
| **Modem Overload** | Modem becomes unresponsive | Switch to serial mode, add `inter_request_delay` |
| **Slow Performance** | High collection times | Switch to parallel mode (if modem supports it) |

### Performance Optimization

1. **Start Conservative**: Begin with serial mode and default settings
2. **Monitor Health**: Watch plugin health chart for issues
3. **Gradual Optimization**: Slowly reduce timeouts or enable parallel mode
4. **Test Thoroughly**: Run for 24+ hours before declaring stable

### Debugging Steps

1. **Check Plugin Health Chart** in Netdata dashboard
2. **Review Logs**: `journalctl -u netdata -f | grep hitron`
3. **Test Manual**: `python3 /usr/libexec/netdata/python.d/hitron_coda.chart.py`
4. **Verify Connectivity**: `curl -k https://192.168.100.1/data/dsinfo.asp`

## 🏠 Supported Modems

This plugin has been tested with:
- **Hitron CODA-4582** ✅
- **Hitron CODA-4680** ✅  
- **Hitron CODA-56** ✅

Should work with other Hitron CODA series modems that expose the same API endpoints.

## 🔧 Advanced Configuration

### Multiple Modems with Load Balancing

```yaml
# Stagger collection times to distribute load
main_modem:
  name: 'hitron_main'
  update_every: 60                    # Collect at :00 seconds
  
backup_modem:
  name: 'hitron_backup' 
  update_every: 75                    # Collect at :15 seconds (offset)
  
office_modem:
  name: 'hitron_office'
  update_every: 90                    # Collect at :30 seconds (offset)
```

### Performance Testing Configuration

```yaml
# For testing different collection modes
test_serial:
  name: 'hitron_serial_test'
  parallel_collection: false
  # ... other settings

test_parallel:
  name: 'hitron_parallel_test'  
  parallel_collection: true
  # ... other settings
```

### Migration from Previous Versions

**Old Configuration (v1.1.0)**:
```yaml
timeout: 10                          # Single timeout
max_retries: 3                       # Fixed retries
```

**New Enhanced Configuration (v1.2.0)**:
```yaml
endpoint_timeout: 4                  # Per-endpoint timeout
collection_timeout: 54               # Overall timeout
parallel_collection: false          # Collection mode
max_retries: 13                      # Auto-calculated retries
inter_request_delay: 0               # Serial mode delays
```

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **Netdata Team** - For the excellent monitoring platform
- **Hitron Technologies** - For providing accessible modem APIs  
- **CableLabs** - For DOCSIS standards and documentation
- **Community Contributors** - Testing and feedback across different collection modes

---

**🎯 Transform your cable modem into a comprehensive network monitoring station with enterprise-grade visibility and flexible collection modes for any environment!**
