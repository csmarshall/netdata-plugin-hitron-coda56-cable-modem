#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Enhanced Netdata plugin for monitoring Hitron CODA cable modems.

Key Features:
- Tiered Polling: Fast endpoints polled frequently, OFDM endpoints less often for stability
- Millisecond Timeout Precision: Sub-second timeout control (0.25 = 250ms)
- Serial collection by default for maximum modem compatibility
- Smart timeout management with dynamic retry calculation
- Comprehensive monitoring of 50+ metrics across 15 charts
- Built-in health monitoring and performance tracking

Version: 2.1.0 - Added millisecond timeout precision support
Author: Enhanced for production stability and sub-second timeout control
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
import re
from datetime import datetime
from bases.FrameworkServices.SimpleService import SimpleService
from typing import Union

# --- Configuration Constants ---
NAME = 'hitron_coda'
UPDATE_EVERY = 60
DEFAULT_OFDM_POLL_MULTIPLE = 5

# --- Disable SSL Warnings ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


def parse_timeout_value(value: Union[str, int, float], param_name: str = 'timeout') -> float:
    """
    Parse timeout value with support for millisecond precision.
    
    Supports formats:
    - Integer seconds: 3 -> 3.0s
    - Float seconds: 0.25 -> 0.25s (250ms), 2.5 -> 2.5s (2500ms)  
    - Millisecond strings: "1500ms" -> 1.5s
    - Second strings: "3s", "2.5s", "0.25s" -> respective seconds
    
    Args:
        value: Timeout value in various formats
        param_name: Parameter name for logging
        
    Returns:
        Timeout in seconds (float for sub-second precision)
    """
    if isinstance(value, str):
        value = value.strip().lower()
        
        if value.endswith('ms'):
            # Millisecond format: "1500ms" or "250ms"
            try:
                ms_value = float(value[:-2])
                if ms_value < 50:
                    logger.warning(f"[{param_name}] Very low timeout {ms_value}ms may cause failures")
                elif ms_value > 60000:
                    logger.warning(f"[{param_name}] Very high timeout {ms_value}ms may block collection")
                return ms_value / 1000.0
            except ValueError:
                logger.error(f"[{param_name}] Invalid millisecond format: {value}")
                raise ValueError(f"Invalid millisecond timeout format: {value}")
                
        elif value.endswith('s'):
            # Second format: "3s" or "2.5s" or "0.25s"
            try:
                sec_value = float(value[:-1])
                return sec_value
            except ValueError:
                logger.error(f"[{param_name}] Invalid second format: {value}")
                raise ValueError(f"Invalid second timeout format: {value}")
                
        else:
            # Try to parse as numeric string - always treat as seconds
            try:
                num_value = float(value)
                logger.debug(f"[{param_name}] Interpreting '{value}' as {num_value} seconds")
                return num_value
            except ValueError:
                logger.error(f"[{param_name}] Could not parse timeout value: {value}")
                raise ValueError(f"Invalid timeout format: {value}")
                
    elif isinstance(value, (int, float)):
        # Numeric value - always treat as seconds for consistency
        logger.debug(f"[{param_name}] Treating {value} as seconds")
        return float(value)
    else:
        raise ValueError(f"Unsupported timeout type: {type(value)}")


class Service(SimpleService):
    """
    Enhanced Netdata service for Hitron CODA modems with intelligent tiered polling
    and millisecond timeout precision.
    
    Implements a two-tier polling strategy:
    - Fast endpoints: Critical data polled every cycle
    - Slow endpoints: OFDM data polled less frequently for stability
    """

    # --- Endpoint Categories for Tiered Polling ---
    FAST_ENDPOINTS = [
        'dsinfo.asp',         # Downstream QAM channels (31 channels)
        'usinfo.asp',         # Upstream QAM channels (5 channels)
        'getCmDocsisWan.asp', # WAN status information
        'getSysInfo.asp'      # System uptime and status
    ]
    
    SLOW_ENDPOINTS = [
        'dsofdminfo.asp',     # Downstream OFDM (DOCSIS 3.1) - Can cause instability
        'usofdminfo.asp'      # Upstream OFDM (DOCSIS 3.1) - Less critical, slower
    ]
    
    ALL_ENDPOINTS = FAST_ENDPOINTS + SLOW_ENDPOINTS

    def __init__(self, configuration=None, name=None):
        """Initialize the service with enhanced configuration and tiered polling logic."""
        SimpleService.__init__(self, configuration=configuration, name=name)
        
        # --- Load and Validate Configuration ---
        self._load_configuration()
        self._validate_configuration()
        
        # --- Initialize Chart Definitions ---
        self.order = []
        self.definitions = {}
        
        # --- Tiered Polling State ---
        self.run_counter = 0
        self._setup_tiered_polling()
        
        # --- Performance Tracking ---
        self.performance_stats = {
            'total_cycles': 0,
            'successful_cycles': 0,
            'failed_cycles': 0,
            'consecutive_failures': 0,
            'response_times': [],
            'collection_times': [],
            'active_endpoints': 0
        }
        
        # --- SSL Context for Async Operations ---
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        
        logger.info(f"[{self.name}] Hitron CODA plugin initialized")
        logger.info(f"[{self.name}] Collection mode: {'Parallel' if self.parallel_collection else 'Serial'}")
        logger.info(f"[{self.name}] Tiered polling: Fast every {self.update_every}s, OFDM every {self.ofdm_poll_multiple * self.update_every}s")

    def _load_configuration(self):
        """Load and process configuration with enhanced millisecond timeout support."""
        if self.configuration is None:
            self.configuration = {}
        if NAME in self.configuration:
            self.configuration = self.configuration[NAME]
            
        # --- Required Configuration ---
        self.host = self.configuration.get('host')
        if not self.host:
            logger.critical(f"[{self.name}] 'host' parameter is required but not defined")
            raise ValueError("'host' must be specified in hitron_coda.conf")

        # --- Basic Settings ---
        self.update_every = int(self.configuration.get('update_every', UPDATE_EVERY))
        self.device_name = self.configuration.get('device_name', 'Hitron CODA Cable Modem')
        
        # --- Collection Strategy ---
        self.parallel_collection = self.configuration.get('parallel_collection', False)
        self.inter_request_delay = float(self.configuration.get('inter_request_delay', 0.2))
        
        # --- Enhanced Timeout Configuration with Millisecond Support ---
        
        # Fast endpoint timeout (critical endpoints like QAM channels, WAN status)
        fast_timeout_config = self.configuration.get('fast_endpoint_timeout', 3)
        self.fast_endpoint_timeout = parse_timeout_value(fast_timeout_config, 'fast_endpoint_timeout')
        
        # OFDM endpoint timeout (DOCSIS 3.1 endpoints that can cause instability)
        ofdm_timeout_config = self.configuration.get('ofdm_endpoint_timeout', 8)
        self.ofdm_endpoint_timeout = parse_timeout_value(ofdm_timeout_config, 'ofdm_endpoint_timeout')
        
        # Legacy timeout support (sets both fast and OFDM to same value)
        if 'endpoint_timeout' in self.configuration:
            legacy_timeout = self.configuration.get('endpoint_timeout')
            legacy_timeout_seconds = parse_timeout_value(legacy_timeout, 'endpoint_timeout (legacy)')
            logger.info(f"[{self.name}] Using legacy endpoint_timeout for both fast and OFDM endpoints")
            self.fast_endpoint_timeout = legacy_timeout_seconds
            self.ofdm_endpoint_timeout = legacy_timeout_seconds
        
        # --- Create Endpoint Timeout Mapping ---
        self.endpoint_timeouts = {}
        for endpoint in self.FAST_ENDPOINTS:
            self.endpoint_timeouts[endpoint] = self.fast_endpoint_timeout
        for endpoint in self.SLOW_ENDPOINTS:
            self.endpoint_timeouts[endpoint] = self.ofdm_endpoint_timeout
        
        # --- Collection Timeout (Enhanced) ---
        collection_timeout_config = self.configuration.get('collection_timeout')
        if collection_timeout_config is not None:
            self.collection_timeout = parse_timeout_value(collection_timeout_config, 'collection_timeout')
        else:
            # Auto-calculate as 90% of update_every (PLUGIN DEFAULT)
            self.collection_timeout = int(self.update_every * 0.9)
        
        # --- Enhanced Auto-calculated Max Retries ---
        self.max_retries = self.configuration.get('max_retries')
        if self.max_retries is None:
            # Use the longer of the two timeouts for retry calculation (PLUGIN LOGIC)
            longer_timeout = max(self.fast_endpoint_timeout, self.ofdm_endpoint_timeout)
            self.max_retries = max(1, int(self.collection_timeout / longer_timeout))
        else:
            self.max_retries = int(self.max_retries)
        
        # --- OFDM Caching Configuration ---
        self.ofdm_cache = {}
        self.ofdm_cache_timestamp = 0
        
        # --- Enhanced Logging with Millisecond Precision ---
        logger.info(f"[{self.name}] Enhanced timeout configuration loaded:")
        logger.info(f"[{self.name}]   Fast endpoints: {self.fast_endpoint_timeout:.3f}s ({self.fast_endpoint_timeout * 1000:.0f}ms)")
        logger.info(f"[{self.name}]   OFDM endpoints: {self.ofdm_endpoint_timeout:.3f}s ({self.ofdm_endpoint_timeout * 1000:.0f}ms)")
        logger.info(f"[{self.name}]   Collection timeout: {self.collection_timeout:.3f}s ({self.collection_timeout * 1000:.0f}ms)")
        longer_timeout = max(self.fast_endpoint_timeout, self.ofdm_endpoint_timeout)
        logger.info(f"[{self.name}]   Max retries: {self.max_retries} (auto-calc: {self.collection_timeout:.3f} ÷ {longer_timeout:.3f})")

    def _setup_tiered_polling(self):
        """Configure the tiered polling system."""
        # --- OFDM Polling Configuration ---
        ofdm_update_every = int(self.configuration.get('ofdm_update_every', 0))
        self.ofdm_poll_multiple = int(self.configuration.get('ofdm_poll_multiple', DEFAULT_OFDM_POLL_MULTIPLE))
        
        # If specific OFDM interval is set, calculate the multiple
        if ofdm_update_every > 0:
            if ofdm_update_every < self.update_every:
                logger.warning(f"[{self.name}] ofdm_update_every ({ofdm_update_every}s) is less than update_every ({self.update_every}s), setting to 1")
                self.ofdm_poll_multiple = 1
            else:
                self.ofdm_poll_multiple = max(1, round(ofdm_update_every / self.update_every))
                logger.info(f"[{self.name}] Calculated OFDM poll multiple: {self.ofdm_poll_multiple} (every {ofdm_update_every}s)")
        
        # OFDM cache TTL
        self.ofdm_cache_ttl = self.ofdm_poll_multiple * self.update_every
        
        logger.info(f"[{self.name}] Tiered polling enabled:")
        logger.info(f"[{self.name}]   Fast endpoints ({len(self.FAST_ENDPOINTS)}): Every {self.update_every}s")
        logger.info(f"[{self.name}]   Slow endpoints ({len(self.SLOW_ENDPOINTS)}): Every {self.ofdm_poll_multiple * self.update_every}s")

    def _validate_configuration(self):
        """Validate configuration parameters with millisecond timeout awareness."""
        # --- Timeout Validation ---
        if self.collection_timeout >= self.update_every:
            logger.warning(f"[{self.name}] collection_timeout ({self.collection_timeout:.3f}s) should be less than update_every ({self.update_every}s)")
        
        # --- Serial Mode Timing Check with Millisecond Precision ---
        if not self.parallel_collection:
            # Calculate worst-case timing for fast endpoints (always polled)
            fast_time = len(self.FAST_ENDPOINTS) * (self.fast_endpoint_timeout + self.inter_request_delay)
            
            # Calculate worst-case timing for full cycles (includes OFDM)
            full_time = (len(self.FAST_ENDPOINTS) * (self.fast_endpoint_timeout + self.inter_request_delay) + 
                        len(self.SLOW_ENDPOINTS) * (self.ofdm_endpoint_timeout + self.inter_request_delay))
            
            if fast_time > self.collection_timeout:
                logger.warning(f"[{self.name}] Fast cycle timing ({fast_time:.3f}s) exceeds collection_timeout ({self.collection_timeout:.3f}s)")
            
            if full_time > self.collection_timeout:
                logger.warning(f"[{self.name}] Full cycle timing ({full_time:.3f}s) exceeds collection_timeout ({self.collection_timeout:.3f}s)")
                logger.warning(f"[{self.name}] Consider increasing collection_timeout or enabling parallel_collection")
        
        # --- Performance Estimates with Millisecond Precision ---
        if self.parallel_collection:
            logger.info(f"[{self.name}] Estimated fast cycle time: {self.fast_endpoint_timeout * 1000:.0f}ms (parallel)")
            logger.info(f"[{self.name}] Estimated full cycle time: {max(self.fast_endpoint_timeout, self.ofdm_endpoint_timeout) * 1000:.0f}ms (parallel)")
        else:
            fast_time = len(self.FAST_ENDPOINTS) * (self.fast_endpoint_timeout + self.inter_request_delay)
            full_time = fast_time + len(self.SLOW_ENDPOINTS) * (self.ofdm_endpoint_timeout + self.inter_request_delay)
            logger.info(f"[{self.name}] Estimated fast cycle time: {fast_time * 1000:.0f}ms (serial)")
            logger.info(f"[{self.name}] Estimated full cycle time: {full_time * 1000:.0f}ms (serial)")
        
        # --- Timeout Optimization Suggestions ---
        if self.fast_endpoint_timeout < 0.5:
            logger.info(f"[{self.name}] 🚀 Aggressive fast timeout ({self.fast_endpoint_timeout * 1000:.0f}ms) - excellent for low-latency endpoints")
        elif self.fast_endpoint_timeout > 5.0:
            logger.warning(f"[{self.name}] ⚠️ Conservative fast timeout ({self.fast_endpoint_timeout * 1000:.0f}ms) - may impact responsiveness")
        
        if self.ofdm_endpoint_timeout < 2.0:
            logger.warning(f"[{self.name}] ⚠️ Aggressive OFDM timeout ({self.ofdm_endpoint_timeout * 1000:.0f}ms) - may cause failures on unstable endpoints")
        elif self.ofdm_endpoint_timeout > 15.0:
            logger.warning(f"[{self.name}] ⚠️ Very conservative OFDM timeout ({self.ofdm_endpoint_timeout * 1000:.0f}ms) - may block collection cycles")

    def check(self):
        """Verify connectivity to the modem before starting."""
        logger.info(f"[{self.name}] Testing connectivity to {self.host}")
        
        try:
            # Test with a fast, lightweight endpoint
            url = f"{self.host}/data/getSysInfo.asp"
            response = requests.get(url, verify=False, timeout=10)
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    if isinstance(data, list) and data:
                        sw_version = data[0].get('swVersion', 'Unknown')
                        logger.info(f"[{self.name}] Successfully connected to modem, SW Version: {sw_version}")
                        return True
                except (json.JSONDecodeError, KeyError, IndexError):
                    pass
                    
            logger.error(f"[{self.name}] Modem connectivity test failed. Status: {response.status_code}")
            logger.error(f"[{self.name}] Response preview: {response.text[:200]}")
            return False
            
        except requests.RequestException as e:
            logger.error(f"[{self.name}] Connection test failed: {e}")
            return False

    def _get_data(self):
        """Main data collection method with intelligent OFDM caching."""
        start_time = time.time()
        self.run_counter += 1
        
        # --- Determine Collection Strategy ---
        current_time = time.time()
        is_ofdm_cycle = self._should_poll_ofdm()
        endpoints_to_poll = self._get_endpoints_for_cycle()
        
        # Always fetch fresh fast endpoint data
        fast_endpoints = [ep for ep in endpoints_to_poll if ep in self.FAST_ENDPOINTS]
        ofdm_endpoints = [ep for ep in endpoints_to_poll if ep in self.SLOW_ENDPOINTS]
        
        cycle_type = "FULL" if ofdm_endpoints else "FAST"
        logger.debug(f"[{self.name}] Cycle {self.run_counter} ({cycle_type}): {len(fast_endpoints)} fast + {len(ofdm_endpoints)} OFDM endpoints")
        
        # --- Fetch Data ---
        try:
            # Always fetch fast endpoints
            if self.parallel_collection:
                fast_data = self._run_async_collection(fast_endpoints) if fast_endpoints else {}
            else:
                fast_data = self._fetch_serial(fast_endpoints) if fast_endpoints else {}
            
            # Handle OFDM endpoints with caching
            if is_ofdm_cycle and ofdm_endpoints:
                # Fetch fresh OFDM data
                if self.parallel_collection:
                    ofdm_data = self._run_async_collection(ofdm_endpoints)
                else:
                    ofdm_data = self._fetch_serial(ofdm_endpoints)
                
                # Update cache with fresh data
                self.ofdm_cache.update(ofdm_data)
                self.ofdm_cache_timestamp = current_time
                logger.debug(f"[{self.name}] Updated OFDM cache with {len(ofdm_data)} endpoints")
                
            elif not is_ofdm_cycle and self._is_ofdm_cache_valid():
                # Use cached OFDM data
                ofdm_data = self.ofdm_cache.copy()
                logger.debug(f"[{self.name}] Using cached OFDM data ({len(ofdm_data)} endpoints)")
                
            else:
                # No OFDM data (either not needed or cache invalid)
                ofdm_data = {}
            
            # Combine all data
            data = {**fast_data, **ofdm_data}
            
        except Exception as e:
            logger.error(f"[{self.name}] Collection failed with exception: {e}")
            self._update_performance_stats(False, time.time() - start_time, 0)
            return None

        # --- Process Results ---
        if not data:
            logger.warning(f"[{self.name}] No data collected in cycle {self.run_counter}")
            self._update_performance_stats(False, time.time() - start_time, 0)
            return None

        # --- Parse Data and Create Charts ---
        self._process_and_define_charts(data)
        
        # --- Add Performance Metrics ---
        collection_time = time.time() - start_time
        self._update_performance_stats(True, collection_time, len(data))
        self._add_plugin_health_metrics(collection_time)
        
        logger.debug(f"[{self.name}] Cycle {self.run_counter} completed in {collection_time:.3f}s (fast: {len(fast_endpoints)}, OFDM: {len(ofdm_endpoints)}, cached: {not is_ofdm_cycle and bool(ofdm_data)})")
        
        return self.data

    def _should_poll_ofdm(self):
        """Determine if this cycle should poll OFDM endpoints."""
        # First run always polls everything
        if self.run_counter == 1:
            return True
        
        # Check if this is an OFDM polling cycle
        if self.ofdm_poll_multiple > 0 and self.run_counter % self.ofdm_poll_multiple == 0:
            return True
            
        return False

    def _is_ofdm_cache_valid(self):
        """Check if OFDM cache is still valid."""
        if not self.ofdm_cache:
            return False
        
        current_time = time.time()
        cache_age = current_time - self.ofdm_cache_timestamp
        
        return cache_age < self.ofdm_cache_ttl

    def _get_endpoints_for_cycle(self):
        """Determine which endpoints to poll based on tiered polling logic."""
        # First run always polls everything to initialize all charts
        if self.run_counter == 1:
            logger.debug(f"[{self.name}] Initial run: polling ALL endpoints")
            return self.ALL_ENDPOINTS
        
        # Check if this is an OFDM polling cycle
        if self.ofdm_poll_multiple > 0 and self.run_counter % self.ofdm_poll_multiple == 0:
            logger.debug(f"[{self.name}] OFDM cycle {self.run_counter}: polling ALL endpoints")
            return self.ALL_ENDPOINTS
        else:
            logger.debug(f"[{self.name}] Fast cycle {self.run_counter}: polling FAST endpoints only")
            return self.FAST_ENDPOINTS

    def _run_async_collection(self, endpoints):
        """Run parallel collection using asyncio."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self._fetch_parallel(endpoints))
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"[{self.name}] Async collection failed: {e}")
            return {}

    def _fetch_serial(self, endpoints):
        """Fetch data from endpoints sequentially for maximum compatibility."""
        data = {}
        
        with requests.Session() as session:
            session.verify = False
            session.headers.update({
                'User-Agent': 'Netdata-Hitron-Plugin/2.1.0',
                'Accept': 'application/json, */*',
                'Connection': 'close'
            })
            
            for endpoint in endpoints:
                success = False
                for attempt in range(self.max_retries):
                    try:
                        url = f"{self.host}/data/{endpoint}"
                        response = session.get(url, timeout=self.endpoint_timeouts.get(endpoint))
                        response.raise_for_status()
                        
                        data[endpoint] = response.json()
                        success = True
                        logger.debug(f"[{self.name}] {endpoint}: Success on attempt {attempt + 1}")
                        break
                        
                    except requests.RequestException as e:
                        logger.debug(f"[{self.name}] {endpoint}: Attempt {attempt + 1} failed: {e}")
                        if attempt < self.max_retries - 1:
                            time.sleep(1)  # Brief pause between retries
                
                if not success:
                    logger.warning(f"[{self.name}] {endpoint}: All {self.max_retries} attempts failed")
                
                # Delay between endpoints to be gentle on the modem
                if len(endpoints) > 1 and endpoint != endpoints[-1]:
                    time.sleep(self.inter_request_delay)
        
        return data

    async def _fetch_parallel(self, endpoints):
        """Fetch data from endpoints concurrently."""
        connector = aiohttp.TCPConnector(
            ssl=self.ssl_context,
            limit=10,
            keepalive_timeout=30,
            enable_cleanup_closed=True
        )
        
        # Overall collection timeout is handled by asyncio.gather, individual request timeouts are below
        async with aiohttp.ClientSession(
            connector=connector, 
            headers={
                'User-Agent': 'Netdata-Hitron-Plugin/2.1.0',
                'Accept': 'application/json, */*'
            }
        ) as session:
            tasks = [self._fetch_endpoint_async(session, endpoint) for endpoint in endpoints]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results and filter out exceptions
        data = {}
        for endpoint, result in zip(endpoints, results):
            if not isinstance(result, Exception) and result is not None:
                data[endpoint] = result
            elif isinstance(result, Exception):
                logger.warning(f"[{self.name}] {endpoint}: Parallel fetch failed: {result}")
        
        return data

    async def _fetch_endpoint_async(self, session, endpoint):
        """Async helper to fetch a single endpoint with retries and per-endpoint timeouts."""
        url = f"{self.host}/data/{endpoint}"
        endpoint_timeout = self.endpoint_timeouts.get(endpoint, self.fast_endpoint_timeout)
        
        for attempt in range(self.max_retries):
            try:
                # Use precise timeout (aiohttp supports float precision)
                timeout = aiohttp.ClientTimeout(total=endpoint_timeout)
                async with session.get(url, timeout=timeout) as response:
                    response.raise_for_status()
                    logger.debug(f"[{self.name}] {endpoint}: Success on attempt {attempt + 1} (timeout: {endpoint_timeout:.3f}s)")
                    return await response.json()
                    
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.debug(f"[{self.name}] {endpoint}: Async attempt {attempt + 1} failed: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(1)
        
        logger.warning(f"[{self.name}] {endpoint}: All {self.max_retries} async attempts failed")
        return None

    def _parse_uptime_string(self, uptime_str: str) -> int:
        """Parse uptime string like '24h:30m:59s' into total seconds."""
        if not isinstance(uptime_str, str):
            return 0
            
        total_seconds = 0
        try:
            # Using regex to find days, hours, minutes, and seconds
            parts = {
                'd': r'(\d+)\s*d',
                'h': r'(\d+)\s*h',
                'm': r'(\d+)\s*m',
                's': r'(\d+)\s*s'
            }
            
            # Extract values and calculate total seconds
            days = re.search(parts['d'], uptime_str)
            if days:
                total_seconds += int(days.group(1)) * 86400
                
            hours = re.search(parts['h'], uptime_str)
            if hours:
                total_seconds += int(hours.group(1)) * 3600
                
            minutes = re.search(parts['m'], uptime_str)
            if minutes:
                total_seconds += int(minutes.group(1)) * 60
                
            seconds = re.search(parts['s'], uptime_str)
            if seconds:
                total_seconds += int(seconds.group(1))
                
        except (ValueError, IndexError) as e:
            logger.debug(f"[{self.name}] Could not parse uptime string '{uptime_str}': {e}")
            return 0
            
        return total_seconds

    def _process_and_define_charts(self, api_data):
        """Parse API data and populate self.data for Netdata."""
        # Reset data for this cycle
        self.data = {}
        
        # --- Process Downstream QAM Channels (DOCSIS 3.0) ---
        if 'dsinfo.asp' in api_data and 'dsinfo' in api_data['dsinfo.asp']:
            for channel in api_data['dsinfo.asp']['dsinfo']:
                try:
                    ch_id = f"ds_{channel['channelid']}"
                    
                    # Scale and store power (multiply by 10 for precision)
                    self.data[f'{ch_id}_power'] = int(float(channel['power']) * 10)
                    
                    # Scale and store SNR (multiply by 100 for precision)
                    self.data[f'{ch_id}_snr'] = int(float(channel['snr']) * 100)
                    
                    # Store error counters (use incremental algorithm)
                    self.data[f'{ch_id}_corrected'] = int(channel['correcteds'])
                    self.data[f'{ch_id}_uncorrected'] = int(channel['uncorrectables'])
                    
                    # Store frequency in Hz for analysis
                    self.data[f'{ch_id}_frequency'] = int(channel.get('frequency', 0))
                    
                except (ValueError, KeyError) as e:
                    logger.debug(f"[{self.name}] Error processing downstream channel {channel.get('channelid', 'unknown')}: {e}")

        # --- Process Upstream QAM Channels (DOCSIS 3.0) ---
        if 'usinfo.asp' in api_data and 'usinfo' in api_data['usinfo.asp']:
            for channel in api_data['usinfo.asp']['usinfo']:
                try:
                    ch_id = f"us_{channel['channelid']}"
                    
                    # Scale and store power (multiply by 100 for precision)
                    self.data[f'{ch_id}_power'] = int(float(channel['power']) * 100)
                    
                    # Store frequency and bandwidth
                    self.data[f'{ch_id}_frequency'] = int(channel.get('frequency', 0))
                    self.data[f'{ch_id}_bandwidth'] = int(float(channel.get('bandwidth', 0)) * 10)
                    
                except (ValueError, KeyError) as e:
                    logger.debug(f"[{self.name}] Error processing upstream channel {channel.get('channelid', 'unknown')}: {e}")

        # --- Process Downstream OFDM (DOCSIS 3.1) ---
        if 'dsofdminfo.asp' in api_data and 'dsofdminfo' in api_data['dsofdminfo.asp']:
            if api_data['dsofdminfo.asp']['dsofdminfo']:
                try:
                    ofdm = api_data['dsofdminfo.asp']['dsofdminfo'][0]
                    
                    self.data['dsofdm_power'] = int(float(ofdm['power']) * 100)
                    self.data['dsofdm_snr'] = int(float(ofdm['snr']) * 100)
                    self.data['dsofdm_corrected'] = int(ofdm['correcteds'])
                    self.data['dsofdm_uncorrected'] = int(ofdm['uncorrectables'])
                    
                except (ValueError, KeyError) as e:
                    logger.debug(f"[{self.name}] Error processing downstream OFDM: {e}")

        # --- Process Upstream OFDM (DOCSIS 3.1) ---
        if 'usofdminfo.asp' in api_data and 'usofdminfo' in api_data['usofdminfo.asp']:
            if api_data['usofdminfo.asp']['usofdminfo']:
                try:
                    ofdm = api_data['usofdminfo.asp']['usofdminfo'][0]
                    self.data['usofdm_power'] = int(float(ofdm['power']) * 100)
                    
                except (ValueError, KeyError) as e:
                    logger.debug(f"[{self.name}] Error processing upstream OFDM: {e}")

        # --- Process System Information ---
        if 'getSysInfo.asp' in api_data and isinstance(api_data['getSysInfo.asp'], list) and api_data['getSysInfo.asp']:
            try:
                sys_info = api_data['getSysInfo.asp'][0]
                uptime_str = sys_info.get('systemUptime', '')
                self.data['system_uptime'] = self._parse_uptime_string(uptime_str)
                
            except (ValueError, KeyError, IndexError) as e:
                logger.debug(f"[{self.name}] Error processing system info: {e}")

        # --- Process WAN Information ---
        if 'getCmDocsisWan.asp' in api_data and 'getCmDocsisWan' in api_data['getCmDocsisWan.asp']:
            if api_data['getCmDocsisWan.asp']['getCmDocsisWan']:
                try:
                    wan = api_data['getCmDocsisWan.asp']['getCmDocsisWan'][0]
                    self.data['wan_ipv4_online'] = 1 if wan.get('IPv4Status') == 'Online' else 0
                    self.data['wan_ipv6_online'] = 1 if wan.get('IPv6Status') == 'Online' else 0
                    
                except (ValueError, KeyError) as e:
                    logger.debug(f"[{self.name}] Error processing WAN info: {e}")

        # --- Create Chart Definitions on First Successful Run ---
        if not self.order and self.data:
            self._create_chart_definitions()

    def _create_chart_definitions(self):
        """Dynamically create all chart definitions for Netdata."""
        logger.info(f"[{self.name}] Creating chart definitions with {len(self.data)} metrics")
        
        self.order = []
        self.definitions = {}

        def add_chart(chart_id, title, units, family, context, chart_type='line'):
            """Helper to add a chart definition."""
            full_chart_id = f"{self.name}.{chart_id}"
            self.order.append(chart_id)
            self.definitions[chart_id] = {
                'options': [None, title, units, family, context, chart_type],
                'lines': []
            }
        
        def add_line(chart_id, dim_id, name, algorithm='absolute', multiplier=1, divisor=1):
            """Helper to add a line to a chart."""
            self.definitions[chart_id]['lines'].append([dim_id, name, algorithm, multiplier, divisor])

        # --- Plugin Health and Performance ---
        add_chart('plugin_health', 'Plugin Health Metrics', 'status', 'plugin', f'{self.name}.plugin_health')
        add_line('plugin_health', 'success_rate', 'Success Rate (%)', 'absolute')
        add_line('plugin_health', 'response_time', 'Avg Response Time (ms)', 'absolute')
        add_line('plugin_health', 'collection_time', 'Collection Time (ms)', 'absolute')
        add_line('plugin_health', 'consecutive_failures', 'Consecutive Failures', 'absolute')
        add_line('plugin_health', 'active_endpoints', 'Active Endpoints', 'absolute')

        # --- System Status ---
        add_chart('system_status', 'WAN Status', 'status', 'system', f'{self.name}.system_status')
        if 'wan_ipv4_online' in self.data:
            add_line('system_status', 'wan_ipv4_online', 'IPv4 Online', 'absolute')
        if 'wan_ipv6_online' in self.data:
            add_line('system_status', 'wan_ipv6_online', 'IPv6 Online', 'absolute')
        
        # --- Uptime Chart ---
        add_chart('system_uptime', 'System Uptime', 'seconds', 'system', f'{self.name}.system_uptime')
        if 'system_uptime' in self.data:
            add_line('system_uptime', 'system_uptime', 'Uptime', 'absolute')
            
        # --- Signal Quality Charts ---
        add_chart('downstream_power', 'Downstream Power Levels', 'dBmV', 'signal', f'{self.name}.downstream_power')
        add_chart('downstream_snr', 'Downstream SNR', 'dB', 'signal', f'{self.name}.downstream_snr')
        add_chart('upstream_power', 'Upstream Power Levels', 'dBmV', 'signal', f'{self.name}.upstream_power')

        # --- Error Rate Charts ---
        add_chart('downstream_corrected', 'Downstream Corrected Errors', 'errors/s', 'errors', f'{self.name}.downstream_corrected', 'stacked')
        add_chart('downstream_uncorrected', 'Downstream Uncorrected Errors', 'errors/s', 'errors', f'{self.name}.downstream_uncorrected', 'stacked')

        # --- Frequency Charts ---
        add_chart('downstream_frequency', 'Downstream Frequencies', 'MHz', 'frequency', f'{self.name}.downstream_frequency')
        add_chart('upstream_frequency', 'Upstream Frequencies', 'MHz', 'frequency', f'{self.name}.upstream_frequency')
        add_chart('upstream_bandwidth', 'Upstream Bandwidth', 'MHz', 'frequency', f'{self.name}.upstream_bandwidth')

        # --- OFDM Charts (DOCSIS 3.1) ---
        if any(key.startswith('dsofdm_') or key.startswith('usofdm_') for key in self.data.keys()):
            add_chart('ofdm_power', 'OFDM Power Levels', 'dBmV', 'signal', f'{self.name}.ofdm_power')
            add_chart('ofdm_snr', 'OFDM SNR', 'dB', 'signal', f'{self.name}.ofdm_snr')
            add_chart('ofdm_errors', 'OFDM Error Rates', 'errors/s', 'errors', f'{self.name}.ofdm_errors', 'stacked')
            
            if 'dsofdm_power' in self.data:
                add_line('ofdm_power', 'dsofdm_power', 'Downstream', 'absolute', 1, 100)
            if 'usofdm_power' in self.data:
                add_line('ofdm_power', 'usofdm_power', 'Upstream', 'absolute', 1, 100)
            if 'dsofdm_snr' in self.data:
                add_line('ofdm_snr', 'dsofdm_snr', 'Downstream', 'absolute', 1, 100)
            if 'dsofdm_corrected' in self.data:
                add_line('ofdm_errors', 'dsofdm_corrected', 'Corrected', 'incremental')
            if 'dsofdm_uncorrected' in self.data:
                add_line('ofdm_errors', 'dsofdm_uncorrected', 'Uncorrected', 'incremental')

        # --- Add Individual Channel Lines Dynamically ---
        for i in range(1, 32):  # Downstream channels 1-31
            if f'ds_{i}_power' in self.data:
                add_line('downstream_power', f'ds_{i}_power', f'Ch {i}', 'absolute', 1, 10)
            if f'ds_{i}_snr' in self.data:
                add_line('downstream_snr', f'ds_{i}_snr', f'Ch {i}', 'absolute', 1, 100)
            if f'ds_{i}_corrected' in self.data:
                add_line('downstream_corrected', f'ds_{i}_corrected', f'Ch {i}', 'incremental')
            if f'ds_{i}_uncorrected' in self.data:
                add_line('downstream_uncorrected', f'ds_{i}_uncorrected', f'Ch {i}', 'incremental')
            if f'ds_{i}_frequency' in self.data:
                add_line('downstream_frequency', f'ds_{i}_frequency', f'Ch {i}', 'absolute', 1, 1000000)

        for i in range(1, 9):  # Upstream channels 1-8
            if f'us_{i}_power' in self.data:
                add_line('upstream_power', f'us_{i}_power', f'Ch {i}', 'absolute', 1, 100)
            if f'us_{i}_frequency' in self.data:
                add_line('upstream_frequency', f'us_{i}_frequency', f'Ch {i}', 'absolute', 1, 1000000)
            if f'us_{i}_bandwidth' in self.data:
                add_line('upstream_bandwidth', f'us_{i}_bandwidth', f'Ch {i}', 'absolute', 1, 10)

        logger.info(f"[{self.name}] Created {len(self.order)} charts with dynamic channel detection")

    def _update_performance_stats(self, success, collection_time, active_endpoints):
        """Update internal performance statistics."""
        self.performance_stats['total_cycles'] += 1
        self.performance_stats['active_endpoints'] = active_endpoints
        self.performance_stats['collection_times'].append(collection_time * 1000)  # Convert to ms
        
        if success:
            self.performance_stats['successful_cycles'] += 1
            self.performance_stats['consecutive_failures'] = 0
        else:
            self.performance_stats['failed_cycles'] += 1
            self.performance_stats['consecutive_failures'] += 1
        
        # Keep only last 100 measurements for rolling averages
        if len(self.performance_stats['collection_times']) > 100:
            self.performance_stats['collection_times'] = self.performance_stats['collection_times'][-100:]

    def _add_plugin_health_metrics(self, collection_time):
        """Add plugin health and performance metrics to the data."""
        stats = self.performance_stats
        
        if stats['total_cycles'] > 0:
            success_rate = (stats['successful_cycles'] / stats['total_cycles']) * 100
            self.data['success_rate'] = int(success_rate)
        else:
            self.data['success_rate'] = 0
        
        # Average response time from recent collections
        if stats['collection_times']:
            avg_response_time = sum(stats['collection_times'][-10:]) / len(stats['collection_times'][-10:])
            self.data['response_time'] = int(avg_response_time)
        else:
            self.data['response_time'] = 0
        
        # Current collection time in milliseconds
        self.data['collection_time'] = int(collection_time * 1000)
        
        # Consecutive failures count
        self.data['consecutive_failures'] = stats['consecutive_failures']
        
        # Active endpoints count
        self.data['active_endpoints'] = stats['active_endpoints']
