# Configuration Values: Required vs Auto-Calculated

## 📋 Configuration Parameters Overview

The plugin uses a smart configuration system where only a few values are required, and the rest are automatically calculated based on optimal relationships and the number of endpoints.

### 🔴 **REQUIRED Parameters** (Must be specified)

| Parameter | Purpose | No Default | Must Specify |
|-----------|---------|------------|--------------|
| `host` | Modem IP address | ❌ | ✅ Required |
| `name` | Plugin instance name | ❌ | ✅ Required |

### 🟡 **SEMI-REQUIRED Parameters** (Have defaults, but should be customized)

| Parameter | Purpose | Default | Recommendation |
|-----------|---------|---------|----------------|
| `device_name` | Display name in charts | `'Hitron CODA Cable Modem'` | Customize for clarity |
| `update_every` | Collection frequency | `60` seconds | Adjust based on needs |

### 🟢 **AUTO-CALCULATED Parameters** (Smart defaults based on other values)

| Parameter | Auto-Calculation Formula | Fallback Default |
|-----------|--------------------------|------------------|
| `collection_timeout` | `int(update_every × 0.9)` | `54s` (when `update_every=60`) |
| `max_retries` | `max(1, int(collection_timeout ÷ endpoint_timeout))` | Calculated |
| `endpoint_timeout` | Fixed default | `6s` |
| `parallel_collection` | Fixed default | `false` (Serial mode) |
| `inter_request_delay` | Fixed default | `0s` (No delay) |

## 🧮 Auto-Calculation Examples

### Example 1: Minimal Configuration

**Input (what you specify):**
```yaml
localhost:
  name: 'hitron_coda'
  host: 'https://192.168.100.1'
  # Everything else auto-calculated
```

**Auto-calculated values:**
```yaml
# Plugin calculates:
device_name: 'Hitron CODA Cable Modem'    # Default
update_every: 60                          # Global default
endpoint_timeout: 6                       # Fixed default
collection_timeout: 54                    # 60 × 0.9 = 54
max_retries: 9                            # 54 ÷ 6 = 9
parallel_collection: false                # Serial mode (safe default)
inter_request_delay: 0                    # No delays
```

**Result:**
- Serial collection mode
- 6 endpoints × 6s timeout = ~36s maximum collection time
- 9 retries per endpoint if needed
- Collection completes well before 60s interval

### Example 2: Custom Update Frequency

**Input:**
```yaml
localhost:
  name: 'hitron_coda'
  host: 'https://192.168.100.1'
  update_every: 30                        # Custom frequency
```

**Auto-calculated values:**
```yaml
# Plugin calculates:
collection_timeout: 27                    # 30 × 0.9 = 27
max_retries: 4                            # 27 ÷ 6 = 4.5 → 4
# Other defaults same as Example 1
```

**Result:**
- Faster 30-second intervals
- Tighter timing (27s collection timeout)
- Fewer retries (4 vs 9) due to time constraints

### Example 3: Custom Endpoint Timeout

**Input:**
```yaml
localhost:
  name: 'hitron_coda'
  host: 'https://192.168.100.1'
  endpoint_timeout: 10                    # Longer individual timeouts
```

**Auto-calculated values:**
```yaml
# Plugin calculates:
collection_timeout: 54                    # Still 60 × 0.9 = 54
max_retries: 5                            # 54 ÷ 10 = 5.4 → 5
```

**Result:**
- More patient per-endpoint (10s vs 6s)
- Fewer total retries (5 vs 9) due to longer individual timeouts
- Serial collection: 6 × 10s = ~60s maximum (still fits in 54s timeout with retries)

## 🔢 Calculation Details

### Collection Timeout Calculation

```python
collection_timeout = int(update_every * 0.9)
```

**Why 90%?**
- Ensures collection completes before next cycle starts
- Provides 10% safety margin to prevent overlapping collections
- Critical for system stability

**Examples:**
- `update_every: 30` → `collection_timeout: 27`
- `update_every: 60` → `collection_timeout: 54`
- `update_every: 120` → `collection_timeout: 108`

### Max Retries Calculation

```python
max_retries = max(1, int(collection_timeout / endpoint_timeout))
```

**Logic:**
- More retries for longer collection windows
- Fewer retries for shorter collection windows
- Always at least 1 retry
- Ensures retries fit within time budget

**Examples:**
- `collection_timeout: 54`, `endpoint_timeout: 6` → `max_retries: 9`
- `collection_timeout: 27`, `endpoint_timeout: 3` → `max_retries: 9`
- `collection_timeout: 108`, `endpoint_timeout: 20` → `max_retries: 5`

### Endpoint Count Impact

**Fixed endpoint count: 6 endpoints**
```
1. dsinfo.asp (Downstream QAM - 31 channels)
2. dsofdminfo.asp (Downstream OFDM - DOCSIS 3.1)
3. usinfo.asp (Upstream QAM - 5 channels)
4. usofdminfo.asp (Upstream OFDM - DOCSIS 3.1)
5. getSysInfo.asp (System information)
6. getLinkStatus.asp (Link speed)
```

**Serial Mode Timing:**
```
Total Time = endpoints × endpoint_timeout
           = 6 × endpoint_timeout
```

**Parallel Mode Timing:**
```
Total Time ≈ endpoint_timeout (limited by slowest endpoint)
```

## 📊 Configuration Scenarios with Auto-Calculations

### Scenario 1: Conservative Production

**Input:**
```yaml
production:
  name: 'hitron_production'
  host: 'https://192.168.100.1'
  device_name: 'Production Gateway'
  update_every: 120                      # 2-minute intervals
  endpoint_timeout: 8                    # Patient timeouts
```

**Auto-calculated:**
```yaml
collection_timeout: 108                  # 120 × 0.9
max_retries: 13                         # 108 ÷ 8 = 13.5 → 13
parallel_collection: false              # Serial (safe)
inter_request_delay: 0                  # No delays
```

**Performance Analysis:**
- Serial mode: 6 × 8s = 48s typical collection time
- With retries: Up to 108s maximum (within timeout)
- Safety margin: 120s - 108s = 12s buffer

### Scenario 2: High-Frequency Monitoring

**Input:**
```yaml
high_freq:
  name: 'hitron_fast'
  host: 'https://192.168.100.1'
  update_every: 15                       # 15-second intervals
  parallel_collection: true              # Enable parallel
```

**Auto-calculated:**
```yaml
collection_timeout: 13                   # 15 × 0.9
endpoint_timeout: 6                      # Default
max_retries: 2                          # 13 ÷ 6 = 2.16 → 2
inter_request_delay: 0                  # No delays
```

**Performance Analysis:**
- Parallel mode: ~6s typical collection time
- Tight timing: Only 2 retries due to short interval
- Aggressive but achievable

### Scenario 3: Ultra-Conservative

**Input:**
```yaml
ultra_safe:
  name: 'hitron_safe'
  host: 'https://192.168.100.1'
  update_every: 300                      # 5-minute intervals
  endpoint_timeout: 15                   # Very patient
  inter_request_delay: 5                 # 5-second pauses
```

**Auto-calculated:**
```yaml
collection_timeout: 270                  # 300 × 0.9
max_retries: 18                         # 270 ÷ 15 = 18
parallel_collection: false              # Serial (safe)
```

**Performance Analysis:**
- Serial + delays: 6 × (15s + 5s) = 120s typical collection time
- Lots of retry budget: Up to 270s maximum
- Very safe: 300s - 270s = 30s buffer

## ⚠️ Configuration Validation

The plugin validates auto-calculated values and warns about problematic configurations:

### Validation Rules

1. **Collection Timeout Rule:**
   ```
   collection_timeout < update_every
   ```
   Prevents overlapping collections

2. **Serial Mode Timing Rule:**
   ```
   (endpoints × (endpoint_timeout + inter_request_delay)) ≤ collection_timeout
   ```
   Ensures serial mode fits within timeout

3. **Minimum Retry Rule:**
   ```
   max_retries ≥ 1
   ```
   Always allows at least one retry

### Warning Examples

**Warning Case 1:**
```yaml
# This will generate a warning
update_every: 30
endpoint_timeout: 15
# Auto-calculated: collection_timeout: 27, max_retries: 1
# Serial time: 6 × 15s = 90s > 27s collection_timeout
```

**Plugin Warning:**
```
WARNING: Serial mode configuration may be problematic:
  Estimated collection time: 90s
  Collection timeout: 27s
  Consider increasing collection_timeout or reducing endpoint_timeout
```

**Warning Case 2:**
```yaml
# This will work but has tight timing
update_every: 20
parallel_collection: true
# Auto-calculated: collection_timeout: 18, max_retries: 3
```

**Plugin Info:**
```
INFO: Tight timing detected - parallel mode recommended for this configuration
```

## 🎯 Best Practices for Configuration

### 1. Start with Minimal Configuration
```yaml
localhost:
  name: 'hitron_coda'
  host: 'https://192.168.100.1'
  device_name: 'My Cable Modem'
  # Let everything else auto-calculate
```

### 2. Adjust Only What You Need
```yaml
# If you need faster monitoring:
localhost:
  name: 'hitron_coda'
  host: 'https://192.168.100.1'
  update_every: 30                       # Only change frequency
  # endpoint_timeout and retries auto-adjust

# If your modem is slow:
localhost:
  name: 'hitron_coda'
  host: 'https://192.168.100.1'
  endpoint_timeout: 10                   # Only increase patience
  # collection_timeout and retries auto-adjust
```

### 3. Override Auto-Calculations Only When Needed
```yaml
# Manual override example:
localhost:
  name: 'hitron_coda'
  host: 'https://192.168.100.1'
  endpoint_timeout: 8
  max_retries: 3                         # Override auto-calculation
  # collection_timeout still auto-calculated
```

## 📈 Performance Impact of Auto-Calculations

### Collection Time Estimation

**Serial Mode:**
```
Typical Time = endpoints × endpoint_timeout
Maximum Time = min(collection_timeout, endpoints × endpoint_timeout × max_retries)
```

**Parallel Mode:**
```
Typical Time ≈ endpoint_timeout
Maximum Time = min(collection_timeout, endpoint_timeout × max_retries)
```

### Memory and CPU Impact

**Base Resource Usage (independent of configuration):**
- Memory: ~3-5MB per modem instance
- CPU: Low burst during collection

**Configuration Impact:**
- Higher `max_retries`: Slightly more memory for retry logic
- Parallel mode: +1-2MB memory, brief CPU spike
- Serial mode: Sustained moderate CPU over longer period
- `inter_request_delay`: No additional resource impact

The auto-calculation system ensures optimal performance while maintaining safety, requiring minimal user configuration for most scenarios!
