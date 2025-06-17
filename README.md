# Netdata Hitron CODA Cable Modem Python.d Plugin

A comprehensive Netdata plugin for monitoring Hitron CODA cable modems via their web interface. This plugin provides detailed visibility into your cable modem's performance with **31 downstream channels**, **5 upstream channels**, and **DOCSIS 3.1 OFDM** monitoring.

## 📊 What This Plugin Monitors

### **Downstream QAM Channels (31 channels)**
Your cable modem uses multiple channels simultaneously for downloading data:

- **Power Levels** (-15 to +15 dBmV optimal) - Signal strength from your ISP
- **SNR (Signal-to-Noise Ratio)** (>30 dB good) - Signal quality vs background noise  
- **Frequencies** (531-711 MHz) - Which TV channels your modem uses for internet
- **Data Throughput** - Real-time data transfer per channel
- **Error Rates** - Corrected/uncorrected transmission errors per channel

### **Upstream QAM Channels (5 channels)**  
Channels used for uploading data to your ISP:

- **Power Levels** (35-50 dBmV optimal) - How hard your modem transmits
- **Frequencies** (16-40 MHz) - Upload channel assignments
- **Bandwidth** (3.2 or 6.4 MHz) - Channel width allocations

### **DOCSIS 3.1 OFDM Channels** 🚀
Next-generation high-speed channels for gigabit+ internet:

- **OFDM Power Levels** - Advanced downstream signal strength
- **OFDM SNR** - Signal quality for high-speed channels  
- **OFDM Throughput** - Gigabit-class data transfer rates

### **System Metrics**
- **Uptime** - How long since modem last rebooted
- **Link Speed** - Negotiated connection speed with ISP

## 🎯 Why These Metrics Matter

### **Power Levels**
- **Too Low**: Weak signal = slow speeds, timeouts, dropped connections
- **Too High**: Over-amplified signal = interference, data corruption
- **Just Right**: Stable, fast internet performance

### **SNR (Signal-to-Noise Ratio)**
- **High SNR** (>35 dB): Clean signal, maximum speeds
- **Low SNR** (<25 dB): Noisy signal, packet loss, slow speeds
- Think of it like trying to have a conversation in a noisy room

### **Error Rates**
- **Corrected Errors**: Your modem is working hard to fix transmission problems
- **Uncorrected Errors**: Data is being lost, causing slowdowns and timeouts
- **Zero Errors**: Perfect signal quality

### **DOCSIS 3.1 OFDM**
- **Traditional QAM**: Like having 31 lanes of traffic
- **OFDM**: Like adding a superhighway with thousands of micro-lanes
- **Combined**: Multi-gigabit internet capability

## 📈 Charts Generated

### **Signal Quality Section**
- `downstream_power` - Downstream QAM Power Levels (31 channels)
- `downstream_snr` - Downstream QAM Signal Quality (31 channels)  
- `upstream_power` - Upstream QAM Power Levels (5 channels)
- `ofdm_downstream_power` - OFDM Power Levels (DOCSIS 3.1)
- `ofdm_downstream_snr` - OFDM Signal Quality (DOCSIS 3.1)

### **Performance Section**
- `downstream_throughput` - Real-time download data per channel
- `ofdm_downstream_throughput` - High-speed OFDM data transfer
- `downstream_frequency` - Channel frequency assignments
- `upstream_frequency` - Upload channel frequencies
- `upstream_bandwidth` - Upload channel widths

### **Error Monitoring Section**
- `downstream_corrected` - Corrected transmission errors per channel
- `downstream_uncorrected` - Uncorrected errors (packet loss)

### **System Section**
- `system_uptime` - Modem uptime in hours
- `link_speed` - Connection speed in Mbps

## 🚨 Smart Health Monitoring

### **Automatic Alerts For:**

#### **Signal Issues**
- Power levels outside optimal range (-15 to +15 dBmV downstream, 35-50 dBmV upstream)
- SNR degradation below 30 dB (25 dB critical)
- OFDM signal quality problems

#### **Performance Problems**
- High error rates indicating line quality issues
- Throughput drops compared to historical performance
- Frequency drift suggesting hardware problems

#### **System Health**
- Frequent modem reboots (uptime < 1 hour)
- Reduced channel count (missing channels)
- Connection speed below expected levels

#### **DOCSIS 3.1 Monitoring**
- OFDM channel availability
- High-speed channel performance degradation
- Advanced modulation profile changes

### **Intelligent Alerting**
- **Progressive Delays**: Waits 2-5 minutes for temporary issues to resolve
- **Escalating Notifications**: Longer delays for repeated alerts  
- **Problem-Specific Guidance**: Each alert includes troubleshooting steps
- **Composite Scoring**: Overall performance health indicators

## 📚 Learn More About Cable Modems

### **Beginner-Friendly Resources**
- [Cable Modem Basics - Arris](https://www.arris.com/globalassets/resources/white-papers/cable_modem_primer.pdf) - Technical primer on how cable modems work
- [DOCSIS Explained - CableLabs](https://www.cablelabs.com/technologies/docsis) - Official DOCSIS technology overview
- [Cable Internet 101 - Motorola](https://www.motorolasolutions.com/content/dam/msi/docs/business/broadband/cable_modems_101.pdf) - Consumer guide to cable internet

### **Signal Quality Guidelines**
- **Downstream Power**: -15 to +15 dBmV (optimal: -10 to +10 dBmV)
- **Upstream Power**: 35 to 50 dBmV (your levels: 39-41 dBmV ✅)
- **SNR**: >30 dB good, >35 dB excellent (your levels: 36-39 dB ✅)
- **Error Rates**: <100 corrected/hour acceptable, 0 uncorrected ideal

### **When to Contact Your ISP**
- Consistent power levels outside -15 to +15 dBmV
- SNR consistently below 25 dB
- High uncorrected error rates (>10 per hour)
- Frequent modem reboots
- Missing channels (fewer than expected active)

## 🔧 Features

- **Comprehensive Monitoring**: 50+ metrics vs basic 6-metric monitoring
- **DOCSIS 3.1 Support**: Monitor next-generation OFDM channels
- **Real-time Performance**: 5-second updates for responsive monitoring
- **Channel-Specific Alerts**: Individual monitoring of all 31+5 channels
- **Historical Trending**: Track performance over time
- **ISP-Grade Monitoring**: Enterprise-level cable modem visibility

## 📸 Screenshots

![Downstream Power](screenshots/downstream_power.png)
*31-channel downstream power monitoring with per-channel visibility*

![SNR Chart](screenshots/snr_chart.png)  
*Signal quality monitoring across all downstream channels*

![OFDM Performance](screenshots/ofdm_performance.png)
*DOCSIS 3.1 OFDM high-speed channel monitoring*

![System Overview](screenshots/system_overview.png)
*Complete cable modem health dashboard*

## 🛠️ Requirements

- Netdata (tested with v1.40+)
- Python 3.6+
- Python packages: `requests`, `urllib3`
- Hitron CODA cable modem (tested models: CODA-4582, CODA-4680, CODA-56)

## ⚡ Quick Installation

### Method 1: Automated Installation

```bash
git clone https://github.com/yourusername/netdata-hitron-plugin.git
cd netdata-hitron-plugin
sudo chmod +x scripts/install-health-monitoring.sh
sudo ./scripts/install-health-monitoring.sh
```

### Method 2: Manual Installation

1. **Install the plugin**:
```bash
sudo cp hitron_coda.chart.py /usr/libexec/netdata/python.d/
sudo cp hitron_coda.conf /etc/netdata/python.d/
```

2. **Install health monitoring**:
```bash
sudo cp health/hitron_coda.conf /etc/netdata/health.d/
```

3. **Install enhanced notifications** (optional):
```bash
sudo cp notifications/alarm-notify-custom.sh /usr/libexec/netdata/plugins.d/
sudo chmod +x /usr/libexec/netdata/plugins.d/alarm-notify-custom.sh
```

4. **Restart Netdata**:
```bash
sudo systemctl restart netdata
```

## ⚙️ Configuration

Edit `/etc/netdata/python.d/hitron_coda.conf`:

```yaml
localhost:
  name: 'hitron_coda'
  host: 'https://192.168.100.1'  # Your modem's IP
  update_every: 5                # Update frequency in seconds
```

### Configuration Options

- `host`: Modem IP address (default: https://192.168.100.1)
- `update_every`: Update interval in seconds (default: 5, recommended: 5-10)
- `name`: Plugin instance name

## 🎛️ Alert Configuration

Customize alert thresholds in `/etc/netdata/health.d/hitron_coda.conf`:

```bash
# Example: Adjust for your internet plan
template: hitron_coda_link_speed_low
    warn: $this < 500    # Warning below 500 Mbps  
    crit: $this < 200    # Critical below 200 Mbps
```

### Notification Setup

Configure recipients in `/etc/netdata/health_alarm_notify.conf`:

```bash
# Email notifications
DEFAULT_RECIPIENT_EMAIL="admin@yourdomain.com"

# Slack notifications  
SLACK_WEBHOOK_URL="https://hooks.slack.com/services/YOUR/WEBHOOK"
DEFAULT_RECIPIENT_SLACK="network-alerts"
```

## 🏠 Supported Modems

This plugin has been tested with:
- **Hitron CODA-4582** ✅
- **Hitron CODA-4680** ✅  
- **Hitron CODA-56** ✅

Should work with other Hitron CODA series modems that expose the same API endpoints:
- `/data/dsinfo.asp` - Downstream QAM channels
- `/data/dsofdminfo.asp` - OFDM downstream channels  
- `/data/usinfo.asp` - Upstream QAM channels
- `/data/usofdminfo.asp` - OFDM upstream channels
- `/data/getSysInfo.asp` - System information
- `/data/getLinkStatus.asp` - Link status

## 🔍 Troubleshooting

### Plugin Not Showing Up

1. **Check if plugin is enabled**:
```bash
sudo netdata -W debug -D | grep hitron
```

2. **Check Netdata logs**:
```bash
sudo journalctl -u netdata -f
```

3. **Verify modem accessibility**:
```bash
curl -k https://192.168.100.1/data/dsinfo.asp
```

### Common Issues

- **SSL Certificate Errors**: Plugin disables SSL verification for self-signed certificates
- **Timeout Errors**: Increase timeout in code if modem is slow to respond  
- **Permission Errors**: Ensure plugin files have proper ownership (`netdata:netdata`)
- **Missing Charts**: Verify modem API endpoints are accessible

### Testing the Plugin

Test independently:

```bash
cd /usr/libexec/netdata/python.d/
python3 hitron_coda.chart.py
```

Expected output with 50+ metrics:
```json
{
  "ds_power_2": 53,      // Downstream power * 10
  "ds_snr_2": 3860,      // Downstream SNR * 100  
  "us_power_1": 4127,    // Upstream power * 100
  "ofdm_ds_power_1": 580, // OFDM power * 100
  ...
}
```

### Performance Tuning

For systems monitoring many modems:

```ini
# /etc/netdata/netdata.conf
[plugin:python.d]
    update every = 10    # Reduce frequency if needed

[plugin:python.d:hitron_coda]  
    update every = 10    # Per-plugin override
```

## 📊 Dashboard Integration

### Custom Dashboard Sections

Access your cable modem data at:
- **Main Dashboard**: `http://your-netdata:19999/`
- **Cable Modem Section**: Look for "hitron_coda" charts
- **Health Monitoring**: `http://your-netdata:19999/alarms.html`

### Integration Tips

- **Group Related Charts**: Drag charts to arrange by signal/performance/errors
- **Set Bookmarks**: Bookmark modem section for quick access
- **Mobile Access**: Responsive design works on phones/tablets
- **Embed Widgets**: Use chart URLs for external dashboards

## 🤝 Development

### Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Test with your modem model
4. Commit changes (`git commit -m 'Add amazing feature'`)
5. Push to branch (`git push origin feature/amazing-feature`)
6. Open a Pull Request

### Testing Guidelines

Before submitting PRs:
- Test with different Hitron CODA models
- Verify health alerts trigger correctly
- Test with various network conditions
- Check for memory leaks during extended runtime

### Plugin Architecture

```
hitron_coda.chart.py
├── Service Class
│   ├── _get_downstream_qam_data()    # 31 QAM channels
│   ├── _get_downstream_ofdm_data()   # DOCSIS 3.1 OFDM
│   ├── _get_upstream_qam_data()      # 5 QAM channels  
│   ├── _get_upstream_ofdm_data()     # DOCSIS 3.1 OFDM
│   └── _get_system_data()            # Uptime, link speed
└── Chart Creation
    ├── Power level charts (QAM + OFDM)
    ├── Signal quality charts (SNR)
    ├── Performance charts (throughput)
    └── Error monitoring charts
```

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **Netdata Team** - For the excellent monitoring platform
- **Hitron Technologies** - For providing accessible modem APIs  
- **CableLabs** - For DOCSIS standards and documentation
- **Community Contributors** - Testing and feedback

## 📞 Support

- **Issues**: Create GitHub issues for bugs or feature requests
- **Discussions**: Use GitHub Discussions for questions
- **Documentation**: Check [docs/HEALTH_MONITORING.md](docs/HEALTH_MONITORING.md) for detailed setup

### When Reporting Issues

Please include:
- Hitron modem model and firmware version
- Netdata version
- Error logs from `journalctl -u netdata`
- Output from manual plugin test
- Network environment details (ISP, signal levels)

---

**🎯 Transform your basic cable modem into a comprehensive network monitoring station with enterprise-grade visibility into every channel, frequency, and performance metric!**
