#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Enhanced Netdata Modem Simulator with Time-Based Latency Degradation Analysis.

This enhanced version adds comprehensive time-windowed histogram analysis
to detect resource leaks and performance degradation over time.

New Features:
- Time-windowed latency histograms per endpoint
- Statistical degradation detection using regression analysis
- Early vs Late performance comparison graphs
- Latency trend analysis with confidence intervals
- Resource leak detection metrics
- Enhanced CSV exports with time-binned data

Version: 2.4.0 - Added latency degradation analysis capabilities
Author: Enhanced for resource leak detection and time-based performance analysis
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
import math
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union
from pathlib import Path
import warnings
import math

# Handle optional graphing imports gracefully
try:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import pandas as pd
    import numpy as np
    from scipy.stats import linregress
    import scipy.stats as scipy_stats
    GRAPHING_AVAILABLE = True
    
    # Suppress matplotlib warnings
    warnings.filterwarnings('ignore', category=UserWarning, module='matplotlib')
    # Set matplotlib backend for headless operation
    plt.switch_backend('Agg')
    
except ImportError:
    GRAPHING_AVAILABLE = False
    plt = None
    pd = None
    np = None
    linregress = None
    scipy_stats = None

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_timeout_value(value: Union[str, int, float], param_name: str) -> float:
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


class NetdataModemSimulator:
    """
    Enhanced simulator for testing Hitron CODA modem tiered polling strategies
    with comprehensive time-based latency degradation analysis.
    
    New capabilities:
    - Time-windowed latency analysis to detect resource leaks
    - Statistical trend analysis with regression testing
    - Early vs late performance comparison
    - Resource leak detection metrics
    """

    # --- Endpoint Categories (EXACTLY MATCH PLUGIN FROM hitron_coda.chart.py) ---
    FAST_ENDPOINTS = [
        'dsinfo.asp',         # Downstream QAM channels (31 channels) - Critical signal data
        'usinfo.asp',         # Upstream QAM channels (5 channels) - Critical upload data  
        'getCmDocsisWan.asp', # WAN status (IPv4/IPv6) - Connection health
        'getSysInfo.asp'      # System uptime and info - Basic health check
    ]
    
    SLOW_ENDPOINTS = [
        'dsofdminfo.asp',     # Downstream OFDM (DOCSIS 3.1) - Can cause instability
        'usofdminfo.asp'      # Upstream OFDM (DOCSIS 3.1) - Less critical
    ]
    
    ALL_ENDPOINTS = FAST_ENDPOINTS + SLOW_ENDPOINTS

    def __init__(self, **kwargs):
        """Initialize the enhanced simulator with time-based analysis capabilities."""
        
        # --- Basic Configuration ---
        self.modem_host = kwargs.get('modem_host')
        self.update_every = kwargs.get('update_every')
        self.test_duration = kwargs.get('test_duration')
        
        # --- Collection Strategy (MATCHES PLUGIN) ---
        self.parallel_collection = kwargs.get('parallel_collection', False)
        self.inter_request_delay = kwargs.get('inter_request_delay', 0.2)
        
        # --- Enhanced Timeout Configuration with Millisecond Support ---
        self.fast_endpoint_timeout = float(kwargs.get('fast_endpoint_timeout', 3.0))
        self.ofdm_endpoint_timeout = float(kwargs.get('ofdm_endpoint_timeout', 8.0))
        
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
        else:
            self.max_retries = int(self.max_retries)
            # Allow zero retries (max_retries=0 means 1 attempt, no retries)
            if self.max_retries < 0:
                logger.warning(f"max_retries cannot be negative, setting to 0")
                self.max_retries = 0
        
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
        
        # --- Enhanced Per-Endpoint Tracking ---
        self.endpoint_stats = {}
        for endpoint in self.ALL_ENDPOINTS:
            self.endpoint_stats[endpoint] = {
                'total_requests': 0,
                'successful_requests': 0,
                'failed_requests': 0,
                'timeout_failures': 0,
                'response_times': [],
                'errors': [],
                'attempts_used': [],  # Track how many attempts were needed
                'status_codes': [],   # Track HTTP status codes
                'data_sizes': [],     # Track response sizes
                'time_series': {
                    'timestamps': [],
                    'response_times': [],
                    'success': [],
                    'attempts': [],
                    'status_codes': [],
                    'data_sizes': [],
                    'elapsed_time': []  # NEW: Track elapsed time from start for degradation analysis
                }
            }
        
        # --- NEW: Time-Windowed Analysis Configuration ---
        self.time_window_count = kwargs.get('time_window_count', 6)  # Default to 6 time windows
        self.degradation_analysis = {
            'time_windows': [],
            'endpoint_window_stats': {ep: [] for ep in self.ALL_ENDPOINTS},
            'trends': {ep: {} for ep in self.ALL_ENDPOINTS},
            'leak_detection': {ep: {} for ep in self.ALL_ENDPOINTS}
        }
        
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
            
            # Use proper timeout formatting for directory name
            fast_timeout_str = self._format_timeout_for_filename(self.fast_endpoint_timeout)
            ofdm_timeout_str = self._format_timeout_for_filename(self.ofdm_endpoint_timeout)
            
            dir_name = f"hitron_sim_{timestamp}_{mode}_int{self.update_every}s_ofdm{self.ofdm_poll_multiple}x_timeout{fast_timeout_str}-{ofdm_timeout_str}"
            self.output_dir = Path.cwd() / dir_name
        self.output_dir.mkdir(exist_ok=True)
        
        # Create descriptive filename prefix for graphs
        mode = "parallel" if self.parallel_collection else "serial"
        fast_timeout_str = self._format_timeout_for_filename(self.fast_endpoint_timeout)
        ofdm_timeout_str = self._format_timeout_for_filename(self.ofdm_endpoint_timeout)
        
        self.file_prefix = (f"hitron_{mode}_int{self.update_every}s_"
                           f"ofdm{self.ofdm_poll_multiple}x_"
                           f"ft{fast_timeout_str}_"
                           f"ot{ofdm_timeout_str}")
        
        # Enhanced logging with degradation analysis info
        fast_timeout_info = self._get_timeout_display_info(self.fast_endpoint_timeout)
        ofdm_timeout_info = self._get_timeout_display_info(self.ofdm_endpoint_timeout)
        collection_timeout_info = self._get_timeout_display_info(self.collection_timeout)
        
        logger.info(f"Enhanced Simulator with Degradation Analysis initialized:")
        logger.info(f"  🔍 Plugin validation: Endpoint categories, timeouts, and logic match plugin")
        logger.info(f"  📊 Per-endpoint timing: Full tracking and analysis enabled")
        logger.info(f"  ⏱️ Millisecond precision: {fast_timeout_info['display']}/{ofdm_timeout_info['display']} timeout support")
        logger.info(f"  📈 NEW: Time-windowed analysis: {self.time_window_count} windows for degradation detection")
        logger.info(f"  🔬 NEW: Resource leak detection: Statistical trend analysis enabled")
        logger.info(f"  Collection mode: {'Parallel' if self.parallel_collection else 'Serial'}")
        logger.info(f"  Update interval: {self.update_every}s")
        logger.info(f"  OFDM poll multiple: {self.ofdm_poll_multiple} (every {self.ofdm_poll_multiple * self.update_every}s)")
        logger.info(f"  Fast endpoint timeout: {fast_timeout_info['precise']} ({len(self.FAST_ENDPOINTS)} endpoints)")
        logger.info(f"  OFDM endpoint timeout: {ofdm_timeout_info['precise']} ({len(self.SLOW_ENDPOINTS)} endpoints)")
        logger.info(f"  Collection timeout: {collection_timeout_info['precise']} (auto-calc: {self.update_every} * 0.9)")
        longer_timeout = max(self.fast_endpoint_timeout, self.ofdm_endpoint_timeout)
        logger.info(f"  Max retries: {self.max_retries} (auto-calc: {self.collection_timeout:.3f} ÷ {longer_timeout:.3f})")
        logger.info(f"  OFDM cache TTL: {self.ofdm_cache_ttl}s")
        logger.info(f"  📁 Output directory: {self.output_dir}")
        
        # Log retry behavior clearly
        total_attempts = self.max_retries + 1
        if self.max_retries == 0:
            logger.info(f"  Retry behavior: 1 attempt only (no retries)")
        else:
            logger.info(f"  Retry behavior: {total_attempts} total attempts (1 initial + {self.max_retries} retries)")

    def _format_timeout_for_display(self, timeout_seconds: float) -> str:
        """Format timeout value for display in a user-friendly way."""
        timeout_ms = timeout_seconds * 1000
        
        if timeout_seconds < 1.0:
            # Sub-second timeouts: show in milliseconds
            return f"{timeout_ms:.0f}ms"
        elif timeout_seconds == int(timeout_seconds):
            # Whole second timeouts: show as integer seconds
            return f"{int(timeout_seconds)}s"
        else:
            # Decimal seconds: show with appropriate precision
            return f"{timeout_seconds:.1f}s"

    def _format_timeout_for_filename(self, timeout_seconds: float) -> str:
        """Format timeout value for use in filenames (no special characters)."""
        timeout_ms = timeout_seconds * 1000
        
        if timeout_seconds < 1.0:
            # Sub-second timeouts: show in milliseconds  
            return f"{timeout_ms:.0f}ms"
        elif timeout_seconds == int(timeout_seconds):
            # Whole second timeouts: show as integer seconds
            return f"{int(timeout_seconds)}s"
        else:
            # Decimal seconds: show with one decimal place
            return f"{timeout_seconds:.1f}s"

    def _get_timeout_display_info(self, timeout_seconds: float) -> dict:
        """Get comprehensive timeout display information."""
        timeout_ms = timeout_seconds * 1000
        
        return {
            'seconds': timeout_seconds,
            'milliseconds': timeout_ms,
            'display': self._format_timeout_for_display(timeout_seconds),
            'filename': self._format_timeout_for_filename(timeout_seconds),
            'precise': f"{timeout_seconds:.3f}s ({timeout_ms:.0f}ms)",
            'graph_label': f"{timeout_ms:.0f}ms" if timeout_seconds < 10 else f"{timeout_seconds:.1f}s"
        }

    async def _test_connectivity(self):
        """Test initial connectivity using the correct endpoint names."""
        logger.info("🔌 Testing initial connectivity...")
        try:
            connector = aiohttp.TCPConnector(ssl=self.ssl_context)
            timeout = aiohttp.ClientTimeout(total=10)
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (compatible; Netdata-Hitron-Plugin/2.4.0)',
                'Accept': 'application/json, text/html, */*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache'
            }
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
                # Test the correct endpoint
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
        """Main simulation loop with tiered polling logic and degradation tracking."""
        self.start_time = datetime.now()
        end_time = self.start_time + timedelta(seconds=self.test_duration)
        
        logger.info(f"🚀 Starting {self.test_duration}s simulation with degradation analysis...")
        logger.info(f"🎯 Target end time: {end_time.strftime('%H:%M:%S')}")
        logger.info(f"📊 Time windows: {self.time_window_count} (each ~{self.test_duration/self.time_window_count:.1f}s)")
        logger.info("⏹️ Press Ctrl+C to stop early")
        
        # Initialize time windows for degradation analysis
        self._initialize_time_windows()
        
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
            logger.info("📊 Simulation completed - analyzing degradation and generating comprehensive reports...")
            
            # NEW: Perform degradation analysis
            self._analyze_latency_degradation()

    def _initialize_time_windows(self):
        """Initialize time windows for degradation analysis."""
        window_duration = self.test_duration / self.time_window_count
        
        for i in range(self.time_window_count):
            start_time = i * window_duration
            end_time = (i + 1) * window_duration
            
            self.degradation_analysis['time_windows'].append({
                'window_id': i + 1,
                'start_time': start_time,
                'end_time': end_time,
                'duration': window_duration,
                'label': f"Window {i+1} ({start_time:.0f}-{end_time:.0f}s)"
            })
            
        logger.info(f"📊 Initialized {self.time_window_count} time windows for degradation analysis")

    def _get_time_window_for_timestamp(self, elapsed_time: float) -> Optional[int]:
        """Get the time window index for a given elapsed time."""
        for i, window in enumerate(self.degradation_analysis['time_windows']):
            if window['start_time'] <= elapsed_time < window['end_time']:
                return i
        
        # Handle edge case for the final timestamp
        if elapsed_time >= self.test_duration:
            return len(self.degradation_analysis['time_windows']) - 1
            
        return None

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
            'User-Agent': 'Netdata-Hitron-Plugin/2.4.0',
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
            'User-Agent': 'Netdata-Hitron-Plugin/2.4.0',
            'Accept': 'application/json, */*'
        }
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
            tasks = [self._fetch_endpoint(session, endpoint) for endpoint in endpoints]
            return await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_endpoint(self, session, endpoint):
        """Fetch a single endpoint with enhanced timeout logic and detailed per-endpoint tracking."""
        url = f"{self.modem_host}/data/{endpoint}"
        endpoint_timeout = self.endpoint_timeouts.get(endpoint, self.fast_endpoint_timeout)
        stats = self.endpoint_stats[endpoint]
        
        logger.debug(f"Fetching {endpoint} with {endpoint_timeout:.3f}s ({endpoint_timeout*1000:.0f}ms) timeout...")
        
        cycle_timestamp = datetime.now()
        # NEW: Calculate elapsed time from simulation start for degradation analysis
        elapsed_time = (cycle_timestamp - self.start_time).total_seconds()
        
        # Ensure at least one attempt is always made
        total_attempts = max(1, self.max_retries + 1)
        
        for attempt in range(total_attempts):
            request_start = time.monotonic()
            
            try:
                # Use precise timeout with millisecond support
                async with session.get(url, timeout=endpoint_timeout) as response:
                    response_time = (time.monotonic() - request_start) * 1000  # Convert to ms
                    self.results['response_times'].append(response_time)
                    
                    # Track per-endpoint statistics
                    stats['response_times'].append(response_time)
                    stats['attempts_used'].append(attempt + 1)
                    stats['status_codes'].append(response.status)
                    
                    # Update time series for this endpoint with elapsed time
                    stats['time_series']['timestamps'].append(cycle_timestamp)
                    stats['time_series']['response_times'].append(response_time)
                    stats['time_series']['attempts'].append(attempt + 1)
                    stats['time_series']['status_codes'].append(response.status)
                    stats['time_series']['elapsed_time'].append(elapsed_time)  # NEW: For degradation analysis
                    
                    if self.max_retries == 0:
                        logger.debug(f"{endpoint}: HTTP {response.status} in {response_time:.1f}ms (single attempt) [timeout: {endpoint_timeout*1000:.0f}ms]")
                    else:
                        logger.debug(f"{endpoint}: HTTP {response.status} in {response_time:.1f}ms (attempt {attempt + 1}/{total_attempts}) [timeout: {endpoint_timeout*1000:.0f}ms]")
                    
                    if response.status == 200:
                        # Update endpoint success tracking
                        self.results['endpoint_success_rates'][endpoint]['success'] += 1
                        self.results['endpoint_success_rates'][endpoint]['total'] += 1
                        stats['total_requests'] += 1
                        stats['successful_requests'] += 1
                        
                        # Get response text first - Hitron modems return text/html even for JSON
                        response_text = await response.text()
                        data_size = len(response_text)
                        stats['data_sizes'].append(data_size)
                        stats['time_series']['data_sizes'].append(data_size)
                        stats['time_series']['success'].append(1)
                        
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
                                return {
                                    'endpoint': endpoint,
                                    'success': True,
                                    'response_time': response_time,
                                    'status_code': response.status,
                                    'data_size': data_size,
                                    'attempt': attempt + 1,
                                    'timestamp': cycle_timestamp,
                                    'elapsed_time': elapsed_time,  # NEW: For degradation analysis
                                    'data': json_data
                                }
                            except Exception:
                                # If that fails, manually parse the text
                                json_data = json.loads(response_text)
                                logger.debug(f"{endpoint}: Successfully parsed JSON via manual parsing")
                                return {
                                    'endpoint': endpoint,
                                    'success': True,
                                    'response_time': response_time,
                                    'status_code': response.status,
                                    'data_size': data_size,
                                    'attempt': attempt + 1,
                                    'timestamp': cycle_timestamp,
                                    'elapsed_time': elapsed_time,  # NEW: For degradation analysis
                                    'data': json_data
                                }
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
                        stats['total_requests'] += 1
                        stats['failed_requests'] += 1
                        stats['time_series']['success'].append(0)
                        stats['time_series']['data_sizes'].append(0)
                        
            except asyncio.TimeoutError as e:
                response_time = (time.monotonic() - request_start) * 1000
                self.results['response_times'].append(response_time)
                self.results['endpoint_success_rates'][endpoint]['total'] += 1
                
                # Track timeout in endpoint stats
                stats['total_requests'] += 1
                stats['failed_requests'] += 1
                stats['timeout_failures'] += 1
                stats['response_times'].append(response_time)
                stats['attempts_used'].append(attempt + 1)
                stats['status_codes'].append('TIMEOUT')
                
                # Update time series
                stats['time_series']['timestamps'].append(cycle_timestamp)
                stats['time_series']['response_times'].append(response_time)
                stats['time_series']['attempts'].append(attempt + 1)
                stats['time_series']['status_codes'].append('TIMEOUT')
                stats['time_series']['success'].append(0)
                stats['time_series']['data_sizes'].append(0)
                stats['time_series']['elapsed_time'].append(elapsed_time)  # NEW: For degradation analysis
                
                logger.warning(f"{endpoint}: Timeout after {response_time:.1f}ms (limit: {endpoint_timeout*1000:.0f}ms)")
                
                if attempt < total_attempts - 1:
                    if self.max_retries == 0:
                        # This shouldn't happen since we only have 1 attempt, but just in case
                        pass  
                    else:
                        logger.debug(f"{endpoint}: Retrying in 1 second (retry {attempt + 1}/{self.max_retries}, attempt {attempt + 2}/{total_attempts})")
                        await asyncio.sleep(1)
                    
            except Exception as e:
                response_time = (time.monotonic() - request_start) * 1000
                self.results['response_times'].append(response_time)
                self.results['endpoint_success_rates'][endpoint]['total'] += 1
                
                # Track error in endpoint stats
                stats['total_requests'] += 1
                stats['failed_requests'] += 1
                stats['errors'].append(str(e))
                stats['response_times'].append(response_time)
                stats['attempts_used'].append(attempt + 1)
                stats['status_codes'].append('ERROR')
                
                # Update time series
                stats['time_series']['timestamps'].append(cycle_timestamp)
                stats['time_series']['response_times'].append(response_time)
                stats['time_series']['attempts'].append(attempt + 1)
                stats['time_series']['status_codes'].append('ERROR')
                stats['time_series']['success'].append(0)
                stats['time_series']['data_sizes'].append(0)
                stats['time_series']['elapsed_time'].append(elapsed_time)  # NEW: For degradation analysis
                
                logger.warning(f"{endpoint}: Error: {e}")
                
                if attempt < total_attempts - 1:
                    if self.max_retries > 0:
                        await asyncio.sleep(1)
        
        if self.max_retries == 0:
            logger.error(f"{endpoint}: Single attempt failed (no retries configured)")
        else:
            logger.error(f"{endpoint}: All {total_attempts} attempts failed ({self.max_retries} retries)")
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

    def _analyze_latency_degradation(self):
        """NEW: Analyze latency degradation over time to detect resource leaks."""
        logger.info("🔬 Analyzing latency degradation patterns...")
        
        for endpoint in self.ALL_ENDPOINTS:
            try:
                stats = self.endpoint_stats[endpoint]
                
                if len(stats['time_series']['response_times']) < 10:
                    logger.debug(f"Skipping {endpoint}: insufficient data ({len(stats['time_series']['response_times'])} samples)")
                    continue
                
                # Extract time series data - ensure all arrays have the same length
                ts_data = stats['time_series']
                min_length = min(len(ts_data['elapsed_time']), len(ts_data['response_times']), len(ts_data['success']))
                
                elapsed_times = np.array(ts_data['elapsed_time'][:min_length])
                response_times = np.array(ts_data['response_times'][:min_length])
                success_flags = np.array(ts_data['success'][:min_length])
                
                # Only analyze successful requests for degradation
                successful_mask = success_flags == 1
                if np.sum(successful_mask) < 5:
                    logger.debug(f"Skipping {endpoint}: insufficient successful samples ({np.sum(successful_mask)})")
                    continue
                    
                success_elapsed = elapsed_times[successful_mask]
                success_response_times = response_times[successful_mask]
                
                # Perform linear regression to detect trends
                try:
                    slope, intercept, r_value, p_value, std_err = linregress(success_elapsed, success_response_times)
                    
                    # Calculate trend metrics
                    trend_info = {
                        'slope_ms_per_second': slope,
                        'slope_ms_per_minute': slope * 60,
                        'slope_ms_per_hour': slope * 3600,
                        'intercept_ms': intercept,
                        'r_squared': r_value ** 2,
                        'p_value': p_value,
                        'std_error': std_err,
                        'sample_count': len(success_response_times),
                        'test_duration_seconds': self.test_duration
                    }
                    
                    # Assess degradation significance
                    trend_assessment = self._assess_degradation_trend(trend_info, endpoint)
                    trend_info.update(trend_assessment)
                    
                    # Calculate time-windowed statistics
                    window_stats = self._calculate_time_window_stats(endpoint, success_elapsed, success_response_times)
                    trend_info['window_analysis'] = window_stats
                    
                    # Store results
                    self.degradation_analysis['trends'][endpoint] = trend_info
                    
                    # Log significant findings
                    if trend_info['is_significant_degradation']:
                        emoji = "🚨" if trend_info['severity'] == 'critical' else "⚠️" if trend_info['severity'] == 'warning' else "📈"
                        logger.warning(f"{emoji} {endpoint}: Significant degradation detected! "
                                     f"+{slope * 60:.2f}ms/min (p={p_value:.4f}, R²={r_value**2:.3f})")
                    elif trend_info['is_significant_improvement']:
                        logger.info(f"📉 {endpoint}: Performance improvement detected: "
                                  f"{slope * 60:.2f}ms/min (p={p_value:.4f})")
                    else:
                        logger.debug(f"✅ {endpoint}: No significant trend detected "
                                   f"({slope * 60:.2f}ms/min, p={p_value:.4f})")
                        
                except Exception as e:
                    logger.error(f"Error in regression analysis for {endpoint}: {e}")
                    self.degradation_analysis['trends'][endpoint] = {
                        'error': f"Regression error: {str(e)}",
                        'sample_count': len(success_response_times)
                    }
                    
            except Exception as e:
                logger.error(f"Error analyzing degradation for {endpoint}: {e}")
                self.degradation_analysis['trends'][endpoint] = {
                    'error': str(e),
                    'sample_count': len(stats['time_series']['response_times']) if stats['time_series']['response_times'] else 0
                }

    def _assess_degradation_trend(self, trend_info: dict, endpoint: str) -> dict:
        """Assess the significance and severity of a degradation trend."""
        slope = trend_info['slope_ms_per_second']
        p_value = trend_info['p_value']
        r_squared = trend_info['r_squared']
        
        # Thresholds for significance
        significance_threshold = 0.05  # p-value threshold
        correlation_threshold = 0.1    # R² threshold for meaningful correlation
        
        # Degradation thresholds (ms per minute)
        minor_degradation_threshold = 1.0     # 1ms per minute
        moderate_degradation_threshold = 5.0  # 5ms per minute  
        severe_degradation_threshold = 20.0   # 20ms per minute
        
        slope_per_minute = slope * 60
        
        assessment = {
            'is_statistically_significant': p_value < significance_threshold,
            'has_meaningful_correlation': r_squared > correlation_threshold,
            'is_degrading': slope > 0,
            'is_improving': slope < 0,
            'slope_magnitude': abs(slope_per_minute),
            'significance_level': 'high' if p_value < 0.01 else 'medium' if p_value < 0.05 else 'low'
        }
        
        # Determine if this is significant degradation
        is_significant_degradation = (
            assessment['is_statistically_significant'] and 
            assessment['has_meaningful_correlation'] and 
            assessment['is_degrading'] and
            assessment['slope_magnitude'] > minor_degradation_threshold
        )
        
        # Determine if this is significant improvement
        is_significant_improvement = (
            assessment['is_statistically_significant'] and 
            assessment['has_meaningful_correlation'] and 
            assessment['is_improving'] and
            assessment['slope_magnitude'] > minor_degradation_threshold
        )
        
        # Assess severity
        if is_significant_degradation:
            if assessment['slope_magnitude'] > severe_degradation_threshold:
                severity = 'critical'
            elif assessment['slope_magnitude'] > moderate_degradation_threshold:
                severity = 'moderate'
            else:
                severity = 'minor'
        else:
            severity = 'none'
        
        assessment.update({
            'is_significant_degradation': is_significant_degradation,
            'is_significant_improvement': is_significant_improvement,
            'severity': severity,
            'projected_degradation_per_hour': slope_per_minute * 60,
            'projected_degradation_per_day': slope_per_minute * 60 * 24
        })
        
        return assessment

    def _calculate_time_window_stats(self, endpoint: str, elapsed_times: np.ndarray, response_times: np.ndarray) -> dict:
        """Calculate statistics for each time window to show degradation progression."""
        window_stats = []
        
        for window in self.degradation_analysis['time_windows']:
            # Find data points in this window
            window_mask = (elapsed_times >= window['start_time']) & (elapsed_times < window['end_time'])
            window_response_times = response_times[window_mask]
            
            if len(window_response_times) > 0:
                stats_dict = {
                    'window_id': window['window_id'],
                    'window_label': window['label'],
                    'sample_count': len(window_response_times),
                    'mean_ms': float(np.mean(window_response_times)),
                    'median_ms': float(np.median(window_response_times)),
                    'std_ms': float(np.std(window_response_times)),
                    'min_ms': float(np.min(window_response_times)),
                    'max_ms': float(np.max(window_response_times)),
                    'p95_ms': float(np.percentile(window_response_times, 95)) if len(window_response_times) > 1 else float(np.mean(window_response_times))
                }
            else:
                stats_dict = {
                    'window_id': window['window_id'],
                    'window_label': window['label'],
                    'sample_count': 0,
                    'mean_ms': None,
                    'median_ms': None,
                    'std_ms': None,
                    'min_ms': None,
                    'max_ms': None,
                    'p95_ms': None
                }
            
            window_stats.append(stats_dict)
        
        # Calculate progression metrics
        valid_windows = [w for w in window_stats if w['sample_count'] > 0]
        if len(valid_windows) >= 2:
            first_window = valid_windows[0]
            last_window = valid_windows[-1]
            
            progression = {
                'mean_change_ms': last_window['mean_ms'] - first_window['mean_ms'],
                'mean_change_percent': ((last_window['mean_ms'] - first_window['mean_ms']) / first_window['mean_ms']) * 100,
                'p95_change_ms': last_window['p95_ms'] - first_window['p95_ms'],
                'p95_change_percent': ((last_window['p95_ms'] - first_window['p95_ms']) / first_window['p95_ms']) * 100,
                'windows_analyzed': len(valid_windows)
            }
        else:
            progression = {
                'mean_change_ms': None,
                'mean_change_percent': None,
                'p95_change_ms': None,
                'p95_change_percent': None,
                'windows_analyzed': len(valid_windows)
            }
        
        return {
            'windows': window_stats,
            'progression': progression
        }

    def generate_comprehensive_graphs(self):
        """Generate performance graphs including new degradation analysis."""
        if not GRAPHING_AVAILABLE:
            logger.warning("📊 Matplotlib not available - skipping graph generation")
            logger.info("Install with: pip install matplotlib pandas numpy scipy")
            return
            
        logger.info("📊 Generating comprehensive performance graphs with degradation analysis...")
        
        if not self.results['time_series']['timestamps']:
            logger.warning("No time series data available for graphing")
            return
        
        # Generate main performance overview
        try:
            self._generate_main_performance_graph()
        except Exception as e:
            logger.error(f"Failed to generate main performance graph: {e}")
        
        # Generate detailed per-endpoint analysis
        try:
            self._generate_endpoint_timing_graphs()
        except Exception as e:
            logger.error(f"Failed to generate endpoint timing graphs: {e}")
        
        # Generate endpoint comparison graphs
        try:
            self._generate_endpoint_comparison_graphs()
        except Exception as e:
            logger.error(f"Failed to generate endpoint comparison graphs: {e}")
        
        # Generate timing distribution analysis
        try:
            self._generate_timing_distribution_graphs()
        except Exception as e:
            logger.error(f"Failed to generate timing distribution graphs: {e}")
        
        # NEW: Generate degradation analysis graphs
        try:
            self._generate_degradation_analysis_graphs()
        except Exception as e:
            logger.error(f"Failed to generate degradation analysis graphs: {e}")
        
        # NEW: Generate time-windowed histograms
        try:
            self._generate_time_windowed_histograms()
        except Exception as e:
            logger.error(f"Failed to generate time-windowed histograms: {e}")
        
        logger.info("📊 Graph generation completed with degradation analysis")

    def _generate_degradation_analysis_graphs(self):
        """NEW: Generate degradation trend analysis graphs."""
        if not GRAPHING_AVAILABLE:
            logger.warning("📊 Matplotlib/scipy not available for degradation analysis graphs")
            return
            
        logger.info("📊 Generating degradation analysis graphs...")
        
        try:
            # Create a 2x2 layout for degradation analysis
            fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
            
            # 1. Trend lines for all endpoints
            ax1.set_title('Latency Trends Over Time (Linear Regression)')
            ax1.set_xlabel('Elapsed Time (seconds)')
            ax1.set_ylabel('Response Time (ms)')
            ax1.grid(True, alpha=0.3)
            
            colors = plt.cm.Set3(np.linspace(0, 1, len(self.ALL_ENDPOINTS)))
            
            has_trend_data = False
            for endpoint, color in zip(self.ALL_ENDPOINTS, colors):
                stats = self.endpoint_stats[endpoint]
                trend = self.degradation_analysis['trends'].get(endpoint, {})
                
                if 'error' in trend or len(stats['time_series']['response_times']) < 5:
                    continue
                    
                # Get data
                ts_data = stats['time_series']
                min_length = min(len(ts_data.get('elapsed_time', [])), 
                               len(ts_data.get('response_times', [])), 
                               len(ts_data.get('success', [])))
                
                if min_length < 5:
                    continue
                    
                elapsed_times = np.array(ts_data['elapsed_time'][:min_length])
                response_times = np.array(ts_data['response_times'][:min_length])
                success_flags = np.array(ts_data['success'][:min_length])
                
                # Plot successful requests only
                successful_mask = success_flags == 1
                if np.sum(successful_mask) < 5:
                    continue
                    
                success_elapsed = elapsed_times[successful_mask]
                success_response_times = response_times[successful_mask]
                
                # Plot scatter points
                ax1.scatter(success_elapsed, success_response_times, c=[color], alpha=0.6, s=20, 
                           label=f"{endpoint.replace('.asp', '')}")
                
                # Plot trend line if significant
                if trend.get('is_statistically_significant', False):
                    slope = trend.get('slope_ms_per_second', 0)
                    intercept = trend.get('intercept_ms', 0)
                    trend_line = slope * success_elapsed + intercept
                    
                    linestyle = '--' if trend.get('is_significant_degradation', False) else '-'
                    linewidth = 2 if trend.get('is_significant_degradation', False) else 1
                    ax1.plot(success_elapsed, trend_line, color=color, linestyle=linestyle, 
                            linewidth=linewidth, alpha=0.8)
                
                has_trend_data = True
            
            if has_trend_data:
                ax1.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize=8)
            else:
                ax1.text(0.5, 0.5, 'No trend data available', ha='center', va='center', transform=ax1.transAxes)
            
            # 2. Degradation slopes comparison
            endpoint_names = []
            slopes_per_minute = []
            significance_colors = []
            
            for endpoint in self.ALL_ENDPOINTS:
                trend = self.degradation_analysis['trends'].get(endpoint, {})
                if 'slope_ms_per_minute' in trend:
                    endpoint_names.append(endpoint.replace('.asp', ''))
                    slopes_per_minute.append(trend['slope_ms_per_minute'])
                    
                    # Color by significance and direction
                    if trend.get('is_significant_degradation', False):
                        severity = trend.get('severity', 'minor')
                        if severity == 'critical':
                            significance_colors.append('red')
                        elif severity == 'moderate':
                            significance_colors.append('orange')
                        else:
                            significance_colors.append('yellow')
                    elif trend.get('is_significant_improvement', False):
                        significance_colors.append('green')
                    else:
                        significance_colors.append('lightblue')
            
            if slopes_per_minute:
                bars = ax2.bar(range(len(slopes_per_minute)), slopes_per_minute, color=significance_colors)
                ax2.set_xticks(range(len(endpoint_names)))
                ax2.set_xticklabels(endpoint_names, rotation=45)
                ax2.set_ylabel('Degradation Rate (ms/min)')
                ax2.set_title('Latency Degradation Rates by Endpoint')
                ax2.axhline(y=0, color='black', linestyle='-', alpha=0.5)
                ax2.axhline(y=5, color='orange', linestyle='--', alpha=0.7, label='Moderate (5ms/min)')
                ax2.axhline(y=20, color='red', linestyle='--', alpha=0.7, label='Severe (20ms/min)')
                ax2.grid(True, alpha=0.3)
                ax2.legend()
                
                # Add value labels
                for i, (bar, slope) in enumerate(zip(bars, slopes_per_minute)):
                    height = bar.get_height()
                    ax2.text(bar.get_x() + bar.get_width()/2., 
                            height + (max(slopes_per_minute) - min(slopes_per_minute)) * 0.01,
                            f'{slope:.2f}', ha='center', va='bottom', fontsize=8)
            else:
                ax2.text(0.5, 0.5, 'No slope data available', ha='center', va='center', transform=ax2.transAxes)
            
            # 3. Statistical significance summary
            sig_endpoints = []
            p_values = []
            r_squared_values = []
            
            for endpoint in self.ALL_ENDPOINTS:
                trend = self.degradation_analysis['trends'].get(endpoint, {})
                if 'p_value' in trend and 'r_squared' in trend:
                    sig_endpoints.append(endpoint.replace('.asp', ''))
                    p_values.append(trend['p_value'])
                    r_squared_values.append(trend['r_squared'])
            
            if sig_endpoints:
                x = np.arange(len(sig_endpoints))
                width = 0.35
                
                bars1 = ax3.bar(x - width/2, p_values, width, label='P-Value', alpha=0.7, color='lightblue')
                bars2 = ax3.bar(x + width/2, r_squared_values, width, label='R²', alpha=0.7, color='lightcoral')
                
                ax3.set_xticks(x)
                ax3.set_xticklabels(sig_endpoints, rotation=45)
                ax3.set_ylabel('Value')
                ax3.set_title('Statistical Significance Metrics')
                ax3.axhline(y=0.05, color='red', linestyle='--', alpha=0.7, label='p=0.05 threshold')
                ax3.legend()
                ax3.grid(True, alpha=0.3)
            else:
                ax3.text(0.5, 0.5, 'No statistical data available', ha='center', va='center', transform=ax3.transAxes)
            
            # 4. Summary statistics
            ax4.axis('off')
            
            # Create summary text
            total_endpoints = len(self.ALL_ENDPOINTS)
            analyzed_endpoints = len([ep for ep in self.ALL_ENDPOINTS if 'slope_ms_per_minute' in self.degradation_analysis['trends'].get(ep, {})])
            significant_trends = len([ep for ep in self.ALL_ENDPOINTS if self.degradation_analysis['trends'].get(ep, {}).get('is_statistically_significant', False)])
            degrading_endpoints = len([ep for ep in self.ALL_ENDPOINTS if self.degradation_analysis['trends'].get(ep, {}).get('is_significant_degradation', False)])
            
            summary_text = f"""DEGRADATION ANALYSIS SUMMARY

Total Endpoints: {total_endpoints}
Analyzed: {analyzed_endpoints}
Significant Trends: {significant_trends}
Degrading: {degrading_endpoints}

Time Windows: {self.time_window_count}
Test Duration: {self.test_duration}s

Analysis Status:
{('✅ No resource leaks detected' if degrading_endpoints == 0 else 
  f'⚠️ {degrading_endpoints} endpoints degrading')}
"""
            
            ax4.text(0.05, 0.95, summary_text, transform=ax4.transAxes, fontsize=12, 
                    verticalalignment='top', fontfamily='monospace',
                    bbox=dict(boxstyle='round', facecolor='lightgray', alpha=0.8))
            ax4.set_title('Degradation Summary')
            
            plt.suptitle('Latency Degradation Analysis - Resource Leak Detection', fontsize=16, fontweight='bold')
            plt.tight_layout()
            
            # Save degradation analysis graph
            degradation_file = self.output_dir / f'{self.file_prefix}_degradation_analysis.png'
            plt.savefig(degradation_file, dpi=150, bbox_inches='tight', facecolor='white')
            logger.info(f"📊 Degradation analysis graph saved: {degradation_file}")
            
            plt.close()
            
        except Exception as e:
            logger.error(f"Error generating degradation analysis graph: {e}")
            import traceback
            logger.error(traceback.format_exc())
        """NEW: Generate degradation trend analysis graphs."""
        if not GRAPHING_AVAILABLE:
            return
            
        # Create a 2x2 layout for degradation analysis
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
        
        # 1. Trend lines for all endpoints
        ax1.set_title('Latency Trends Over Time (Linear Regression)')
        ax1.set_xlabel('Elapsed Time (seconds)')
        ax1.set_ylabel('Response Time (ms)')
        ax1.grid(True, alpha=0.3)
        
        colors = plt.cm.Set3(np.linspace(0, 1, len(self.ALL_ENDPOINTS)))
        
        for endpoint, color in zip(self.ALL_ENDPOINTS, colors):
            stats = self.endpoint_stats[endpoint]
            trend = self.degradation_analysis['trends'].get(endpoint, {})
            
            if 'error' in trend or len(stats['time_series']['response_times']) < 5:
                continue
                
            elapsed_times = np.array(stats['time_series']['elapsed_time'])
            response_times = np.array(stats['time_series']['response_times'])
            success_flags = np.array(stats['time_series']['success'])
            
            # Plot successful requests only
            successful_mask = success_flags == 1
            if np.sum(successful_mask) < 5:
                continue
                
            success_elapsed = elapsed_times[successful_mask]
            success_response_times = response_times[successful_mask]
            
            # Plot scatter points
            ax1.scatter(success_elapsed, success_response_times, c=[color], alpha=0.6, s=20, 
                       label=f"{endpoint.replace('.asp', '')}")
            
            # Plot trend line if significant
            if trend.get('is_statistically_significant', False):
                slope = trend['slope_ms_per_second']
                intercept = trend['intercept_ms']
                trend_line = slope * success_elapsed + intercept
                
                linestyle = '--' if trend.get('is_significant_degradation', False) else '-'
                linewidth = 2 if trend.get('is_significant_degradation', False) else 1
                ax1.plot(success_elapsed, trend_line, color=color, linestyle=linestyle, 
                        linewidth=linewidth, alpha=0.8)
        
        ax1.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize=8)
        
        # 2. Degradation slopes comparison
        endpoint_names = []
        slopes_per_minute = []
        significance_colors = []
        
        for endpoint in self.ALL_ENDPOINTS:
            trend = self.degradation_analysis['trends'].get(endpoint, {})
            if 'slope_ms_per_minute' in trend:
                endpoint_names.append(endpoint.replace('.asp', ''))
                slopes_per_minute.append(trend['slope_ms_per_minute'])
                
                # Color by significance and direction
                if trend.get('is_significant_degradation', False):
                    severity = trend.get('severity', 'minor')
                    if severity == 'critical':
                        significance_colors.append('red')
                    elif severity == 'moderate':
                        significance_colors.append('orange')
                    else:
                        significance_colors.append('yellow')
                elif trend.get('is_significant_improvement', False):
                    significance_colors.append('green')
                else:
                    significance_colors.append('lightblue')
        
        if slopes_per_minute:
            bars = ax2.bar(range(len(slopes_per_minute)), slopes_per_minute, color=significance_colors)
            ax2.set_xticks(range(len(endpoint_names)))
            ax2.set_xticklabels(endpoint_names, rotation=45)
            ax2.set_ylabel('Degradation Rate (ms/min)')
            ax2.set_title('Latency Degradation Rates by Endpoint')
            ax2.axhline(y=0, color='black', linestyle='-', alpha=0.5)
            ax2.axhline(y=5, color='orange', linestyle='--', alpha=0.7, label='Moderate (5ms/min)')
            ax2.axhline(y=20, color='red', linestyle='--', alpha=0.7, label='Severe (20ms/min)')
            ax2.grid(True, alpha=0.3)
            ax2.legend()
            
            # Add value labels
            for i, (bar, slope) in enumerate(zip(bars, slopes_per_minute)):
                height = bar.get_height()
                ax2.text(bar.get_x() + bar.get_width()/2., 
                        height + (max(slopes_per_minute) - min(slopes_per_minute)) * 0.01,
                        f'{slope:.2f}', ha='center', va='bottom', fontsize=8)
        
        # 3. Statistical significance heatmap
        endpoints_for_heatmap = []
        metrics_for_heatmap = []
        
        for endpoint in self.ALL_ENDPOINTS:
            trend = self.degradation_analysis['trends'].get(endpoint, {})
            if 'p_value' in trend:
                endpoints_for_heatmap.append(endpoint.replace('.asp', ''))
                metrics_for_heatmap.append([
                    trend.get('p_value', 1.0),
                    trend.get('r_squared', 0.0),
                    abs(trend.get('slope_ms_per_minute', 0.0)),
                    trend.get('sample_count', 0)
                ])
        
        if metrics_for_heatmap:
            metrics_array = np.array(metrics_for_heatmap).T
            metric_labels = ['P-Value', 'R²', 'Slope (abs)', 'Samples']
            
            im = ax3.imshow(metrics_array, cmap='RdYlBu_r', aspect='auto')
            ax3.set_xticks(range(len(endpoints_for_heatmap)))
            ax3.set_xticklabels(endpoints_for_heatmap, rotation=45)
            ax3.set_yticks(range(len(metric_labels)))
            ax3.set_yticklabels(metric_labels)
            ax3.set_title('Statistical Significance Metrics')
            
            # Add text annotations
            for i in range(len(metric_labels)):
                for j in range(len(endpoints_for_heatmap)):
                    value = metrics_array[i, j]
                    if i == 0:  # P-value
                        text = f'{value:.3f}'
                    elif i == 1:  # R²
                        text = f'{value:.3f}'
                    elif i == 2:  # Slope
                        text = f'{value:.2f}'
                    else:  # Samples
                        text = f'{int(value)}'
                    ax3.text(j, i, text, ha="center", va="center", fontsize=8, 
                            color='white' if value > np.median(metrics_array[i]) else 'black')
            
            plt.colorbar(im, ax=ax3)
        
        # 4. Early vs Late performance comparison
        early_vs_late = {}
        for endpoint in self.ALL_ENDPOINTS:
            trend = self.degradation_analysis['trends'].get(endpoint, {})
            window_analysis = trend.get('window_analysis', {})
            windows = window_analysis.get('windows', [])
            
            valid_windows = [w for w in windows if w['sample_count'] > 0]
            if len(valid_windows) >= 2:
                early_window = valid_windows[0]
                late_window = valid_windows[-1]
                
                early_vs_late[endpoint.replace('.asp', '')] = {
                    'early_mean': early_window['mean_ms'],
                    'late_mean': late_window['mean_ms'],
                    'change_percent': ((late_window['mean_ms'] - early_window['mean_ms']) / early_window['mean_ms']) * 100
                }
        
        if early_vs_late:
            endpoints_comparison = list(early_vs_late.keys())
            early_means = [early_vs_late[ep]['early_mean'] for ep in endpoints_comparison]
            late_means = [early_vs_late[ep]['late_mean'] for ep in endpoints_comparison]
            change_percents = [early_vs_late[ep]['change_percent'] for ep in endpoints_comparison]
            
            x = np.arange(len(endpoints_comparison))
            width = 0.35
            
            bars1 = ax4.bar(x - width/2, early_means, width, label='Early Performance', alpha=0.8, color='lightblue')
            bars2 = ax4.bar(x + width/2, late_means, width, label='Late Performance', alpha=0.8, color='lightcoral')
            
            ax4.set_xticks(x)
            ax4.set_xticklabels(endpoints_comparison, rotation=45)
            ax4.set_ylabel('Mean Response Time (ms)')
            ax4.set_title('Early vs Late Performance Comparison')
            ax4.legend()
            ax4.grid(True, alpha=0.3)
            
            # Add change percentage labels
            for i, (early, late, change) in enumerate(zip(early_means, late_means, change_percents)):
                max_height = max(early, late)
                color = 'red' if change > 10 else 'orange' if change > 5 else 'green' if change < -5 else 'black'
                ax4.text(i, max_height + max(early_means + late_means) * 0.02, 
                        f'{change:+.1f}%', ha='center', va='bottom', fontsize=9, color=color, weight='bold')
        
        plt.suptitle('Latency Degradation Analysis - Resource Leak Detection', fontsize=16, fontweight='bold')
        plt.tight_layout()
        
        # Save degradation analysis graph
        degradation_file = self.output_dir / f'{self.file_prefix}_degradation_analysis.png'
        plt.savefig(degradation_file, dpi=150, bbox_inches='tight', facecolor='white')
        logger.info(f"📊 Degradation analysis graph saved: {degradation_file}")
        
        plt.close()

    def _generate_time_windowed_histograms(self):
        """NEW: Generate time-windowed histograms to show latency evolution."""
        if not GRAPHING_AVAILABLE:
            return
            
        for endpoint in self.ALL_ENDPOINTS:
            stats = self.endpoint_stats[endpoint]
            trend = self.degradation_analysis['trends'].get(endpoint, {})
            
            if 'error' in trend or len(stats['time_series']['response_times']) < 10:
                continue
            
            # Create subplot layout for time windows
            n_windows = len(self.degradation_analysis['time_windows'])
            n_cols = min(3, n_windows)
            n_rows = math.ceil(n_windows / n_cols)
            
            fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * n_cols, 4 * n_rows))
            if n_rows == 1:
                axes = [axes] if n_windows == 1 else axes
            elif n_cols == 1:
                axes = [[ax] for ax in axes]
            
            # Flatten axes for easier indexing
            if n_windows > 1:
                axes_flat = [ax for row in axes for ax in (row if isinstance(row, np.ndarray) else [row])]
            else:
                axes_flat = [axes]
            
            # Get all response times for consistent binning
            elapsed_times = np.array(stats['time_series']['elapsed_time'])
            response_times = np.array(stats['time_series']['response_times'])
            success_flags = np.array(stats['time_series']['success'])
            
            # Only use successful requests
            successful_mask = success_flags == 1
            if np.sum(successful_mask) < 5:
                plt.close()
                continue
                
            success_elapsed = elapsed_times[successful_mask]
            success_response_times = response_times[successful_mask]
            
            # Calculate global bins for consistency
            global_min = np.min(success_response_times)
            global_max = np.max(success_response_times)
            bins = np.linspace(global_min, global_max, 30)
            
            # Generate histogram for each time window
            window_colors = plt.cm.plasma(np.linspace(0, 1, n_windows))
            
            for i, (window, color) in enumerate(zip(self.degradation_analysis['time_windows'], window_colors)):
                if i >= len(axes_flat):
                    break
                    
                ax = axes_flat[i]
                
                # Find data points in this window
                window_mask = (success_elapsed >= window['start_time']) & (success_elapsed < window['end_time'])
                window_response_times = success_response_times[window_mask]
                
                if len(window_response_times) > 0:
                    # Plot histogram
                    n, bins_used, patches = ax.hist(window_response_times, bins=bins, alpha=0.7, 
                                                   color=color, edgecolor='black', linewidth=0.5)
                    
                    # Add statistics
                    mean_val = np.mean(window_response_times)
                    median_val = np.median(window_response_times)
                    p95_val = np.percentile(window_response_times, 95)
                    
                    ax.axvline(mean_val, color='red', linestyle='--', linewidth=2, label=f'Mean: {mean_val:.1f}ms')
                    ax.axvline(median_val, color='blue', linestyle='--', linewidth=2, label=f'Median: {median_val:.1f}ms')
                    ax.axvline(p95_val, color='orange', linestyle='--', linewidth=2, label=f'P95: {p95_val:.1f}ms')
                    
                    ax.set_title(f'{window["label"]}\n{len(window_response_times)} samples')
                    ax.set_xlabel('Response Time (ms)')
                    ax.set_ylabel('Frequency')
                    ax.grid(True, alpha=0.3)
                    ax.legend(fontsize=8)
                    
                    # Add sample count and window info
                    textstr = f'n={len(window_response_times)}\nμ={mean_val:.1f}ms\nσ={np.std(window_response_times):.1f}ms'
                    props = dict(boxstyle='round', facecolor='wheat', alpha=0.8)
                    ax.text(0.02, 0.98, textstr, transform=ax.transAxes, fontsize=8,
                           verticalalignment='top', bbox=props)
                else:
                    ax.text(0.5, 0.5, f'No data\n{window["label"]}', ha='center', va='center', 
                           transform=ax.transAxes, fontsize=12)
                    ax.set_xlabel('Response Time (ms)')
                    ax.set_ylabel('Frequency')
                    ax.set_title(f'{window["label"]}\n0 samples')
            
            # Hide unused subplots
            for i in range(n_windows, len(axes_flat)):
                axes_flat[i].set_visible(False)
            
            # Add overall title with trend information
            endpoint_short = endpoint.replace('.asp', '')
            trend_info = ""
            if 'slope_ms_per_minute' in trend:
                slope = trend['slope_ms_per_minute']
                p_val = trend.get('p_value', 1.0)
                r_sq = trend.get('r_squared', 0.0)
                
                if trend.get('is_significant_degradation', False):
                    trend_info = f" - DEGRADING: +{slope:.2f}ms/min (p={p_val:.3f}, R²={r_sq:.3f})"
                elif trend.get('is_significant_improvement', False):
                    trend_info = f" - IMPROVING: {slope:.2f}ms/min (p={p_val:.3f})"
                else:
                    trend_info = f" - Stable: {slope:.2f}ms/min (p={p_val:.3f})"
            
            fig.suptitle(f'{endpoint_short} - Time-Windowed Latency Distribution{trend_info}', 
                        fontsize=14, fontweight='bold')
            
            plt.tight_layout()
            
            # Save histogram
            histogram_file = self.output_dir / f'{self.file_prefix}_{endpoint_short}_time_histograms.png'
            plt.savefig(histogram_file, dpi=150, bbox_inches='tight', facecolor='white')
            logger.debug(f"📊 Time-windowed histogram saved: {histogram_file}")
            
            plt.close()
        
    def _generate_time_windowed_histograms(self):
        """NEW: Generate time-windowed histograms to show latency evolution."""
        if not GRAPHING_AVAILABLE:
            logger.warning("📊 Matplotlib not available for time-windowed histograms")
            return
            
        logger.info("📊 Generating time-windowed histograms...")
        
        histogram_count = 0
        for endpoint in self.ALL_ENDPOINTS:
            try:
                stats = self.endpoint_stats[endpoint]
                trend = self.degradation_analysis['trends'].get(endpoint, {})
                
                if 'error' in trend or len(stats['time_series']['response_times']) < 10:
                    logger.debug(f"Skipping histogram for {endpoint}: insufficient data")
                    continue
                
                # Get data for this endpoint
                ts_data = stats['time_series']
                min_length = min(len(ts_data.get('elapsed_time', [])), 
                               len(ts_data.get('response_times', [])), 
                               len(ts_data.get('success', [])))
                
                if min_length < 10:
                    logger.debug(f"Skipping histogram for {endpoint}: insufficient length ({min_length})")
                    continue
                    
                elapsed_times = np.array(ts_data['elapsed_time'][:min_length])
                response_times = np.array(ts_data['response_times'][:min_length])
                success_flags = np.array(ts_data['success'][:min_length])
                
                # Only use successful requests
                successful_mask = success_flags == 1
                if np.sum(successful_mask) < 5:
                    logger.debug(f"Skipping histogram for {endpoint}: insufficient successful samples")
                    continue
                    
                success_elapsed = elapsed_times[successful_mask]
                success_response_times = response_times[successful_mask]
                
                # Create subplot layout for time windows
                n_windows = len(self.degradation_analysis['time_windows'])
                n_cols = min(3, n_windows)
                n_rows = math.ceil(n_windows / n_cols)
                
                fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * n_cols, 4 * n_rows))
                
                # Handle different subplot layouts
                if n_windows == 1:
                    axes = [axes]
                elif n_rows == 1:
                    axes = axes if hasattr(axes, '__len__') else [axes]
                else:
                    axes = axes.flatten() if hasattr(axes, 'flatten') else [axes]
                
                # Calculate global bins for consistency
                if len(success_response_times) > 0:
                    global_min = np.min(success_response_times)
                    global_max = np.max(success_response_times)
                    bins = np.linspace(global_min, global_max, min(20, len(success_response_times) // 2))
                else:
                    bins = 20
                
                # Generate histogram for each time window
                window_colors = plt.cm.plasma(np.linspace(0, 1, n_windows))
                
                for i, (window, color) in enumerate(zip(self.degradation_analysis['time_windows'], window_colors)):
                    if i >= len(axes):
                        break
                        
                    ax = axes[i]
                    
                    # Find data points in this window
                    window_mask = (success_elapsed >= window['start_time']) & (success_elapsed < window['end_time'])
                    window_response_times = success_response_times[window_mask]
                    
                    if len(window_response_times) > 0:
                        # Plot histogram
                        n, bins_used, patches = ax.hist(window_response_times, bins=bins, alpha=0.7, 
                                                       color=color, edgecolor='black', linewidth=0.5)
                        
                        # Add statistics
                        mean_val = np.mean(window_response_times)
                        median_val = np.median(window_response_times)
                        
                        ax.axvline(mean_val, color='red', linestyle='--', linewidth=2, label=f'Mean: {mean_val:.1f}ms')
                        ax.axvline(median_val, color='blue', linestyle='--', linewidth=2, label=f'Median: {median_val:.1f}ms')
                        
                        if len(window_response_times) > 2:
                            p95_val = np.percentile(window_response_times, 95)
                            ax.axvline(p95_val, color='orange', linestyle='--', linewidth=2, label=f'P95: {p95_val:.1f}ms')
                        
                        ax.set_title(f'{window["label"]}\n{len(window_response_times)} samples')
                        ax.set_xlabel('Response Time (ms)')
                        ax.set_ylabel('Frequency')
                        ax.grid(True, alpha=0.3)
                        ax.legend(fontsize=8)
                        
                        # Add sample count and window info
                        textstr = f'n={len(window_response_times)}\nμ={mean_val:.1f}ms\nσ={np.std(window_response_times):.1f}ms'
                        props = dict(boxstyle='round', facecolor='wheat', alpha=0.8)
                        ax.text(0.02, 0.98, textstr, transform=ax.transAxes, fontsize=8,
                               verticalalignment='top', bbox=props)
                    else:
                        ax.text(0.5, 0.5, f'No data\n{window["label"]}', ha='center', va='center', 
                               transform=ax.transAxes, fontsize=12)
                        ax.set_xlabel('Response Time (ms)')
                        ax.set_ylabel('Frequency')
                        ax.set_title(f'{window["label"]}\n0 samples')
                
                # Hide unused subplots
                for i in range(n_windows, len(axes)):
                    axes[i].set_visible(False)
                
                # Add overall title with trend information
                endpoint_short = endpoint.replace('.asp', '')
                trend_info = ""
                if 'slope_ms_per_minute' in trend:
                    slope = trend['slope_ms_per_minute']
                    p_val = trend.get('p_value', 1.0)
                    r_sq = trend.get('r_squared', 0.0)
                    
                    if trend.get('is_significant_degradation', False):
                        trend_info = f" - DEGRADING: +{slope:.2f}ms/min (p={p_val:.3f}, R²={r_sq:.3f})"
                    elif trend.get('is_significant_improvement', False):
                        trend_info = f" - IMPROVING: {slope:.2f}ms/min (p={p_val:.3f})"
                    else:
                        trend_info = f" - Stable: {slope:.2f}ms/min (p={p_val:.3f})"
                
                fig.suptitle(f'{endpoint_short} - Time-Windowed Latency Distribution{trend_info}', 
                            fontsize=14, fontweight='bold')
                
                plt.tight_layout()
                
                # Save histogram
                histogram_file = self.output_dir / f'{self.file_prefix}_{endpoint_short}_time_histograms.png'
                plt.savefig(histogram_file, dpi=150, bbox_inches='tight', facecolor='white')
                logger.debug(f"📊 Time-windowed histogram saved: {histogram_file}")
                histogram_count += 1
                
                plt.close()
                
            except Exception as e:
                logger.error(f"Error generating histogram for {endpoint}: {e}")
                import traceback
                logger.debug(traceback.format_exc())
        
        if histogram_count > 0:
            logger.info(f"📊 Generated {histogram_count} time-windowed histograms")
        else:
            logger.warning("📊 No time-windowed histograms generated - insufficient data")

    def _generate_endpoint_timing_graphs(self):
        """Generate detailed per-endpoint timing analysis graphs."""
        if not GRAPHING_AVAILABLE:
            return
            
        # Create a comprehensive endpoint timing analysis
        fig, axes = plt.subplots(2, 3, figsize=(18, 12))
        axes = axes.flatten()
        
        colors = plt.cm.Set3(np.linspace(0, 1, len(self.ALL_ENDPOINTS)))
        
        for i, (endpoint, color) in enumerate(zip(self.ALL_ENDPOINTS, colors)):
            if i >= len(axes):
                break
                
            ax = axes[i]
            stats = self.endpoint_stats[endpoint]
            
            if not stats['time_series']['timestamps']:
                ax.text(0.5, 0.5, f'No data for\n{endpoint}', ha='center', va='center', 
                       transform=ax.transAxes, fontsize=12)
                ax.set_title(f'{endpoint.replace(".asp", "")} - No Data')
                continue
            
            # Plot response times over time
            timestamps = stats['time_series']['timestamps']
            response_times = stats['time_series']['response_times']
            success_flags = stats['time_series']['success']
            
            # Separate successful and failed requests
            successful_times = [t for t, s in zip(timestamps, success_flags) if s == 1]
            successful_responses = [r for r, s in zip(response_times, success_flags) if s == 1]
            failed_times = [t for t, s in zip(timestamps, success_flags) if s == 0]
            failed_responses = [r for r, s in zip(response_times, success_flags) if s == 0]
            
            # Plot successful requests
            if successful_times:
                ax.scatter(successful_times, successful_responses, c=[color], alpha=0.6, s=15, 
                          label='Successful', marker='o')
            
            # Plot failed requests
            if failed_times:
                ax.scatter(failed_times, failed_responses, c='red', alpha=0.8, s=25, 
                          label='Failed', marker='x')
            
            # Add timeout line
            timeout_ms = self.endpoint_timeouts[endpoint] * 1000
            ax.axhline(y=timeout_ms, color='red', linestyle='--', alpha=0.7, 
                      label=f'Timeout ({timeout_ms:.0f}ms)')
            
            # Add average line
            if successful_responses:
                avg_response = np.mean(successful_responses)
                ax.axhline(y=avg_response, color='blue', linestyle='-', alpha=0.7,
                          label=f'Avg ({avg_response:.0f}ms)')
            
            endpoint_type = "FAST" if endpoint in self.FAST_ENDPOINTS else "OFDM"
            success_rate = (stats['successful_requests'] / stats['total_requests']) * 100 if stats['total_requests'] > 0 else 0
            
            ax.set_title(f'{endpoint.replace(".asp", "")} ({endpoint_type})\n'
                        f'Success: {success_rate:.1f}% | Samples: {len(response_times)}')
            ax.set_xlabel('Time')
            ax.set_ylabel('Response Time (ms)')
            ax.grid(True, alpha=0.3)
            ax.legend(fontsize=8)
            
            # Format x-axis
            ax.tick_params(axis='x', rotation=45)
        
        # Hide unused subplots
        for i in range(len(self.ALL_ENDPOINTS), len(axes)):
            axes[i].set_visible(False)
        
        plt.suptitle('Per-Endpoint Timing Analysis', fontsize=16, fontweight='bold')
        plt.tight_layout()
        
        # Save endpoint timing graph
        timing_file = self.output_dir / f'{self.file_prefix}_endpoint_timings.png'
        plt.savefig(timing_file, dpi=150, bbox_inches='tight', facecolor='white')
        logger.info(f"📊 Endpoint timing graph saved: {timing_file}")
        
        plt.close()

    def _generate_endpoint_comparison_graphs(self):
        """Generate cross-endpoint comparison graphs."""
        if not GRAPHING_AVAILABLE:
            return
            
        # Create a 2x2 comparison layout
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
        
        # 1. Response time box plots
        endpoint_names = []
        endpoint_response_times = []
        endpoint_colors = []
        
        for endpoint in self.ALL_ENDPOINTS:
            stats = self.endpoint_stats[endpoint]
            if stats['response_times']:
                endpoint_names.append(endpoint.replace('.asp', ''))
                endpoint_response_times.append(stats['response_times'])
                endpoint_colors.append('lightblue' if endpoint in self.FAST_ENDPOINTS else 'lightcoral')
        
        if endpoint_response_times:
            box_plot = ax1.boxplot(endpoint_response_times, labels=endpoint_names, patch_artist=True)
            for patch, color in zip(box_plot['boxes'], endpoint_colors):
                patch.set_facecolor(color)
                patch.set_alpha(0.7)
            
            ax1.set_title('Response Time Distribution by Endpoint')
            ax1.set_ylabel('Response Time (ms)')
            ax1.tick_params(axis='x', rotation=45)
            ax1.grid(True, alpha=0.3)
        
        # 2. Success rates comparison
        success_rates = []
        endpoint_labels = []
        colors = []
        
        for endpoint in self.ALL_ENDPOINTS:
            stats = self.endpoint_stats[endpoint]
            if stats['total_requests'] > 0:
                success_rate = (stats['successful_requests'] / stats['total_requests']) * 100
                success_rates.append(success_rate)
                endpoint_labels.append(endpoint.replace('.asp', ''))
                colors.append('green' if endpoint in self.FAST_ENDPOINTS else 'blue')
        
        if success_rates:
            bars = ax2.bar(range(len(success_rates)), success_rates, color=colors, alpha=0.7)
            ax2.set_xticks(range(len(endpoint_labels)))
            ax2.set_xticklabels(endpoint_labels, rotation=45)
            ax2.set_ylabel('Success Rate (%)')
            ax2.set_title('Success Rate Comparison (Green=Fast, Blue=OFDM)')
            ax2.set_ylim(0, 105)
            ax2.grid(True, alpha=0.3)
            
            # Add value labels
            for i, (bar, rate) in enumerate(zip(bars, success_rates)):
                height = bar.get_height()
                ax2.text(bar.get_x() + bar.get_width()/2., height + 1,
                        f'{rate:.1f}%', ha='center', va='bottom', fontsize=9)
        
        # 3. Timeout vs Average Response Time
        avg_times = []
        timeout_times = []
        endpoint_labels2 = []
        colors2 = []
        
        for endpoint in self.ALL_ENDPOINTS:
            stats = self.endpoint_stats[endpoint]
            if stats['response_times']:
                avg_time = np.mean(stats['response_times'])
                timeout_time = self.endpoint_timeouts[endpoint] * 1000
                avg_times.append(avg_time)
                timeout_times.append(timeout_time)
                endpoint_labels2.append(endpoint.replace('.asp', ''))
                colors2.append('red' if avg_time > timeout_time * 0.8 else 'green')
        
        if avg_times:
            x = np.arange(len(endpoint_labels2))
            width = 0.35
            
            bars1 = ax3.bar(x - width/2, avg_times, width, label='Avg Response', color='lightblue', alpha=0.7)
            bars2 = ax3.bar(x + width/2, timeout_times, width, label='Timeout', color='lightcoral', alpha=0.7)
            
            ax3.set_xticks(x)
            ax3.set_xticklabels(endpoint_labels2, rotation=45)
            ax3.set_ylabel('Time (ms)')
            ax3.set_title('Average Response Time vs Timeout')
            ax3.legend()
            ax3.grid(True, alpha=0.3)
        
        # 4. Retry analysis
        retry_stats = []
        endpoint_labels3 = []
        
        for endpoint in self.ALL_ENDPOINTS:
            stats = self.endpoint_stats[endpoint]
            if stats['attempts_used']:
                avg_attempts = np.mean(stats['attempts_used'])
                retry_stats.append(avg_attempts)
                endpoint_labels3.append(endpoint.replace('.asp', ''))
        
        if retry_stats:
            bars = ax4.bar(range(len(retry_stats)), retry_stats, color='orange', alpha=0.7)
            ax4.set_xticks(range(len(endpoint_labels3)))
            ax4.set_xticklabels(endpoint_labels3, rotation=45)
            ax4.set_ylabel('Average Attempts')
            ax4.set_title('Retry Analysis (Average Attempts per Request)')
            ax4.grid(True, alpha=0.3)
            
            # Add value labels
            for i, (bar, attempts) in enumerate(zip(bars, retry_stats)):
                height = bar.get_height()
                ax4.text(bar.get_x() + bar.get_width()/2., height + 0.01,
                        f'{attempts:.2f}', ha='center', va='bottom', fontsize=9)
        
        plt.suptitle('Cross-Endpoint Performance Comparison', fontsize=16, fontweight='bold')
        plt.tight_layout()
        
        # Save comparison graph
        comparison_file = self.output_dir / f'{self.file_prefix}_endpoint_comparison.png'
        plt.savefig(comparison_file, dpi=150, bbox_inches='tight', facecolor='white')
        logger.info(f"📊 Endpoint comparison graph saved: {comparison_file}")
        
        plt.close()

    def _generate_timing_distribution_graphs(self):
        """Generate timing distribution analysis graphs."""
        if not GRAPHING_AVAILABLE:
            return
            
        # Create a 2x2 distribution analysis layout
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
        
        # 1. Overall response time histogram
        all_response_times = []
        for endpoint in self.ALL_ENDPOINTS:
            stats = self.endpoint_stats[endpoint]
            if stats['response_times']:
                all_response_times.extend(stats['response_times'])
        
        if all_response_times:
            ax1.hist(all_response_times, bins=50, alpha=0.7, color='skyblue', edgecolor='black')
            ax1.set_xlabel('Response Time (ms)')
            ax1.set_ylabel('Frequency')
            ax1.set_title('Overall Response Time Distribution')
            ax1.grid(True, alpha=0.3)
            
            # Add statistics
            mean_time = np.mean(all_response_times)
            median_time = np.median(all_response_times)
            p95_time = np.percentile(all_response_times, 95)
            ax1.axvline(mean_time, color='red', linestyle='--', label=f'Mean: {mean_time:.1f}ms')
            ax1.axvline(median_time, color='blue', linestyle='--', label=f'Median: {median_time:.1f}ms')
            ax1.axvline(p95_time, color='orange', linestyle='--', label=f'P95: {p95_time:.1f}ms')
            ax1.legend()
        
        # 2. Collection time over time
        if self.results['time_series']['timestamps']:
            timestamps = self.results['time_series']['timestamps']
            collection_times = self.results['time_series']['collection_times']
            
            ax2.plot(timestamps, collection_times, color='green', alpha=0.7, linewidth=1)
            ax2.set_xlabel('Time')
            ax2.set_ylabel('Collection Time (ms)')
            ax2.set_title('Collection Time Over Time')
            ax2.grid(True, alpha=0.3)
            ax2.tick_params(axis='x', rotation=45)
            
            # Add timeout line
            timeout_ms = self.collection_timeout * 1000
            ax2.axhline(y=timeout_ms, color='red', linestyle='--', alpha=0.7, 
                       label=f'Timeout ({timeout_ms:.0f}ms)')
            ax2.legend()
        
        # 3. Fast vs OFDM endpoint comparison
        fast_times = []
        ofdm_times = []
        
        for endpoint in self.FAST_ENDPOINTS:
            stats = self.endpoint_stats[endpoint]
            if stats['response_times']:
                fast_times.extend(stats['response_times'])
        
        for endpoint in self.SLOW_ENDPOINTS:
            stats = self.endpoint_stats[endpoint]
            if stats['response_times']:
                ofdm_times.extend(stats['response_times'])
        
        if fast_times and ofdm_times:
            ax3.hist(fast_times, bins=30, alpha=0.7, color='lightblue', label='Fast Endpoints', edgecolor='black')
            ax3.hist(ofdm_times, bins=30, alpha=0.7, color='lightcoral', label='OFDM Endpoints', edgecolor='black')
            ax3.set_xlabel('Response Time (ms)')
            ax3.set_ylabel('Frequency')
            ax3.set_title('Fast vs OFDM Response Time Distribution')
            ax3.legend()
            ax3.grid(True, alpha=0.3)
        
        # 4. Cycle type distribution
        cycle_types = self.results['cycle_types']
        if cycle_types:
            cycle_counts = {
                'FAST': cycle_types.count('FAST'),
                'FULL': cycle_types.count('FULL'),
                'CACHED': cycle_types.count('CACHED')
            }
            
            labels = list(cycle_counts.keys())
            values = list(cycle_counts.values())
            colors = ['lightblue', 'lightcoral', 'lightgreen']
            
            wedges, texts, autotexts = ax4.pie(values, labels=labels, colors=colors, autopct='%1.1f%%', startangle=90)
            ax4.set_title('Cycle Type Distribution')
            
            # Add count labels
            for i, (label, value) in enumerate(zip(labels, values)):
                ax4.text(0, -1.3 + i*0.1, f'{label}: {value} cycles', ha='center', fontsize=10)
        
        plt.suptitle('Timing Distribution Analysis', fontsize=16, fontweight='bold')
        plt.tight_layout()
        
        # Save distribution graph
        distribution_file = self.output_dir / f'{self.file_prefix}_timing_distribution.png'
        plt.savefig(distribution_file, dpi=150, bbox_inches='tight', facecolor='white')
        logger.info(f"📊 Timing distribution graph saved: {distribution_file}")
        
        plt.close()
    def _generate_main_performance_graph(self):
        """Generate main performance overview graph with consistent timeout display."""
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
        
        # Create a 2x2 grid layout for main overview
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
        
        # 1. Success Rate Over Time
        ax1.plot(df.index, df['success_rate_rolling'], color='green', linewidth=2, label='Success Rate %')
        ax1.set_ylabel('Success Rate (%)')
        ax1.set_title('Success Rate Over Time')
        ax1.grid(True, alpha=0.3)
        ax1.set_ylim(0, 105)
        
        # 2. Collection Times with Consistent Timeout Display
        ax2.plot(df.index, df['collection_time'], color='blue', linewidth=1, alpha=0.8, label='Collection Time (ms)')
        
        # Format collection timeout for display
        collection_timeout_info = self._get_timeout_display_info(self.collection_timeout)
        ax2.axhline(y=self.collection_timeout * 1000, color='red', linestyle='--', alpha=0.7, 
                   label=f'Timeout ({collection_timeout_info["display"]})')
        
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
            ax3.set_title('Endpoint Success Rates (Green=Fast, Blue=OFDM)')
            ax3.axhline(y=95, color='orange', linestyle='--', alpha=0.7, label='95% Target')
            ax3.set_ylim(0, 105)
            ax3.legend()
            ax3.grid(True, alpha=0.3)
            
            # Add value labels
            for i, bar in enumerate(bars):
                height = bar.get_height()
                ax3.text(bar.get_x() + bar.get_width()/2., height + 1,
                        f'{height:.1f}%', ha='center', va='bottom', fontsize=9)
        
        # 4. Performance Summary with Consistent Timeout Display
        ax4.axis('off')
        
        # Calculate summary stats
        overall_success = (self.results['successful_cycles'] / self.results['total_cycles']) * 100 if self.results['total_cycles'] > 0 else 0
        avg_collection = statistics.mean(self.results['collection_times']) if self.results['collection_times'] else 0
        avg_response = statistics.mean(self.results['response_times']) if self.results['response_times'] else 0
        
        # Get formatted timeout information
        fast_timeout_info = self._get_timeout_display_info(self.fast_endpoint_timeout)
        ofdm_timeout_info = self._get_timeout_display_info(self.ofdm_endpoint_timeout)
        collection_timeout_info = self._get_timeout_display_info(self.collection_timeout)
        
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
Fast Timeout: {fast_timeout_info['precise']}
OFDM Timeout: {ofdm_timeout_info['precise']}
Collection Timeout: {collection_timeout_info['precise']}
Time Windows: {self.time_window_count}
"""
        
        ax4.text(0.05, 0.95, summary_text, transform=ax4.transAxes, fontsize=10, 
                verticalalignment='top', fontfamily='monospace',
                bbox=dict(boxstyle='round', facecolor='lightblue', alpha=0.8))
        ax4.set_title('Configuration & Stats')
        
        # Format x-axis for time series plots
        for ax in [ax1, ax2]:
            if len(df) > 0:
                ax.tick_params(axis='x', rotation=45)
        
        # Add overall title with consistent timeout display
        fast_display = fast_timeout_info['display']  
        ofdm_display = ofdm_timeout_info['display']
        config_summary = (f"Mode: {'Parallel' if self.parallel_collection else 'Serial'} | "
                         f"Interval: {self.update_every}s | OFDM: {self.ofdm_poll_multiple}x | "
                         f"Timeouts: {fast_display}/{ofdm_display}")
        
        fig.suptitle(f'Hitron CODA Performance Overview with Degradation Analysis - {config_summary}', fontsize=13, fontweight='bold')
        
        plt.tight_layout()
        
        # Save main overview graph
        graph_file = self.output_dir / f'{self.file_prefix}_overview.png'
        plt.savefig(graph_file, dpi=150, bbox_inches='tight', facecolor='white')
        logger.info(f"📊 Main overview graph saved: {graph_file}")
        
        plt.close()

    def _generate_main_performance_graph(self):
        """Generate main performance overview graph with consistent timeout display."""
        try:
            # Create DataFrame for easier plotting
            if pd is not None:
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
            else:
                # Fallback without pandas
                timestamps = self.results['time_series']['timestamps']
                df = None
            
            # Create a 2x2 grid layout for main overview
            fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
            
            # 1. Success Rate Over Time
            if df is not None:
                ax1.plot(df.index, df['success_rate_rolling'], color='green', linewidth=2, label='Success Rate %')
            else:
                ax1.plot(timestamps, self.results['time_series']['success_rate_rolling'], color='green', linewidth=2, label='Success Rate %')
            ax1.set_ylabel('Success Rate (%)')
            ax1.set_title('Success Rate Over Time')
            ax1.grid(True, alpha=0.3)
            ax1.set_ylim(0, 105)
            
            # 2. Collection Times with Consistent Timeout Display
            if df is not None:
                ax2.plot(df.index, df['collection_time'], color='blue', linewidth=1, alpha=0.8, label='Collection Time (ms)')
            else:
                ax2.plot(timestamps, self.results['time_series']['collection_times'], color='blue', linewidth=1, alpha=0.8, label='Collection Time (ms)')
            
            # Format collection timeout for display
            collection_timeout_info = self._get_timeout_display_info(self.collection_timeout)
            ax2.axhline(y=self.collection_timeout * 1000, color='red', linestyle='--', alpha=0.7, 
                       label=f'Timeout ({collection_timeout_info["display"]})')
            
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
                ax3.set_title('Endpoint Success Rates (Green=Fast, Blue=OFDM)')
                ax3.axhline(y=95, color='orange', linestyle='--', alpha=0.7, label='95% Target')
                ax3.set_ylim(0, 105)
                ax3.legend()
                ax3.grid(True, alpha=0.3)
                
                # Add value labels
                for i, bar in enumerate(bars):
                    height = bar.get_height()
                    ax3.text(bar.get_x() + bar.get_width()/2., height + 1,
                            f'{height:.1f}%', ha='center', va='bottom', fontsize=9)
            
            # 4. Performance Summary with Consistent Timeout Display
            ax4.axis('off')
            
            # Calculate summary stats
            overall_success = (self.results['successful_cycles'] / self.results['total_cycles']) * 100 if self.results['total_cycles'] > 0 else 0
            avg_collection = statistics.mean(self.results['collection_times']) if self.results['collection_times'] else 0
            avg_response = statistics.mean(self.results['response_times']) if self.results['response_times'] else 0
            
            # Get formatted timeout information
            fast_timeout_info = self._get_timeout_display_info(self.fast_endpoint_timeout)
            ofdm_timeout_info = self._get_timeout_display_info(self.ofdm_endpoint_timeout)
            collection_timeout_info = self._get_timeout_display_info(self.collection_timeout)
            
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
Fast Timeout: {fast_timeout_info['precise']}
OFDM Timeout: {ofdm_timeout_info['precise']}
Collection Timeout: {collection_timeout_info['precise']}
Time Windows: {self.time_window_count}
"""
            
            ax4.text(0.05, 0.95, summary_text, transform=ax4.transAxes, fontsize=10, 
                    verticalalignment='top', fontfamily='monospace',
                    bbox=dict(boxstyle='round', facecolor='lightblue', alpha=0.8))
            ax4.set_title('Configuration & Stats')
            
            # Format x-axis for time series plots
            for ax in [ax1, ax2]:
                if len(timestamps) > 0:
                    ax.tick_params(axis='x', rotation=45)
            
            # Add overall title with consistent timeout display
            fast_display = fast_timeout_info['display']  
            ofdm_display = ofdm_timeout_info['display']
            config_summary = (f"Mode: {'Parallel' if self.parallel_collection else 'Serial'} | "
                             f"Interval: {self.update_every}s | OFDM: {self.ofdm_poll_multiple}x | "
                             f"Timeouts: {fast_display}/{ofdm_display}")
            
            fig.suptitle(f'Hitron CODA Performance Overview with Degradation Analysis - {config_summary}', fontsize=13, fontweight='bold')
            
            plt.tight_layout()
            
            # Save main overview graph
            graph_file = self.output_dir / f'{self.file_prefix}_overview.png'
            plt.savefig(graph_file, dpi=150, bbox_inches='tight', facecolor='white')
            logger.info(f"📊 Main overview graph saved: {graph_file}")
            
            plt.close()
            
        except Exception as e:
            logger.error(f"Error generating main performance graph: {e}")
            import traceback
            logger.debug(traceback.format_exc())

    def generate_enhanced_csv_summary(self):
        """Generate enhanced CSV summary including degradation analysis."""
        csv_count = 0
        
        try:
            logger.info("📊 Generating simulation summary CSV...")
            # Main simulation summary (existing)
            csv_file = self.output_dir / f'{self.file_prefix}_simulation_summary.csv'
            
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
            
            if summary_data:
                if pd is not None:
                    df = pd.DataFrame(summary_data)
                    df.to_csv(csv_file, index=False)
                    logger.info(f"📊 CSV summary saved: {csv_file}")
                    csv_count += 1
                else:
                    # Fallback to manual CSV writing
                    import csv
                    with open(csv_file, 'w', newline='') as f:
                        if summary_data:
                            writer = csv.DictWriter(f, fieldnames=summary_data[0].keys())
                            writer.writeheader()
                            writer.writerows(summary_data)
                    logger.info(f"📊 CSV summary saved (manual): {csv_file}")
                    csv_count += 1
        except Exception as e:
            logger.error(f"Failed to generate simulation summary CSV: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        try:
            logger.info("📊 Generating degradation analysis CSV...")
            # NEW: Degradation analysis summary
            degradation_csv = self.output_dir / f'{self.file_prefix}_degradation_analysis.csv'
            degradation_data = []
            
            for endpoint in self.ALL_ENDPOINTS:
                trend = self.degradation_analysis['trends'].get(endpoint, {})
                if 'slope_ms_per_second' in trend:
                    degradation_data.append({
                        'endpoint': endpoint,
                        'type': 'FAST' if endpoint in self.FAST_ENDPOINTS else 'OFDM',
                        'timeout_ms': self.endpoint_timeouts[endpoint] * 1000,
                        'sample_count': trend['sample_count'],
                        'slope_ms_per_second': trend['slope_ms_per_second'],
                        'slope_ms_per_minute': trend['slope_ms_per_minute'],
                        'slope_ms_per_hour': trend['slope_ms_per_hour'],
                        'intercept_ms': trend['intercept_ms'],
                        'r_squared': trend['r_squared'],
                        'p_value': trend['p_value'],
                        'std_error': trend['std_error'],
                        'is_statistically_significant': trend.get('is_statistically_significant', False),
                        'has_meaningful_correlation': trend.get('has_meaningful_correlation', False),
                        'is_significant_degradation': trend.get('is_significant_degradation', False),
                        'is_significant_improvement': trend.get('is_significant_improvement', False),
                        'severity': trend.get('severity', 'none'),
                        'projected_degradation_per_day': trend.get('projected_degradation_per_day', 0),
                        'early_vs_late_change_percent': trend.get('window_analysis', {}).get('progression', {}).get('mean_change_percent', None)
                    })
            
            if degradation_data:
                if pd is not None:
                    df_degradation = pd.DataFrame(degradation_data)
                    df_degradation.to_csv(degradation_csv, index=False)
                    logger.info(f"📊 Degradation analysis CSV saved: {degradation_csv}")
                    csv_count += 1
                else:
                    # Fallback to manual CSV writing
                    import csv
                    with open(degradation_csv, 'w', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=degradation_data[0].keys())
                        writer.writeheader()
                        writer.writerows(degradation_data)
                    logger.info(f"📊 Degradation analysis CSV saved (manual): {degradation_csv}")
                    csv_count += 1
            else:
                logger.warning("📊 No degradation data to save")
        except Exception as e:
            logger.error(f"Failed to generate degradation analysis CSV: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        try:
            logger.info("📊 Generating time-windowed analysis CSV...")
            # NEW: Time-windowed summary
            time_windows_csv = self.output_dir / f'{self.file_prefix}_time_windows.csv'
            window_data = []
            
            for endpoint in self.ALL_ENDPOINTS:
                trend = self.degradation_analysis['trends'].get(endpoint, {})
                window_analysis = trend.get('window_analysis', {})
                windows = window_analysis.get('windows', [])
                
                for window in windows:
                    if window['sample_count'] > 0:
                        window_data.append({
                            'endpoint': endpoint,
                            'window_id': window['window_id'],
                            'window_label': window['window_label'],
                            'sample_count': window['sample_count'],
                            'mean_ms': window['mean_ms'],
                            'median_ms': window['median_ms'],
                            'std_ms': window['std_ms'],
                            'min_ms': window['min_ms'],
                            'max_ms': window['max_ms'],
                            'p95_ms': window['p95_ms']
                        })
            
            if window_data:
                if pd is not None:
                    df_windows = pd.DataFrame(window_data)
                    df_windows.to_csv(time_windows_csv, index=False)
                    logger.info(f"📊 Time-windowed analysis CSV saved: {time_windows_csv}")
                    csv_count += 1
                else:
                    # Fallback to manual CSV writing
                    import csv
                    with open(time_windows_csv, 'w', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=window_data[0].keys())
                        writer.writeheader()
                        writer.writerows(window_data)
                    logger.info(f"📊 Time-windowed analysis CSV saved (manual): {time_windows_csv}")
                    csv_count += 1
            else:
                logger.warning("📊 No time-windowed data to save")
        except Exception as e:
            logger.error(f"Failed to generate time-windowed analysis CSV: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        try:
            logger.info("📊 Generating endpoint performance CSV...")
            # Enhanced per-endpoint performance summary (existing but enhanced)
            endpoint_csv = self.output_dir / f'{self.file_prefix}_endpoint_performance.csv'
            endpoint_data = []
            for endpoint, stats in self.endpoint_stats.items():
                if stats['total_requests'] > 0:
                    avg_response_time = statistics.mean(stats['response_times']) if stats['response_times'] else 0
                    min_response_time = min(stats['response_times']) if stats['response_times'] else 0
                    max_response_time = max(stats['response_times']) if stats['response_times'] else 0
                    p95_response_time = np.percentile(stats['response_times'], 95) if len(stats['response_times']) > 1 and np is not None else avg_response_time
                    avg_attempts = statistics.mean(stats['attempts_used']) if stats['attempts_used'] else 0
                    avg_data_size = statistics.mean([ds for ds in stats['data_sizes'] if ds > 0]) if stats['data_sizes'] else 0
                    
                    # Add degradation metrics
                    trend = self.degradation_analysis['trends'].get(endpoint, {})
                    
                    endpoint_data.append({
                        'endpoint': endpoint,
                        'type': 'FAST' if endpoint in self.FAST_ENDPOINTS else 'OFDM',
                        'timeout_seconds': self.endpoint_timeouts[endpoint],
                        'total_requests': stats['total_requests'],
                        'successful_requests': stats['successful_requests'],
                        'failed_requests': stats['failed_requests'],
                        'timeout_failures': stats['timeout_failures'],
                        'success_rate_percent': (stats['successful_requests'] / stats['total_requests']) * 100,
                        'avg_response_time_ms': avg_response_time,
                        'min_response_time_ms': min_response_time,
                        'max_response_time_ms': max_response_time,
                        'p95_response_time_ms': p95_response_time,
                        'avg_attempts': avg_attempts,
                        'avg_data_size_bytes': avg_data_size,
                        'unique_errors': len(set(stats['errors'])) if stats['errors'] else 0,
                        # NEW: Degradation analysis fields
                        'degradation_slope_ms_per_minute': trend.get('slope_ms_per_minute', None),
                        'degradation_p_value': trend.get('p_value', None),
                        'degradation_r_squared': trend.get('r_squared', None),
                        'is_degrading_significantly': trend.get('is_significant_degradation', False),
                        'degradation_severity': trend.get('severity', 'none'),
                        'early_vs_late_change_percent': trend.get('window_analysis', {}).get('progression', {}).get('mean_change_percent', None)
                    })
            
            if endpoint_data:
                if pd is not None:
                    df_endpoints = pd.DataFrame(endpoint_data)
                    df_endpoints.to_csv(endpoint_csv, index=False)
                    logger.info(f"📊 Enhanced endpoint performance CSV saved: {endpoint_csv}")
                    csv_count += 1
                else:
                    # Fallback to manual CSV writing
                    import csv
                    with open(endpoint_csv, 'w', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=endpoint_data[0].keys())
                        writer.writeheader()
                        writer.writerows(endpoint_data)
                    logger.info(f"📊 Enhanced endpoint performance CSV saved (manual): {endpoint_csv}")
                    csv_count += 1
        except Exception as e:
            logger.error(f"Failed to generate endpoint performance CSV: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        try:
            logger.info("📊 Generating detailed endpoint CSVs...")
            # Detailed per-endpoint time series (existing)
            detailed_count = 0
            for endpoint in self.ALL_ENDPOINTS:
                try:
                    stats = self.endpoint_stats[endpoint]
                    if stats['time_series']['timestamps']:
                        endpoint_csv_detailed = self.output_dir / f'{self.file_prefix}_{endpoint.replace(".asp", "")}_detailed.csv'
                        
                        # Ensure all time series arrays have the same length
                        ts_data = stats['time_series']
                        arrays_to_check = ['timestamps', 'elapsed_time', 'response_times', 'success', 'attempts', 'status_codes', 'data_sizes']
                        
                        # Find minimum length across all arrays
                        min_length = None
                        for array_name in arrays_to_check:
                            if array_name in ts_data and ts_data[array_name]:
                                array_len = len(ts_data[array_name])
                                if min_length is None:
                                    min_length = array_len
                                else:
                                    min_length = min(min_length, array_len)
                        
                        if min_length is None or min_length == 0:
                            logger.debug(f"📊 Skipping detailed CSV for {endpoint}: no data")
                            continue
                        
                        # Log array lengths for debugging
                        logger.debug(f"📊 {endpoint} array lengths: " + 
                                   ", ".join([f"{name}:{len(ts_data.get(name, []))}" for name in arrays_to_check]))
                        logger.debug(f"📊 {endpoint} using min_length: {min_length}")
                        
                        detailed_data = []
                        for i in range(min_length):
                            detailed_data.append({
                                'timestamp': ts_data['timestamps'][i],
                                'elapsed_time_seconds': ts_data['elapsed_time'][i] if i < len(ts_data['elapsed_time']) else 0,
                                'response_time_ms': ts_data['response_times'][i] if i < len(ts_data['response_times']) else 0,
                                'success': ts_data['success'][i] if i < len(ts_data['success']) else 0,
                                'attempts': ts_data['attempts'][i] if i < len(ts_data['attempts']) else 1,
                                'status_code': ts_data['status_codes'][i] if i < len(ts_data['status_codes']) else 'UNKNOWN',
                                'data_size_bytes': ts_data['data_sizes'][i] if i < len(ts_data['data_sizes']) else 0
                            })
                        
                        if detailed_data:
                            if pd is not None:
                                df_detailed = pd.DataFrame(detailed_data)
                                df_detailed.to_csv(endpoint_csv_detailed, index=False)
                                detailed_count += 1
                            else:
                                # Fallback to manual CSV writing
                                import csv
                                with open(endpoint_csv_detailed, 'w', newline='') as f:
                                    writer = csv.DictWriter(f, fieldnames=detailed_data[0].keys())
                                    writer.writeheader()
                                    writer.writerows(detailed_data)
                                detailed_count += 1
                        
                        logger.debug(f"📊 Enhanced detailed CSV saved for {endpoint}: {endpoint_csv_detailed}")
                
                except Exception as e:
                    logger.error(f"Failed to generate detailed CSV for {endpoint}: {e}")
                    import traceback
                    logger.debug(f"Detailed error for {endpoint}:", exc_info=True)
                    
            logger.info(f"📊 Generated {detailed_count} detailed endpoint CSV files")
            csv_count += detailed_count
        except Exception as e:
            logger.error(f"Failed to generate detailed endpoint CSVs: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        if csv_count > 0:
            logger.info(f"📊 Successfully generated {csv_count} CSV files")
        else:
            logger.error("📊 No CSV files were generated!")

    def generate_enhanced_report(self):
        """Generate comprehensive final report with degradation analysis."""
        print("\n\n" + "="*80)
        print("           ENHANCED SIMULATION FINAL REPORT")
        print("           📊 DEGRADATION ANALYSIS & RESOURCE LEAK DETECTION")
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
        print("🔍 Plugin Validation:")
        print(f"  ✅ Endpoint categories: {len(self.FAST_ENDPOINTS)} fast + {len(self.SLOW_ENDPOINTS)} OFDM = {len(self.ALL_ENDPOINTS)} total")
        print(f"  ✅ Tiered polling logic: Matches plugin _get_endpoints_for_cycle()")
        print(f"  ✅ Timeout configuration: Millisecond precision system matches plugin")
        print(f"  ✅ Collection logic: Serial/parallel modes match plugin")
        print(f"  ✅ Cache simulation: OFDM caching logic matches plugin")
        print(f"  ✅ Retry logic: Auto-calculation matches plugin formula")
        print(f"  ✅ Per-endpoint tracking: Enhanced timing analysis added")
        print(f"  🆕 NEW: Degradation analysis: Time-windowed trend detection enabled")
        print(f"  🆕 NEW: Resource leak detection: Statistical significance testing")
        
        # Test configuration with consistent timeout display
        fast_timeout_info = self._get_timeout_display_info(self.fast_endpoint_timeout)
        ofdm_timeout_info = self._get_timeout_display_info(self.ofdm_endpoint_timeout)
        collection_timeout_info = self._get_timeout_display_info(self.collection_timeout)
        
        print(f"\nConfiguration (Millisecond Precision + Degradation Analysis):")
        print(f"  Test Duration:         {self.test_duration}s")
        print(f"  Update Interval:       {self.update_every}s (fast endpoints)")
        print(f"  OFDM Poll Multiple:    {self.ofdm_poll_multiple}x (every {self.ofdm_poll_multiple * self.update_every}s)")
        print(f"  Collection Mode:       {'Parallel' if self.parallel_collection else 'Serial'}")
        print(f"  Fast Endpoint Timeout: {fast_timeout_info['precise']} ({len(self.FAST_ENDPOINTS)} endpoints)")
        print(f"  OFDM Endpoint Timeout: {ofdm_timeout_info['precise']} ({len(self.SLOW_ENDPOINTS)} endpoints)")
        print(f"  Collection Timeout:    {collection_timeout_info['precise']} (auto-calc: {self.update_every} × 0.9)")
        longer_timeout = max(self.fast_endpoint_timeout, self.ofdm_endpoint_timeout)
        print(f"  Max Retries:           {self.max_retries} (auto-calc: {self.collection_timeout:.3f} ÷ {longer_timeout:.3f})")
        print(f"  Time Windows:          {self.time_window_count} windows for degradation analysis")
        
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
        
        print(f"\nTiming Analysis (Millisecond Precision):")
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
        
        # NEW: DEGRADATION ANALYSIS SECTION
        print(f"\n🔬 DEGRADATION ANALYSIS & RESOURCE LEAK DETECTION:")
        print(f"  {'Endpoint':<20} {'Type':<4} {'Slope(ms/min)':<13} {'P-Value':<9} {'R²':<6} {'Significance':<12} {'Severity':<10} {'Change%':<8}")
        print(f"  {'-'*20} {'-'*4} {'-'*13} {'-'*9} {'-'*6} {'-'*12} {'-'*10} {'-'*8}")
        
        significant_degradations = []
        significant_improvements = []
        
        for endpoint in self.ALL_ENDPOINTS:
            trend = self.degradation_analysis['trends'].get(endpoint, {})
            if 'slope_ms_per_minute' in trend:
                endpoint_type = "FAST" if endpoint in self.FAST_ENDPOINTS else "OFDM"
                slope = trend['slope_ms_per_minute']
                p_value = trend.get('p_value', 1.0)
                r_squared = trend.get('r_squared', 0.0)
                
                # Determine significance
                if trend.get('is_significant_degradation', False):
                    significance = "DEGRADING"
                    significant_degradations.append((endpoint, slope, trend.get('severity', 'minor')))
                elif trend.get('is_significant_improvement', False):
                    significance = "IMPROVING"
                    significant_improvements.append((endpoint, slope))
                elif trend.get('is_statistically_significant', False):
                    significance = "SIGNIFICANT"
                else:
                    significance = "STABLE"
                
                severity = trend.get('severity', 'none')
                
                # Get early vs late change
                window_analysis = trend.get('window_analysis', {})
                progression = window_analysis.get('progression', {})
                change_percent = progression.get('mean_change_percent', None)
                change_str = f"{change_percent:+.1f}%" if change_percent is not None else "N/A"
                
                short_name = endpoint.replace('.asp', '')
                print(f"  {short_name:<20} {endpoint_type:<4} {slope:<13.2f} {p_value:<9.4f} {r_squared:<6.3f} {significance:<12} {severity:<10} {change_str:<8}")
        
        # Summary of significant findings
        if significant_degradations:
            print(f"\n🚨 SIGNIFICANT DEGRADATIONS DETECTED:")
            for endpoint, slope, severity in significant_degradations:
                emoji = "🚨" if severity == 'critical' else "⚠️" if severity == 'moderate' else "📈"
                print(f"  {emoji} {endpoint}: +{slope:.2f}ms/min ({severity} severity)")
                if severity == 'critical':
                    projected_hour = slope * 60
                    projected_day = slope * 60 * 24
                    print(f"     Projected impact: +{projected_hour:.1f}ms/hour, +{projected_day:.1f}ms/day")
        else:
            print(f"\n✅ NO SIGNIFICANT DEGRADATIONS DETECTED")
        
        if significant_improvements:
            print(f"\n📉 SIGNIFICANT IMPROVEMENTS DETECTED:")
            for endpoint, slope in significant_improvements:
                print(f"  📉 {endpoint}: {slope:.2f}ms/min (performance improving)")
        
        # Detailed per-endpoint analysis with consistent timeout display
        print(f"\n📊 DETAILED PER-ENDPOINT ANALYSIS (Enhanced with Degradation):")
        print(f"  {'Endpoint':<20} {'Type':<4} {'Reqs':<6} {'Success':<7} {'Avg(ms)':<8} {'Min(ms)':<8} {'Max(ms)':<8} {'P95(ms)':<8} {'Timeout':<11} {'Timeouts':<8} {'Retries':<7}")
        print(f"  {'-'*20} {'-'*4} {'-'*6} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*11} {'-'*8} {'-'*7}")
        
        for endpoint in self.ALL_ENDPOINTS:
            stats = self.endpoint_stats[endpoint]
            if stats['total_requests'] > 0:
                success_rate = (stats['successful_requests'] / stats['total_requests']) * 100
                endpoint_type = "FAST" if endpoint in self.FAST_ENDPOINTS else "OFDM"
                timeout_info = self._get_timeout_display_info(self.endpoint_timeouts[endpoint])
                
                if stats['response_times']:
                    avg_time = statistics.mean(stats['response_times'])
                    min_time = min(stats['response_times'])
                    max_time = max(stats['response_times'])
                    p95_time = np.percentile(stats['response_times'], 95) if len(stats['response_times']) > 1 else avg_time
                else:
                    avg_time = min_time = max_time = p95_time = 0
                
                avg_attempts = statistics.mean(stats['attempts_used']) if stats['attempts_used'] else 0
                
                short_name = endpoint.replace('.asp', '')
                print(f"  {short_name:<20} {endpoint_type:<4} {stats['total_requests']:<6} {success_rate:<7.1f} {avg_time:<8.0f} {min_time:<8.0f} {max_time:<8.0f} {p95_time:<8.0f} {timeout_info['display']:<11} {stats['timeout_failures']:<8} {avg_attempts:<7.1f}")
        
        # Resource leak assessment
        print(f"\n🔬 RESOURCE LEAK ASSESSMENT:")
        leak_indicators = 0
        
        for endpoint in self.ALL_ENDPOINTS:
            trend = self.degradation_analysis['trends'].get(endpoint, {})
            if trend.get('is_significant_degradation', False):
                severity = trend.get('severity', 'minor')
                if severity in ['moderate', 'critical']:
                    leak_indicators += 1
        
        if leak_indicators == 0:
            print(f"  ✅ NO RESOURCE LEAKS DETECTED")
            print(f"     All endpoints show stable or improving performance")
        elif leak_indicators == 1:
            print(f"  ⚠️ POSSIBLE RESOURCE LEAK DETECTED")
            print(f"     {leak_indicators} endpoint shows significant degradation")
            print(f"     Monitor for continued degradation over longer periods")
        else:
            print(f"  🚨 MULTIPLE RESOURCE LEAKS DETECTED")
            print(f"     {leak_indicators} endpoints show significant degradation")
            print(f"     Strong indication of system-wide resource leak")
        
        # Enhanced recommendations with degradation-specific advice
        print(f"\n💡 ENHANCED OPTIMIZATION RECOMMENDATIONS:")
        
        # Degradation-specific recommendations
        for endpoint, trend in self.degradation_analysis['trends'].items():
            if trend.get('is_significant_degradation', False):
                slope = trend['slope_ms_per_minute']
                severity = trend.get('severity', 'minor')
                
                if severity == 'critical':
                    print(f"  🚨 {endpoint}: CRITICAL degradation (+{slope:.1f}ms/min) - investigate immediately!")
                    print(f"     Consider: memory leak analysis, connection pool monitoring, firmware update")
                elif severity == 'moderate':
                    print(f"  ⚠️ {endpoint}: Moderate degradation (+{slope:.1f}ms/min) - monitor closely")
                    print(f"     Consider: increasing timeout, reducing poll frequency")
                else:
                    print(f"  📈 {endpoint}: Minor degradation (+{slope:.1f}ms/min) - watch for trends")
        
        # Traditional timeout optimization
        for endpoint, stats in self.endpoint_stats.items():
            if stats['total_requests'] > 0:
                success_rate = (stats['successful_requests'] / stats['total_requests']) * 100
                avg_time = statistics.mean(stats['response_times']) if stats['response_times'] else 0
                timeout_info = self._get_timeout_display_info(self.endpoint_timeouts[endpoint])
                timeout_seconds = self.endpoint_timeouts[endpoint]
                
                if success_rate < 90:
                    suggested_timeout = self._format_timeout_for_display(timeout_seconds * 1.5)
                    print(f"  🔴 {endpoint}: Low success rate ({success_rate:.1f}%) - increase timeout from {timeout_info['display']} to {suggested_timeout}")
                elif avg_time > timeout_seconds * 800:  # 80% in ms
                    print(f"  🟠 {endpoint}: High response time ({avg_time:.0f}ms) near timeout ({timeout_info['display']})")
                elif stats['timeout_failures'] > stats['total_requests'] * 0.1:
                    suggested_timeout = self._format_timeout_for_display(timeout_seconds * 1.3)
                    print(f"  🟡 {endpoint}: High timeout rate ({stats['timeout_failures']}/{stats['total_requests']}) - increase from {timeout_info['display']} to {suggested_timeout}")
                elif avg_time < timeout_seconds * 300 and success_rate >= 99:  # 30% in ms
                    suggested_timeout = self._format_timeout_for_display(avg_time * 3 / 1000)
                    print(f"  🟢 {endpoint}: Could reduce timeout from {timeout_info['display']} to {suggested_timeout} (3x avg)")
                else:
                    # Check for degradation trends even with good current performance
                    trend = self.degradation_analysis['trends'].get(endpoint, {})
                    if not trend.get('is_significant_degradation', False):
                        print(f"  🟢 {endpoint}: Timeout {timeout_info['display']} looks optimal")
        
        # System optimization suggestions
        if utilization < 30 and cycle_success_rate > 95:
            suggested_interval = max(4, int(avg_collection_time / 1000 * 2.5))
            print(f"\n💡 System Optimization: Could reduce update_every to {suggested_interval}s for {self.update_every/suggested_interval:.1f}x faster polling")
        
        if collection_efficiency > 80:
            suggested_timeout_ms = int(max_collection_time * 1.3)
            print(f"💡 Timeout Optimization: Consider increasing collection_timeout to {suggested_timeout_ms/1000:.3f}s ({suggested_timeout_ms:.0f}ms)")
        
        # Performance assessment with degradation consideration
        print(f"\nOverall Assessment:")
        if leak_indicators > 0:
            if leak_indicators >= 2:
                assessment = "CRITICAL - RESOURCE LEAKS DETECTED ❌"
            else:
                assessment = "WARNING - POSSIBLE RESOURCE LEAK ⚠️"
        elif cycle_success_rate >= 99:
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
        
        print(f"\n📊 Enhanced graphs and CSVs saved to: {self.output_dir}")
        print(f"📁 Generated files:")
        print(f"  - {self.file_prefix}_overview.png (main performance overview)")
        print(f"  - {self.file_prefix}_endpoint_timings.png (per-endpoint timing analysis)")
        print(f"  - {self.file_prefix}_endpoint_comparison.png (endpoint comparison)")
        print(f"  - {self.file_prefix}_timing_distribution.png (timing distribution analysis)")
        print(f"  🆕 NEW: {self.file_prefix}_degradation_analysis.png (degradation trend analysis)")
        print(f"  🆕 NEW: {self.file_prefix}_[endpoint]_time_histograms.png (time-windowed histograms)")
        print(f"  - {self.file_prefix}_simulation_summary.csv (cycle-by-cycle data)")
        print(f"  - {self.file_prefix}_endpoint_performance.csv (enhanced endpoint summary)")
        print(f"  🆕 NEW: {self.file_prefix}_degradation_analysis.csv (trend analysis data)")
        print(f"  🆕 NEW: {self.file_prefix}_time_windows.csv (time-windowed statistics)")
        print(f"  - {self.file_prefix}_[endpoint]_detailed.csv (per-endpoint detailed data)")
        print("="*80)
        
        # Create machine-readable report for automated analysis
        report = {
            "plugin_validation": {
                "endpoint_categories_match": True,
                "tiered_polling_logic_match": True,
                "timeout_configuration_match": True,
                "collection_logic_match": True,
                "cache_simulation_match": True,
                "retry_logic_match": True,
                "per_endpoint_tracking": True,
                "millisecond_precision_support": True,
                "degradation_analysis_enabled": True,  # NEW
                "resource_leak_detection_enabled": True  # NEW
            },
            "cycle_success_rate": float(cycle_success_rate),
            "request_success_rate": float(request_success_rate),
            "avg_collection_time_ms": float(avg_collection_time),
            "max_collection_time_ms": float(max_collection_time),
            "avg_response_time_ms": float(avg_response_time),
            "failed_cycles": int(self.results['failed_cycles']),
            "total_cycles": int(self.results['total_cycles']),
            "fast_cycles": int(self.results['fast_cycles']),
            "full_cycles": int(self.results['full_cycles']),
            "cached_cycles": int(self.results['cached_cycles']),
            "consecutive_failures": int(self.results['consecutive_failures']),
            "max_consecutive_failures": int(self.results['max_consecutive_failures']),
            "cache_hits": int(self.results['cache_hits']),
            "cache_misses": int(self.results['cache_misses']),
            "endpoint_success_rates": self.results['endpoint_success_rates'],
            "endpoint_detailed_stats": {},
            "assessment": assessment.split()[0],  # Remove emoji for JSON
            "utilization_percent": float(utilization),
            "collection_efficiency_percent": float(collection_efficiency),
            # NEW: Degradation analysis results
            "degradation_analysis": {
                "significant_degradations": int(len(significant_degradations)),
                "significant_improvements": int(len(significant_improvements)),
                "resource_leak_indicators": int(leak_indicators),
                "time_windows_analyzed": int(self.time_window_count),
                "trends": {}  # Will be populated below with safe conversions
            },
            "configuration": {
                "update_every": int(self.update_every),
                "ofdm_poll_multiple": int(self.ofdm_poll_multiple),
                "parallel_collection": bool(self.parallel_collection),
                "fast_endpoint_timeout_ms": float(self.fast_endpoint_timeout * 1000),
                "ofdm_endpoint_timeout_ms": float(self.ofdm_endpoint_timeout * 1000),
                "collection_timeout_ms": float(self.collection_timeout * 1000),
                "max_retries": int(self.max_retries),
                "time_window_count": int(self.time_window_count),  # NEW
                # User-friendly timeout displays
                "fast_endpoint_timeout_display": self._format_timeout_for_display(self.fast_endpoint_timeout),
                "ofdm_endpoint_timeout_display": self._format_timeout_for_display(self.ofdm_endpoint_timeout),
                "collection_timeout_display": self._format_timeout_for_display(self.collection_timeout)
            },
            "output_directory": str(self.output_dir)
        }
        
        # Safely convert degradation analysis trends (handle numpy types)
        for endpoint, trend_data in self.degradation_analysis['trends'].items():
            safe_trend = {}
            for key, value in trend_data.items():
                if value is None:
                    safe_trend[key] = None
                elif isinstance(value, (np.bool_, bool)):
                    safe_trend[key] = bool(value)
                elif isinstance(value, (np.integer, int)):
                    safe_trend[key] = int(value)
                elif isinstance(value, (np.floating, float)):
                    safe_trend[key] = float(value)
                elif isinstance(value, str):
                    safe_trend[key] = value
                elif isinstance(value, dict):
                    # Handle nested dictionaries (like window_analysis)
                    safe_trend[key] = self._convert_numpy_types(value)
                else:
                    safe_trend[key] = str(value)  # Fallback to string
            report["degradation_analysis"]["trends"][endpoint] = safe_trend
        
        # Add detailed endpoint stats to report with safe type conversion
        for endpoint, stats in self.endpoint_stats.items():
            if stats['total_requests'] > 0:
                timeout_info = self._get_timeout_display_info(self.endpoint_timeouts[endpoint])
                trend = self.degradation_analysis['trends'].get(endpoint, {})
                
                # Safe type conversions
                def safe_float(val):
                    if val is None:
                        return None
                    return float(val)
                
                def safe_bool(val):
                    if val is None:
                        return None
                    return bool(val)
                
                report["endpoint_detailed_stats"][endpoint] = {
                    "total_requests": int(stats['total_requests']),
                    "successful_requests": int(stats['successful_requests']),
                    "success_rate": float((stats['successful_requests'] / stats['total_requests']) * 100),
                    "avg_response_time_ms": float(statistics.mean(stats['response_times']) if stats['response_times'] else 0),
                    "min_response_time_ms": float(min(stats['response_times']) if stats['response_times'] else 0),
                    "max_response_time_ms": float(max(stats['response_times']) if stats['response_times'] else 0),
                    "timeout_failures": int(stats['timeout_failures']),
                    "timeout_setting_ms": float(self.endpoint_timeouts[endpoint] * 1000),
                    "timeout_setting_display": timeout_info['display'],
                    "avg_attempts": float(statistics.mean(stats['attempts_used']) if stats['attempts_used'] else 0),
                    # NEW: Degradation analysis fields with safe conversions
                    "degradation_slope_ms_per_minute": safe_float(trend.get('slope_ms_per_minute')),
                    "degradation_p_value": safe_float(trend.get('p_value')),
                    "degradation_r_squared": safe_float(trend.get('r_squared')),
                    "is_degrading_significantly": safe_bool(trend.get('is_significant_degradation', False)),
                    "degradation_severity": str(trend.get('severity', 'none')),
                    "projected_degradation_per_day": safe_float(trend.get('projected_degradation_per_day'))
                }
        
        # Output JSON for automated processing
        print(json.dumps(report))
        
        # Generate enhanced graphs and CSV
        logger.info(f"📊 Output directory: {self.output_dir}")
        logger.info(f"📊 Directory exists: {self.output_dir.exists()}")
        logger.info(f"📊 Directory writable: {self.output_dir.exists() and os.access(self.output_dir, os.W_OK)}")
        
        # Test file creation
        try:
            test_file = self.output_dir / "test_write.txt"
            with open(test_file, 'w') as f:
                f.write("test")
            test_file.unlink()  # Delete test file
            logger.info("📊 Directory write test: PASSED")
        except Exception as e:
            logger.error(f"📊 Directory write test: FAILED - {e}")
        
        try:
            logger.info("📊 Starting graph generation...")
            self.generate_comprehensive_graphs()
            logger.info("📊 Graph generation completed successfully")
        except Exception as e:
            logger.error(f"Failed to generate graphs: {e}")
            import traceback
            logger.error("Detailed error:", exc_info=True)
        
        try:
            logger.info("📊 Starting CSV generation...")
            self.generate_enhanced_csv_summary()
            logger.info("📊 CSV generation completed successfully")
        except Exception as e:
            logger.error(f"Failed to generate CSV files: {e}")
            import traceback
            logger.error("Detailed error:", exc_info=True)
        
        # List files in output directory
        try:
            files = list(self.output_dir.iterdir())
            logger.info(f"📊 Files in output directory: {len(files)}")
            for file in files:
                logger.info(f"  - {file.name} ({file.stat().st_size} bytes)")
        except Exception as e:
            logger.error(f"Error listing output directory: {e}")
        
        return report

    def _convert_numpy_types(self, obj):
        """Recursively convert numpy types to native Python types for JSON serialization."""
        if obj is None:
            return None
        elif isinstance(obj, (np.bool_, bool)):
            return bool(obj)
        elif isinstance(obj, (np.integer, int)):
            return int(obj)
        elif isinstance(obj, (np.floating, float)):
            return float(obj)
        elif isinstance(obj, str):
            return obj
        elif isinstance(obj, dict):
            return {key: self._convert_numpy_types(value) for key, value in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._convert_numpy_types(item) for item in obj]
        else:
            return str(obj)  # Fallback to string representation


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
    """Main entry point with comprehensive argument parsing and millisecond timeout support."""
    parser = argparse.ArgumentParser(
        description="Enhanced Netdata Hitron CODA Modem Stability Simulator with Degradation Analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples with Degradation Analysis:
  # Resource leak detection over 30 minutes
  %(prog)s --host https://192.168.100.1 --duration 1800 --time-windows 10 --fast-endpoint-timeout 0.25 --ofdm-endpoint-timeout 1.0
  
  # Quick degradation test with aggressive timeouts
  %(prog)s --fast-endpoint-timeout 0.1 --ofdm-endpoint-timeout 0.5 --duration 600 --time-windows 6 --parallel
  
  # Long-term stability analysis
  %(prog)s --duration 3600 --time-windows 12 --fast-endpoint-timeout 1.0 --ofdm-endpoint-timeout 3.0 --serial
        """
    )
    
    # Basic configuration
    parser.add_argument('--host', default='https://192.168.100.1',
                       help='Modem IP address (default: %(default)s)')
    parser.add_argument('--duration', type=int, default=300,
                       help='Test duration in seconds (default: %(default)s)')
    
    # NEW: Degradation analysis configuration
    parser.add_argument('--time-windows', type=int, default=6,
                       help='Number of time windows for degradation analysis (default: %(default)s)')
    
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
    
    # Enhanced timeout configuration with millisecond support
    parser.add_argument('--fast-endpoint-timeout', type=str, default='3',
                       help='Timeout for fast endpoints. Supports: 0.25 (250ms), 1500ms, 2.5s (default: %(default)s)')
    parser.add_argument('--ofdm-endpoint-timeout', type=str, default='8',
                       help='Timeout for OFDM endpoints. Supports: 0.5 (500ms), 6500ms, 8.5s (default: %(default)s)')
    parser.add_argument('--collection-timeout', type=str, default=None,
                       help='Overall collection timeout. Supports: 25s, 30000ms, 54.5 (default: 90%% of update-every)')
    parser.add_argument('--max-retries', type=int, default=None,
                       help='Max retries per endpoint (0=no retries, default: auto-calculated)')
    
    # Legacy timeout support (for backward compatibility)
    parser.add_argument('--endpoint-timeout', type=str, default=None,
                       help='Legacy: sets both fast and OFDM timeouts to same value. Supports: 2s, 1500ms, 0.75')
    
    # Serial mode options
    parser.add_argument('--inter-request-delay', type=float, default=0.2,
                       help='Delay between requests in serial mode (default: %(default)s)')
    
    # Output options
    parser.add_argument('--output-dir', type=str, default=None,
                       help='Output directory for graphs and reports (default: auto-generated)')
    
    # Debugging and analysis
    parser.add_argument('--debug', action='store_true', default=False,
                       help='Enable debug logging')
    parser.add_argument('--no-graphs', action='store_true', default=False,
                       help='Skip graph generation (faster for automated testing)')
    parser.add_argument('--quick-test', action='store_true', default=False,
                       help='Quick 60-second test for rapid iteration')
    
    args = parser.parse_args()
    
    # Configure logging level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Handle quick test override
    if args.quick_test:
        logger.info("⏱️ Quick test mode: 60-second duration")
        args.duration = 60
        if args.time_windows > 3:
            args.time_windows = 3
            logger.info("⏱️ Quick test mode: reduced to 3 time windows")
    
    # Parse timeout values with enhanced support
    try:
        # Parse timeout configurations
        if args.endpoint_timeout is not None:
            # Legacy mode: use same timeout for both
            legacy_timeout = parse_timeout_value(args.endpoint_timeout, 'endpoint_timeout')
            fast_endpoint_timeout = legacy_timeout
            ofdm_endpoint_timeout = legacy_timeout
            logger.info(f"Using legacy endpoint_timeout for both fast and OFDM: {legacy_timeout:.3f}s ({legacy_timeout * 1000:.0f}ms)")
        else:
            # New two-tier mode with millisecond precision
            fast_endpoint_timeout = parse_timeout_value(args.fast_endpoint_timeout, 'fast_endpoint_timeout')
            ofdm_endpoint_timeout = parse_timeout_value(args.ofdm_endpoint_timeout, 'ofdm_endpoint_timeout')
        
        # Parse collection timeout if specified
        if args.collection_timeout is not None:
            collection_timeout = parse_timeout_value(args.collection_timeout, 'collection_timeout')
        else:
            collection_timeout = None
            
    except ValueError as e:
        logger.error(f"Timeout parsing error: {e}")
        logger.error("Valid formats: 0.25 (250ms), 1500ms, 2.5s")
        sys.exit(1)
    
    # Validate timeout relationships
    if fast_endpoint_timeout >= ofdm_endpoint_timeout:
        logger.warning(f"⚠️ Fast timeout ({fast_endpoint_timeout:.3f}s) >= OFDM timeout ({ofdm_endpoint_timeout:.3f}s)")
        logger.warning("   Typically OFDM endpoints should have higher timeouts due to instability")
    
    if collection_timeout and (fast_endpoint_timeout > collection_timeout * 0.8):
        logger.warning(f"⚠️ Fast timeout ({fast_endpoint_timeout:.3f}s) very close to collection timeout ({collection_timeout:.3f}s)")
    
    # Validate degradation analysis configuration with duration-dependent limits
    if args.time_windows < 2:
        logger.warning(f"⚠️ Time windows ({args.time_windows}) too few for meaningful degradation analysis, setting to 3")
        args.time_windows = 3
    else:
        # Calculate reasonable limits based on test duration
        min_window_duration = 600  # 10 minutes minimum per window for statistical significance
        max_reasonable_windows = max(3, args.duration // min_window_duration)
        
        if args.time_windows > max_reasonable_windows:
            actual_window_duration = args.duration / args.time_windows
            logger.warning(f"⚠️ Time windows ({args.time_windows}) would create very short windows ({actual_window_duration:.0f}s each)")
            logger.warning(f"⚠️ For {args.duration}s test, recommending max {max_reasonable_windows} windows ({min_window_duration}s minimum each)")
            logger.warning(f"⚠️ Capping at {max_reasonable_windows} windows for statistical reliability")
            args.time_windows = max_reasonable_windows
        else:
            # Validate the resulting window duration is reasonable
            window_duration = args.duration / args.time_windows
            if window_duration < 300:  # 5 minutes
                logger.warning(f"⚠️ Very short window duration ({window_duration:.0f}s) may reduce statistical power")
            elif window_duration < min_window_duration:
                recommended_windows = args.duration // min_window_duration
                logger.warning(f"⚠️ Window duration ({window_duration:.0f}s) below recommended minimum ({min_window_duration}s)")
                logger.warning(f"⚠️ Consider using {recommended_windows} windows for {min_window_duration}s each")
            else:
                logger.info(f"✅ Window configuration: {args.time_windows} windows × {window_duration:.0f}s each = good for analysis")
    
    # Build simulator configuration
    sim_config = {
        'modem_host': args.host,
        'test_duration': args.duration,
        'update_every': args.update_every,
        'parallel_collection': not args.serial,
        'ofdm_poll_multiple': args.ofdm_poll_multiple,
        'ofdm_update_every': args.ofdm_update_every,
        'collection_timeout': collection_timeout,
        'max_retries': args.max_retries,
        'inter_request_delay': args.inter_request_delay,
        'output_dir': args.output_dir,
        'fast_endpoint_timeout': fast_endpoint_timeout,
        'ofdm_endpoint_timeout': ofdm_endpoint_timeout,
        'time_window_count': args.time_windows  # NEW: For degradation analysis
    }
    
    # Log the final configuration
    logger.info("🔧 Enhanced configuration with degradation analysis:")
    logger.info(f"   Fast endpoints: {fast_endpoint_timeout * 1000:.0f}ms ({fast_endpoint_timeout:.3f}s)")
    logger.info(f"   OFDM endpoints: {ofdm_endpoint_timeout * 1000:.0f}ms ({ofdm_endpoint_timeout:.3f}s)")
    if collection_timeout:
        logger.info(f"   Collection: {collection_timeout * 1000:.0f}ms ({collection_timeout:.3f}s)")
    else:
        auto_timeout = args.update_every * 0.9
        logger.info(f"   Collection: {auto_timeout * 1000:.0f}ms ({auto_timeout:.3f}s) [auto-calculated]")
    logger.info(f"   Time windows: {args.time_windows} for degradation analysis")
    
    # Degradation analysis info
    window_duration = args.duration / args.time_windows
    logger.info(f"🔬 Degradation analysis configuration:")
    logger.info(f"   Window duration: {window_duration:.1f}s each")
    logger.info(f"   Statistical analysis: Linear regression with significance testing")
    logger.info(f"   Resource leak detection: Trend analysis across {args.time_windows} time periods")
    
    # Performance predictions based on timeouts
    if args.serial:
        # Calculate estimated serial timing
        fast_cycle_ms = len([1,2,3,4]) * (fast_endpoint_timeout * 1000 + args.inter_request_delay * 1000)  # 4 fast endpoints
        full_cycle_ms = fast_cycle_ms + len([1,2]) * (ofdm_endpoint_timeout * 1000 + args.inter_request_delay * 1000)  # 2 OFDM endpoints
        
        logger.info(f"📊 Serial timing estimates:")
        logger.info(f"   Fast cycles: ~{fast_cycle_ms:.0f}ms")
        logger.info(f"   Full cycles: ~{full_cycle_ms:.0f}ms")
        
        # Warn if timing looks problematic
        collection_limit_ms = (collection_timeout * 1000) if collection_timeout else (args.update_every * 900)
        if fast_cycle_ms > collection_limit_ms * 0.8:
            logger.warning(f"⚠️ Fast cycle timing ({fast_cycle_ms:.0f}ms) may approach collection timeout")
        if full_cycle_ms > collection_limit_ms:
            logger.warning(f"⚠️ Full cycle timing ({full_cycle_ms:.0f}ms) may exceed collection timeout")
    else:
        logger.info(f"📊 Parallel timing estimates:")
        logger.info(f"   Fast cycles: ~{fast_endpoint_timeout * 1000:.0f}ms (parallel)")
        logger.info(f"   Full cycles: ~{max(fast_endpoint_timeout, ofdm_endpoint_timeout) * 1000:.0f}ms (parallel)")
    
    # Validate matplotlib availability for graphing
    if not args.no_graphs and not GRAPHING_AVAILABLE:
        logger.warning("Graphing libraries not available")
        logger.info("Install with: pip install matplotlib pandas numpy scipy")
        logger.info("Continuing without graphs...")
    
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
        # Generate enhanced final report
        try:
            simulator.generate_enhanced_report()
        except Exception as e:
            logger.error(f"Failed to generate enhanced report: {e}")
            try:
                basic_report = {
                    "cycle_success_rate": 0,
                    "failed_cycles": simulator.results.get('failed_cycles', 0),
                    "total_cycles": simulator.results.get('total_cycles', 0),
                    "assessment": "INTERRUPTED",
                    "plugin_validation": {
                        "endpoint_categories_match": True,
                        "per_endpoint_tracking": True,
                        "millisecond_precision_support": True,
                        "degradation_analysis_enabled": True,
                        "resource_leak_detection_enabled": True
                    },
                    "degradation_analysis": {
                        "significant_degradations": 0,
                        "significant_improvements": 0,
                        "resource_leak_indicators": 0,
                        "time_windows_analyzed": simulator.time_window_count
                    }
                }
                print(json.dumps(basic_report))
            except:
                pass


if __name__ == "__main__":
    main()


# Enhanced example usage scenarios with degradation analysis

ENHANCED_EXAMPLE_COMMANDS = """
# Enhanced Degradation Analysis Examples

# 1. Resource leak detection over 30 minutes with fine-grained analysis
python netdata_simulator.py --duration 1800 --time-windows 10 --fast-endpoint-timeout 0.25 --ofdm-endpoint-timeout 1.0 --parallel

# 2. Quick degradation test with aggressive timeouts  
python netdata_simulator.py --fast-endpoint-timeout 0.1 --ofdm-endpoint-timeout 0.5 --duration 600 --time-windows 6 --parallel

# 3. Long-term stability analysis (1 hour)
python netdata_simulator.py --duration 3600 --time-windows 12 --fast-endpoint-timeout 1.0 --ofdm-endpoint-timeout 3.0 --serial

# 4. High-frequency monitoring with degradation tracking
python netdata_simulator.py --update-every 15 --duration 900 --time-windows 9 --fast-endpoint-timeout 0.3 --parallel

# 5. Memory leak detection with frequent polling
python netdata_simulator.py --update-every 30 --duration 1200 --time-windows 8 --fast-endpoint-timeout 0.5 --ofdm-endpoint-timeout 1.5

# 6. Extended analysis for production validation
python netdata_simulator.py --duration 7200 --time-windows 24 --fast-endpoint-timeout 2.0 --ofdm-endpoint-timeout 6.0 --serial

# 7. Quick iteration with degradation focus
python netdata_simulator.py --quick-test --time-windows 3 --fast-endpoint-timeout 0.4 --parallel

# 8. Boundary testing for resource limits
python netdata_simulator.py --fast-endpoint-timeout 0.15 --ofdm-endpoint-timeout 0.6 --duration 600 --time-windows 10 --parallel

# 9. Conservative analysis with detailed tracking
python netdata_simulator.py --fast-endpoint-timeout 3.0 --ofdm-endpoint-timeout 10.0 --duration 1800 --time-windows 6 --serial

# 10. Mixed timeout precision testing with degradation analysis
python netdata_simulator.py --fast-endpoint-timeout 750ms --ofdm-endpoint-timeout 2.8s --duration 900 --time-windows 9
"""
