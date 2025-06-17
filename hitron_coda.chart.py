#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Comprehensive Netdata plugin for monitoring Hitron CODA cable modems.

This plugin collects:
- Downstream QAM channels (31 channels!)
- Downstream OFDM channels (DOCSIS 3.1)
- Upstream QAM channels (5 channels)
- Upstream OFDM channels (DOCSIS 3.1)
- Data throughput statistics
- System information

Version: 1.0.0-beta
Author: Your Name
License: MIT
"""

import sys
import json
import time
import requests
import urllib3
from bases.FrameworkServices.SimpleService import SimpleService

NAME = 'hitron_coda'
UPDATE_EVERY = 15

# Disable SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class Service(SimpleService):
    """Comprehensive Netdata service for Hitron CODA modem monitoring."""

    def __init__(self, configuration=None, name=None):
        """Initialize the service with configuration."""
        SimpleService.__init__(self, configuration=configuration, name=name)

        self.order = []
        self.definitions = {}
        self.host = configuration.get('host', 'https://192.168.100.1')
        self.session = requests.Session()
        self.session.verify = False

        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Netdata Hitron Plugin)',
            'Accept': '*/*'
        }

    def init_charts(self, data):
        """Initialize charts based on available data."""
        # Downstream QAM Power chart
        ds_power_ids = sorted([k for k in data.keys()
                              if k.startswith('ds_power_')])
        if ds_power_ids:
            self._create_downstream_power_chart(ds_power_ids)

        # Downstream QAM SNR chart
        ds_snr_ids = sorted([k for k in data.keys()
                            if k.startswith('ds_snr_')])
        if ds_snr_ids:
            self._create_downstream_snr_chart(ds_snr_ids)

        # Downstream QAM Frequency chart
        ds_freq_ids = sorted([k for k in data.keys()
                             if k.startswith('ds_freq_')])
        if ds_freq_ids:
            self._create_downstream_frequency_chart(ds_freq_ids)

        # Downstream data throughput
        ds_octets_ids = sorted([k for k in data.keys()
                               if k.startswith('ds_octets_')])
        if ds_octets_ids:
            self._create_downstream_throughput_chart(ds_octets_ids)

        # Downstream QAM corrected errors
        ds_corrected_ids = sorted([k for k in data.keys()
                                  if k.startswith('ds_corrected_')])
        if ds_corrected_ids:
            self._create_downstream_corrected_chart(ds_corrected_ids)

        # Downstream QAM uncorrected errors
        ds_uncorrected_ids = sorted([k for k in data.keys()
                                    if k.startswith('ds_uncorrected_')])
        if ds_uncorrected_ids:
            self._create_downstream_uncorrected_chart(ds_uncorrected_ids)

        # Upstream QAM Power chart
        us_power_ids = sorted([k for k in data.keys()
                              if k.startswith('us_power_')])
        if us_power_ids:
            self._create_upstream_power_chart(us_power_ids)

        # Upstream QAM Frequency chart
        us_freq_ids = sorted([k for k in data.keys()
                             if k.startswith('us_freq_')])
        if us_freq_ids:
            self._create_upstream_frequency_chart(us_freq_ids)

        # Upstream QAM Bandwidth chart
        us_bw_ids = sorted([k for k in data.keys()
                           if k.startswith('us_bandwidth_')])
        if us_bw_ids:
            self._create_upstream_bandwidth_chart(us_bw_ids)

        # OFDM Downstream Power
        ofdm_ds_power_ids = sorted([k for k in data.keys()
                                   if k.startswith('ofdm_ds_power_')])
        if ofdm_ds_power_ids:
            self._create_ofdm_downstream_power_chart(ofdm_ds_power_ids)

        # OFDM Downstream SNR
        ofdm_ds_snr_ids = sorted([k for k in data.keys()
                                 if k.startswith('ofdm_ds_snr_')])
        if ofdm_ds_snr_ids:
            self._create_ofdm_downstream_snr_chart(ofdm_ds_snr_ids)

        # OFDM Downstream Throughput
        ofdm_ds_octets_ids = sorted([k for k in data.keys()
                                    if k.startswith('ofdm_ds_octets_')])
        if ofdm_ds_octets_ids:
            self._create_ofdm_downstream_throughput_chart(ofdm_ds_octets_ids)

        # System uptime chart
        if 'uptime' in data:
            self._create_uptime_chart()

        # Link speed chart
        if 'speed' in data:
            self._create_speed_chart()

    def _create_downstream_power_chart(self, power_ids):
        """Create downstream QAM power chart."""
        chart_name = 'downstream_power'
        chart_def = {
            'options': [chart_name, 'Downstream QAM Power Levels', 'dBmV',
                       'downstream', 'hitron_coda.downstream_power', 'line'],
            'lines': []
        }
        for power_id in power_ids:
            channel = power_id.split('_')[-1]
            chart_def['lines'].append([power_id, f'CH{channel}',
                                     'absolute', 1, 10])  # Scale by 10 for precision

        self.definitions[chart_name] = chart_def
        self.order.append(chart_name)

    def _create_downstream_snr_chart(self, snr_ids):
        """Create downstream QAM SNR chart."""
        chart_name = 'downstream_snr'
        chart_def = {
            'options': [chart_name, 'Downstream QAM SNR', 'dB',
                       'downstream', 'hitron_coda.downstream_snr', 'line'],
            'lines': []
        }
        for snr_id in snr_ids:
            channel = snr_id.split('_')[-1]
            chart_def['lines'].append([snr_id, f'CH{channel}',
                                     'absolute', 1, 100])  # Scale by 100 for precision

        self.definitions[chart_name] = chart_def
        self.order.append(chart_name)

    def _create_downstream_frequency_chart(self, freq_ids):
        """Create downstream QAM frequency chart."""
        chart_name = 'downstream_frequency'
        chart_def = {
            'options': [chart_name, 'Downstream QAM Frequencies', 'MHz',
                       'downstream', 'hitron_coda.downstream_frequency', 'line'],
            'lines': []
        }
        for freq_id in freq_ids:
            channel = freq_id.split('_')[-1]
            chart_def['lines'].append([freq_id, f'CH{channel}',
                                     'absolute', 1, 1])

        self.definitions[chart_name] = chart_def
        self.order.append(chart_name)

    def _create_downstream_throughput_chart(self, octets_ids):
        """Create downstream data throughput chart."""
        chart_name = 'downstream_throughput'
        chart_def = {
            'options': [chart_name, 'Downstream Data Throughput', 'octets/s',
                       'downstream', 'hitron_coda.downstream_throughput', 'incremental'],
            'lines': []
        }
        for octets_id in octets_ids:
            channel = octets_id.split('_')[-1]
            chart_def['lines'].append([octets_id, f'CH{channel}',
                                     'incremental', 1, 1])

        self.definitions[chart_name] = chart_def
        self.order.append(chart_name)

    def _create_downstream_corrected_chart(self, corrected_ids):
        """Create downstream corrected errors chart."""
        chart_name = 'downstream_corrected'
        chart_def = {
            'options': [chart_name, 'Downstream Corrected Errors', 'errors/s',
                       'downstream', 'hitron_coda.downstream_corrected', 'incremental'],
            'lines': []
        }
        for corrected_id in corrected_ids:
            channel = corrected_id.split('_')[-1]
            chart_def['lines'].append([corrected_id, f'CH{channel}',
                                     'incremental', 1, 1])

        self.definitions[chart_name] = chart_def
        self.order.append(chart_name)

    def _create_downstream_uncorrected_chart(self, uncorrected_ids):
        """Create downstream uncorrected errors chart."""
        chart_name = 'downstream_uncorrected'
        chart_def = {
            'options': [chart_name, 'Downstream Uncorrected Errors', 'errors/s',
                       'downstream', 'hitron_coda.downstream_uncorrected', 'incremental'],
            'lines': []
        }
        for uncorrected_id in uncorrected_ids:
            channel = uncorrected_id.split('_')[-1]
            chart_def['lines'].append([uncorrected_id, f'CH{channel}',
                                     'incremental', 1, 1])

        self.definitions[chart_name] = chart_def
        self.order.append(chart_name)

    def _create_upstream_power_chart(self, power_ids):
        """Create upstream QAM power chart."""
        chart_name = 'upstream_power'
        chart_def = {
            'options': [chart_name, 'Upstream QAM Power Levels', 'dBmV',
                       'upstream', 'hitron_coda.upstream_power', 'line'],
            'lines': []
        }
        for power_id in power_ids:
            channel = power_id.split('_')[-1]
            chart_def['lines'].append([power_id, f'CH{channel}',
                                     'absolute', 1, 100])  # Scale by 100 for precision

        self.definitions[chart_name] = chart_def
        self.order.append(chart_name)

    def _create_upstream_frequency_chart(self, freq_ids):
        """Create upstream QAM frequency chart."""
        chart_name = 'upstream_frequency'
        chart_def = {
            'options': [chart_name, 'Upstream QAM Frequencies', 'MHz',
                       'upstream', 'hitron_coda.upstream_frequency', 'line'],
            'lines': []
        }
        for freq_id in freq_ids:
            channel = freq_id.split('_')[-1]
            chart_def['lines'].append([freq_id, f'CH{channel}',
                                     'absolute', 1, 1])

        self.definitions[chart_name] = chart_def
        self.order.append(chart_name)

    def _create_upstream_bandwidth_chart(self, bw_ids):
        """Create upstream bandwidth chart."""
        chart_name = 'upstream_bandwidth'
        chart_def = {
            'options': [chart_name, 'Upstream Channel Bandwidth', 'MHz',
                       'upstream', 'hitron_coda.upstream_bandwidth', 'line'],
            'lines': []
        }
        for bw_id in bw_ids:
            channel = bw_id.split('_')[-1]
            chart_def['lines'].append([bw_id, f'CH{channel}',
                                     'absolute', 1, 10])  # Scale by 10 for precision

        self.definitions[chart_name] = chart_def
        self.order.append(chart_name)

    def _create_ofdm_downstream_power_chart(self, power_ids):
        """Create OFDM downstream power chart."""
        chart_name = 'ofdm_downstream_power'
        chart_def = {
            'options': [chart_name, 'OFDM Downstream Power Levels', 'dBmV',
                       'ofdm', 'hitron_coda.ofdm_downstream_power', 'line'],
            'lines': []
        }
        for power_id in power_ids:
            channel = power_id.split('_')[-1]
            chart_def['lines'].append([power_id, f'OFDM{channel}',
                                     'absolute', 1, 100])

        self.definitions[chart_name] = chart_def
        self.order.append(chart_name)

    def _create_ofdm_downstream_snr_chart(self, snr_ids):
        """Create OFDM downstream SNR chart."""
        chart_name = 'ofdm_downstream_snr'
        chart_def = {
            'options': [chart_name, 'OFDM Downstream SNR', 'dB',
                       'ofdm', 'hitron_coda.ofdm_downstream_snr', 'line'],
            'lines': []
        }
        for snr_id in snr_ids:
            channel = snr_id.split('_')[-1]
            chart_def['lines'].append([snr_id, f'OFDM{channel}',
                                     'absolute', 1, 1])

        self.definitions[chart_name] = chart_def
        self.order.append(chart_name)

    def _create_ofdm_downstream_throughput_chart(self, octets_ids):
        """Create OFDM downstream throughput chart."""
        chart_name = 'ofdm_downstream_throughput'
        chart_def = {
            'options': [chart_name, 'OFDM Downstream Throughput', 'octets/s',
                       'ofdm', 'hitron_coda.ofdm_downstream_throughput', 'incremental'],
            'lines': []
        }
        for octets_id in octets_ids:
            channel = octets_id.split('_')[-1]
            chart_def['lines'].append([octets_id, f'OFDM{channel}',
                                     'incremental', 1, 1])

        self.definitions[chart_name] = chart_def
        self.order.append(chart_name)

    def _create_uptime_chart(self):
        """Create system uptime chart."""
        chart_name = 'system_uptime'
        chart_def = {
            'options': [chart_name, 'System Uptime', 'hours',
                       'system', 'hitron_coda.system_uptime', 'line'],
            'lines': [
                ['uptime', 'Uptime', 'absolute', 1, 1]
            ]
        }
        self.definitions[chart_name] = chart_def
        self.order.append(chart_name)

    def _create_speed_chart(self):
        """Create link speed chart."""
        chart_name = 'link_speed'
        chart_def = {
            'options': [chart_name, 'Link Speed', 'Mbps',
                       'link', 'hitron_coda.link_speed', 'line'],
            'lines': [
                ['speed', 'Speed', 'absolute', 1, 1]
            ]
        }
        self.definitions[chart_name] = chart_def
        self.order.append(chart_name)

    def check(self):
        """Check if the modem is accessible and initialize charts."""
        try:
            data = self.get_data()
            if data is None:
                return False

            self.init_charts(data)
            self.info(f"Hitron CODA plugin initialized with {len(data)} metrics")
            return True

        except Exception as e:
            self.error(f"Cannot connect to Hitron cable modem: {str(e)}")
            return False

    def get_data(self):
        """Get comprehensive data from modem."""
        data = {}

        try:
            # Get downstream QAM data (31 channels!)
            self._get_downstream_qam_data(data)
            
            # Get downstream OFDM data (DOCSIS 3.1)
            self._get_downstream_ofdm_data(data)

            # Get upstream QAM data (5 channels)
            self._get_upstream_qam_data(data)
            
            # Get upstream OFDM data (DOCSIS 3.1)
            self._get_upstream_ofdm_data(data)

            # Get system info for uptime
            self._get_system_data(data)

            # Get link speed
            self._get_link_data(data)

            return data

        except Exception as e:
            self.error(f"Failed to get data: {str(e)}")
            return None

    def _get_downstream_qam_data(self, data):
        """Get downstream QAM channel data (31 channels)."""
        try:
            response = self.session.get(
                f'{self.host}/data/dsinfo.asp',
                headers=self.headers,
                timeout=5
            )
            response.raise_for_status()

            ds_data = response.json()
            for channel in ds_data:
                idx = int(channel['channelId'])
                
                # Power level (signalStrength)
                data[f'ds_power_{idx}'] = int(float(channel['signalStrength']) * 10)
                
                # SNR 
                data[f'ds_snr_{idx}'] = int(float(channel['snr']) * 100)
                
                # Frequency (convert Hz to MHz)
                freq_mhz = int(channel['frequency']) / 1000000
                data[f'ds_freq_{idx}'] = int(freq_mhz)
                
                # Data throughput (dsoctets)
                data[f'ds_octets_{idx}'] = int(channel['dsoctets'])
                
                # Error statistics
                data[f'ds_corrected_{idx}'] = int(channel['correcteds'])
                data[f'ds_uncorrected_{idx}'] = int(channel['uncorrect'])

            self.debug(f"Collected {len([k for k in data.keys() if k.startswith('ds_')])} downstream QAM metrics")

        except requests.exceptions.RequestException as e:
            self.debug(f"Failed to get downstream QAM data: {str(e)}")
        except (ValueError, KeyError, IndexError) as e:
            self.debug(f"Failed to parse downstream QAM data: {str(e)}")

    def _get_downstream_ofdm_data(self, data):
        """Get downstream OFDM channel data (DOCSIS 3.1)."""
        try:
            response = self.session.get(
                f'{self.host}/data/dsofdminfo.asp',
                headers=self.headers,
                timeout=5
            )
            response.raise_for_status()

            ofdm_data = response.json()
            for idx, channel in enumerate(ofdm_data):
                # Only process active OFDM channels
                if (channel.get('receive') == '1' and 
                    channel.get('plcpower') != 'NA' and
                    channel.get('plcpower')):
                    
                    # OFDM Power level
                    power = float(channel['plcpower'])
                    data[f'ofdm_ds_power_{idx}'] = int(power * 100)
                    
                    # OFDM SNR
                    if channel.get('SNR') != 'NA' and channel.get('SNR'):
                        snr = float(channel['SNR'])
                        data[f'ofdm_ds_snr_{idx}'] = int(snr)
                    
                    # OFDM Throughput
                    if channel.get('dsoctets') != 'NA' and channel.get('dsoctets'):
                        octets = int(channel['dsoctets'])
                        data[f'ofdm_ds_octets_{idx}'] = octets

            if any(k.startswith('ofdm_ds_') for k in data.keys()):
                self.debug("DOCSIS 3.1 OFDM downstream channels detected and active")

        except requests.exceptions.RequestException as e:
            self.debug(f"Failed to get downstream OFDM data: {str(e)}")
        except (ValueError, KeyError, IndexError) as e:
            self.debug(f"Failed to parse downstream OFDM data: {str(e)}")

    def _get_upstream_qam_data(self, data):
        """Get upstream QAM channel data (5 channels)."""
        try:
            response = self.session.get(
                f'{self.host}/data/usinfo.asp',
                headers=self.headers,
                timeout=5
            )
            response.raise_for_status()

            us_data = response.json()
            for channel in us_data:
                idx = int(channel['channelId'])
                
                # Upstream power (signalStrength)
                power = float(channel['signalStrength'])
                data[f'us_power_{idx}'] = int(power * 100)
                
                # Upstream frequency (convert Hz to MHz)
                freq_hz = int(channel['frequency'])
                freq_mhz = freq_hz / 1000000
                data[f'us_freq_{idx}'] = int(freq_mhz)
                
                # Upstream bandwidth (convert Hz to MHz)
                bw_hz = int(channel['bandwidth'])
                bw_mhz = bw_hz / 1000000
                data[f'us_bandwidth_{idx}'] = int(bw_mhz * 10)  # Scale for precision

            self.debug(f"Collected {len([k for k in data.keys() if k.startswith('us_')])} upstream QAM metrics")

        except requests.exceptions.RequestException as e:
            self.debug(f"Failed to get upstream QAM data: {str(e)}")
        except (ValueError, KeyError, IndexError) as e:
            self.debug(f"Failed to parse upstream QAM data: {str(e)}")

    def _get_upstream_ofdm_data(self, data):
        """Get upstream OFDM channel data (DOCSIS 3.1)."""
        try:
            response = self.session.get(
                f'{self.host}/data/usofdminfo.asp',
                headers=self.headers,
                timeout=5
            )
            response.raise_for_status()

            ofdm_data = response.json()
            for idx, channel in enumerate(ofdm_data):
                # Check if OFDM channel is enabled/active
                state = channel.get('state', '').strip()
                if state != 'DISABLED' and float(channel.get('frequency', '0')) > 0:
                    
                    # OFDM upstream power
                    power = float(channel.get('repPower', '0'))
                    if power > 0:
                        data[f'ofdm_us_power_{idx}'] = int(power * 100)
                    
                    # OFDM upstream frequency
                    freq = float(channel.get('frequency', '0'))
                    if freq > 0:
                        freq_mhz = freq / 1000000
                        data[f'ofdm_us_freq_{idx}'] = int(freq_mhz)

        except requests.exceptions.RequestException as e:
            self.debug(f"Failed to get upstream OFDM data: {str(e)}")
        except (ValueError, KeyError, IndexError) as e:
            self.debug(f"Failed to parse upstream OFDM data: {str(e)}")

    def _get_system_data(self, data):
        """Get system information."""
        try:
            response = self.session.get(
                f'{self.host}/data/getSysInfo.asp',
                headers=self.headers,
                timeout=5
            )
            response.raise_for_status()

            sys_info = response.json()
            if sys_info and len(sys_info) > 0:
                uptime_str = sys_info[0].get('systemUptime', '')
                if uptime_str:
                    data['uptime'] = self._parse_uptime(uptime_str)

        except requests.exceptions.RequestException as e:
            self.debug(f"Failed to get system data: {str(e)}")
        except (ValueError, KeyError, IndexError) as e:
            self.debug(f"Failed to parse system data: {str(e)}")

    def _get_link_data(self, data):
        """Get link speed information."""
        try:
            response = self.session.get(
                f'{self.host}/data/getLinkStatus.asp',
                headers=self.headers,
                timeout=5
            )
            response.raise_for_status()

            link_info = response.json()
            if link_info and len(link_info) > 0:
                speed_str = link_info[0].get('LinkSpeed', '')
                if speed_str:
                    data['speed'] = int(float(speed_str.replace('Mbps', '')))

        except requests.exceptions.RequestException as e:
            self.debug(f"Failed to get link data: {str(e)}")
        except (ValueError, KeyError, IndexError) as e:
            self.debug(f"Failed to parse link data: {str(e)}")

    def _parse_uptime(self, uptime_str):
        """Parse uptime string in format '128h:20m:54s' to hours."""
        try:
            parts = uptime_str.split(':')
            if len(parts) != 3:
                return 0

            hours = float(parts[0].replace('h', ''))
            minutes = float(parts[1].replace('m', ''))
            seconds = float(parts[2].replace('s', ''))

            return hours + (minutes / 60) + (seconds / 3600)

        except (ValueError, AttributeError):
            return 0


class TestService(Service):
    """Test service for standalone testing."""

    def __init__(self):
        """Initialize test service with default configuration."""
        configuration = {'name': 'test', 'host': 'https://192.168.100.1'}
        Service.__init__(self, configuration=configuration, name='test')


def main():
    """Main function for standalone testing."""
    import logging
    logging.basicConfig(level=logging.DEBUG)

    service = TestService()

    # Check if we can connect to the modem
    if not service.check():
        print("❌ Failed to connect to modem")
        sys.exit(1)

    print("✅ Plugin initialized successfully!")
    print("🎯 Testing data collection...")

    try:
        data = service.get_data()
        if data is not None:
            print(f"📊 Collected {len(data)} metrics:")
            
            # Group metrics by type
            downstream_qam = len([k for k in data.keys() if k.startswith('ds_')])
            upstream_qam = len([k for k in data.keys() if k.startswith('us_')])
            ofdm = len([k for k in data.keys() if k.startswith('ofdm_')])
            system = len([k for k in data.keys() if k in ['uptime', 'speed']])
            
            print(f"   📡 Downstream QAM: {downstream_qam} metrics")
            print(f"   📤 Upstream QAM: {upstream_qam} metrics")
            print(f"   🚀 OFDM: {ofdm} metrics")
            print(f"   🖥️  System: {system} metrics")
            
            print("\n📋 Sample data:")
            print(json.dumps(dict(list(data.items())[:10]), indent=2))
            
            print(f"\n🎉 Beta test successful! Plugin is working correctly.")
            print("Ready for Netdata integration.")

    except KeyboardInterrupt:
        print('\n👋 Exiting...')


if __name__ == '__main__':
    main()
