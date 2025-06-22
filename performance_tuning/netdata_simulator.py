#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Enhanced Netdata Modem Simulator for Stability Testing.

This script simulates the behavior of the hitron_coda.chart.py plugin,
including the tiered polling logic, to help determine optimal polling
configurations for Hitron CODA modems.

Key Features:
- Tiered polling simulation (fast vs slow endpoints)
- Parallel and serial collection modes
- Comprehensive performance metrics
- Real-time progress monitoring
- JSON output for automated analysis

Version: 2.0.0
Author: Enhanced for tiered polling analysis
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
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class NetdataModemSimulator:
    """
    Enhanced simulator for testing Hitron CODA modem tiered polling strategies.
    
    Mirrors the actual plugin's endpoint categorization and polling logic
    to provide accurate performance predictions.
    """

    # --- Endpoint Categories (Mirror plugin exactly) ---
    FAST_ENDPOINTS = [
        'dsinfo.asp',         # Downstream QAM channels - Critical data
        'usinfo.asp',         # Upstream QAM channels - Critical data  
        'getCmDocsisWan.asp', # WAN status - Connection health
        'getViewInfo.asp'     # System uptime - Basic health
    ]
    
    SLOW_ENDPOINTS = [
        'dsofdminfo.asp',     # Downstream OFDM - Can cause instability
        'usofdminfo.asp'      # Upstream OFDM - Less critical
    ]
    
    ALL_ENDPOINTS = FAST_ENDPOINTS + SLOW_ENDPOINTS

    def __init__(self, **kwargs):
        """Initialize the simulator with enhanced two-tier timeout configuration."""
        
        # --- Basic Configuration ---
        self.modem_host = kwargs.get('modem_host')
        self.update_every = kwargs.get('update_every')
        self.test_duration = kwargs.get('test_duration')
        
        # --- Collection Strategy ---
        self.parallel_collection = kwargs.get('parallel_collection', False)
        self.inter_request_delay = kwargs.get('inter_request_delay', 0.2)
        
        # --- Enhanced Two-Tier Timeout Configuration ---
        self.fast_endpoint_timeout = kwargs.get('fast_endpoint_timeout', 3)
        self.ofdm_endpoint_timeout = kwargs.get('ofdm_endpoint_timeout', 8)
        
        # Create endpoint timeout mapping
        self.endpoint_timeouts = {}
        for endpoint in self.FAST_ENDPOINTS:
            self.endpoint_timeouts[endpoint] = self.fast_endpoint_timeout
        for endpoint in self.SLOW_ENDPOINTS:
            self.endpoint_timeouts[endpoint] = self.ofdm_endpoint_timeout
        
        # --- Tiered Polling Configuration ---
        self.run_counter = 0
        ofdm_update_every = kwargs.get('ofdm_update_every', 0)
        self.ofdm_poll_multiple = kwargs.get('ofdm_poll_multiple', 5)
        
        # If specific OFDM interval is set, calculate the multiple
        if ofdm_update_every > 0:
            if ofdm_update_every < self.update_every:
                self.ofdm_poll_multiple = 1
            else:
                self.ofdm_poll_multiple = max(1, round(ofdm_update_every / self.update_every))
        
        # --- OFDM Caching Simulation ---
        self.ofdm_cache = {}
        self.ofdm_cache_timestamp = 0
        self.ofdm_cache_ttl = self.ofdm_poll_multiple * self.update_every
        
        # --- Collection Timeout and Retries ---
        self.collection_timeout = kwargs.get('collection_timeout')
        if self.collection_timeout is None:
            self.collection_timeout = int(self.update_every * 0.9)
            
        self.max_retries = kwargs.get('max_retries')
        if self.max_retries is None:
            longer_timeout = max(self.fast_endpoint_timeout, self.ofdm_endpoint_timeout)
            self.max_retries = max(1, int(self.collection_timeout / longer_timeout))
        
        # --- SSL Context ---
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        
        # --- Simulation State ---
        self.is_running = True
        self.start_time = None
        
        # --- Enhanced Performance Tracking ---
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
            'cache_misses': 0
        }
        
        # Initialize endpoint success tracking
        for endpoint in self.ALL_ENDPOINTS:
            self.results['endpoint_success_rates'][endpoint] = {'success': 0, 'total': 0}
        
        logger.info(f"Enhanced Simulator initialized:")
        logger.info(f"  Collection mode: {'Parallel' if self.parallel_collection else 'Serial'}")
        logger.info(f"  Update interval: {self.update_every}s")
        logger.info(f"  OFDM poll multiple: {self.ofdm_poll_multiple} (every {self.ofdm_poll_multiple * self.update_every}s)")
        logger.info(f"  Fast endpoint timeout: {self.fast_endpoint_timeout}s")
        logger.info(f"  OFDM endpoint timeout: {self.ofdm_endpoint_timeout}s")
        logger.info(f"  Collection timeout: {self.collection_timeout}s")
        logger.info(f"  Max retries: {self.max_retries}")
        logger.info(f"  OFDM cache TTL: {self.ofdm_cache_ttl}s")

    async def run_simulation(self):
        """Main simulation loop with tiered polling logic."""
        self.start_time = datetime.now()
        end_time = self.start_time + timedelta(seconds=self.test_duration)
        
        logger.info(f"Starting {self.test_duration}s simulation...")
        logger.info(f"Target end time: {end_time.strftime('%H:%M:%S')}")
        logger.info("Press Ctrl+C to stop early")
        
        while self.is_running and datetime.now() < end_time:
            cycle_start_time = time.monotonic()
            
            # --- Determine endpoints for this cycle (mirror plugin logic) ---
            self.run_counter += 1
            endpoints_to_poll = self._get_endpoints_for_cycle()
            cycle_type = "FULL" if len(endpoints_to_poll) == len(self.ALL_ENDPOINTS) else "FAST"
            
            # Track cycle type
            self.results['cycle_types'].append(cycle_type)
            if cycle_type == "FAST":
                self.results['fast_cycles'] += 1
            else:
                self.results['full_cycles'] += 1
            
            logger.debug(f"Cycle {self.run_counter} ({cycle_type}): {len(endpoints_to_poll)} endpoints")
            
            # --- Run Collection Cycle ---
            success, collection_time, successful_requests = await self._run_collection_cycle(endpoints_to_poll)
            
            # --- Update Statistics ---
            self._update_statistics(success, collection_time, endpoints_to_poll, successful_requests)
            
            # --- Progress Display ---
            self._display_progress()
            
            # --- Wait for Next Cycle ---
            elapsed = time.monotonic() - cycle_start_time
            sleep_time = max(0, self.update_every - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

        logger.info("\nSimulation completed.")

    def _get_endpoints_for_cycle(self):
        """Determine which endpoints to poll (mirrors plugin logic exactly)."""
        # First run always polls everything
        if self.run_counter == 1:
            return self.ALL_ENDPOINTS
        
        # Check if this is an OFDM polling cycle
        if self.ofdm_poll_multiple > 0 and self.run_counter % self.ofdm_poll_multiple == 0:
            return self.ALL_ENDPOINTS
        else:
            return self.FAST_ENDPOINTS

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
            
            # Always fetch fast endpoints
            if fast_endpoints:
                if self.parallel_collection:
                    fast_results = await self._fetch_parallel(fast_endpoints)
                else:
                    fast_results = await self._fetch_serial(fast_endpoints)
                results.extend(fast_results)
            
            # Handle OFDM endpoints with caching logic
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
                    
                elif self._is_ofdm_cache_valid():
                    # Use cached OFDM data (simulate success)
                    cached_results = [{"cached": True} for _ in ofdm_endpoints]
                    results.extend(cached_results)
                    self.results['cache_hits'] += 1
                    
                else:
                    # No valid cache, no OFDM data
                    pass
            
            collection_time = time.monotonic() - start_time
            successful_requests = sum(1 for r in results if r is not None)
            
            # Determine cycle type for tracking
            if should_poll_ofdm and ofdm_endpoints:
                cycle_type = "FULL"
                self.results['full_cycles'] += 1
            elif not should_poll_ofdm and ofdm_endpoints and self._is_ofdm_cache_valid():
                cycle_type = "CACHED"
                self.results['cached_cycles'] += 1
            else:
                cycle_type = "FAST"
                self.results['fast_cycles'] += 1
            
            self.results['cycle_types'].append(cycle_type)
            
            is_cycle_success = successful_requests == len(endpoints)
            return is_cycle_success, collection_time, successful_requests
            
        except Exception as e:
            logger.error(f"Collection cycle failed: {e}")
            return False, time.monotonic() - start_time, 0

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
        
        current_time = time.monotonic()
        cache_age = current_time - self.ofdm_cache_timestamp
        
        return cache_age < self.ofdm_cache_ttl

    async def _fetch_serial(self, endpoints):
        """Simulate serial endpoint fetching."""
        results = []
        
        # Create a single session for the entire serial collection
        connector = aiohttp.TCPConnector(ssl=self.ssl_context)
        timeout = aiohttp.ClientTimeout(total=self.collection_timeout)
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            for endpoint in endpoints:
                result = await self._fetch_endpoint(session, endpoint)
                results.append(result)
                
                # Add inter-request delay
                if self.inter_request_delay > 0 and endpoint != endpoints[-1]:
                    await asyncio.sleep(self.inter_request_delay)
        
        return results

    async def _fetch_parallel(self, endpoints):
        """Simulate parallel endpoint fetching."""
        connector = aiohttp.TCPConnector(
            ssl=self.ssl_context,
            limit=10,
            keepalive_timeout=30
        )
        timeout = aiohttp.ClientTimeout(total=self.collection_timeout, sock_read=self.endpoint_timeout)
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            tasks = [self._fetch_endpoint(session, endpoint) for endpoint in endpoints]
            return await asyncio.gather(*tasks)

    async def _fetch_endpoint(self, session, endpoint):
        """Fetch a single endpoint with enhanced timeout logic."""
        url = f"{self.modem_host}/data/{endpoint}"
        endpoint_timeout = self.endpoint_timeouts.get(endpoint, self.fast_endpoint_timeout)
        
        for attempt in range(self.max_retries):
            request_start = time.monotonic()
            
            try:
                async with session.get(url, timeout=endpoint_timeout) as response:
                    response_time = (time.monotonic() - request_start) * 1000  # Convert to ms
                    self.results['response_times'].append(response_time)
                    
                    if response.status == 200:
                        # Update endpoint success tracking
                        self.results['endpoint_success_rates'][endpoint]['success'] += 1
                        self.results['endpoint_success_rates'][endpoint]['total'] += 1
                        return await response.json()
                    else:
                        self.results['endpoint_success_rates'][endpoint]['total'] += 1
                        
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                response_time = (time.monotonic() - request_start) * 1000
                self.results['response_times'].append(response_time)
                self.results['endpoint_success_rates'][endpoint]['total'] += 1
                
                logger.debug(f"{endpoint}: Attempt {attempt + 1} failed: {e} (timeout: {endpoint_timeout}s)")
                
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(1)  # Brief pause between retries
        
        return None

    def _update_statistics(self, cycle_success, collection_time, endpoints, successful_requests):
        """Update performance statistics."""
        self.results['total_cycles'] += 1
        self.results['collection_times'].append(collection_time * 1000)  # Convert to ms
        self.results['total_requests'] += len(endpoints)
        self.results['successful_requests'] += successful_requests
        self.results['failed_requests'] += (len(endpoints) - successful_requests)
        
        if cycle_success:
            self.results['successful_cycles'] += 1
        else:
            self.results['failed_cycles'] += 1

    def _display_progress(self):
        """Display simple progress information without fancy terminal effects."""
        if self.results['total_cycles'] == 0:
            return
        
        # Calculate rates
        cycle_success_rate = (self.results['successful_cycles'] / self.results['total_cycles']) * 100
        request_success_rate = (self.results['successful_requests'] / self.results['total_requests']) * 100 if self.results['total_requests'] > 0 else 0
        
        # Calculate average times
        avg_collection_time = statistics.mean(self.results['collection_times']) if self.results['collection_times'] else 0
        avg_response_time = statistics.mean(self.results['response_times']) if self.results['response_times'] else 0
        
        # Calculate elapsed and remaining time
        elapsed = datetime.now() - self.start_time
        remaining = self.test_duration - elapsed.total_seconds()
        
        # Progress percentage
        progress = (elapsed.total_seconds() / self.test_duration) * 100
        
        # Simple line-by-line progress (no carriage returns)
        print(f"Progress: {progress:.1f}% | Cycles: {self.results['total_cycles']} "
              f"(Fast: {self.results['fast_cycles']}, Full: {self.results['full_cycles']}, Cached: {self.results['cached_cycles']}) | "
              f"Success: {cycle_success_rate:.1f}% | Avg Time: {avg_collection_time:.0f}ms | Remaining: {remaining:.0f}s")
        
        # Show detailed progress every 10 cycles
        if self.results['total_cycles'] % 10 == 0:
            print(f"  -> Request Success: {request_success_rate:.1f}% | Avg Response: {avg_response_time:.0f}ms | Cache Hits: {self.results['cache_hits']}")

    def generate_report(self):
        """Generate comprehensive final report."""
        print("\n\n" + "="*80)
        print("           ENHANCED SIMULATION REPORT")
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
        
        # Test configuration
        print(f"Configuration:")
        print(f"  Test Duration:         {self.test_duration}s")
        print(f"  Update Interval:       {self.update_every}s (fast endpoints)")
        print(f"  OFDM Poll Multiple:    {self.ofdm_poll_multiple}x (every {self.ofdm_poll_multiple * self.update_every}s)")
        print(f"  Collection Mode:       {'Parallel' if self.parallel_collection else 'Serial'}")
        print(f"  Endpoint Timeout:      {self.endpoint_timeout}s")
        print(f"  Collection Timeout:    {self.collection_timeout}s")
        print(f"  Max Retries:           {self.max_retries}")
        
        print(f"\nCycle Results:")
        print(f"  Total Cycles:          {self.results['total_cycles']}")
        print(f"  Fast Cycles:           {self.results['fast_cycles']} ({(self.results['fast_cycles']/self.results['total_cycles']*100):.1f}%)")
        print(f"  Full Cycles:           {self.results['full_cycles']} ({(self.results['full_cycles']/self.results['total_cycles']*100):.1f}%)")
        print(f"  Cycle Success Rate:    {cycle_success_rate:.2f}% ({self.results['successful_cycles']}/{self.results['total_cycles']})")
        
        print(f"\nRequest Results:")
        print(f"  Total Requests:        {self.results['total_requests']}")
        print(f"  Successful Requests:   {self.results['successful_requests']}")
        print(f"  Failed Requests:       {self.results['failed_requests']}")
        print(f"  Request Success Rate:  {request_success_rate:.2f}%")
        
        print(f"\nTiming Analysis:")
        print(f"  Avg Collection Time:   {avg_collection_time:.1f}ms")
        print(f"  Max Collection Time:   {max_collection_time:.1f}ms")
        print(f"  Avg Response Time:     {avg_response_time:.1f}ms")
        print(f"  Collection Efficiency: {(avg_collection_time/self.collection_timeout/10):.1f}% of timeout")
        
        print(f"\nEndpoint Analysis:")
        for endpoint in self.ALL_ENDPOINTS:
            stats = self.results['endpoint_success_rates'][endpoint]
            if stats['total'] > 0:
                success_rate = (stats['success'] / stats['total']) * 100
                endpoint_type = "FAST" if endpoint in self.FAST_ENDPOINTS else "SLOW"
                print(f"  {endpoint:20} ({endpoint_type}): {success_rate:5.1f}% ({stats['success']}/{stats['total']})")
        
        # Performance Assessment
        print(f"\nPerformance Assessment:")
        if cycle_success_rate >= 99:
            assessment = "EXCELLENT"
        elif cycle_success_rate >= 95:
            assessment = "GOOD"
        elif cycle_success_rate >= 90:
            assessment = "ACCEPTABLE"
        elif cycle_success_rate >= 80:
            assessment = "POOR"
        else:
            assessment = "CRITICAL"
        
        print(f"  Overall Rating:        {assessment}")
        
        if avg_collection_time > self.collection_timeout * 1000 * 0.8:
            print(f"  ⚠️  Warning: Collection time approaching timeout limit")
        
        if request_success_rate < 95:
            print(f"  ⚠️  Warning: High request failure rate detected")
        
        print("="*80)
        
        # Create machine-readable report for automated analysis
        report = {
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
            "consecutive_failures": 0,  # Calculate this if needed
            "endpoint_success_rates": self.results['endpoint_success_rates'],
            "assessment": assessment,
            "configuration": {
                "update_every": self.update_every,
                "ofdm_poll_multiple": self.ofdm_poll_multiple,
                "parallel_collection": self.parallel_collection,
                "fast_endpoint_timeout": self.fast_endpoint_timeout,
                "ofdm_endpoint_timeout": self.ofdm_endpoint_timeout,
                "collection_timeout": self.collection_timeout,
                "max_retries": self.max_retries
            }
        }
        
        # Output JSON for automated processing
        print(json.dumps(report))
        return report


def signal_handler(simulator):
    """Handle interrupt signals gracefully."""
    def handler(signum, frame):
        logger.info(f"Received signal {signum}, stopping simulation...")
        simulator.is_running = False
    return handler


def main():
    """Main entry point with comprehensive argument parsing."""
    parser = argparse.ArgumentParser(
        description="Enhanced Netdata Hitron CODA Modem Stability Simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test default tiered polling
  %(prog)s --host https://192.168.100.1 --duration 300
  
  # Test aggressive settings
  %(prog)s --update-every 30 --ofdm-poll-multiple 10 --parallel --duration 600
  
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
    
    # Debugging
    parser.add_argument('--debug', action='store_true',
                       help='Enable debug logging')
    
    args = parser.parse_args()
    
    # Configure logging level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
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
        'inter_request_delay': args.inter_request_delay
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
        finally:
            # Clean shutdown
            pending = asyncio.all_tasks(loop)
            if pending:
                logger.debug(f"Cancelling {len(pending)} pending tasks...")
                for task in pending:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
    except KeyboardInterrupt:
        logger.info("Simulation interrupted by user")
    except Exception as e:
        logger.error(f"Simulation failed: {e}")
        sys.exit(1)
    finally:
        # Generate final report
        try:
            simulator.generate_report()
        except Exception as e:
            logger.error(f"Failed to generate report: {e}")


if __name__ == "__main__":
    main()
