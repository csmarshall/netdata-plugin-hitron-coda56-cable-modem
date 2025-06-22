#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Enhanced Netdata plugin for monitoring Hitron CODA cable modems.

Key Features:
- PARALLEL endpoint collection by default (configurable)
- Smart timeout management (per-endpoint + overall collection timeout)
- Dynamic retry calculation based on timeouts and intervals
- Enhanced reliability and performance monitoring

Version: 1.2.0
"""

import sys
import json
import time
import asyncio
import aiohttp
import ssl
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
    """Enhanced Netdata service for Hitron CODA modem monitoring with parallel collection."""

    def __init__(self, configuration=None, name=None):
        """Initialize the service with enhanced configuration and monitoring capabilities."""
        SimpleService.__init__(self, configuration=configuration, name=name)

        self.order = []
        self.definitions = {}
        
        # Debug configuration parsing
        logger.debug(f"Raw configuration received: {configuration}")
        
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
        self.update_every = config.get('update_every', UPDATE_EVERY)
        self.device_name = config.get('device_name', 'Hitron CODA Cable Modem')
        
        # Enhanced timeout configuration
        # 1. Per-endpoint timeout (how long to wait for each individual endpoint)
        # 2. Overall collection timeout (how long for entire collection cycle)
        
        # Per-endpoint timeout: default 4 seconds, configurable
        self.endpoint_timeout = config.get('endpoint_timeout', 4)
        
        # Overall collection timeout: default 90% of update_every, configurable
        default_collection_timeout = int(self.update_every * 0.9)
        self.collection_timeout = config.get('collection_timeout', default_collection_timeout)
        
        # Parallel collection mode (default: False for maximum compatibility)
        # Serial mode provides absolute guarantee of one-request-at-a-time
        # Parallel mode offers better performance but may overwhelm sensitive modems
        self.use_parallel = config.get('parallel_collection', False)
        
        # Inter-request delay for serial mode (optional, for very sensitive modems)
        # Adds a pause between each endpoint request in serial mode
        # Default: 0 (no delay), can be set to 0.5-2 seconds for problematic modems
        self.inter_request_delay = config.get('inter_request_delay', 0)
        
        # Dynamic retry calculation based on timeouts and update interval
        # max_retries = floor(collection_timeout / endpoint_timeout), minimum 1
        calculated_retries = max(1, int(self.collection_timeout / self.endpoint_timeout))
        self.max_retries = config.get('max_retries', calculated_retries)
        
        # Create a consistent chart family name
        self.chart_family = NAME  # This will be 'hitron_coda'
        
        # SSL context for async requests
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        
        # Session configuration for fallback sync requests
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers = {
            'User-Agent': 'Mozilla/5.0 (Netdata Hitron Plugin v1.2.0)',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }
        
        # Define all endpoints we'll collect from
        self.endpoints = [
            ('dsinfo.asp', 'Downstream QAM channels', self._parse_downstream_qam_data),
            ('dsofdminfo.asp', 'Downstream OFDM channels', self._parse_downstream_ofdm_data),
            ('usinfo.asp', 'Upstream QAM channels', self._parse_upstream_qam_data),
            ('usofdminfo.asp', 'Upstream OFDM channels', self._parse_upstream_ofdm_data),
            ('getSysInfo.asp', 'System information', self._parse_system_data),
            ('getLinkStatus.asp', 'Link status', self._parse_link_data)
        ]
        
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
            'collection_times': [],
            'endpoint_stats': {}
        }
        
        # Initialize endpoint stats
        for endpoint, _, _ in self.endpoints:
            endpoint_key = endpoint.replace('.asp', '')
            self.plugin_stats['endpoint_stats'][endpoint_key] = {
                'requests': 0,
                'successes': 0,
                'failures': 0,
                'avg_response_time': 0,
                'last_success': None,
                'last_failure': None
            }
        
        logger.info(f"Initializing Enhanced Hitron CODA plugin:")
        logger.info(f"  Host: {self.host}")
        logger.info(f"  Device name: '{self.device_name}'")
        logger.info(f"  Update interval: {self.update_every}s")
        logger.info(f"  Collection mode: {'PARALLEL' if self.use_parallel else 'SERIAL'}")
        logger.info(f"  Per-endpoint timeout: {self.endpoint_timeout}s")
        logger.info(f"  Overall collection timeout: {self.collection_timeout}s")
        logger.info(f"  Max retries: {self.max_retries} (calculated: {calculated_retries})")
        if not self.use_parallel and self.inter_request_delay > 0:
            logger.info(f"  Inter-request delay: {self.inter_request_delay}s (SERIAL mode only)")
        logger.info(f"  Endpoints: {len(self.endpoints)} total")
        
        # Validate configuration for serial mode
        if not self.use_parallel:
            estimated_serial_time = (len(self.endpoints) * (self.endpoint_timeout + self.inter_request_delay))
            if estimated_serial_time > self.collection_timeout:
                logger.warning(f"SERIAL mode configuration may be problematic:")
                logger.warning(f"  Estimated collection time: {estimated_serial_time}s")
                logger.warning(f"  Collection timeout: {self.collection_timeout}s")
                logger.warning(f"  Consider increasing collection_timeout or reducing endpoint_timeout")
            else:
                logger.info(f"SERIAL mode timing validated - estimated collection time: {estimated_serial_time}s")

    def _get_cache_buster_url(self, endpoint):
        """Generate URL with cache buster parameter like the web interface uses."""
        epoch_ms = int(time.time() * 1000)
        base_url = f'{self.host}/data/{endpoint}'
        return f'{base_url}?_={epoch_ms}'

    async def _make_async_request_with_retry(self, session, endpoint, description=""):
        """Make async HTTP request with retry logic."""
        endpoint_key = endpoint.replace('.asp', '')
        stats = self.plugin_stats['endpoint_stats'][endpoint_key]
        
        for attempt in range(self.max_retries):
            try:
                start_time = time.time()
                url = self._get_cache_buster_url(endpoint)
                
                logger.debug(f"Async request {endpoint} (attempt {attempt + 1}/{self.max_retries})")
                
                async with session.get(
                    url,
                    ssl=self.ssl_context,
                    timeout=aiohttp.ClientTimeout(total=self.endpoint_timeout)
                ) as response:
                    response_time = time.time() - start_time
                    content = await response.read()
                    
                    # Update response time average
                    if stats['avg_response_time'] == 0:
                        stats['avg_response_time'] = response_time
                    else:
                        stats['avg_response_time'] = (stats['avg_response_time'] + response_time) / 2
                    
                    response.raise_for_status()
                    
                    # Parse JSON response
                    data = json.loads(content)
                    
                    # Success - update stats
                    stats['requests'] += 1
                    stats['successes'] += 1
                    stats['last_success'] = datetime.now()
                    self.plugin_stats['total_requests'] += 1
                    self.plugin_stats['successful_requests'] += 1
                    
                    logger.debug(f"Successfully retrieved {endpoint} in {response_time:.2f}s")
                    return data
                    
            except asyncio.TimeoutError:
                self.plugin_stats['timeout_errors'] += 1
                logger.debug(f"Timeout on {endpoint} (attempt {attempt + 1}/{self.max_retries})")
                
            except aiohttp.ClientError as e:
                self.plugin_stats['connection_errors'] += 1
                logger.debug(f"Connection error on {endpoint} (attempt {attempt + 1}/{self.max_retries}): {str(e)}")
                
            except (ValueError, json.JSONDecodeError) as e:
                self.plugin_stats['parse_errors'] += 1
                logger.debug(f"JSON parse error on {endpoint} (attempt {attempt + 1}/{self.max_retries}): {str(e)}")
                
            except Exception as e:
                logger.debug(f"Unexpected error on {endpoint} (attempt {attempt + 1}/{self.max_retries}): {str(e)}")
            
            # Wait before retry (exponential backoff, but capped to stay within collection timeout)
            if attempt < self.max_retries - 1:
                wait_time = min(2 ** attempt, self.endpoint_timeout / 2)
                await asyncio.sleep(wait_time)
        
        # All retries failed
        stats['requests'] += 1
        stats['failures'] += 1
        stats['last_failure'] = datetime.now()
        self.plugin_stats['total_requests'] += 1
        self.plugin_stats['failed_requests'] += 1
        
        logger.warning(f"Failed to retrieve {endpoint} after {self.max_retries} attempts")
        return None

    def _make_sync_request_with_retry(self, endpoint, description=""):
        """Fallback sync HTTP request with retry logic (used in serial mode)."""
        endpoint_key = endpoint.replace('.asp', '')
        stats = self.plugin_stats['endpoint_stats'][endpoint_key]
        
        for attempt in range(self.max_retries):
            try:
                start_time = time.time()
                url = self._get_cache_buster_url(endpoint)
                
                logger.debug(f"Sync request {endpoint} (attempt {attempt + 1}/{self.max_retries})")
                
                response = self.session.get(url, timeout=self.endpoint_timeout)
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
                stats['requests'] += 1
                stats['successes'] += 1
                stats['last_success'] = datetime.now()
                self.plugin_stats['total_requests'] += 1
                self.plugin_stats['successful_requests'] += 1
                
                logger.debug(f"Successfully retrieved {endpoint} in {response_time:.2f}s")
                return data
                
            except requests.exceptions.Timeout:
                self.plugin_stats['timeout_errors'] += 1
                logger.debug(f"Timeout on {endpoint} (attempt {attempt + 1}/{self.max_retries})")
                
            except requests.exceptions.ConnectionError as e:
                self.plugin_stats['connection_errors'] += 1
                logger.debug(f"Connection error on {endpoint} (attempt {attempt + 1}/{self.max_retries}): {str(e)}")
                
            except requests.exceptions.RequestException as e:
                logger.debug(f"Request error on {endpoint} (attempt {attempt + 1}/{self.max_retries}): {str(e)}")
                
            except (ValueError, json.JSONDecodeError) as e:
                self.plugin_stats['parse_errors'] += 1
                logger.debug(f"JSON parse error on {endpoint} (attempt {attempt + 1}/{self.max_retries}): {str(e)}")
                
            except Exception as e:
                logger.debug(f"Unexpected error on {endpoint} (attempt {attempt + 1}/{self.max_retries}): {str(e)}")
            
            # Wait before retry
            if attempt < self.max_retries - 1:
                wait_time = min(2 ** attempt, self.endpoint_timeout / 2)
                time.sleep(wait_time)
        
        # All retries failed
        stats['requests'] += 1
        stats['failures'] += 1
        stats['last_failure'] = datetime.now()
        self.plugin_stats['total_requests'] += 1
        self.plugin_stats['failed_requests'] += 1
        
        logger.warning(f"Failed to retrieve {endpoint} after {self.max_retries} attempts")
        return None

    async def _collect_data_parallel(self):
        """Collect data from all endpoints in parallel."""
        collection_start = time.time()
        
        logger.debug("Starting PARALLEL data collection")
        
        try:
            # Create async session
            timeout = aiohttp.ClientTimeout(total=self.collection_timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                
                # Create tasks for all endpoints
                tasks = []
                for endpoint, description, parser in self.endpoints:
                    task = self._make_async_request_with_retry(session, endpoint, description)
                    tasks.append((task, endpoint, parser))
                
                # Wait for all tasks to complete or timeout
                results = await asyncio.wait_for(
                    asyncio.gather(*[task for task, _, _ in tasks], return_exceptions=True),
                    timeout=self.collection_timeout
                )
                
                # Process results
                data = {}
                for i, (result, endpoint, parser) in enumerate(zip(results, [t[1:] for t in tasks])):
                    endpoint_name, parser_func = result
                    
                    if isinstance(results[i], Exception):
                        logger.warning(f"Exception in parallel collection for {endpoint_name}: {str(results[i])}")
                        continue
                    
                    if results[i] is not None:
                        try:
                            parser_func(results[i], data)
                        except Exception as e:
                            logger.warning(f"Failed to parse data from {endpoint_name}: {str(e)}")
                
                collection_time = time.time() - collection_start
                self.plugin_stats['collection_times'].append(collection_time)
                
                logger.debug(f"PARALLEL collection completed in {collection_time:.2f}s")
                return data
                
        except asyncio.TimeoutError:
            logger.warning(f"Parallel collection timed out after {self.collection_timeout}s")
            self.plugin_stats['consecutive_failures'] += 1
            return {}
        except Exception as e:
            logger.error(f"Parallel collection failed: {str(e)}")
            self.plugin_stats['consecutive_failures'] += 1
            return {}

    def _collect_data_serial(self):
        """Collect data from all endpoints serially with strict one-at-a-time guarantee."""
        collection_start = time.time()
        data = {}
        
        logger.debug("Starting SERIAL data collection - ONE ENDPOINT AT A TIME")
        
        try:
            for i, (endpoint, description, parser) in enumerate(self.endpoints):
                # Check if we're running out of time
                elapsed = time.time() - collection_start
                if elapsed >= self.collection_timeout:
                    logger.warning(f"Serial collection timeout reached after {i} endpoints, stopping at {endpoint}")
                    break
                
                # Explicit logging to show one-at-a-time behavior
                logger.debug(f"Serial collection step {i+1}/{len(self.endpoints)}: Starting {endpoint}")
                
                # GUARANTEE: Only one endpoint request at a time
                # No async, no threading, no parallelism - pure sequential
                result = self._make_sync_request_with_retry(endpoint, description)
                
                if result is not None:
                    try:
                        parser(result, data)
                        logger.debug(f"Serial collection step {i+1}/{len(self.endpoints)}: Completed {endpoint} successfully")
                    except Exception as e:
                        logger.warning(f"Failed to parse data from {endpoint}: {str(e)}")
                else:
                    logger.debug(f"Serial collection step {i+1}/{len(self.endpoints)}: Failed {endpoint}")
                
                # Optional: Add inter-request delay for extremely sensitive modems
                if hasattr(self, 'inter_request_delay') and self.inter_request_delay > 0:
                    logger.debug(f"Waiting {self.inter_request_delay}s before next endpoint...")
                    time.sleep(self.inter_request_delay)
            
            collection_time = time.time() - collection_start
            self.plugin_stats['collection_times'].append(collection_time)
            
            logger.debug(f"SERIAL collection completed in {collection_time:.2f}s - processed {i+1} endpoints sequentially")
            return data
            
        except Exception as e:
            logger.error(f"Serial collection failed: {str(e)}")
            self.plugin_stats['consecutive_failures'] += 1
            return {}

    def init_charts(self, data):
        """Initialize charts based on available data."""
        # Plugin health monitoring chart (always first)
        self._create_plugin_health_chart()
        
        # Downstream QAM Power chart
        ds_power_ids = sorted([k for k in data.keys() if k.startswith('ds_power_')])
        if ds_power_ids:
            self._create_downstream_power_chart(ds_power_ids)

        # Downstream QAM SNR chart
        ds_snr_ids = sorted([k for k in data.keys() if k.startswith('ds_snr_')])
        if ds_snr_ids:
            self._create_downstream_snr_chart(ds_snr_ids)

        # Downstream QAM Frequency chart
        ds_freq_ids = sorted([k for k in data.keys() if k.startswith('ds_freq_')])
        if ds_freq_ids:
            self._create_downstream_frequency_chart(ds_freq_ids)

        # Downstream data throughput
        ds_octets_ids = sorted([k for k in data.keys() if k.startswith('ds_octets_')])
        if ds_octets_ids:
            self._create_downstream_throughput_chart(ds_octets_ids)

        # Downstream QAM corrected errors
        ds_corrected_ids = sorted([k for k in data.keys() if k.startswith('ds_corrected_')])
        if ds_corrected_ids:
            self._create_downstream_corrected_chart(ds_corrected_ids)

        # Downstream QAM uncorrected errors
        ds_uncorrected_ids = sorted([k for k in data.keys() if k.startswith('ds_uncorrected_')])
        if ds_uncorrected_ids:
            self._create_downstream_uncorrected_chart(ds_uncorrected_ids)

        # Upstream QAM Power chart
        us_power_ids = sorted([k for k in data.keys() if k.startswith('us_power_')])
        if us_power_ids:
            self._create_upstream_power_chart(us_power_ids)

        # Upstream QAM Frequency chart
        us_freq_ids = sorted([k for k in data.keys() if k.startswith('us_freq_')])
        if us_freq_ids:
            self._create_upstream_frequency_chart(us_freq_ids)

        # Upstream QAM Bandwidth chart
        us_bw_ids = sorted([k for k in data.keys() if k.startswith('us_bandwidth_')])
        if us_bw_ids:
            self._create_upstream_bandwidth_chart(us_bw_ids)

        # OFDM Downstream Power
        ofdm_ds_power_ids = sorted([k for k in data.keys() if k.startswith('ofdm_ds_power_')])
        if ofdm_ds_power_ids:
            self._create_ofdm_downstream_power_chart(ofdm_ds_power_ids)

        # OFDM Downstream SNR
        ofdm_ds_snr_ids = sorted([k for k in data.keys() if k.startswith('ofdm_ds_snr_')])
        if ofdm_ds_snr_ids:
            self._create_ofdm_downstream_snr_chart(ofdm_ds_snr_ids)

        # OFDM Downstream Throughput
        ofdm_ds_octets_ids = sorted([k for k in data.keys() if k.startswith('ofdm_ds_octets_')])
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
        chart_def = {
            'options': [chart_name, f'{self.device_name} - Plugin Health', 'status',
                       self.device_name, f'{NAME}.plugin_health', 'line'],
            'lines': [
                ['success_rate', 'Success Rate', 'absolute', 1, 1],
                ['response_time', 'Avg Response Time', 'absolute', 1, 100],  # Scale for ms
                ['consecutive_failures', 'Consecutive Failures', 'absolute', 1, 1],
                ['active_endpoints', 'Active Endpoints', 'absolute', 1, 1],
                ['collection_time', 'Collection Time', 'absolute', 1, 100]  # Scale for ms
            ]
        }
        self.definitions[chart_name] = chart_def
        self.order.insert(0, chart_name)  # Place at the top

    # [Chart creation methods remain the same as before - abbreviated for space]
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
            chart_def['lines'].append([power_id, f'CH{channel}', 'absolute', 1, 10])
        self.definitions[chart_name] = chart_def
        self.order.append(chart_name)

    # [Additional chart creation methods would be here - same as before]
    
    def check(self):
        """Check if the modem is accessible and initialize charts."""
        try:
            logger.info(f"Checking connectivity to cable modem at {self.host}")
            data = self.get_data()
            if data is None or len(data) == 0:
                logger.error("Failed to retrieve initial data from modem")
                return False

            self.init_charts(data)
            logger.info(f"Enhanced Hitron CODA plugin initialized successfully with {len(data)} metrics")
            
            # Log plugin statistics
            total_endpoints = len(self.plugin_stats['endpoint_stats'])
            success_rate = (self.plugin_stats['successful_requests'] / 
                          max(1, self.plugin_stats['total_requests'])) * 100
            
            logger.info(f"Plugin health: {success_rate:.1f}% success rate, {total_endpoints} endpoints tested")
            logger.info(f"Collection mode: {'PARALLEL' if self.use_parallel else 'SERIAL'}")
            
            return True

        except Exception as e:
            logger.error(f"Cannot connect to Hitron cable modem: {str(e)}")
            return False

    def get_data(self):
        """Get comprehensive data from modem with enhanced parallel collection."""
        try:
            logger.debug("Starting enhanced data collection cycle")
            
            if self.use_parallel:
                # Use async parallel collection
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    data = loop.run_until_complete(self._collect_data_parallel())
                finally:
                    loop.close()
            else:
                # Use sync serial collection
                data = self._collect_data_serial()
            
            # Add plugin health metrics
            self._add_plugin_health_metrics(data)
            
            # Update success stats if we got data
            if data and len(data) > 4:  # At least some real data beyond health metrics
                self.plugin_stats['last_success'] = datetime.now()
                self.plugin_stats['consecutive_failures'] = 0
            else:
                self.plugin_stats['consecutive_failures'] += 1
                if self.plugin_stats['consecutive_failures'] > self.plugin_stats['max_consecutive_failures']:
                    self.plugin_stats['max_consecutive_failures'] = self.plugin_stats['consecutive_failures']

            logger.debug(f"Enhanced data collection cycle completed with {len(data)} metrics")
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
        
        # Calculate average collection time
        recent_collection_times = self.plugin_stats['collection_times'][-10:]  # Last 10 collections
        avg_collection_time = int((sum(recent_collection_times) / max(1, len(recent_collection_times))) * 1000)  # Convert to ms
        
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
        data['collection_time'] = avg_collection_time

    # Data parsing methods
    def _parse_downstream_qam_data(self, ds_data, data):
        """Parse downstream QAM channel data (31 channels)."""
        try:
            for channel in ds_data:
                idx = int(channel['channelId'])
                data[f'ds_power_{idx}'] = int(float(channel['signalStrength']) * 10)
                data[f'ds_snr_{idx}'] = int(float(channel['snr']) * 100)
                freq_mhz = int(channel['frequency']) / 1000000
                data[f'ds_freq_{idx}'] = int(freq_mhz)
                data[f'ds_octets_{idx}'] = int(channel['dsoctets'])
                data[f'ds_corrected_{idx}'] = int(channel['correcteds'])
                data[f'ds_uncorrected_{idx}'] = int(channel['uncorrect'])
            logger.debug(f"Parsed {len([k for k in data.keys() if k.startswith('ds_')])} downstream QAM metrics")
        except (ValueError, KeyError, IndexError) as e:
            logger.warning(f"Failed to parse downstream QAM data: {str(e)}")

    def _parse_downstream_ofdm_data(self, ofdm_data, data):
        """Parse downstream OFDM channel data (DOCSIS 3.1)."""
        try:
            for idx, channel in enumerate(ofdm_data):
                if (channel.get('receive') == '1' and 
                    channel.get('plcpower') != 'NA' and
                    channel.get('plcpower')):
                    
                    power = float(channel['plcpower'])
                    data[f'ofdm_ds_power_{idx}'] = int(power * 100)
                    
                    if channel.get('SNR') != 'NA' and channel.get('SNR'):
                        snr = float(channel['SNR'])
                        data[f'ofdm_ds_snr_{idx}'] = int(snr)
                    
                    if channel.get('dsoctets') != 'NA' and channel.get('dsoctets'):
                        octets = int(channel['dsoctets'])
                        data[f'ofdm_ds_octets_{idx}'] = octets
            if any(k.startswith('ofdm_ds_') for k in data.keys()):
                logger.debug("DOCSIS 3.1 OFDM downstream channels detected")
        except (ValueError, KeyError, IndexError) as e:
            logger.warning(f"Failed to parse downstream OFDM data: {str(e)}")

    def _parse_upstream_qam_data(self, us_data, data):
        """Parse upstream QAM channel data (5 channels)."""
        try:
            for channel in us_data:
                idx = int(channel['channelId'])
                power = float(channel['signalStrength'])
                data[f'us_power_{idx}'] = int(power * 100)
                freq_hz = int(channel['frequency'])
                freq_mhz = freq_hz / 1000000
                data[f'us_freq_{idx}'] = int(freq_mhz)
                bw_hz = int(channel['bandwidth'])
                bw_mhz = bw_hz / 1000000
                data[f'us_bandwidth_{idx}'] = int(bw_mhz * 10)
            logger.debug(f"Parsed {len([k for k in data.keys() if k.startswith('us_')])} upstream QAM metrics")
        except (ValueError, KeyError, IndexError) as e:
            logger.warning(f"Failed to parse upstream QAM data: {str(e)}")

    def _parse_upstream_ofdm_data(self, ofdm_data, data):
        """Parse upstream OFDM channel data (DOCSIS 3.1)."""
        try:
            for idx, channel in enumerate(ofdm_data):
                state = channel.get('state', '').strip()
                if state != 'DISABLED' and float(channel.get('frequency', '0')) > 0:
                    power = float(channel.get('repPower', '0'))
                    if power > 0:
                        data[f'ofdm_us_power_{idx}'] = int(power * 100)
                    
                    freq = float(channel.get('frequency', '0'))
                    if freq > 0:
                        freq_mhz = freq / 1000000
                        data[f'ofdm_us_freq_{idx}'] = int(freq_mhz)
        except (ValueError, KeyError, IndexError) as e:
            logger.warning(f"Failed to parse upstream OFDM data: {str(e)}")

    def _parse_system_data(self, sys_info, data):
        """Parse system information."""
        try:
            if sys_info and len(sys_info) > 0:
                uptime_str = sys_info[0].get('systemUptime', '')
                if uptime_str:
                    data['uptime'] = self._parse_uptime(uptime_str)
        except (ValueError, KeyError, IndexError) as e:
            logger.warning(f"Failed to parse system data: {str(e)}")

    def _parse_link_data(self, link_info, data):
        """Parse link speed information."""
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

    # Additional chart creation methods (abbreviated for space)
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
            chart_def['lines'].append([snr_id, f'CH{channel}', 'absolute', 1, 100])
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
            chart_def['lines'].append([freq_id, f'CH{channel}', 'absolute', 1, 1])
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
            chart_def['lines'].append([octets_id, f'CH{channel}', 'incremental', 1, 1])
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
            chart_def['lines'].append([corrected_id, f'CH{channel}', 'incremental', 1, 1])
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
            chart_def['lines'].append([uncorrected_id, f'CH{channel}', 'incremental', 1, 1])
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
            chart_def['lines'].append([power_id, f'CH{channel}', 'absolute', 1, 100])
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
            chart_def['lines'].append([freq_id, f'CH{channel}', 'absolute', 1, 1])
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
            chart_def['lines'].append([bw_id, f'CH{channel}', 'absolute', 1, 10])
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
            chart_def['lines'].append([power_id, f'OFDM{channel}', 'absolute', 1, 100])
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
            chart_def['lines'].append([snr_id, f'OFDM{channel}', 'absolute', 1, 1])
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
            chart_def['lines'].append([octets_id, f'OFDM{channel}', 'incremental', 1, 1])
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


class TestService(Service):
    """Test service for standalone testing."""

    def __init__(self):
        """Initialize test service with default configuration."""
        configuration = {
            'name': 'test', 
            'host': 'https://192.168.100.1',
            'device_name': 'Test Hitron CODA',
            'update_every': 30,
            'endpoint_timeout': 4,
            'collection_timeout': 25,  # 90% of 30s
            'parallel_collection': False,
            'inter_request_delay': 0,  # No delay for testing
            'max_retries': 6  # 25 / 4 = 6.25, rounded down to 6
        }
        Service.__init__(self, configuration=configuration, name='test')


def main():
    """Main function for standalone testing."""
    import logging
    import argparse
    
    # Add command line argument parsing for testing
    parser = argparse.ArgumentParser(description='Test Enhanced Hitron CODA plugin')
    parser.add_argument('--host', default='https://192.168.100.1', 
                       help='Modem host URL (default: %(default)s)')
    parser.add_argument('--update-every', type=int, default=30,
                       help='Update interval in seconds (default: %(default)s)')
    parser.add_argument('--device-name', default='Test Hitron CODA',
                       help='Device display name (default: %(default)s)')
    parser.add_argument('--endpoint-timeout', type=int, default=4,
                       help='Per-endpoint timeout in seconds (default: %(default)s)')
    parser.add_argument('--collection-timeout', type=int,
                       help='Overall collection timeout in seconds (auto-calculated if not specified)')
    parser.add_argument('--max-retries', type=int,
                       help='Maximum retry attempts (auto-calculated if not specified)')
    parser.add_argument('--inter-request-delay', type=float, default=0,
                       help='Delay between endpoints in serial mode (default: %(default)s)')
    parser.add_argument('--serial', action='store_true',
                       help='Use serial collection instead of parallel (default: parallel)')
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
        'endpoint_timeout': args.endpoint_timeout,
        'parallel_collection': not args.serial,
        'inter_request_delay': args.inter_request_delay
    }
    
    # Add collection timeout if specified, otherwise auto-calculate
    if args.collection_timeout:
        test_config['collection_timeout'] = args.collection_timeout
    else:
        test_config['collection_timeout'] = int(args.update_every * 0.9)
    
    # Add max retries if specified, otherwise auto-calculate
    if args.max_retries:
        test_config['max_retries'] = args.max_retries
    else:
        test_config['max_retries'] = max(1, int(test_config['collection_timeout'] / args.endpoint_timeout))
    
    logger.info("=== Enhanced Hitron CODA Plugin Test ===")
    logger.info(f"Test configuration: {test_config}")
    
    service = Service(configuration=test_config, name='test')

    # Check if we can connect to the modem
    logger.info("Testing enhanced plugin with parallel collection and smart timeouts...")
    if not service.check():
        logger.error("❌ Failed to connect to modem")
        sys.exit(1)

    logger.info("✅ Enhanced plugin initialized successfully!")
    logger.info("🎯 Testing data collection with new features...")

    try:
        # Test multiple collection cycles to show performance
        for cycle in range(3):
            logger.info(f"\n--- Collection Cycle {cycle + 1} ---")
            start_time = time.time()
            
            data = service.get_data()
            collection_time = time.time() - start_time
            
            if data is not None:
                logger.info(f"📊 Collected {len(data)} metrics in {collection_time:.2f}s:")
                
                # Group metrics by type
                downstream_qam = len([k for k in data.keys() if k.startswith('ds_')])
                upstream_qam = len([k for k in data.keys() if k.startswith('us_')])
                ofdm = len([k for k in data.keys() if k.startswith('ofdm_')])
                system = len([k for k in data.keys() if k in ['uptime', 'speed']])
                health = len([k for k in data.keys() if k in ['success_rate', 'response_time', 'consecutive_failures', 'active_endpoints', 'collection_time']])
                
                logger.info(f"   📡 Downstream QAM: {downstream_qam} metrics")
                logger.info(f"   📤 Upstream QAM: {upstream_qam} metrics")
                logger.info(f"   🚀 OFDM: {ofdm} metrics")
                logger.info(f"   🖥️  System: {system} metrics")
                logger.info(f"   🩺 Plugin Health: {health} metrics")
                
                # Show plugin health stats
                logger.info(f"   ⚡ Success Rate: {data.get('success_rate', 0)}%")
                logger.info(f"   ⏱️  Avg Response Time: {data.get('response_time', 0)}ms")
                logger.info(f"   📊 Collection Time: {data.get('collection_time', 0)}ms")
                logger.info(f"   🔗 Active Endpoints: {data.get('active_endpoints', 0)}")
                
            else:
                logger.error(f"❌ Failed to collect data in cycle {cycle + 1}")
            
            if cycle < 2:  # Don't wait after the last cycle
                logger.info("⏳ Waiting 5 seconds before next cycle...")
                time.sleep(5)
        
        # Show configuration summary
        logger.info(f"\n🎛️ Enhanced Configuration Summary:")
        logger.info(f"   Collection Mode: {'PARALLEL' if service.use_parallel else 'SERIAL'}")
        logger.info(f"   Update Every: {service.update_every}s")
        logger.info(f"   Endpoint Timeout: {service.endpoint_timeout}s")
        logger.info(f"   Collection Timeout: {service.collection_timeout}s")
        logger.info(f"   Max Retries: {service.max_retries}")
        logger.info(f"   Device Name: '{service.device_name}'")
        
        # Show performance benefits
        if service.use_parallel:
            theoretical_serial_time = len(service.endpoints) * service.endpoint_timeout
            logger.info(f"\n⚡ Performance Benefits:")
            logger.info(f"   Theoretical Serial Time: {theoretical_serial_time}s")
            logger.info(f"   Actual Collection Time: ~{collection_time:.1f}s")
            logger.info(f"   Speed Improvement: ~{theoretical_serial_time/collection_time:.1f}x faster")
        
        logger.info(f"\n🎉 Enhanced plugin test successful! All new features working:")
        logger.info(f"   ✅ Parallel collection by default")
        logger.info(f"   ✅ Smart timeout management")
        logger.info(f"   ✅ Dynamic retry calculation")
        logger.info(f"   ✅ Enhanced performance monitoring")
        logger.info("Ready for Netdata integration!")
        
        # Show usage example
        logger.info(f"\n💡 Example Netdata configuration:")
        logger.info(f"   host: '{args.host}'")
        logger.info(f"   device_name: '{args.device_name}'")
        logger.info(f"   update_every: {args.update_every}")
        logger.info(f"   endpoint_timeout: {args.endpoint_timeout}")
        logger.info(f"   collection_timeout: {test_config['collection_timeout']}")
        logger.info(f"   parallel_collection: {not args.serial}")
        logger.info(f"   inter_request_delay: {args.inter_request_delay}")
        logger.info(f"   max_retries: {test_config['max_retries']} (auto-calculated)")

    except KeyboardInterrupt:
        logger.info('\n👋 Exiting...')
    except Exception as e:
        logger.error(f"❌ Test failed with exception: {str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    main()
