# Netdata Hitron CODA Cable Modem Plugin

A comprehensive Netdata plugin for monitoring Hitron CODA cable modems with **31 downstream channels**, **5 upstream channels**, **DOCSIS 3.1 OFDM support**, and **built-in reliability monitoring**.

## 🚀 Features

- **50+ Metrics**: Complete visibility into all cable modem channels and performance
- **DOCSIS 3.1 Support**: Monitor next-generation OFDM channels for gigabit+ speeds
- **Built-in Health Monitoring**: Track plugin reliability, response times, and success rates
- **Enhanced Reliability**: Auto-calculated timeouts, retry logic, and cache-busting URLs
- **Smart Alerts**: Comprehensive health monitoring for both modem and plugin performance

## 📊 Charts Generated (15 Total)

### Plugin Monitoring
- **Plugin Health**: Success rates, response times, consecutive failures, active endpoints

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

## ⚡ Quick Installation

```bash
# Download and install the plugin
sudo cp hitron_coda.chart.py /usr/libexec/netdata/python.d/
sudo cp hitron_coda.conf /etc/netdata/python.d/
sudo cp health/hitron_coda.conf /etc/netdata/health.d/

# Restart Netdata
sudo systemctl restart netdata
```

## ⚙️ Configuration

Edit `/etc/netdata/python.d/hitron_coda.conf`:

```yaml
localhost:
  name: 'hitron_coda'
  host: 'https://192.168.100.1'       # Your modem's IP
  device_name: 'Hitron CODA56 Cable Modem'  # Custom display name
  update_every: 20                    # Collection interval (seconds)
  # timeout: 10                       # Auto-calculated (70% of update_every)
  # max_retries: 3                    # Retry attempts for failed requests
```

**Auto-Timeout Calculation**: Plugin automatically sets timeout to 70% of `update_every` (min 5s, max 15s) for optimal reliability.

**Custom Device Names**: The `device_name` appears in all chart titles and the Netdata menu. Use any name you prefer:
- `'Hitron CODA56 Cable Modem'` (technical)
- `'Living Room Internet'` (location-based)
- `'Main Office Modem'` (simple)
- `'Home Gateway'` (functional)
- `'MyModem'` (personal)

## 🩺 Plugin Health Monitoring

Monitor the monitoring system itself:

- **Success Rate**: >90% indicates reliable operation
- **Response Times**: <2000ms typical for healthy modems  
- **Consecutive Failures**: High values indicate modem or network issues
- **Active Endpoints**: Should show 5-6 responding API endpoints

## 🚨 Key Alert Thresholds

### Signal Quality Alerts
- **Power Levels**: Warning outside -15 to +15 dBmV (downstream), 35-50 dBmV (upstream)
- **SNR**: Warning below 30 dB, critical below 25 dB
- **Error Rates**: Warning >100 corrected errors/hour, any uncorrected errors

### Plugin Health Alerts  
- **Success Rate**: Warning <90%, critical <75%
- **Response Time**: Warning >2000ms, critical >5000ms
- **Consecutive Failures**: Warning >5, critical >15

## 🏠 Supported Modems

- **Hitron CODA-4582** ✅
- **Hitron CODA-4680** ✅  
- **Hitron CODA-56** ✅

## 🔍 Troubleshooting

### Quick Health Check
1. **Check Plugin Health chart** in Netdata dashboard first
2. **Success rate <90%**: Increase `update_every` interval or check network connectivity
3. **High response times**: Modem may be overloaded or slow
4. **High consecutive failures**: Check modem accessibility and power

### Common Solutions
- **Timeout errors**: Plugin auto-calculates optimal timeout, but manually set `timeout: 15` for slow modems
- **Connection issues**: Verify modem IP in `host` parameter
- **Plugin not in Remote Devices**: Charts are now properly categorized as `cable_modem_*`

### Manual Testing
```bash
cd /usr/libexec/netdata/python.d/
python3 hitron_coda.chart.py
```

Expected output includes health metrics:
```json
{
  "success_rate": 100,
  "response_time": 250,
  "consecutive_failures": 0,
  "active_endpoints": 5,
  "ds_power_2": 53,
  "uptime": 7680,  // Now in minutes, not hours
  ...
}
```

## 📈 What the Numbers Mean

### Power Levels
- **-15 to +15 dBmV** (downstream): Goldilocks zone for stable internet
- **35 to 50 dBmV** (upstream): Optimal transmit power range
- **Outside range**: Causes slow speeds, dropouts, or data corruption

### SNR (Signal-to-Noise Ratio)  
- **>35 dB**: Excellent signal quality, maximum speeds
- **30-35 dB**: Good signal quality  
- **<25 dB**: Poor signal, packet loss and slow speeds likely

### Error Rates
- **Corrected Errors**: Modem fixing transmission problems (some acceptable)
- **Uncorrected Errors**: Lost data causing slowdowns (should be zero)

### Plugin Health
- **Success Rate**: How reliably the plugin collects data from your modem
- **Response Time**: How quickly your modem responds to requests

## 🎯 Why This Matters

- **Proactive Monitoring**: Identify cable/signal issues before they affect your internet
- **ISP Accountability**: Objective data when calling your ISP about performance issues  
- **Reliability Insight**: Know if slowdowns are due to signal issues or monitoring problems
- **Performance Optimization**: Track the impact of cable/splitter changes

---

**Transform your cable modem into a comprehensive network monitoring station with enterprise-grade visibility into every channel and performance metric!**
