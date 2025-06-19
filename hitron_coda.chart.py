#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Enhanced Netdata plugin for monitoring Hitron CODA cable modems.

This plugin collects:
- Downstream QAM channels (31 channels!)
- Downstream OFDM channels (DOCSIS 3.1)
- Upstream QAM channels (5 channels)
- Upstream OFDM channels (DOCSIS 3.1)
- Data throughput statistics
- System information
- Plugin health and reliability metrics

Version: 1.1.0
"""

import sys
import json
import time
import requests
import urllib3
import logging
from datetime import datetime, timedelta
from bases.FrameworkServices.SimpleService import SimpleService

NAME = 'hitron_coda'
UPDATE_EVERY = 60

# Disable SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging with timestamps
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class Service(SimpleService):
    """Enhanced Netdata service for Hitron CODA modem monitoring with reliability features."""

    def __init__(self, configuration=None, name=None):
        """Initialize the service with configuration and monitoring capabilities."""
        SimpleService.__init__(self, configuration=configuration, name=name)

        self.order = []
        self.definitions = {}
        
        # Debug configuration parsing
        logger.debug(f"Raw configuration received: {configuration}")
        logger.debug(f"Configuration type: {type(configuration)}")
        if hasattr(configuration, 'keys'):
            logger.debug(f"Configuration keys: {list(configuration.keys())}")
        
        # Handle configuration properly - Netdata passes different types
        if configuration is None:
            configuration = {}
        
        # Netdata sometimes passes configuration as a list or nested dict
        config = configuration
        if isinstance(configuration, dict) and len(configuration) == 1:
            # If config is like {'localhost': {actual_config}}, extract the actual config
            config_key = list(configuration.keys())[0]
            if isinstance(configuration[config_key], dict):
                config = configuration[config_key]
                logger.debug(f"Extracted nested config for '{config_key}': {config}")
        
        # Parse individual config values with detailed logging
        self.host = config.get('host', 'https://192.168.100.1')
        logger.debug(f"Host from config: {self.host}")
        
        self.update_every = config.get('update_every', UPDATE_EVERY)
        logger.debug(f"Update_every from config: {self.update_every} (default: {UPDATE_EVERY})")
        
        self.device_name = config.get('device_name', 'Hitron CODA Cable Modem')
        logger.debug(f"Device_name from config: '{self.device_name}'")
        
        # Create a consistent chart family name that matches plugin expectations
        # Use the plugin name as family to avoid hierarchy issues
        self.chart_family = NAME  # This will be 'hitron_coda'
        logger.debug(f"Chart family: '{self.chart_family}' (using plugin name to avoid hierarchy)")
        
        # Auto-calculate timeout based on update_every (70% of interval, min 5s, max 15s)
        auto_timeout = max(5, min(15, int(self.update_every * 0.7)))
        self.timeout = config.get('timeout', auto_timeout)
        logger.debug(f"Timeout: {self.timeout} (auto-calculated: {auto_timeout})")
        
        self.max_retries = config.get('max_retries', 3)  # Configurable retries
        logger.debug(f"Max retries: {self.max_retries}")
        
        # Session configuration with enhanced reliability
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers = {
            'User-Agent': 'Mozilla/5.0 (Netdata Hitron Plugin v1.1.0)',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }
        
        # Plugin health monitoring
        self.plugin_stats = {
            'start_time': datetime.now(),
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'timeout_errors': 0,
            'connection_errors': 0,
            'parse_errors': 0,
            'last_success': None,
            'last_failure': None,
            'consecutive_failures': 0,
            'max_consecutive_failures': 0,
            'endpoint_stats': {}
        }
        
        logger.info(f"Initializing Hitron CODA plugin with host: {self.host}")
        logger.info(f"Update interval: {self.update_every}s (from config: {config.get('update_every', 'not set')})")
        logger.info(f"Timeout: {self.timeout}s (auto-calculated: {auto_timeout}s), Max retries: {self.max_retries}")
        logger.info(f"Device name: '{self.device_name}'")

    def _get_cache_buster_url(self, endpoint):
        """Generate URL with cache buster parameter like the web interface uses."""
        import time
        epoch_ms = int(time.time() * 1000)
        base_url = f'{self.host}/data/{endpoint}'
        return f'{base_url}?_={epoch_ms}'

    def _make_request_with_retry(self, endpoint, description=""):
        """Make HTTP request with retry logic and comprehensive error handling."""
        endpoint_key = endpoint.replace('.asp', '')
        
        if endpoint_key not in self.plugin_stats['endpoint_stats']:
            self.plugin_stats['endpoint_stats'][endpoint_key] = {
                'requests': 0,
                'successes': 0,
                'failures': 0,
                'avg_response_time': 0,
                'last_success': None,
                'last_failure': None
            }
        
        stats = self.plugin_stats['endpoint_stats'][endpoint_key]
        stats['requests'] += 1
        self.plugin_stats['total_requests'] += 1
        
        for attempt in range(self.max_retries):
            try:
                start_time = time.time()
                url = self._get_cache_buster_url(endpoint)
                
                logger.debug(f"Requesting {endpoint} (attempt {attempt + 1}/{self.max_retries})")
                
                response = self.session.get(url, timeout=self.timeout)
                response_time = time.time() - start_time
                
                # Update response time average
                if stats['avg_response_time'] == 0:
                    stats['avg_response_time'] = response_time
                else:
                    stats['avg_response_time'] = (stats['avg_response_time'] + response_time) / 2
                
                response.raise_for_status()
                
                # Parse JSON response
                data = response.json()
                
                # Success - update stats
                stats['successes'] += 1
                stats['last_success'] = datetime.now()
                self.plugin_stats['successful_requests'] += 1
                self.plugin_stats['last_success'] = datetime.now()
                self.plugin_stats['consecutive_failures'] = 0
                
                logger.debug(f"Successfully retrieved {endpoint} in {response_time:.2f}s")
                return data
                
            except requests.exceptions.Timeout as e:
                self.plugin_stats['timeout_errors'] += 1
                logger.warning(f"Timeout on {endpoint} (attempt {attempt + 1}/{self.max_retries}): {str(e)}")
                
            except requests.exceptions.ConnectionError as e:
                self.plugin_stats['connection_errors'] += 1
                logger.warning(f"Connection error on {endpoint} (attempt {attempt + 1}/{self.max_retries}): {str(e)}")
                
            except requests.exceptions.RequestException as e:
                logger.warning(f"Request error on {endpoint} (attempt {attempt + 1}/{self.max_retries}): {str(e)}")
                
            except (ValueError, json.JSONDecodeError) as e:
                self.plugin_stats['parse_errors'] += 1
                logger.warning(f"JSON parse error on {endpoint} (attempt {attempt + 1}/{self.max_retries}): {str(e)}")
                
            except Exception as e:
                logger.error(f"Unexpected error on {endpoint} (attempt {attempt + 1}/{self.max_retries}): {str(e)}")
            
            # Wait before retry (exponential backoff)
            if attempt < self.max_retries - 1:
                wait_time = 2 ** attempt
                logger.debug(f"Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
        
        # All retries failed
        stats['failures'] += 1
        stats['last_failure'] = datetime.now()
        self.plugin_stats['failed_requests'] += 1
        self.plugin_stats['last_failure'] = datetime.now()
        self.plugin_stats['consecutive_failures'] += 1
        
        if self.plugin_stats['consecutive_failures'] > self.plugin_stats['max_consecutive_failures']:
            self.plugin_stats['max_consecutive_failures'] = self.plugin_stats['consecutive_failures']
        
        logger.error(f"Failed to retrieve {endpoint} after {self.max_retries} attempts")
        return None

    def init_charts(self, data):
        """Initialize charts based on available data."""
        # Plugin health monitoring chart (always first)
        self._create_plugin_health_chart()
        
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

    def _create_plugin_health_chart(self):
        """Create plugin health monitoring chart."""
        chart_name = 'plugin_health'
        # Use device_name directly as family - Netdata should preserve the casing in chart definitions
        chart_def = {
            'options': [chart_name, f'{self.device_name} - Plugin Health', 'status',
                       self.device_name, f'{NAME}.plugin_health', 'line'],
            'lines': [
                ['success_rate', 'Success Rate', 'absolute', 1, 1],
                ['response_time', 'Avg Response Time', 'absolute', 1, 100],  # Scale for ms
                ['consecutive_failures', 'Consecutive Failures', 'absolute', 1, 1],
                ['active_endpoints', 'Active Endpoints', 'absolute', 1, 1]
            ]
        }
        self.definitions[chart_name] = chart_def
        self.order.insert(0, chart_name)  # Place at the top

    def _create_downstream_power_chart(self, power_ids):
        """Create downstream QAM power chart."""
        chart_name = 'downstream_power'
        chart_def = {
            'options': [chart_name, f'{self.device_name} - Downstream Power', 'dBmV',
                       self.device_name, f'{NAME}.downstream_power', 'line'],
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
            'options': [chart_name, f'{self.device_name} - Downstream SNR', 'dB',
                       self.device_name, f'{NAME}.downstream_snr', 'line'],
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
            'options': [chart_name, f'{self.device_name} - Downstream Frequency', 'MHz',
                       self.device_name, f'{NAME}.downstream_frequency', 'line'],
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
            'options': [chart_name, f'{self.device_name} - Downstream Throughput', 'octets/s',
                       NAME, f'{NAME}.downstream_throughput', 'incremental'],
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
            'options': [chart_name, f'{self.device_name} - Corrected Errors', 'errors/s',
                       NAME, f'{NAME}.downstream_corrected', 'incremental'],
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
            'options': [chart_name, f'{self.device_name} - Uncorrected Errors', 'errors/s',
                       NAME, f'{NAME}.downstream_uncorrected', 'incremental'],
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
            'options': [chart_name, f'{self.device_name} - Upstream Power', 'dBmV',
                       NAME, f'{NAME}.upstream_power', 'line'],
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
            'options': [chart_name, f'{self.device_name} - Upstream Frequency', 'MHz',
                       NAME, f'{NAME}.upstream_frequency', 'line'],
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
            'options': [chart_name, f'{self.device_name} - Upstream Bandwidth', 'MHz',
                       NAME, f'{NAME}.upstream_bandwidth', 'line'],
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
            'options': [chart_name, f'{self.device_name} - OFDM Downstream Power', 'dBmV',
                       NAME, f'{NAME}.ofdm_downstream_power', 'line'],
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
            'options': [chart_name, f'{self.device_name} - OFDM Downstream SNR', 'dB',
                       NAME, f'{NAME}.ofdm_downstream_snr', 'line'],
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
            'options': [chart_name, f'{self.device_name} - OFDM Downstream Throughput', 'octets/s',
                       NAME, f'{NAME}.ofdm_downstream_throughput', 'incremental'],
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
            'options': [chart_name, f'{self.device_name} - Uptime', 'minutes',
                       NAME, f'{NAME}.system_uptime', 'line'],
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
            'options': [chart_name, f'{self.device_name} - Link Speed', 'Mbps',
                       NAME, f'{NAME}.link_speed', 'line'],
            'lines': [
                ['speed', 'Speed', 'absolute', 1, 1]
            ]
        }
        self.definitions[chart_name] = chart_def
        self.order.append(chart_name)

    def check(self):
        """Check if the modem is accessible and initialize charts."""
        try:
            logger.info(f"Checking connectivity to cable modem at {self.host}")
            data = self.get_data()
            if data is None:
                logger.error("Failed to retrieve initial data from modem")
                return False

            self.init_charts(data)
            logger.info(f"Hitron CODA plugin initialized successfully with {len(data)} metrics")
            
            # Log plugin statistics
            total_endpoints = len(self.plugin_stats['endpoint_stats'])
            success_rate = (self.plugin_stats['successful_requests'] / 
                          max(1, self.plugin_stats['total_requests'])) * 100
            
            logger.info(f"Plugin health: {success_rate:.1f}% success rate, {total_endpoints} endpoints tested")
            
            return True

        except Exception as e:
            logger.error(f"Cannot connect to Hitron cable modem: {str(e)}")
            return False

    def get_data(self):
        """Get comprehensive data from modem with health monitoring."""
        data = {}

        try:
            logger.debug("Starting data collection cycle")
            
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
            
            # Add plugin health metrics
            self._add_plugin_health_metrics(data)

            logger.debug(f"Data collection cycle completed with {len(data)} metrics")
            return data

        except Exception as e:
            logger.error(f"Failed to get data: {str(e)}")
            self.plugin_stats['consecutive_failures'] += 1
            return None

    def _add_plugin_health_metrics(self, data):
        """Add plugin health monitoring metrics to the data."""
        total_requests = max(1, self.plugin_stats['total_requests'])
        success_rate = int((self.plugin_stats['successful_requests'] / total_requests) * 100)
        
        # Calculate average response time across all endpoints
        avg_response_times = [
            stats['avg_response_time'] 
            for stats in self.plugin_stats['endpoint_stats'].values() 
            if stats['avg_response_time'] > 0
        ]
        avg_response_time = int((sum(avg_response_times) / max(1, len(avg_response_times))) * 1000)  # Convert to ms
        
        # Count active endpoints (those with recent success)
        now = datetime.now()
        active_endpoints = 0
        for stats in self.plugin_stats['endpoint_stats'].values():
            if (stats['last_success'] and 
                (now - stats['last_success']).total_seconds() < 300):  # Active within 5 minutes
                active_endpoints += 1
        
        data['success_rate'] = success_rate
        data['response_time'] = avg_response_time
        data['consecutive_failures'] = self.plugin_stats['consecutive_failures']
        data['active_endpoints'] = active_endpoints

    def _get_downstream_qam_data(self, data):
        """Get downstream QAM channel data (31 channels)."""
        ds_data = self._make_request_with_retry('dsinfo.asp', 'Downstream QAM channels')
        if ds_data is None:
            return
            
        try:
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

            logger.debug(f"Collected {len([k for k in data.keys() if k.startswith('ds_')])} downstream QAM metrics")

        except (ValueError, KeyError, IndexError) as e:
            logger.warning(f"Failed to parse downstream QAM data: {str(e)}")

    def _get_downstream_ofdm_data(self, data):
        """Get downstream OFDM channel data (DOCSIS 3.1)."""
        ofdm_data = self._make_request_with_retry('dsofdminfo.asp', 'Downstream OFDM channels')
        if ofdm_data is None:
            return
            
        try:
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
                logger.debug("DOCSIS 3.1 OFDM downstream channels detected and active")

        except (ValueError, KeyError, IndexError) as e:
            logger.warning(f"Failed to parse downstream OFDM data: {str(e)}")

    def _get_upstream_qam_data(self, data):
        """Get upstream QAM channel data (5 channels)."""
        us_data = self._make_request_with_retry('usinfo.asp', 'Upstream QAM channels')
        if us_data is None:
            return
            
        try:
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

            logger.debug(f"Collected {len([k for k in data.keys() if k.startswith('us_')])} upstream QAM metrics")

        except (ValueError, KeyError, IndexError) as e:
            logger.warning(f"Failed to parse upstream QAM data: {str(e)}")

    def _get_upstream_ofdm_data(self, data):
        """Get upstream OFDM channel data (DOCSIS 3.1)."""
        ofdm_data = self._make_request_with_retry('usofdminfo.asp', 'Upstream OFDM channels')
        if ofdm_data is None:
            return
            
        try:
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

        except (ValueError, KeyError, IndexError) as e:
            logger.warning(f"Failed to parse upstream OFDM data: {str(e)}")

    def _get_system_data(self, data):
        """Get system information."""
        sys_info = self._make_request_with_retry('getSysInfo.asp', 'System information')
        if sys_info is None:
            return
            
        try:
            if sys_info and len(sys_info) > 0:
                uptime_str = sys_info[0].get('systemUptime', '')
                if uptime_str:
                    data['uptime'] = self._parse_uptime(uptime_str)

        except (ValueError, KeyError, IndexError) as e:
            logger.warning(f"Failed to parse system data: {str(e)}")

    def _get_link_data(self, data):
        """Get link speed information."""
        link_info = self._make_request_with_retry('getLinkStatus.asp', 'Link status')
        if link_info is None:
            return
            
        try:
            if link_info and len(link_info) > 0:
                speed_str = link_info[0].get('LinkSpeed', '')
                if speed_str:
                    data['speed'] = int(float(speed_str.replace('Mbps', '')))

        except (ValueError, KeyError, IndexError) as e:
            logger.warning(f"Failed to parse link data: {str(e)}")

    def _parse_uptime(self, uptime_str):
        """Parse uptime string in format '128h:20m:54s' to minutes for better granularity."""
        try:
            parts = uptime_str.split(':')
            if len(parts) != 3:
                return 0

            hours = float(parts[0].replace('h', ''))
            minutes = float(parts[1].replace('m', ''))
            seconds = float(parts[2].replace('s', ''))

            # Convert to minutes for better chart granularity
            total_minutes = (hours * 60) + minutes + (seconds / 60)
            return int(total_minutes)

        except (ValueError, AttributeError):
            return 0


class TestService(Service):
    """Test service for standalone testing."""

    def __init__(self):
        """Initialize test service with default configuration."""
        configuration = {
            'name': 'test', 
            'host': 'https://192.168.100.1',
            'device_name': 'Test Hitron CODA',
            'update_every': 30,  # Test with 30 seconds instead of default
            'timeout': 10,
            'max_retries': 3
        }
        Service.__init__(self, configuration=configuration, name='test')


def main():
    """Main function for standalone testing."""
    import logging
    import argparse
    
    # Add command line argument parsing for testing
    parser = argparse.ArgumentParser(description='Test Hitron CODA plugin')
    parser.add_argument('--host', default='https://192.168.100.1', 
                       help='Modem host URL (default: %(default)s)')
    parser.add_argument('--update-every', type=int, default=30,
                       help='Update interval in seconds (default: %(default)s)')
    parser.add_argument('--device-name', default='Test Hitron CODA',
                       help='Device display name (default: %(default)s)')
    parser.add_argument('--timeout', type=int, 
                       help='Request timeout in seconds (auto-calculated if not specified)')
    parser.add_argument('--max-retries', type=int, default=3,
                       help='Maximum retry attempts (default: %(default)s)')
    parser.add_argument('--debug', action='store_true',
                       help='Enable debug logging')
    
    args = parser.parse_args()
    
    # Configure logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Create test configuration from command line args
    test_config = {
        'name': 'test',
        'host': args.host,
        'device_name': args.device_name,
        'update_every': args.update_every,
        'max_retries': args.max_retries
    }
    
    # Add timeout if specified
    if args.timeout:
        test_config['timeout'] = args.timeout
    
    logger.info("=== Hitron CODA Plugin Standalone Test ===")
    logger.info(f"Test configuration: {test_config}")
    
    service = Service(configuration=test_config, name='test')

    # Check if we can connect to the modem
    logger.info("Testing enhanced plugin with health monitoring...")
    if not service.check():
        logger.error("❌ Failed to connect to modem")
        sys.exit(1)

    logger.info("✅ Plugin initialized successfully!")
    logger.info("🎯 Testing data collection with reliability features...")

    try:
        data = service.get_data()
        if data is not None:
            logger.info(f"📊 Collected {len(data)} metrics:")
            
            # Group metrics by type
            downstream_qam = len([k for k in data.keys() if k.startswith('ds_')])
            upstream_qam = len([k for k in data.keys() if k.startswith('us_')])
            ofdm = len([k for k in data.keys() if k.startswith('ofdm_')])
            system = len([k for k in data.keys() if k in ['uptime', 'speed']])
            health = len([k for k in data.keys() if k in ['success_rate', 'response_time', 'consecutive_failures', 'active_endpoints']])
            
            logger.info(f"   📡 Downstream QAM: {downstream_qam} metrics")
            logger.info(f"   📤 Upstream QAM: {upstream_qam} metrics")
            logger.info(f"   🚀 OFDM: {ofdm} metrics")
            logger.info(f"   🖥️  System: {system} metrics")
            logger.info(f"   🩺 Plugin Health: {health} metrics")
            
            # Show plugin health stats
            logger.info(f"\n🩺 Plugin Health Report:")
            logger.info(f"   Success Rate: {data.get('success_rate', 0)}%")
            logger.info(f"   Avg Response Time: {data.get('response_time', 0)}ms")
            logger.info(f"   Consecutive Failures: {data.get('consecutive_failures', 0)}")
            logger.info(f"   Active Endpoints: {data.get('active_endpoints', 0)}")
            
            # Show configuration validation
            logger.info(f"\n⚙️ Configuration Validation:")
            logger.info(f"   Update Every: {service.update_every}s")
            logger.info(f"   Timeout: {service.timeout}s")
            logger.info(f"   Max Retries: {service.max_retries}")
            logger.info(f"   Device Name: '{service.device_name}'")
            
            # Show sample data
            logger.info(f"\n📋 Sample data:")
            sample_data = dict(list(data.items())[:10])
            print(json.dumps(sample_data, indent=2))
            
            logger.info(f"\n🎉 Enhanced plugin test successful! All reliability features working.")
            logger.info("Ready for Netdata integration with health monitoring.")
            
            # Show usage example
            logger.info(f"\n💡 To use this configuration in Netdata:")
            logger.info(f"   host: '{args.host}'")
            logger.info(f"   device_name: '{args.device_name}'")
            logger.info(f"   update_every: {args.update_every}")
            if args.timeout:
                logger.info(f"   timeout: {args.timeout}")
            logger.info(f"   max_retries: {args.max_retries}")

    except KeyboardInterrupt:
        logger.info('\n👋 Exiting...')
    except Exception as e:
        logger.error(f"❌ Test failed with exception: {str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    main()
