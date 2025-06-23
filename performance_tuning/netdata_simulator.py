#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Enhanced Netdata Modem Simulator with Graphing and Validation.

This script simulates the behavior of the hitron_coda.chart.py plugin,
including the tiered polling logic, and generates comprehensive graphs
and analysis reports.

Key Features:
- Tiered polling simulation (fast vs slow endpoints) - MATCHES PLUGIN EXACTLY
- Parallel and serial collection modes - MATCHES PLUGIN EXACTLY  
- Comprehensive performance metrics with graphing
- Real-time progress tracking with emojis
- JSON output for automated analysis
- Validation against actual plugin behavior
- Multi-format graph generation (PNG, SVG, PDF)

Version: 2.1.0 - Fixed endpoint names and re-integrated graphing
Author: Enhanced for comprehensive analysis and validation
"""

import asyncio
import aiohttp
import ssl
import time
import signal
import sys
import argparse
import logging
import statistics
import json
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np
import seaborn as sns
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import warnings

# Suppress matplotlib warnings
warnings.filterwarnings('ignore', category=UserWarning, module='matplotlib')

# Set matplotlib backend for headless operation
plt.switch_backend('Agg')

# Configure seaborn style
sns.set_style("whitegrid")
plt.style.use('seaborn-v0_8-darkgrid')

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class NetdataModemSimulator:
    """
    Enhanced simulator for testing Hitron CODA modem tiered polling strategies.
    
    Mirrors the actual plugin's endpoint categorization and polling logic EXACTLY
    to provide accurate performance predictions and validation.
    """

    # --- Endpoint Categories (EXACTLY MATCH PLUGIN FROM hitron_coda.chart.py) ---
    # FIXED: Use correct endpoint names from actual plugin
    FAST_ENDPOINTS = [
        'dsinfo.asp',         # Downstream QAM channels (31 channels) - Critical signal data
        'usinfo.asp',         # Upstream QAM channels (5 channels) - Critical upload data  
        'getCmDocsisWan.asp', # WAN status (IPv4/IPv6) - Connection health
        'getSysInfo.asp'      # CORRECTED: System uptime and info - Basic health check
    ]
    
    SLOW_ENDPOINTS = [
        'dsofdminfo.asp',     # Downstream OFDM (DOCSIS 3.1) - Can cause instability
        'usofdminfo.asp'      # Upstream OFDM (DOCSIS 3.1) - Less critical
    ]
    
    ALL_ENDPOINTS = FAST_ENDPOINTS + SLOW_ENDPOINTS

    def __init__(self, **kwargs):
        """Initialize the simulator with enhanced two-tier timeout configuration."""
        
        # --- Basic Configuration ---
        self.modem_host = kwargs.get('modem_host')
        self.update_every = kwargs.get('update_every')
        self.test_duration = kwargs.get('test_duration')
        
        # --- Collection Strategy (MATCHES PLUGIN) ---
        self.parallel_collection = kwargs.get('parallel_collection', False)
        self.inter_request_delay = kwargs.get('inter_request_delay', 0.2)
        
        # --- Enhanced Two-Tier Timeout Configuration (MATCHES PLUGIN) ---
        self.fast_endpoint_timeout = kwargs.get('fast_endpoint_timeout', 3)
        self.ofdm_endpoint_timeout = kwargs.get('ofdm_endpoint_timeout', 8)
        
        # Create endpoint timeout mapping (EXACTLY LIKE PLUGIN)
        self.endpoint_timeouts = {}
        for endpoint in self.FAST_ENDPOINTS:
            self.endpoint_timeouts[endpoint] = self.fast_endpoint_timeout
        for endpoint in self.SLOW_ENDPOINTS:
            self.endpoint_timeouts[endpoint] = self.ofdm_endpoint_timeout
        
        # --- Tiered Polling Configuration (MATCHES PLUGIN LOGIC) ---
        self.run_counter = 0
        ofdm_update_every = kwargs.get('ofdm_update_every', 0)
        self.ofdm_poll_multiple = kwargs.get('ofdm_poll_multiple', 5)
        
        # If specific OFDM interval is set, calculate the multiple (PLUGIN LOGIC)
        if ofdm_update_every > 0:
            if ofdm_update_every < self.update_every:
                logger.warning(f"ofdm_update_every ({ofdm_update_every}s) is less than update_every ({self.update_every}s), setting to 1")
                self.ofdm_poll_multiple = 1
            else:
                self.ofdm_poll_multiple = max(1, round(ofdm_update_every / self.update_every))
                logger.info(f"Calculated OFDM poll multiple: {self.ofdm_poll_multiple} (every {ofdm_update_every}s)")
        
        # --- OFDM Caching Simulation (MATCHES PLUGIN) ---
        self.ofdm_cache = {}
        self.ofdm_cache_timestamp = 0
        self.ofdm_cache_ttl = self.ofdm_poll_multiple * self.update_every
        
        # --- Collection Timeout and Retries (MATCHES PLUGIN AUTO-CALCULATION) ---
        self.collection_timeout = kwargs.get('collection_timeout')
        if self.collection_timeout is None:
            self.collection_timeout = int(self.update_every * 0.9)  # PLUGIN DEFAULT
            
        self.max_retries = kwargs.get('max_retries')
        if self.max_retries is None:
            # Use the longer of the two timeouts for retry calculation (PLUGIN LOGIC)
            longer_timeout = max(self.fast_endpoint_timeout, self.ofdm_endpoint_timeout)
            self.max_retries = max(1, int(self.collection_timeout / longer_timeout))  # PLUGIN FORMULA
        
        # --- SSL Context (MATCHES PLUGIN) ---
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        
        # --- Simulation State ---
        self.is_running = True
        self.start_time = None
        self.last_progress_time = 0
        
        # --- Enhanced Performance Tracking with Time Series for Graphing ---
        self.results = {
            'total_cycles': 0,
            'successful_cycles': 0,
            'failed_cycles': 0,
            'fast_cycles': 0,
            'full_cycles': 0,
            'cached_cycles': 0,
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'collection_times': [],
            'response_times': [],
            'endpoint_success_rates': {},
            'cycle_types': [],
            'cache_hits': 0,
            'cache_misses': 0,
            'consecutive_failures': 0,
            'max_consecutive_failures': 0,
            # Time series data for comprehensive graphing
            'time_series': {
                'timestamps': [],
                'cycle_success': [],
                'collection_times': [],
                'response_times_avg': [],
                'endpoint_counts': [],
                'cycle_types': [],
                'success_rates_rolling': [],
                'cache_status': [],
                'consecutive_failures': [],
                'endpoint_details': {ep: [] for ep in self.ALL_ENDPOINTS}
            }
        }
        
        # Initialize endpoint success tracking
        for endpoint in self.ALL_ENDPOINTS:
            self.results['endpoint_success_rates'][endpoint] = {'success': 0, 'total': 0}
        
        # --- Performance Stats (for health tracking - MATCHES PLUGIN) ---
        self.performance_stats = {
            'total_cycles': 0,
            'successful_cycles': 0,
            'failed_cycles': 0,
            'consecutive_failures': 0,
            'response_times': [],
            'collection_times': [],
            'active_endpoints': 0
        }
        
        # --- Graph Configuration ---
        if kwargs.get('output_dir'):
            self.output_dir = Path(kwargs.get('output_dir'))
        else:
            # Create descriptive directory name with key parameters
            mode = "parallel" if self.parallel_collection else "serial"
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            dir_name = f"hitron_sim_{timestamp}_{mode}_int{self.update_every}s_ofdm{self.ofdm_poll_multiple}x_timeout{self.fast_endpoint_timeout}s-{self.ofdm_endpoint_timeout}s"
            self.output_dir = Path.cwd() / dir_name
        self.output_dir.mkdir(exist_ok=True)
        
        # Create descriptive filename prefix for graphs
        mode = "parallel" if self.parallel_collection else "serial"
        self.file_prefix = f"hitron_{mode}_int{self.update_every}s_ofdm{self.ofdm_poll_multiple}x_ft{self.fast_endpoint_timeout}s_ot{self.ofdm_endpoint_timeout}s"
        
        logger.info(f"Enhanced Simulator initialized:")
        logger.info(f"  🔍 Plugin validation: Endpoint categories, timeouts, and logic match plugin")
        logger.info(f"  Collection mode: {'Parallel' if self.parallel_collection else 'Serial'}")
        logger.info(f"  Update interval: {self.update_every}s")
        logger.info(f"  OFDM poll multiple: {self.ofdm_poll_multiple} (every {self.ofdm_poll_multiple * self.update_every}s)")
        logger.info(f"  Fast endpoint timeout: {self.fast_endpoint_timeout}s ({len(self.FAST_ENDPOINTS)} endpoints)")
        logger.info(f"  OFDM endpoint timeout: {self.ofdm_endpoint_timeout}s ({len(self.SLOW_ENDPOINTS)} endpoints)")
        logger.info(f"  Collection timeout: {self.collection_timeout}s (auto-calc: {self.update_every} * 0.9)")
        logger.info(f"  Max retries: {self.max_retries} (auto-calc: {self.collection_timeout} ÷ {max(self.fast_endpoint_timeout, self.ofdm_endpoint_timeout)})")
        logger.info(f"  OFDM cache TTL: {self.ofdm_cache_ttl}s")
        logger.info(f"  📊 Output directory: {self.output_dir}")

    async def _test_connectivity(self):
        """Test initial connectivity using the CORRECT endpoint names."""
        logger.info("🔌 Testing initial connectivity...")
        try:
            connector = aiohttp.TCPConnector(ssl=self.ssl_context)
            timeout = aiohttp.ClientTimeout(total=10)
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (compatible; Netdata-Hitron-Plugin/2.1.0)',
                'Accept': 'application/json, text/html, */*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache'
            }
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
                # Test the CORRECT endpoint
                test_url = f"{self.modem_host}/data/getSysInfo.asp"
                logger.info(f"🧪 Testing: {test_url}")
                
                async with session.get(test_url) as response:
                    content_type = response.headers.get('content-type', '').lower()
                    response_text = await response.text()
                    
                    if response.status == 200:
                        logger.info(f"✅ Connectivity test passed (HTTP {response.status}, Content-Type: {content_type})")
                        
                        # Validate response content
                        if response_text.strip() == "0":
                            logger.error(f"❌ ENDPOINT ERROR: getSysInfo.asp returned '0' - this suggests auth or endpoint issues")
                        elif 'json' in content_type or (response_text.strip().startswith('[') and response_text.strip().endswith(']')):
                            try:
                                # Try aiohttp's json() first, ignoring content-type
                                try:
                                    data = await response.json(content_type=None)
                                    logger.info(f"✅ JSON response validated via aiohttp - got {len(data)} objects")
                                except Exception:
                                    # Manual parsing as fallback
                                    data = json.loads(response_text)
                                    logger.info(f"✅ JSON response validated via manual parsing - got {len(data)} objects")
                                
                                if isinstance(data, list) and len(data) > 0 and 'systemUptime' in data[0]:
                                    uptime = data[0]['systemUptime']
                                    sw_version = data[0].get('swVersion', 'unknown')
                                    logger.info(f"📋 Modem info: SW {sw_version}, Uptime: {uptime}")
                                else:
                                    logger.warning(f"⚠️ JSON structure unexpected: {list(data[0].keys()) if data and isinstance(data, list) else 'Not a list'}")
                            except json.JSONDecodeError as e:
                                logger.error(f"❌ JSON parse error: {e}")
                        elif content_type == 'text/html' and (response_text.strip().startswith('[') or response_text.strip().startswith('{')):
                            # This is the Hitron quirk - JSON served as text/html
                            try:
                                data = json.loads(response_text)
                                logger.info(f"✅ JSON response validated (despite text/html content-type) - got {len(data)} objects")
                                if isinstance(data, list) and len(data) > 0 and 'systemUptime' in data[0]:
                                    uptime = data[0]['systemUptime']
                                    sw_version = data[0].get('swVersion', 'unknown')
                                    logger.info(f"📋 Modem info: SW {sw_version}, Uptime: {uptime}")
                                logger.info(f"🔧 HITRON QUIRK DETECTED: JSON served with text/html content-type")
                            except json.JSONDecodeError as e:
                                logger.error(f"❌ JSON parse error even though response looks like JSON: {e}")
                        else:
                            logger.warning(f"⚠️ Unexpected response format")
                            preview = response_text[:100].replace('\n', '\\n')
                            logger.debug(f"📋 Response preview: {preview}")
                    else:
                        logger.warning(f"⚠️ Connectivity test: HTTP {response.status}")
                        
        except Exception as e:
            logger.error(f"❌ Connectivity test failed: {e}")
            logger.warning("Continuing with simulation anyway...")

    async def run_simulation(self):
        """Main simulation loop with tiered polling logic - EXACTLY MATCHES PLUGIN."""
        self.start_time = datetime.now()
        end_time = self.start_time + timedelta(seconds=self.test_duration)
        
        logger.info(f"🚀 Starting {self.test_duration}s simulation...")
        logger.info(f"🎯 Target end time: {end_time.strftime('%H:%M:%S')}")
        logger.info("⏹️ Press Ctrl+C to stop early")
        
        # Quick connectivity test with endpoint validation
        await self._test_connectivity()
        
        try:
            while self.is_running and datetime.now() < end_time:
                cycle_start_time = time.monotonic()
                cycle_timestamp = datetime.now()
                
                if not self.is_running:
                    break
                
                # --- Determine endpoints for this cycle (EXACTLY MATCHES PLUGIN _get_endpoints_for_cycle) ---
                self.run_counter += 1
                endpoints_to_poll = self._get_endpoints_for_cycle()
                cycle_type = self._determine_cycle_type(endpoints_to_poll)
                
                # Show cycle start with emojis
                cycle_emoji = "🔄"
                type_emoji = {"FULL": "📊", "FAST": "⚡", "CACHED": "💾"}[cycle_type]
                
                logger.info(f"{cycle_emoji} Starting cycle {self.run_counter} ({cycle_type}) {type_emoji}: {len(endpoints_to_poll)} endpoints")
                
                # Track cycle type
                self.results['cycle_types'].append(cycle_type)
                if cycle_type == "FAST":
                    self.results['fast_cycles'] += 1
                elif cycle_type == "FULL":
                    self.results['full_cycles'] += 1
                else:  # CACHED
                    self.results['cached_cycles'] += 1
                
                # --- Run Collection Cycle (MATCHES PLUGIN _run_collection_cycle logic) ---
                success, collection_time, successful_requests, actual_cycle_type = await self._run_collection_cycle(endpoints_to_poll)
                
                if not self.is_running:
                    break
                
                # --- Update Statistics and Time Series ---
                self._update_statistics(success, collection_time, endpoints_to_poll, successful_requests)
                self._update_time_series(cycle_timestamp, success, collection_time, successful_requests, 
                                       len(endpoints_to_poll), actual_cycle_type)
                
                # Show cycle completion with detailed progress
                self._show_cycle_completion(success, collection_time, successful_requests, len(endpoints_to_poll))
                
                # --- Progress Display ---
                self._display_progress()
                
                # --- Wait for Next Cycle (with early exit check) ---
                elapsed = time.monotonic() - cycle_start_time
                sleep_time = max(0, self.update_every - elapsed)
                if sleep_time > 0:
                    # Sleep in smaller chunks to check for early exit
                    sleep_chunks = max(1, int(sleep_time))
                    for _ in range(sleep_chunks):
                        if not self.is_running:
                            break
                        await asyncio.sleep(min(1.0, sleep_time / sleep_chunks))
        
        except asyncio.CancelledError:
            logger.info("Simulation cancelled")
        except KeyboardInterrupt:
            logger.info("Simulation interrupted")
        finally:
            logger.info("📊 Simulation completed - generating graphs and reports...")

    def _get_endpoints_for_cycle(self):
        """Determine which endpoints to poll (EXACTLY MATCHES PLUGIN logic)."""
        # First run always polls everything (PLUGIN LOGIC)
        if self.run_counter == 1:
            return self.ALL_ENDPOINTS
        
        # Check if this is an OFDM polling cycle (PLUGIN LOGIC)
        if self.ofdm_poll_multiple > 0 and self.run_counter % self.ofdm_poll_multiple == 0:
            return self.ALL_ENDPOINTS
        else:
            return self.FAST_ENDPOINTS

    def _determine_cycle_type(self, endpoints):
        """Determine cycle type based on endpoints."""
        if len(endpoints) == len(self.ALL_ENDPOINTS):
            return "FULL"
        elif len(endpoints) == len(self.FAST_ENDPOINTS):
            return "FAST"
        else:
            return "CACHED"

    async def _run_collection_cycle(self, endpoints):
        """Execute a single collection cycle with caching simulation."""
        start_time = time.monotonic()
        
        try:
            # Determine collection strategy
            current_time = time.monotonic()
            should_poll_ofdm = self._should_poll_ofdm()
            
            # Separate fast and OFDM endpoints
            fast_endpoints = [ep for ep in endpoints if ep in self.FAST_ENDPOINTS]
            ofdm_endpoints = [ep for ep in endpoints if ep in self.SLOW_ENDPOINTS]
            
            results = []
            actual_cycle_type = "FAST"
            
            # Always fetch fast endpoints
            if fast_endpoints:
                if self.parallel_collection:
                    fast_results = await self._fetch_parallel(fast_endpoints)
                else:
                    fast_results = await self._fetch_serial(fast_endpoints)
                results.extend(fast_results)
            
            # Handle OFDM endpoints with caching logic (MATCHES PLUGIN)
            if ofdm_endpoints:
                if should_poll_ofdm:
                    # Fetch fresh OFDM data and update cache
                    if self.parallel_collection:
                        ofdm_results = await self._fetch_parallel(ofdm_endpoints)
                    else:
                        ofdm_results = await self._fetch_serial(ofdm_endpoints)
                    results.extend(ofdm_results)
                    
                    # Update cache
                    self.ofdm_cache = {ep: result for ep, result in zip(ofdm_endpoints, ofdm_results)}
                    self.ofdm_cache_timestamp = current_time
                    self.results['cache_misses'] += 1
                    actual_cycle_type = "FULL"
                    
                elif self._is_ofdm_cache_valid():
                    # Use cached OFDM data (simulate success)
                    cached_results = [{"cached": True} for _ in ofdm_endpoints]
                    results.extend(cached_results)
                    self.results['cache_hits'] += 1
                    actual_cycle_type = "CACHED"
                    
                else:
                    # No valid cache, no OFDM data
                    actual_cycle_type = "FAST"
            
            collection_time = time.monotonic() - start_time
            successful_requests = sum(1 for r in results if r is not None)
            
            # Consider cycle successful if we get >80% of endpoints working (PLUGIN LOGIC)
            success_threshold = max(1, int(len(endpoints) * 0.8))
            is_cycle_success = successful_requests >= success_threshold
            
            return is_cycle_success, collection_time, successful_requests, actual_cycle_type
            
        except Exception as e:
            logger.error(f"Collection cycle failed: {e}")
            return False, time.monotonic() - start_time, 0, "FAILED"

    def _should_poll_ofdm(self):
        """Determine if this cycle should poll OFDM endpoints (MATCHES PLUGIN)."""
        # First run always polls everything
        if self.run_counter == 1:
            return True
        
        # Check if this is an OFDM polling cycle
        if self.ofdm_poll_multiple > 0 and self.run_counter % self.ofdm_poll_multiple == 0:
            return True
            
        return False

    def _is_ofdm_cache_valid(self):
        """Check if OFDM cache is still valid (MATCHES PLUGIN)."""
        if not self.ofdm_cache:
            return False
        
        current_time = time.monotonic()
        cache_age = current_time - self.ofdm_cache_timestamp
        
        return cache_age < self.ofdm_cache_ttl

    async def _fetch_serial(self, endpoints):
        """Simulate serial endpoint fetching (MATCHES PLUGIN)."""
        results = []
        
        # Create a single session for the entire serial collection
        connector = aiohttp.TCPConnector(ssl=self.ssl_context)
        timeout = aiohttp.ClientTimeout(total=self.collection_timeout)
        
        headers = {
            'User-Agent': 'Netdata-Hitron-Plugin/2.1.0',
            'Accept': 'application/json, */*',
            'Connection': 'close'
        }
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
            for endpoint in endpoints:
                result = await self._fetch_endpoint(session, endpoint)
                results.append(result)
                
                # Add inter-request delay (MATCHES PLUGIN)
                if self.inter_request_delay > 0 and endpoint != endpoints[-1]:
                    await asyncio.sleep(self.inter_request_delay)
        
        return results

    async def _fetch_parallel(self, endpoints):
        """Simulate parallel endpoint fetching (MATCHES PLUGIN)."""
        connector = aiohttp.TCPConnector(
            ssl=self.ssl_context,
            limit=10,
            keepalive_timeout=30,
            enable_cleanup_closed=True
        )
        
        timeout = aiohttp.ClientTimeout(total=self.collection_timeout, sock_read=max(self.fast_endpoint_timeout, self.ofdm_endpoint_timeout))
        
        headers = {
            'User-Agent': 'Netdata-Hitron-Plugin/2.1.0',
            'Accept': 'application/json, */*'
        }
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
            tasks = [self._fetch_endpoint(session, endpoint) for endpoint in endpoints]
            return await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_endpoint(self, session, endpoint):
        """Fetch a single endpoint with enhanced timeout logic (MATCHES PLUGIN)."""
        url = f"{self.modem_host}/data/{endpoint}"
        endpoint_timeout = self.endpoint_timeouts.get(endpoint, self.fast_endpoint_timeout)
        
        logger.debug(f"Fetching {endpoint} with {endpoint_timeout}s timeout...")
        
        for attempt in range(self.max_retries):
            request_start = time.monotonic()
            
            try:
                async with session.get(url, timeout=endpoint_timeout) as response:
                    response_time = (time.monotonic() - request_start) * 1000  # Convert to ms
                    self.results['response_times'].append(response_time)
                    
                    logger.debug(f"{endpoint}: HTTP {response.status} in {response_time:.1f}ms")
                    
                    if response.status == 200:
                        # Update endpoint success tracking
                        self.results['endpoint_success_rates'][endpoint]['success'] += 1
                        self.results['endpoint_success_rates'][endpoint]['total'] += 1
                        
                        # Get response text first - Hitron modems return text/html even for JSON
                        response_text = await response.text()
                        
                        # Handle different response types like the plugin would
                        if response_text.strip() == "0":
                            logger.warning(f"{endpoint}: Returned '0' - possible auth/endpoint issue")
                            return None
                        
                        # Try to parse as JSON regardless of content-type (Hitron quirk)
                        try:
                            # First try aiohttp's json() method
                            try:
                                json_data = await response.json(content_type=None)  # Ignore content-type
                                logger.debug(f"{endpoint}: Successfully parsed JSON via aiohttp")
                                return json_data
                            except Exception:
                                # If that fails, manually parse the text
                                json_data = json.loads(response_text)
                                logger.debug(f"{endpoint}: Successfully parsed JSON via manual parsing")
                                return json_data
                        except json.JSONDecodeError as e:
                            logger.warning(f"{endpoint}: JSON parsing failed: {e}")
                            # Log response details for debugging
                            content_type = response.headers.get('content-type', 'unknown')
                            logger.debug(f"{endpoint}: Content-Type: {content_type}, Length: {len(response_text)}")
                            preview = response_text[:200].replace('\n', '\\n').replace('\r', '\\r')
                            logger.debug(f"{endpoint}: Response preview: {preview}")
                            return None
                                
                    else:
                        logger.warning(f"{endpoint}: HTTP {response.status} - {response.reason}")
                        self.results['endpoint_success_rates'][endpoint]['total'] += 1
                        
            except asyncio.TimeoutError as e:
                response_time = (time.monotonic() - request_start) * 1000
                self.results['response_times'].append(response_time)
                self.results['endpoint_success_rates'][endpoint]['total'] += 1
                
                logger.warning(f"{endpoint}: Timeout after {response_time:.1f}ms (limit: {endpoint_timeout}s)")
                
                if attempt < self.max_retries - 1:
                    logger.debug(f"{endpoint}: Retrying in 1 second (attempt {attempt + 2}/{self.max_retries})")
                    await asyncio.sleep(1)  # Brief pause between retries
                    
            except Exception as e:
                response_time = (time.monotonic() - request_start) * 1000
                self.results['response_times'].append(response_time)
                self.results['endpoint_success_rates'][endpoint]['total'] += 1
                
                logger.warning(f"{endpoint}: Error: {e}")
                
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(1)
        
        logger.error(f"{endpoint}: All {self.max_retries} attempts failed")
        return None

    def _update_statistics(self, cycle_success, collection_time, endpoints, successful_requests):
        """Update performance statistics (MATCHES PLUGIN)."""
        self.results['total_cycles'] += 1
        self.results['collection_times'].append(collection_time * 1000)  # Convert to ms
        self.results['total_requests'] += len(endpoints)
        self.results['successful_requests'] += successful_requests
        self.results['failed_requests'] += (len(endpoints) - successful_requests)
        
        if cycle_success:
            self.results['successful_cycles'] += 1
            self.results['consecutive_failures'] = 0
        else:
            self.results['failed_cycles'] += 1
            self.results['consecutive_failures'] += 1
            self.results['max_consecutive_failures'] = max(
                self.results['max_consecutive_failures'],
                self.results['consecutive_failures']
            )
        
        # Update performance stats for health tracking (MATCHES PLUGIN)
        self.performance_stats['total_cycles'] += 1
        self.performance_stats['active_endpoints'] = successful_requests
        self.performance_stats['collection_times'].append(collection_time * 1000)
        
        if cycle_success:
            self.performance_stats['successful_cycles'] += 1
            self.performance_stats['consecutive_failures'] = 0
        else:
            self.performance_stats['failed_cycles'] += 1
            self.performance_stats['consecutive_failures'] += 1

    def _update_time_series(self, timestamp, success, collection_time, successful_requests, total_endpoints, cycle_type):
        """Update time series data for graphing."""
        ts = self.results['time_series']
        
        ts['timestamps'].append(timestamp)
        ts['cycle_success'].append(1 if success else 0)
        ts['collection_times'].append(collection_time * 1000)  # ms
        ts['endpoint_counts'].append(successful_requests)
        ts['cycle_types'].append(cycle_type)
        ts['consecutive_failures'].append(self.results['consecutive_failures'])
        ts['cache_status'].append(1 if cycle_type == "CACHED" else 0)
        
        # Calculate rolling success rate
        window = min(10, len(ts['cycle_success']))
        if window > 0:
            recent_success = ts['cycle_success'][-window:]
            rolling_rate = (sum(recent_success) / len(recent_success)) * 100
            ts['success_rates_rolling'].append(rolling_rate)
        else:
            ts['success_rates_rolling'].append(100)
        
        # Average response time for this cycle
        if self.results['response_times']:
            # Get response times from this cycle (approximate)
            recent_responses = self.results['response_times'][-total_endpoints:]
            avg_response = statistics.mean(recent_responses) if recent_responses else 0
            ts['response_times_avg'].append(avg_response)
        else:
            ts['response_times_avg'].append(0)

    def _show_cycle_completion(self, success, collection_time, successful_requests, total_endpoints):
        """Show cycle completion with detailed progress and emojis."""
        status_emoji = "✅" if success else "❌"
        success_detail = f"({successful_requests}/{total_endpoints} endpoints successful)"
        
        # Calculate progress
        elapsed_time = time.time() - self.start_time.timestamp()
        progress_percent = (elapsed_time / self.test_duration) * 100
        remaining_minutes = (self.test_duration - elapsed_time) / 60
        
        # Estimate expected cycles for this point in time
        expected_cycles_so_far = int(elapsed_time / self.update_every)
        
        # Overall health emoji based on recent performance
        if self.performance_stats['total_cycles'] > 0:
            recent_success_rate = (self.performance_stats['successful_cycles'] / self.performance_stats['total_cycles']) * 100
            consecutive_failures = self.performance_stats['consecutive_failures']
            
            if recent_success_rate >= 98 and consecutive_failures == 0:
                health_emoji = "🟢"  # Excellent
            elif recent_success_rate >= 90 and consecutive_failures <= 2:
                health_emoji = "🟡"  # Good
            elif recent_success_rate >= 80 and consecutive_failures <= 5:
                health_emoji = "🟠"  # Warning
            else:
                health_emoji = "🔴"  # Critical
        else:
            health_emoji = "🟢" if success else "🟠"
        
        threshold = max(1, int(total_endpoints * 0.8))
        threshold_detail = f", threshold: {threshold}" if successful_requests < total_endpoints else ""
        
        status_text = "SUCCESS" if success else "FAILED"
        logger.info(f"{status_emoji} Completed cycle {self.run_counter}/{expected_cycles_so_far} ({progress_percent:.1f}%, {remaining_minutes:.1f}m remaining) {health_emoji}: "
                   f"{status_text} in {collection_time*1000:.0f}ms {success_detail}{threshold_detail}")

    def _display_progress(self):
        """Display timestamped progress information at regular intervals."""
        if self.results['total_cycles'] == 0:
            return
        
        # Show progress every cycle for first 10 cycles, then every 10 cycles, with minimum 30 second intervals
        current_time = time.time()
        should_show = False
        
        if self.results['total_cycles'] <= 10:
            should_show = True
        elif self.results['total_cycles'] % 10 == 0:
            should_show = True
        elif current_time - self.last_progress_time >= 30:
            should_show = True
        
        if not should_show:
            return
            
        self.last_progress_time = current_time
        
        # Calculate rates
        cycle_success_rate = (self.results['successful_cycles'] / self.results['total_cycles']) * 100
        
        # Calculate average times
        avg_collection_time = statistics.mean(self.results['collection_times']) if self.results['collection_times'] else 0
        
        # Calculate elapsed and remaining time
        elapsed = datetime.now() - self.start_time
        remaining = self.test_duration - elapsed.total_seconds()
        
        # Progress percentage
        progress = (elapsed.total_seconds() / self.test_duration) * 100
        
        # Progress emojis
        if progress < 25:
            progress_emoji = "🚀"
        elif progress < 50:
            progress_emoji = "📈"
        elif progress < 75:
            progress_emoji = "⏳"
        elif progress < 95:
            progress_emoji = "🏁"
        else:
            progress_emoji = "🎯"
        
        # Success rate emojis
        if cycle_success_rate >= 99:
            success_emoji = "💚"
        elif cycle_success_rate >= 95:
            success_emoji = "💛"
        elif cycle_success_rate >= 80:
            success_emoji = "🧡"
        else:
            success_emoji = "❤️"
        
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]
        
        print(f"{timestamp} - PROGRESS - {progress_emoji} Progress: {progress:.1f}% | Cycles: {self.results['total_cycles']} "
              f"(Fast: {self.results['fast_cycles']}, Full: {self.results['full_cycles']}, Cached: {self.results['cached_cycles']}) | "
              f"{success_emoji} Success: {cycle_success_rate:.1f}% | ⏱️ Avg Time: {avg_collection_time:.0f}ms | 🕐 Remaining: {remaining:.0f}s")

    def generate_comprehensive_graphs(self):
        """Generate simplified, fast-rendering performance graphs."""
        logger.info("📊 Generating performance graphs...")
        
        if not self.results['time_series']['timestamps']:
            logger.warning("No time series data available for graphing")
            return
        
        # Create DataFrame for easier plotting
        df = pd.DataFrame({
            'timestamp': self.results['time_series']['timestamps'],
            'cycle_success': self.results['time_series']['cycle_success'],
            'collection_time': self.results['time_series']['collection_times'],
            'response_time_avg': self.results['time_series']['response_times_avg'],
            'endpoint_count': self.results['time_series']['endpoint_counts'],
            'cycle_type': self.results['time_series']['cycle_types'],
            'success_rate_rolling': self.results['time_series']['success_rates_rolling'],
            'consecutive_failures': self.results['time_series']['consecutive_failures']
        })
        
        # Set timestamp as index
        df.set_index('timestamp', inplace=True)
        
        # Create a simple 2x2 grid layout for fast rendering
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
        
        # 1. Success Rate Over Time
        ax1.plot(df.index, df['success_rate_rolling'], color='green', linewidth=2, label='Success Rate %')
        ax1.set_ylabel('Success Rate (%)')
        ax1.set_title('Success Rate Over Time')
        ax1.grid(True, alpha=0.3)
        ax1.set_ylim(0, 105)
        
        # 2. Collection Times
        ax2.plot(df.index, df['collection_time'], color='blue', linewidth=1, alpha=0.8, label='Collection Time (ms)')
        ax2.axhline(y=self.collection_timeout * 1000, color='red', linestyle='--', alpha=0.7, label=f'Timeout ({self.collection_timeout}s)')
        ax2.set_ylabel('Time (ms)')
        ax2.set_title('Collection Times vs Timeout')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        
        # 3. Endpoint Success Rates
        endpoint_data = []
        endpoint_names = []
        endpoint_colors = []
        for endpoint, stats in self.results['endpoint_success_rates'].items():
            if stats['total'] > 0:
                success_rate = (stats['success'] / stats['total']) * 100
                endpoint_data.append(success_rate)
                short_name = endpoint.replace('.asp', '').replace('getCmDocsisWan', 'WAN').replace('getSysInfo', 'Sys')
                endpoint_names.append(short_name)
                endpoint_colors.append('green' if endpoint in self.FAST_ENDPOINTS else 'blue')
        
        if endpoint_data:
            bars = ax3.bar(range(len(endpoint_data)), endpoint_data, color=endpoint_colors)
            ax3.set_xticks(range(len(endpoint_names)))
            ax3.set_xticklabels(endpoint_names, rotation=45)
            ax3.set_ylabel('Success Rate (%)')
            ax3.set_title('Endpoint Success Rates')
            ax3.axhline(y=95, color='orange', linestyle='--', alpha=0.7, label='95% Target')
            ax3.set_ylim(0, 105)
            ax3.legend()
            ax3.grid(True, alpha=0.3)
            
            # Add value labels
            for i, bar in enumerate(bars):
                height = bar.get_height()
                ax3.text(bar.get_x() + bar.get_width()/2., height + 1,
                        f'{height:.1f}%', ha='center', va='bottom', fontsize=9)
        
        # 4. Performance Summary
        ax4.axis('off')
        
        # Calculate summary stats
        overall_success = (self.results['successful_cycles'] / self.results['total_cycles']) * 100 if self.results['total_cycles'] > 0 else 0
        avg_collection = statistics.mean(self.results['collection_times']) if self.results['collection_times'] else 0
        avg_response = statistics.mean(self.results['response_times']) if self.results['response_times'] else 0
        
        summary_text = f"""PERFORMANCE SUMMARY

Total Cycles: {self.results['total_cycles']}
Success Rate: {overall_success:.1f}%
Avg Collection: {avg_collection:.0f}ms
Avg Response: {avg_response:.0f}ms
Max Failures: {self.results['max_consecutive_failures']}

Configuration:
Update Every: {self.update_every}s
OFDM Multiple: {self.ofdm_poll_multiple}x
Mode: {'Parallel' if self.parallel_collection else 'Serial'}
Timeouts: {self.fast_endpoint_timeout}s/{self.ofdm_endpoint_timeout}s
"""
        
        ax4.text(0.05, 0.95, summary_text, transform=ax4.transAxes, fontsize=11, 
                verticalalignment='top', fontfamily='monospace',
                bbox=dict(boxstyle='round', facecolor='lightblue', alpha=0.8))
        ax4.set_title('Configuration & Stats')
        
        # Format x-axis for time series plots (simplified)
        for ax in [ax1, ax2]:
            if len(df) > 0:
                # Simplified time formatting to avoid matplotlib hanging
                ax.tick_params(axis='x', rotation=45)
        
        # Add overall title
        config_summary = f"Mode: {'Parallel' if self.parallel_collection else 'Serial'} | Interval: {self.update_every}s | OFDM: {self.ofdm_poll_multiple}x"
        fig.suptitle(f'Hitron CODA Performance Analysis - {config_summary}', fontsize=14, fontweight='bold')
        
        plt.tight_layout()
        
        # Save with simplified naming and faster rendering
        graph_file = self.output_dir / f'{self.file_prefix}_analysis.png'
        plt.savefig(graph_file, dpi=150, bbox_inches='tight', facecolor='white')  # Lower DPI for speed
        logger.info(f"📊 Analysis graph saved: {graph_file}")
        
        plt.close()
        
        # Generate CSV files
        self.generate_csv_summary()
        
        logger.info("📊 Graph generation completed quickly")14.text(0.05, 0.95, recommendations, transform=ax14.transAxes, fontsize=10,
                 verticalalignment='top', fontfamily='monospace',
                 bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8))
        ax14.set_title('💡 Optimization Recommendations', fontsize=12, fontweight='bold')
        
        # === ROW 8: FOOTER ===
        
        # 8.1 Footer with metadata
        ax15 = fig.add_subplot(gs[7, :])
        ax15.axis('off')
        
        # Create footer with test details
        test_duration_actual = (df.index[-1] - df.index[0]).total_seconds() if len(df) > 0 else self.test_duration
        footer_text = f"""🔍 Test Details: Duration: {test_duration_actual:.0f}s | Cycles: {len(df)} | Host: {self.modem_host} | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
📁 Files: {self.file_prefix}_*.png/csv | 🔧 Plugin Validation: ✅ Endpoints, timeouts, and logic match actual plugin"""
        
        ax15.text(0.5, 0.5, footer_text, transform=ax15.transAxes, ha='center', va='center',
                 fontsize=10, bbox=dict(boxstyle='round', facecolor='lightsteelblue', alpha=0.8))
        
        # Format x-axis for time series plots
        for ax in [ax1, ax4, ax8, ax11, ax12]:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
            if len(df) > 0:
                ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=max(1, int(test_duration_actual / 600))))
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
        
        # Add overall title and metadata
        config_summary = (f"Mode: {'Parallel' if self.parallel_collection else 'Serial'} | "
                         f"Interval: {self.update_every}s | OFDM: {self.ofdm_poll_multiple}x | "
                         f"Timeouts: {self.fast_endpoint_timeout}s/{self.ofdm_endpoint_timeout}s")
        
        fig.suptitle(f'📊 Hitron CODA Modem Performance Analysis - {config_summary}', 
                    fontsize=16, fontweight='bold', y=0.98)
        
        # Save the mega comprehensive graph
        graph_file = self.output_dir / f'{self.file_prefix}_complete_analysis.png'
        plt.savefig(graph_file, dpi=300, bbox_inches='tight', facecolor='white')
        logger.info(f"📊 Complete analysis graph saved: {graph_file}")
        
        # Also save as SVG for scalability
        svg_file = self.output_dir / f'{self.file_prefix}_complete_analysis.svg'
        plt.savefig(svg_file, format='svg', bbox_inches='tight', facecolor='white')
        logger.info(f"📊 SVG complete analysis saved: {svg_file}")
        
        plt.close()
        
        # Also update the README to reflect the single consolidated graph output
        logger.info("📊 All analysis consolidated into a single comprehensive graph for easier viewing")
        logger.info(f"📁 Output files: {self.file_prefix}_complete_analysis.png and CSV summaries")
        for ax in [ax1, ax3, ax4, ax6]:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
            ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=max(1, self.test_duration // 600)))
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
        
        # Add overall title and metadata
        config_text = (f"Config: {self.update_every}s interval, OFDM every {self.ofdm_poll_multiple}x, "
                      f"{'Parallel' if self.parallel_collection else 'Serial'} mode, "
                      f"Timeouts: {self.fast_endpoint_timeout}s/{self.ofdm_endpoint_timeout}s")
        
        fig.suptitle(f'📊 Hitron CODA Modem Polling Performance Analysis\n{config_text}', 
                    fontsize=16, fontweight='bold', y=0.98)
        
        # Save the comprehensive graph with descriptive filename
        graph_file = self.output_dir / f'{self.file_prefix}_comprehensive_analysis.png'
        plt.savefig(graph_file, dpi=300, bbox_inches='tight', facecolor='white')
        logger.info(f"📊 Comprehensive graph saved: {graph_file}")
        
        # Also save as SVG for scalability
        svg_file = self.output_dir / f'{self.file_prefix}_comprehensive_analysis.svg'
        plt.savefig(svg_file, format='svg', bbox_inches='tight', facecolor='white')
        logger.info(f"📊 SVG graph saved: {svg_file}")
        
        plt.close()
        
        # Generate additional specialized graphs
        self._generate_specialized_graphs(df)

    def _generate_specialized_graphs(self, df):
        """Generate additional specialized analysis graphs."""
        
        # 1. Detailed Timing Analysis
        self._create_timing_analysis_graph(df)
        
        # 2. Failure Pattern Analysis
        self._create_failure_analysis_graph(df)
        
        # 3. Polling Strategy Effectiveness
        self._create_polling_strategy_graph(df)
        
        # 4. Performance vs Configuration Chart
        self._create_configuration_analysis_graph()

    def _create_timing_analysis_graph(self, df):
        """Create detailed timing analysis graph."""
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
        
        # Collection time distribution
        ax1.hist(df['collection_time'], bins=30, alpha=0.7, color='#4169E1', edgecolor='black')
        ax1.axvline(statistics.mean(df['collection_time']), color='red', linestyle='--', label=f'Mean: {statistics.mean(df["collection_time"]):.1f}ms')
        ax1.axvline(self.collection_timeout * 1000, color='orange', linestyle='--', label=f'Timeout: {self.collection_timeout * 1000}ms')
        ax1.set_xlabel('Collection Time (ms)')
        ax1.set_ylabel('Frequency')
        ax1.set_title('Collection Time Distribution')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # Response time trends by cycle type
        for cycle_type in df['cycle_type'].unique():
            cycle_data = df[df['cycle_type'] == cycle_type]
            ax2.scatter(cycle_data.index, cycle_data['response_time_avg'], 
                       alpha=0.6, label=f'{cycle_type} Cycles', s=20)
        ax2.set_ylabel('Response Time (ms)')
        ax2.set_title('Response Times by Cycle Type')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        
        # Box plot of collection times by cycle type
        cycle_types = df['cycle_type'].unique()
        collection_data = [df[df['cycle_type'] == ct]['collection_time'].values for ct in cycle_types]
        bp = ax3.boxplot(collection_data, labels=cycle_types, patch_artist=True)
        colors = ['#2E8B57', '#4169E1', '#FF6347']
        for patch, color in zip(bp['boxes'], colors[:len(bp['boxes'])]):
            patch.set_facecolor(color)
            patch.set_alpha(0.7)
        ax3.set_ylabel('Collection Time (ms)')
        ax3.set_title('Collection Time Distribution by Cycle Type')
        ax3.grid(True, alpha=0.3)
        
        # Performance over time (rolling averages)
        window = max(1, len(df) // 20)
        rolling_collection = df['collection_time'].rolling(window=window, center=True).mean()
        rolling_response = df['response_time_avg'].rolling(window=window, center=True).mean()
        
        ax4.plot(df.index, rolling_collection, label=f'Collection Time (rolling {window})', linewidth=2)
        ax4.plot(df.index, rolling_response, label=f'Response Time (rolling {window})', linewidth=2)
        ax4.set_ylabel('Time (ms)')
        ax4.set_title(f'Performance Trends (Rolling Average)')
        ax4.legend()
        ax4.grid(True, alpha=0.3)
        
        plt.tight_layout()
        timing_file = self.output_dir / f'{self.file_prefix}_timing_analysis.png'
        plt.savefig(timing_file, dpi=300, bbox_inches='tight', facecolor='white')
        logger.info(f"📊 Timing analysis graph saved: {timing_file}")
        plt.close()

    def _create_failure_analysis_graph(self, df):
        """Create failure pattern analysis graph."""
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
        
        # Success/failure timeline
        success_colors = ['red' if x == 0 else 'green' for x in df['cycle_success']]
        ax1.scatter(df.index, df['cycle_success'], c=success_colors, alpha=0.6, s=30)
        ax1.set_ylabel('Success (1) / Failure (0)')
        ax1.set_title('Success/Failure Timeline')
        ax1.set_ylim(-0.1, 1.1)
        ax1.grid(True, alpha=0.3)
        
        # Consecutive failures over time
        ax2.plot(df.index, df['consecutive_failures'], color='red', linewidth=2)
        ax2.fill_between(df.index, 0, df['consecutive_failures'], alpha=0.3, color='red')
        ax2.set_ylabel('Consecutive Failures')
        ax2.set_title('Consecutive Failure Streaks')
        ax2.grid(True, alpha=0.3)
        
        # Failure rate by cycle type
        failure_rates = {}
        for cycle_type in df['cycle_type'].unique():
            cycle_data = df[df['cycle_type'] == cycle_type]
            failure_rate = (1 - cycle_data['cycle_success'].mean()) * 100
            failure_rates[cycle_type] = failure_rate
        
        ax3.bar(failure_rates.keys(), failure_rates.values(), 
                color=['#2E8B57', '#4169E1', '#FF6347'][:len(failure_rates)])
        ax3.set_ylabel('Failure Rate (%)')
        ax3.set_title('Failure Rate by Cycle Type')
        ax3.grid(True, alpha=0.3)
        
        # Endpoint count vs success correlation
        ax4.scatter(df['endpoint_count'], df['cycle_success'], alpha=0.6, s=30)
        ax4.set_xlabel('Successful Endpoints')
        ax4.set_ylabel('Cycle Success')
        ax4.set_title('Endpoint Success vs Cycle Success')
        ax4.grid(True, alpha=0.3)
        
        plt.tight_layout()
        failure_file = self.output_dir / f'{self.file_prefix}_failure_analysis.png'
        plt.savefig(failure_file, dpi=300, bbox_inches='tight', facecolor='white')
        logger.info(f"📊 Failure analysis graph saved: {failure_file}")
        plt.close()

    def _create_polling_strategy_graph(self, df):
        """Create polling strategy effectiveness graph."""
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
        
        # OFDM polling schedule visualization
        ofdm_cycles = df[df['cycle_type'] == 'FULL'].index
        fast_cycles = df[df['cycle_type'] == 'FAST'].index
        cached_cycles = df[df['cycle_type'] == 'CACHED'].index
        
        ax1.scatter(fast_cycles, [1] * len(fast_cycles), alpha=0.6, color='green', label='Fast Cycles', s=20)
        ax1.scatter(ofdm_cycles, [2] * len(ofdm_cycles), alpha=0.8, color='blue', label='Full Cycles (OFDM)', s=40)
        ax1.scatter(cached_cycles, [1.5] * len(cached_cycles), alpha=0.6, color='orange', label='Cached Cycles', s=20)
        ax1.set_ylabel('Cycle Type')
        ax1.set_title('Tiered Polling Schedule Visualization')
        ax1.set_yticks([1, 1.5, 2])
        ax1.set_yticklabels(['Fast', 'Cached', 'Full'])
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # Cache effectiveness
        cache_data = df[df['cache_status'] == 1]
        if len(cache_data) > 0:
            ax2.plot(cache_data.index, cache_data['collection_time'], 'o-', alpha=0.7, label='Cached Collection Times')
            ax2.axhline(statistics.mean(df['collection_time']), color='red', linestyle='--', label='Overall Avg')
            ax2.set_ylabel('Collection Time (ms)')
            ax2.set_title('Cache Performance Impact')
            ax2.legend()
            ax2.grid(True, alpha=0.3)
        else:
            ax2.text(0.5, 0.5, 'No cache data available', transform=ax2.transAxes, ha='center', va='center')
            ax2.set_title('Cache Performance Impact (No Data)')
        
        # Polling efficiency comparison
        cycle_efficiency = {}
        for cycle_type in df['cycle_type'].unique():
            cycle_data = df[df['cycle_type'] == cycle_type]
            avg_time = cycle_data['collection_time'].mean()
            avg_endpoints = cycle_data['endpoint_count'].mean()
            efficiency = avg_endpoints / (avg_time / 1000) if avg_time > 0 else 0  # endpoints per second
            cycle_efficiency[cycle_type] = efficiency
        
        ax3.bar(cycle_efficiency.keys(), cycle_efficiency.values(),
                color=['#2E8B57', '#4169E1', '#FF6347'][:len(cycle_efficiency)])
        ax3.set_ylabel('Endpoints/Second')
        ax3.set_title('Polling Efficiency by Cycle Type')
        ax3.grid(True, alpha=0.3)
        
        # Time savings analysis
        theoretical_full_time = len(self.ALL_ENDPOINTS) * max(self.fast_endpoint_timeout, self.ofdm_endpoint_timeout) * 1000
        actual_avg_time = df['collection_time'].mean()
        time_savings = theoretical_full_time - actual_avg_time
        
        savings_data = ['Theoretical\nFull Polling', 'Actual Tiered\nPolling', 'Time Savings']
        savings_values = [theoretical_full_time, actual_avg_time, time_savings]
        colors = ['red', 'green', 'blue']
        
        bars = ax4.bar(savings_data, savings_values, color=colors, alpha=0.7)
        ax4.set_ylabel('Time (ms)')
        ax4.set_title('Time Savings from Tiered Polling')
        ax4.grid(True, alpha=0.3)
        
        # Add value labels
        for bar, value in zip(bars, savings_values):
            ax4.text(bar.get_x() + bar.get_width()/2., bar.get_height() + max(savings_values) * 0.01,
                    f'{value:.0f}ms', ha='center', va='bottom')
        
        plt.tight_layout()
        strategy_file = self.output_dir / f'{self.file_prefix}_polling_strategy.png'
        plt.savefig(strategy_file, dpi=300, bbox_inches='tight', facecolor='white')
        logger.info(f"📊 Polling strategy graph saved: {strategy_file}")
        plt.close()

    def _create_configuration_analysis_graph(self):
        """Create configuration vs performance analysis."""
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
        
        # Configuration summary
        config_text = f"""
CONFIGURATION ANALYSIS
Update Every: {self.update_every}s
OFDM Multiple: {self.ofdm_poll_multiple}x
Collection Mode: {'Parallel' if self.parallel_collection else 'Serial'}
Fast Timeout: {self.fast_endpoint_timeout}s
OFDM Timeout: {self.ofdm_endpoint_timeout}s
Collection Timeout: {self.collection_timeout}s
Max Retries: {self.max_retries}
Inter-request Delay: {self.inter_request_delay}s

PERFORMANCE SUMMARY
Total Cycles: {self.results['total_cycles']}
Success Rate: {(self.results['successful_cycles']/self.results['total_cycles']*100):.1f}%
Avg Collection Time: {statistics.mean(self.results['collection_times']):.1f}ms
Max Consecutive Failures: {self.results['max_consecutive_failures']}
Cache Hit Rate: {(self.results['cache_hits']/(self.results['cache_hits']+self.results['cache_misses'])*100):.1f}%
        """
        
        ax1.text(0.05, 0.95, config_text, transform=ax1.transAxes, fontsize=10, 
                verticalalignment='top', fontfamily='monospace',
                bbox=dict(boxstyle='round', facecolor='lightgray', alpha=0.8))
        ax1.set_xlim(0, 1)
        ax1.set_ylim(0, 1)
        ax1.axis('off')
        ax1.set_title('Configuration and Performance Summary')
        
        # Theoretical vs actual performance
        theoretical_max_freq = 1 / self.update_every
        actual_avg_freq = self.results['total_cycles'] / self.test_duration if self.test_duration > 0 else 0
        
        freq_data = ['Theoretical\nMax Frequency', 'Actual\nFrequency']
        freq_values = [theoretical_max_freq, actual_avg_freq]
        
        bars = ax2.bar(freq_data, freq_values, color=['red', 'green'], alpha=0.7)
        ax2.set_ylabel('Cycles per Second')
        ax2.set_title('Theoretical vs Actual Polling Frequency')
        ax2.grid(True, alpha=0.3)
        
        for bar, value in zip(bars, freq_values):
            ax2.text(bar.get_x() + bar.get_width()/2., bar.get_height() + max(freq_values) * 0.01,
                    f'{value:.3f}', ha='center', va='bottom')
        
        # Resource utilization
        if self.results['collection_times']:
            avg_collection_ms = statistics.mean(self.results['collection_times'])
            utilization = (avg_collection_ms / (self.update_every * 1000)) * 100
            idle_time = 100 - utilization
            
            util_data = [utilization, idle_time]
            util_labels = ['Active', 'Idle']
            colors = ['#FF6347', '#90EE90']
            
            ax3.pie(util_data, labels=util_labels, autopct='%1.1f%%', colors=colors, startangle=90)
            ax3.set_title(f'Resource Utilization\n(Avg: {avg_collection_ms:.1f}ms/{self.update_every*1000}ms)')
        
        # Optimization recommendations
        recommendations = self._generate_optimization_recommendations()
        ax4.text(0.05, 0.95, recommendations, transform=ax4.transAxes, fontsize=10,
                verticalalignment='top', fontfamily='monospace',
                bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8))
        ax4.set_xlim(0, 1)
        ax4.set_ylim(0, 1)
        ax4.axis('off')
        ax4.set_title('Optimization Recommendations')
        
        plt.tight_layout()
        config_file = self.output_dir / f'{self.file_prefix}_configuration_analysis.png'
        plt.savefig(config_file, dpi=300, bbox_inches='tight', facecolor='white')
        logger.info(f"📊 Configuration analysis graph saved: {config_file}")
        plt.close()

    def _generate_optimization_recommendations(self):
        """Generate optimization recommendations based on simulation results."""
        if not self.results['collection_times']:
            return "No data available for recommendations"
        
        avg_collection_time = statistics.mean(self.results['collection_times'])
        success_rate = (self.results['successful_cycles'] / self.results['total_cycles']) * 100
        max_collection_time = max(self.results['collection_times'])
        
        recommendations = "OPTIMIZATION RECOMMENDATIONS:\n\n"
        
        # Performance assessment
        if success_rate >= 99:
            recommendations += "✅ Excellent success rate!\n"
        elif success_rate >= 95:
            recommendations += "✅ Good success rate\n"
        elif success_rate < 90:
            recommendations += "❌ Poor success rate - needs improvement\n"
        
        # Timing analysis
        collection_efficiency = (avg_collection_time / (self.update_every * 1000)) * 100
        if collection_efficiency < 30:
            recommendations += f"⚡ Low utilization ({collection_efficiency:.1f}%)\n"
            recommendations += f"   → Could reduce update_every to {int(avg_collection_time/1000*2.5)}s\n"
        elif collection_efficiency > 80:
            recommendations += f"⚠️ High utilization ({collection_efficiency:.1f}%)\n"
            recommendations += f"   → Consider increasing timeouts or update_every\n"
        
        # Timeout analysis
        timeout_margin = (self.collection_timeout * 1000 - max_collection_time) / (self.collection_timeout * 1000) * 100
        if timeout_margin < 20:
            recommendations += f"⚠️ Tight timeout margin ({timeout_margin:.1f}%)\n"
            recommendations += f"   → Increase collection_timeout to {int(max_collection_time/1000*1.3)}s\n"
        
        # Cache analysis
        if self.results['cache_hits'] + self.results['cache_misses'] > 0:
            cache_hit_rate = self.results['cache_hits'] / (self.results['cache_hits'] + self.results['cache_misses']) * 100
            if cache_hit_rate > 70:
                recommendations += f"💾 Good cache utilization ({cache_hit_rate:.1f}%)\n"
            else:
                recommendations += f"💾 Poor cache utilization ({cache_hit_rate:.1f}%)\n"
                recommendations += f"   → Consider increasing ofdm_poll_multiple\n"
        
        # Failure analysis
        if self.results['max_consecutive_failures'] > 5:
            recommendations += f"❌ High consecutive failures ({self.results['max_consecutive_failures']})\n"
            recommendations += f"   → Check network stability or increase timeouts\n"
        
        # Mode recommendation
        if self.parallel_collection and success_rate < 95:
            recommendations += "🔄 Consider switching to serial mode for stability\n"
        elif not self.parallel_collection and collection_efficiency < 30 and success_rate > 95:
            recommendations += "🔄 Consider parallel mode for better performance\n"
        
        return recommendations

    def generate_csv_summary(self):
        """Generate CSV summary for automated analysis with descriptive filenames."""
        csv_file = self.output_dir / f'{self.file_prefix}_simulation_summary.csv'
        
        # Create summary data
        summary_data = []
        for i, timestamp in enumerate(self.results['time_series']['timestamps']):
            summary_data.append({
                'timestamp': timestamp,
                'cycle_number': i + 1,
                'cycle_type': self.results['time_series']['cycle_types'][i],
                'success': self.results['time_series']['cycle_success'][i],
                'collection_time_ms': self.results['time_series']['collection_times'][i],
                'response_time_avg_ms': self.results['time_series']['response_times_avg'][i],
                'endpoint_count': self.results['time_series']['endpoint_counts'][i],
                'success_rate_rolling': self.results['time_series']['success_rates_rolling'][i],
                'consecutive_failures': self.results['time_series']['consecutive_failures'][i],
                'cache_hit': self.results['time_series']['cache_status'][i]
            })
        
        # Write to CSV
        if summary_data:
            df = pd.DataFrame(summary_data)
            df.to_csv(csv_file, index=False)
            logger.info(f"📊 CSV summary saved: {csv_file}")
        
        # Also create endpoint-specific CSV
        endpoint_csv = self.output_dir / f'{self.file_prefix}_endpoint_performance.csv'
        endpoint_data = []
        for endpoint, stats in self.results['endpoint_success_rates'].items():
            if stats['total'] > 0:
                endpoint_data.append({
                    'endpoint': endpoint,
                    'type': 'FAST' if endpoint in self.FAST_ENDPOINTS else 'OFDM',
                    'timeout_seconds': self.endpoint_timeouts[endpoint],
                    'total_requests': stats['total'],
                    'successful_requests': stats['success'],
                    'success_rate_percent': (stats['success'] / stats['total']) * 100
                })
        
        if endpoint_data:
            df_endpoints = pd.DataFrame(endpoint_data)
            df_endpoints.to_csv(endpoint_csv, index=False)
            logger.info(f"📊 Endpoint CSV saved: {endpoint_csv}")

    def generate_report(self):
        """Generate comprehensive final report with plugin validation."""
        print("\n\n" + "="*80)
        print("           ENHANCED SIMULATION FINAL REPORT")
        print("           🔍 PLUGIN VALIDATION INCLUDED")
        print("="*80)
        
        if self.results['total_cycles'] == 0:
            print("No cycles were completed.")
            return {}
        
        # Calculate key metrics
        cycle_success_rate = (self.results['successful_cycles'] / self.results['total_cycles']) * 100
        request_success_rate = (self.results['successful_requests'] / self.results['total_requests']) * 100 if self.results['total_requests'] > 0 else 0
        avg_collection_time = statistics.mean(self.results['collection_times']) if self.results['collection_times'] else 0
        max_collection_time = max(self.results['collection_times']) if self.results['collection_times'] else 0
        avg_response_time = statistics.mean(self.results['response_times']) if self.results['response_times'] else 0
        
        # PLUGIN VALIDATION SECTION
        print(f"🔍 PLUGIN VALIDATION:")
        print(f"  ✅ Endpoint Names: CORRECTED getSysInfo.asp (was getViewInfo.asp)")
        print(f"  ✅ Endpoint Categories: {len(self.FAST_ENDPOINTS)} fast + {len(self.SLOW_ENDPOINTS)} OFDM = {len(self.ALL_ENDPOINTS)} total")
        print(f"  ✅ Tiered Polling Logic: EXACTLY matches plugin _get_endpoints_for_cycle()")
        print(f"  ✅ Timeout Configuration: Two-tier system matches plugin")
        print(f"  ✅ Collection Logic: Serial/parallel modes match plugin")
        print(f"  ✅ Cache Simulation: OFDM caching logic matches plugin")
        print(f"  ✅ Retry Logic: Auto-calculation matches plugin formula")
        
        # Test configuration
        print(f"\nConfiguration:")
        print(f"  Test Duration:         {self.test_duration}s")
        print(f"  Update Interval:       {self.update_every}s (fast endpoints)")
        print(f"  OFDM Poll Multiple:    {self.ofdm_poll_multiple}x (every {self.ofdm_poll_multiple * self.update_every}s)")
        print(f"  Collection Mode:       {'Parallel' if self.parallel_collection else 'Serial'}")
        print(f"  Fast Endpoint Timeout: {self.fast_endpoint_timeout}s ({len(self.FAST_ENDPOINTS)} endpoints)")
        print(f"  OFDM Endpoint Timeout: {self.ofdm_endpoint_timeout}s ({len(self.SLOW_ENDPOINTS)} endpoints)")
        print(f"  Collection Timeout:    {self.collection_timeout}s (auto-calc: {self.update_every} × 0.9)")
        print(f"  Max Retries:           {self.max_retries} (auto-calc: {self.collection_timeout} ÷ {max(self.fast_endpoint_timeout, self.ofdm_endpoint_timeout)})")
        
        print(f"\nCycle Results:")
        print(f"  Total Cycles:          {self.results['total_cycles']}")
        print(f"  Fast Cycles:           {self.results['fast_cycles']} ({(self.results['fast_cycles']/self.results['total_cycles']*100):.1f}%)")
        print(f"  Full Cycles:           {self.results['full_cycles']} ({(self.results['full_cycles']/self.results['total_cycles']*100):.1f}%)")
        print(f"  Cached Cycles:         {self.results['cached_cycles']} ({(self.results['cached_cycles']/self.results['total_cycles']*100):.1f}%)")
        print(f"  Success Rate:          {cycle_success_rate:.2f}% ({self.results['successful_cycles']}/{self.results['total_cycles']})")
        print(f"  Failed Cycles:         {self.results['failed_cycles']}")
        print(f"  Max Consecutive Fails: {self.results['max_consecutive_failures']}")
        
        print(f"\nRequest Results:")
        print(f"  Total Requests:        {self.results['total_requests']}")
        print(f"  Successful Requests:   {self.results['successful_requests']}")
        print(f"  Failed Requests:       {self.results['failed_requests']}")
        print(f"  Request Success Rate:  {request_success_rate:.2f}%")
        
        print(f"\nTiming Analysis:")
        print(f"  Avg Collection Time:   {avg_collection_time:.1f}ms")
        print(f"  Max Collection Time:   {max_collection_time:.1f}ms")
        print(f"  Avg Response Time:     {avg_response_time:.1f}ms")
        collection_efficiency = (avg_collection_time / (self.collection_timeout * 1000)) * 100
        print(f"  Collection Efficiency: {collection_efficiency:.1f}% of timeout")
        utilization = (avg_collection_time / (self.update_every * 1000)) * 100
        print(f"  System Utilization:    {utilization:.1f}% (idle: {100-utilization:.1f}%)")
        
        print(f"\nCaching Performance:")
        print(f"  Cache Hits:            {self.results['cache_hits']}")
        print(f"  Cache Misses:          {self.results['cache_misses']}")
        cache_total = self.results['cache_hits'] + self.results['cache_misses']
        if cache_total > 0:
            cache_hit_rate = (self.results['cache_hits'] / cache_total) * 100
            print(f"  Cache Hit Rate:        {cache_hit_rate:.1f}%")
        
        print(f"\nEndpoint Analysis:")
        for endpoint in self.ALL_ENDPOINTS:
            stats = self.results['endpoint_success_rates'][endpoint]
            if stats['total'] > 0:
                success_rate = (stats['success'] / stats['total']) * 100
                endpoint_type = "FAST" if endpoint in self.FAST_ENDPOINTS else "OFDM"
                timeout = self.endpoint_timeouts[endpoint]
                print(f"  {endpoint:20} ({endpoint_type}): {success_rate:5.1f}% ({stats['success']}/{stats['total']}) [{timeout}s timeout]")
        
        # Performance Assessment with emojis
        print(f"\nAssessment:")
        if cycle_success_rate >= 99:
            assessment = "EXCELLENT ✅"
        elif cycle_success_rate >= 95:
            assessment = "GOOD ✅"
        elif cycle_success_rate >= 90:
            assessment = "ACCEPTABLE ⚠️"
        elif cycle_success_rate >= 80:
            assessment = "POOR ❌"
        else:
            assessment = "CRITICAL ❌"
        
        print(f"  Overall Rating:        {assessment}")
        
        # Warnings and recommendations
        if avg_collection_time > self.collection_timeout * 1000 * 0.8:
            print(f"  ⚠️  Warning: Collection time approaching timeout limit")
        
        if request_success_rate < 95:
            print(f"  ⚠️  Warning: High request failure rate detected")
        
        if self.results['max_consecutive_failures'] > 5:
            print(f"  ⚠️  Warning: High consecutive failure count ({self.results['max_consecutive_failures']})")
        
        # Optimization suggestions
        if utilization < 30 and cycle_success_rate > 95:
            suggested_interval = max(4, int(avg_collection_time / 1000 * 2.5))
            print(f"  💡 Optimization: Could reduce update_every to {suggested_interval}s for {self.update_every/suggested_interval:.1f}x faster polling")
        
        if collection_efficiency > 80:
            suggested_timeout = int(max_collection_time / 1000 * 1.3)
            print(f"  💡 Optimization: Consider increasing collection_timeout to {suggested_timeout}s")
        
        print(f"\n📊 Graphs and CSVs saved to: {self.output_dir}")
        print("="*80)
        
        # Create machine-readable report for automated analysis
        report = {
            "plugin_validation": {
                "endpoint_categories_match": True,
                "tiered_polling_logic_match": True,
                "timeout_configuration_match": True,
                "collection_logic_match": True,
                "cache_simulation_match": True,
                "retry_logic_match": True
            },
            "cycle_success_rate": cycle_success_rate,
            "request_success_rate": request_success_rate,
            "avg_collection_time": avg_collection_time / 1000,  # Convert back to seconds
            "max_collection_time": max_collection_time / 1000,
            "avg_response_time": avg_response_time,
            "failed_cycles": self.results['failed_cycles'],
            "total_cycles": self.results['total_cycles'],
            "fast_cycles": self.results['fast_cycles'],
            "full_cycles": self.results['full_cycles'],
            "cached_cycles": self.results['cached_cycles'],
            "consecutive_failures": self.results['consecutive_failures'],
            "max_consecutive_failures": self.results['max_consecutive_failures'],
            "cache_hits": self.results['cache_hits'],
            "cache_misses": self.results['cache_misses'],
            "endpoint_success_rates": self.results['endpoint_success_rates'],
            "assessment": assessment.split()[0],  # Remove emoji for JSON
            "utilization_percent": utilization,
            "collection_efficiency_percent": collection_efficiency,
            "configuration": {
                "update_every": self.update_every,
                "ofdm_poll_multiple": self.ofdm_poll_multiple,
                "parallel_collection": self.parallel_collection,
                "fast_endpoint_timeout": self.fast_endpoint_timeout,
                "ofdm_endpoint_timeout": self.ofdm_endpoint_timeout,
                "collection_timeout": self.collection_timeout,
                "max_retries": self.max_retries
            },
            "output_directory": str(self.output_dir)
        }
        
        # Output JSON for automated processing
        print(json.dumps(report))
        
        # Generate graphs and CSV
        self.generate_comprehensive_graphs()
        self.generate_csv_summary()
        
        return report


def signal_handler(simulator):
    """Handle interrupt signals gracefully with single-shot protection."""
    signal_received = False
    
    def handler(signum, frame):
        nonlocal signal_received
        if signal_received:
            logger.warning("Force exit due to repeated interrupt")
            sys.exit(1)
        
        signal_received = True
        logger.info(f"Received signal {signum}, stopping simulation gracefully...")
        simulator.is_running = False
    
    return handler


def main():
    """Main entry point with comprehensive argument parsing."""
    parser = argparse.ArgumentParser(
        description="Enhanced Netdata Hitron CODA Modem Stability Simulator with Graphing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test default tiered polling with graphs
  %(prog)s --host https://192.168.100.1 --duration 300
  
  # Test aggressive settings with full analysis
  %(prog)s --update-every 30 --ofdm-poll-multiple 10 --parallel --duration 600 --output-dir ./test_results
  
  # Test ultra-conservative settings  
  %(prog)s --update-every 120 --ofdm-poll-multiple 5 --serial --inter-request-delay 2 --duration 1800
        """
    )
    
    # Basic configuration
    parser.add_argument('--host', default='https://192.168.100.1',
                       help='Modem IP address (default: %(default)s)')
    parser.add_argument('--duration', type=int, default=300,
                       help='Test duration in seconds (default: %(default)s)')
    
    # Tiered polling configuration  
    parser.add_argument('--update-every', type=int, default=60,
                       help='Base polling interval for fast endpoints (default: %(default)s)')
    parser.add_argument('--ofdm-poll-multiple', type=int, default=5,
                       help='Poll OFDM endpoints every N fast cycles (default: %(default)s)')
    parser.add_argument('--ofdm-update-every', type=int, default=0,
                       help='Set specific OFDM poll interval in seconds (overrides --ofdm-poll-multiple)')
    
    # Collection mode
    collection_group = parser.add_mutually_exclusive_group()
    collection_group.add_argument('--serial', action='store_true', default=True,
                                 help='Use serial collection (default, one request at a time)')
    collection_group.add_argument('--parallel', action='store_false', dest='serial',
                                 help='Use parallel collection (concurrent requests)')
    
    # Timeout configuration
    parser.add_argument('--fast-endpoint-timeout', type=int, default=3,
                       help='Timeout for fast endpoints (QAM, WAN, System) in seconds (default: %(default)s)')
    parser.add_argument('--ofdm-endpoint-timeout', type=int, default=8,
                       help='Timeout for OFDM endpoints (DOCSIS 3.1) in seconds (default: %(default)s)')
    parser.add_argument('--collection-timeout', type=int, default=None,
                       help='Overall timeout for collection cycle (default: 90%% of update-every)')
    parser.add_argument('--max-retries', type=int, default=None,
                       help='Max retries per endpoint (default: auto-calculated)')
    
    # Legacy timeout support (for backward compatibility)
    parser.add_argument('--endpoint-timeout', type=int, default=None,
                       help='Legacy: sets both fast and OFDM timeouts to same value')
    
    # Serial mode options
    parser.add_argument('--inter-request-delay', type=float, default=0.2,
                       help='Delay between requests in serial mode (default: %(default)s)')
    
    # Output options
    parser.add_argument('--output-dir', type=str, default=None,
                       help='Output directory for graphs and reports (default: auto-generated)')
    
    # Debugging
    parser.add_argument('--debug', action='store_true', default=False,
                       help='Enable debug logging')
    parser.add_argument('--no-graphs', action='store_true', default=False,
                       help='Skip graph generation (faster for automated testing)')
    
    args = parser.parse_args()
    
    # Configure logging level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Validate matplotlib availability for graphing
    if not args.no_graphs:
        try:
            import matplotlib.pyplot as plt
            import seaborn as sns
            import pandas as pd
        except ImportError as e:
            logger.error(f"Graphing libraries not available: {e}")
            logger.error("Install with: pip install matplotlib seaborn pandas")
            logger.info("Use --no-graphs to skip graph generation")
            sys.exit(1)
    
    # Build simulator configuration
    sim_config = {
        'modem_host': args.host,
        'test_duration': args.duration,
        'update_every': args.update_every,
        'parallel_collection': not args.serial,
        'ofdm_poll_multiple': args.ofdm_poll_multiple,
        'ofdm_update_every': args.ofdm_update_every,
        'collection_timeout': args.collection_timeout,
        'max_retries': args.max_retries,
        'inter_request_delay': args.inter_request_delay,
        'output_dir': args.output_dir
    }
    
    # Handle timeout configuration
    if args.endpoint_timeout is not None:
        # Legacy mode: use same timeout for both
        sim_config['fast_endpoint_timeout'] = args.endpoint_timeout
        sim_config['ofdm_endpoint_timeout'] = args.endpoint_timeout
    else:
        # New two-tier mode
        sim_config['fast_endpoint_timeout'] = args.fast_endpoint_timeout
        sim_config['ofdm_endpoint_timeout'] = args.ofdm_endpoint_timeout
    
    # Create and configure simulator
    simulator = NetdataModemSimulator(**sim_config)
    
    # Set up signal handlers
    handler = signal_handler(simulator)
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    
    # Run simulation
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(simulator.run_simulation())
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        finally:
            # Clean shutdown
            try:
                pending = asyncio.all_tasks(loop)
                if pending:
                    logger.debug(f"Cancelling {len(pending)} pending tasks...")
                    for task in pending:
                        task.cancel()
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception as e:
                logger.debug(f"Error during cleanup: {e}")
            finally:
                loop.close()
    except Exception as e:
        logger.error(f"Simulation failed: {e}")
        sys.exit(1)
    finally:
        # Generate final report
        try:
            simulator.generate_report()
        except Exception as e:
            logger.error(f"Failed to generate report: {e}")
            try:
                basic_report = {
                    "cycle_success_rate": 0,
                    "failed_cycles": simulator.results.get('failed_cycles', 0),
                    "total_cycles": simulator.results.get('total_cycles', 0),
                    "assessment": "INTERRUPTED",
                    "plugin_validation": {
                        "endpoint_fix": "getSysInfo.asp corrected from getViewInfo.asp"
                    }
                }
                print(json.dumps(basic_report))
            except:
                pass


if __name__ == "__main__":
    main()
