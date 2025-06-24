import asyncio
import aiohttp
import ssl
import time
from datetime import datetime, timedelta
import signal
import sys
from typing import Dict, List, Tuple, Optional
import logging
import statistics
import csv
from collections import deque
import numpy as np
from pathlib import Path
import json

# Set matplotlib backend to non-interactive
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns
import pandas as pd

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class NetdataModemSimulator:
    """Simulates the Netdata hitron_coda plugin behavior with configurable parameters."""
    
    def __init__(self, 
                 modem_host: str = "https://192.168.100.1",
                 update_every: int = 20,
                 endpoint_timeout: Optional[str] = None,
                 collection_timeout: Optional[int] = None,
                 max_retries: int = 3,
                 test_duration: int = 300,  # 5 minutes default
                 parallel_collection: bool = False,
                 inter_request_delay: float = 0.0,
                 endpoints_to_test: Optional[List[str]] = None):
        
        self.modem_host = modem_host
        self.update_every = update_every
        self.max_retries = max_retries
        self.test_duration = test_duration
        self.parallel_collection = parallel_collection
        self.inter_request_delay = inter_request_delay
        
        # Smart timeout handling: auto-detect ms vs seconds
        if endpoint_timeout is None:
            # Auto-calculate timeout like the plugin does (70% of update_every, min 5s, max 15s)
            self.endpoint_timeout = max(5, min(15, int(update_every * 0.7)))
            self.timeout_source = "auto-calculated"
        else:
            self.endpoint_timeout = self._parse_timeout_value(endpoint_timeout)
            self.timeout_source = "user-specified"
        
        # Collection timeout: auto-calculate if not specified (like the plugin does)
        if collection_timeout is None:
            self.collection_timeout = int(self.update_every * 0.9)
            self.collection_timeout_source = "auto-calculated"
        else:
            self.collection_timeout = collection_timeout
            self.collection_timeout_source = "user-specified"
            
        # Default endpoints (same as plugin)
        self.all_endpoints = [
            'dsinfo.asp',           # Downstream QAM (31 channels) - CRITICAL
            'usinfo.asp',           # Upstream QAM (5 channels) - CRITICAL  
            'dsofdminfo.asp',       # Downstream OFDM (DOCSIS 3.1)
            'usofdminfo.asp',       # Upstream OFDM (DOCSIS 3.1)
            'getSysInfo.asp',       # System uptime
            'getLinkStatus.asp'     # Link speed
        ]
        
        self.endpoints_to_test = endpoints_to_test or self.all_endpoints
        
        # Statistics tracking
        self.start_time = None
        self.cycle_count = 0
        self.active_cycles = {}  # Track overlapping cycles
        self.cycle_results = []  # Store all cycle results
        self.endpoint_stats = {}  # Per-endpoint statistics
        self.is_running = True
        self.last_progress_update = 0  # For progress reporting throttling
        
        # SSL context
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        
        # Initialize endpoint stats
        for endpoint in self.endpoints_to_test:
            self.endpoint_stats[endpoint] = {
                'total_requests': 0,
                'successful_requests': 0,
                'failed_requests': 0,
                'timeout_failures': 0,
                'response_times': [],
                'errors': []
            }
        
        logger.info(f"Initialized Netdata simulation:")
        logger.info(f"  Host: {self.modem_host}")
        logger.info(f"  Update interval: {self.update_every}s")
        logger.info(f"  Endpoint timeout: {self.endpoint_timeout}s ({self.timeout_source})") 
        logger.info(f"  Collection timeout: {self.collection_timeout}s ({self.collection_timeout_source})")
        logger.info(f"  Max retries: {self.max_retries}")
        logger.info(f"  Collection mode: {'PARALLEL' if self.parallel_collection else 'SERIAL'}")
        if not self.parallel_collection and self.inter_request_delay > 0:
            logger.info(f"  Inter-request delay: {self.inter_request_delay}s (SERIAL mode only)")
        logger.info(f"  Endpoints: {', '.join(self.endpoints_to_test)}")
        logger.info(f"  Test duration: {self.test_duration}s")
        logger.info(f"  Expected cycles: ~{int(self.test_duration / self.update_every)}")

    def _parse_timeout_value(self, timeout_value: str) -> float:
        """
        Parse timeout value with support for explicit units and intelligent detection.
        
        Supported formats:
        1. Explicit units: "5s", "1500ms", "30s", "5000ms"
        2. Numeric with auto-detection:
           - Values >= 1000: Treated as milliseconds
           - Values < 1000: Treated as seconds
        
        Examples:
        - "5s" -> 5.0 seconds
        - "1500ms" -> 1.5 seconds
        - "30s" -> 30.0 seconds
        - 5 -> 5.0 seconds (numeric fallback)
        - 1500 -> 1.5 seconds (numeric fallback, treated as ms)
        """
        # Handle string input with explicit units
        if isinstance(timeout_value, str):
            timeout_str = timeout_value.lower().strip()
            
            if timeout_str.endswith('ms'):
                # Explicit milliseconds
                try:
                    ms_value = float(timeout_str[:-2])
                    timeout_seconds = ms_value / 1000.0
                    logger.info(f"Timeout value '{timeout_value}' interpreted as {ms_value}ms = {timeout_seconds}s")
                    return timeout_seconds
                except ValueError:
                    raise ValueError(f"Invalid milliseconds value: '{timeout_value}'. Expected format: '1500ms'")
            
            elif timeout_str.endswith('s'):
                # Explicit seconds
                try:
                    s_value = float(timeout_str[:-1])
                    logger.info(f"Timeout value '{timeout_value}' interpreted as {s_value}s")
                    return float(s_value)
                except ValueError:
                    raise ValueError(f"Invalid seconds value: '{timeout_value}'. Expected format: '5s'")
            
            else:
                # String without unit suffix - try to parse as numeric
                try:
                    numeric_value = float(timeout_str)
                    return self._parse_numeric_timeout(numeric_value)
                except ValueError:
                    raise ValueError(f"Invalid timeout format: '{timeout_value}'. Expected: '5s', '1500ms', or numeric value")
        
        # Handle numeric input (backward compatibility)
        elif isinstance(timeout_value, (int, float)):
            return self._parse_numeric_timeout(float(timeout_value))
        
        else:
            raise ValueError(f"Unsupported timeout type: {type(timeout_value)}. Expected string or numeric value")
    
    def _parse_numeric_timeout(self, numeric_value: float) -> float:
        """
        Parse numeric timeout value with intelligent seconds vs milliseconds detection.
        
        Rules:
        - Values >= 1000: Treated as milliseconds, converted to seconds
        - Values < 1000: Treated as seconds
        """
        if numeric_value >= 1000:
            # Treat as milliseconds
            timeout_seconds = numeric_value / 1000.0
            logger.info(f"Timeout value {numeric_value} interpreted as {numeric_value}ms = {timeout_seconds}s")
            return timeout_seconds
        else:
            # Treat as seconds
            timeout_seconds = float(numeric_value)
            logger.info(f"Timeout value {numeric_value} interpreted as {timeout_seconds}s")
            return timeout_seconds

    def _calculate_progress_info(self, current_time: float) -> Dict:
        """
        Calculate comprehensive progress information including percentage complete,
        estimated time remaining, and cycle progress.
        """
        if not self.start_time:
            return {}
            
        elapsed_time = current_time - self.start_time
        time_progress = min(elapsed_time / self.test_duration, 1.0) * 100
        
        # Calculate expected vs actual cycles
        expected_cycles_by_now = int(elapsed_time / self.update_every)
        completed_cycles = len(self.cycle_results)
        active_cycles_count = len(self.active_cycles)
        
        # Calculate cycle-based progress (more accurate for actual work done)
        total_expected_cycles = int(self.test_duration / self.update_every)
        cycle_progress = min(completed_cycles / total_expected_cycles, 1.0) * 100 if total_expected_cycles > 0 else 0
        
        # Estimate time remaining based on current pace
        if completed_cycles > 0 and elapsed_time > 0:
            avg_cycle_interval = elapsed_time / completed_cycles
            remaining_cycles = max(0, total_expected_cycles - completed_cycles)
            estimated_remaining_time = remaining_cycles * avg_cycle_interval
        else:
            estimated_remaining_time = self.test_duration - elapsed_time
            
        # Calculate success rate trend
        recent_cycles = self.cycle_results[-5:] if len(self.cycle_results) >= 5 else self.cycle_results
        if recent_cycles:
            recent_success_rate = sum(
                (cycle['successful_endpoints'] / cycle['total_endpoints']) * 100 
                for cycle in recent_cycles
            ) / len(recent_cycles)
        else:
            recent_success_rate = 0
            
        return {
            'elapsed_time': elapsed_time,
            'time_progress': time_progress,
            'cycle_progress': cycle_progress,
            'completed_cycles': completed_cycles,
            'expected_cycles_by_now': expected_cycles_by_now,
            'total_expected_cycles': total_expected_cycles,
            'active_cycles': active_cycles_count,
            'estimated_remaining_time': max(0, estimated_remaining_time),
            'recent_success_rate': recent_success_rate,
            'is_ahead_of_schedule': completed_cycles > expected_cycles_by_now,
            'cycle_deficit': max(0, expected_cycles_by_now - completed_cycles)
        }

    def _log_progress_update(self, force: bool = False):
        """
        Log progress update with throttling to avoid spam.
        Updates every 30 seconds or when forced.
        """
        current_time = time.time()
        
        # Throttle progress updates (every 30 seconds unless forced)
        if not force and (current_time - self.last_progress_update) < 30:
            return
            
        progress = self._calculate_progress_info(current_time)
        if not progress:
            return
            
        self.last_progress_update = current_time
        
        # Create progress bar
        progress_percent = max(progress['time_progress'], progress['cycle_progress'])
        bar_length = 30
        filled_length = int(bar_length * progress_percent / 100)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        
        # Format time remaining
        remaining_mins = int(progress['estimated_remaining_time'] / 60)
        remaining_secs = int(progress['estimated_remaining_time'] % 60)
        
        # Schedule status
        if progress['is_ahead_of_schedule']:
            schedule_status = f"✅ +{progress['completed_cycles'] - progress['expected_cycles_by_now']} cycles ahead"
        elif progress['cycle_deficit'] > 0:
            schedule_status = f"⚠️  -{progress['cycle_deficit']} cycles behind"
        else:
            schedule_status = "✅ On schedule"
        
        logger.info(f"PROGRESS: [{bar}] {progress_percent:.1f}% complete")
        logger.info(f"  Time: {progress['elapsed_time']:.0f}s elapsed, ~{remaining_mins}m{remaining_secs:02d}s remaining")
        logger.info(f"  Cycles: {progress['completed_cycles']}/{progress['total_expected_cycles']} "
                   f"({progress['active_cycles']} active) - {schedule_status}")
        logger.info(f"  Success rate (recent): {progress['recent_success_rate']:.1f}%")

    def _get_cache_buster_url(self, endpoint: str) -> str:
        """Generate URL with cache buster like the plugin does."""
        epoch_ms = int(time.time() * 1000)
        return f'{self.modem_host}/data/{endpoint}?_={epoch_ms}'

    async def _make_request_with_retry(self, endpoint: str, cycle_id: int) -> Dict:
        """Make HTTP request with retry logic (mimics plugin behavior)."""
        stats = self.endpoint_stats[endpoint]
        
        # Ensure at least one attempt is made, even if max_retries is 0
        max_attempts = max(1, self.max_retries + 1)
        
        for attempt in range(max_attempts):
            try:
                start_time = time.time()
                url = self._get_cache_buster_url(endpoint)
                
                logger.debug(f"Cycle {cycle_id}: Attempting {endpoint} (attempt {attempt+1}/{max_attempts})")
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url,
                        ssl=self.ssl_context,
                        timeout=aiohttp.ClientTimeout(total=self.endpoint_timeout)
                    ) as response:
                        content = await response.read()
                        response_time = time.time() - start_time
                        
                        # Try to parse JSON (like plugin does)
                        try:
                            data = json.loads(content)
                            data_valid = True
                        except:
                            data = None
                            data_valid = False
                        
                        stats['total_requests'] += 1
                        stats['response_times'].append(response_time)
                        
                        if response.status == 200 and data_valid:
                            stats['successful_requests'] += 1
                            return {
                                'endpoint': endpoint,
                                'cycle_id': cycle_id,
                                'attempt': attempt + 1,
                                'success': True,
                                'response_time': response_time,
                                'status_code': response.status,
                                'data_size': len(content),
                                'data_valid': data_valid,
                                'timestamp': datetime.now()
                            }
                        else:
                            raise Exception(f"HTTP {response.status} or invalid JSON")
                            
            except asyncio.TimeoutError:
                response_time = time.time() - start_time
                stats['timeout_failures'] += 1
                logger.warning(f"Cycle {cycle_id}: {endpoint} timeout after {response_time:.1f}s (attempt {attempt+1}/{max_attempts})")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff like plugin
                    
            except Exception as e:
                response_time = time.time() - start_time
                logger.warning(f"Cycle {cycle_id}: {endpoint} error after {response_time:.1f}s (attempt {attempt+1}/{max_attempts}): {str(e)}")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** attempt)
        
        # All attempts failed
        stats['total_requests'] += 1
        stats['failed_requests'] += 1
        error_msg = str(e) if 'e' in locals() else 'Unknown error'
        stats['errors'].append(error_msg)
        
        return {
            'endpoint': endpoint,
            'cycle_id': cycle_id,
            'attempt': max_attempts,
            'success': False,
            'response_time': self.endpoint_timeout,
            'status_code': 'FAILED',
            'data_size': 0,
            'data_valid': False,
            'timestamp': datetime.now(),
            'error': f'Max attempts exceeded: {error_msg}'
        }

    async def _collect_data_serial(self, cycle_id: int) -> Dict:
        """Collect data serially (like current plugin when parallel_collection=false)."""
        cycle_start = time.time()
        results = []
        
        logger.info(f"Cycle {cycle_id}: Starting SERIAL collection")
        
        for i, endpoint in enumerate(self.endpoints_to_test):
            result = await self._make_request_with_retry(endpoint, cycle_id)
            results.append(result)
            
            status = "✅" if result['success'] else "❌"
            # Show actual response time, not just timeout value
            actual_time_ms = result['response_time'] * 1000
            logger.info(f"  {status} {endpoint}: {actual_time_ms:.0f}ms")
            
            # Add inter-request delay if configured (mimics plugin behavior)
            if i < len(self.endpoints_to_test) - 1 and self.inter_request_delay > 0:
                logger.debug(f"Inter-request delay: {self.inter_request_delay}s")
                await asyncio.sleep(self.inter_request_delay)
        
        cycle_time = time.time() - cycle_start
        
        return {
            'cycle_id': cycle_id,
            'method': 'serial',
            'cycle_time': cycle_time,
            'results': results,
            'start_time': cycle_start,
            'end_time': time.time(),
            'successful_endpoints': sum(1 for r in results if r['success']),
            'total_endpoints': len(results)
        }

    async def _collect_data_parallel(self, cycle_id: int) -> Dict:
        """Collect data in parallel (mimics plugin when parallel_collection=true)."""
        cycle_start = time.time()
        
        logger.info(f"Cycle {cycle_id}: Starting PARALLEL collection")
        
        # Create tasks for all endpoints
        tasks = [
            self._make_request_with_retry(endpoint, cycle_id) 
            for endpoint in self.endpoints_to_test
        ]
        
        # Wait for all to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        processed_results = []
        for result in results:
            if isinstance(result, Exception):
                processed_results.append({
                    'endpoint': 'unknown',
                    'cycle_id': cycle_id,
                    'success': False,
                    'error': str(result),
                    'response_time': self.endpoint_timeout,
                    'timestamp': datetime.now()
                })
            else:
                processed_results.append(result)
                status = "✅" if result['success'] else "❌"
                actual_time_ms = result['response_time'] * 1000
                logger.info(f"  {status} {result['endpoint']}: {actual_time_ms:.0f}ms")
        
        cycle_time = time.time() - cycle_start
        
        return {
            'cycle_id': cycle_id,
            'method': 'parallel',
            'cycle_time': cycle_time,
            'results': processed_results,
            'start_time': cycle_start,
            'end_time': time.time(),
            'successful_endpoints': sum(1 for r in processed_results if r['success']),
            'total_endpoints': len(processed_results)
        }

    async def _collection_cycle(self, cycle_id: int):
        """Single collection cycle (like plugin's get_data method)."""
        self.active_cycles[cycle_id] = time.time()
        
        try:
            if self.parallel_collection:
                cycle_result = await self._collect_data_parallel(cycle_id)
            else:
                cycle_result = await self._collect_data_serial(cycle_id)
            
            self.cycle_results.append(cycle_result)
            
            # Log cycle completion
            success_rate = (cycle_result['successful_endpoints'] / cycle_result['total_endpoints']) * 100
            active_count = len(self.active_cycles)
            
            logger.info(f"Cycle {cycle_id}: Completed in {cycle_result['cycle_time']:.1f}s "
                       f"({success_rate:.0f}% success) - {active_count} cycles active")
            
            # Log progress update after every 5th cycle or if we're behind schedule
            if cycle_id % 5 == 0 or len(self.cycle_results) % 3 == 0:
                self._log_progress_update()
            
        except Exception as e:
            logger.error(f"Cycle {cycle_id}: Unexpected error: {str(e)}")
        finally:
            if cycle_id in self.active_cycles:
                del self.active_cycles[cycle_id]

    async def run_simulation(self):
        """Main simulation loop (mimics Netdata's scheduler)."""
        self.start_time = time.time()
        expected_cycles = int(self.test_duration / self.update_every)
        
        logger.info(f"Starting Netdata plugin simulation for {self.test_duration} seconds")
        logger.info(f"Expected to complete ~{expected_cycles} cycles at {self.update_every}s intervals")
        
        # Log initial progress
        self._log_progress_update(force=True)
        
        try:
            while self.is_running:
                current_time = time.time()
                elapsed = current_time - self.start_time
                
                if elapsed >= self.test_duration:
                    logger.info("Test duration reached")
                    break
                
                # Start new collection cycle (this is what Netdata does every update_every seconds)
                self.cycle_count += 1
                asyncio.create_task(self._collection_cycle(self.cycle_count))
                
                # Log progress updates periodically
                self._log_progress_update()
                
                # Wait for next scheduled cycle
                await asyncio.sleep(self.update_every)
                
        except asyncio.CancelledError:
            logger.info("Simulation cancelled")
        except Exception as e:
            logger.error(f"Simulation error: {str(e)}")
            raise
        finally:
            # Log final progress
            self._log_progress_update(force=True)

    def generate_report(self):
        """Generate comprehensive analysis report."""
        total_time = time.time() - self.start_time if self.start_time else 0
        
        if not self.cycle_results:
            print("\n" + "="*80)
            print("NETDATA HITRON PLUGIN SIMULATION REPORT")
            print("="*80)
            print(f"\n⚠️  WARNING: No cycle results to analyze!")
            print(f"   This likely means the simulation ended before any cycles completed.")
            print(f"   Test duration: {total_time:.1f}s")
            print(f"   Cycles started: {self.cycle_count}")
            print(f"   Active cycles when stopped: {len(self.active_cycles)}")
            
            if total_time < self.update_every:
                print(f"\n💡 SUGGESTION: Test duration ({total_time:.1f}s) was shorter than update interval ({self.update_every}s)")
                print(f"   Try: --duration {self.update_every * 3} (for at least 3 cycles)")
            
            return
        
        print("\n" + "="*80)
        print("NETDATA HITRON PLUGIN SIMULATION REPORT")
        print("="*80)
        
        # Configuration
        print(f"\nCONFIGURATION:")
        print(f"  Host: {self.modem_host}")
        print(f"  Collection Method: {'PARALLEL' if self.parallel_collection else 'SERIAL'}")
        print(f"  Update Interval: {self.update_every}s")
        print(f"  Endpoint Timeout: {self.endpoint_timeout}s ({self.timeout_source})")
        print(f"  Collection Timeout: {self.collection_timeout}s ({self.collection_timeout_source})")
        print(f"  Max Retries: {self.max_retries}")
        if not self.parallel_collection and self.inter_request_delay > 0:
            print(f"  Inter-request Delay: {self.inter_request_delay}s")
        print(f"  Endpoints: {len(self.endpoints_to_test)} ({', '.join(self.endpoints_to_test)})")
        
        # Overall statistics
        print(f"\nOVERALL STATISTICS:")
        print(f"  Test Duration: {total_time:.1f}s")
        print(f"  Completed Cycles: {len(self.cycle_results)}")
        print(f"  Expected Cycles: {int(total_time / self.update_every)}")
        
        # Cycle timing analysis
        cycle_times = [cr['cycle_time'] for cr in self.cycle_results]
        success_rates = [(cr['successful_endpoints'] / cr['total_endpoints']) * 100 for cr in self.cycle_results]
        
        print(f"\nCYCLE PERFORMANCE:")
        print(f"  Average Cycle Time: {statistics.mean(cycle_times):.1f}s")
        print(f"  Fastest Cycle: {min(cycle_times):.1f}s") 
        print(f"  Slowest Cycle: {max(cycle_times):.1f}s")
        print(f"  Cycles > Update Interval: {sum(1 for ct in cycle_times if ct > self.update_every)}")
        print(f"  Cycles > Collection Timeout: {sum(1 for ct in cycle_times if ct > self.collection_timeout)}")
        print(f"  Average Success Rate: {statistics.mean(success_rates):.1f}%")
        
        # Cycle time breakdown analysis
        if self.cycle_results:
            print(f"\nCYCLE TIME BREAKDOWN:")
            total_endpoint_time = 0
            endpoint_contributions = {}
            
            for cycle in self.cycle_results:
                for result in cycle['results']:
                    endpoint = result['endpoint']
                    if endpoint not in endpoint_contributions:
                        endpoint_contributions[endpoint] = []
                    endpoint_contributions[endpoint].append(result['response_time'])
                    total_endpoint_time += result['response_time']
            
            # Calculate contribution percentages
            print(f"  Total time spent on endpoints: {total_endpoint_time:.1f}s")
            print(f"  {'Endpoint':<20} {'Total Time (s)':<15} {'Avg Time (ms)':<15} {'% of Total':<12}")
            print(f"  {'-'*20} {'-'*15} {'-'*15} {'-'*12}")
            
            endpoint_totals = []
            for endpoint, times in endpoint_contributions.items():
                total_time = sum(times)
                avg_time_ms = statistics.mean(times) * 1000
                percentage = (total_time / total_endpoint_time) * 100 if total_endpoint_time > 0 else 0
                endpoint_totals.append((endpoint, total_time, avg_time_ms, percentage))
            
            # Sort by total time contribution
            endpoint_totals.sort(key=lambda x: x[1], reverse=True)
            
            for endpoint, total_time, avg_time_ms, percentage in endpoint_totals:
                endpoint_short = endpoint.replace('.asp', '')
                print(f"  {endpoint_short:<20} {total_time:<15.1f} {avg_time_ms:<15.0f} {percentage:<12.1f}%")
        
        # Endpoint statistics
        print(f"\nPER-ENDPOINT STATISTICS:")
        for endpoint, stats in self.endpoint_stats.items():
            if stats['response_times']:
                response_times_ms = [t * 1000 for t in stats['response_times']]
                success_rate = (stats['successful_requests'] / stats['total_requests']) * 100
                
                # Calculate percentiles
                min_time = min(response_times_ms)
                avg_time = statistics.mean(response_times_ms)
                p50_time = statistics.median(response_times_ms)
                p90_time = np.percentile(response_times_ms, 90) if len(response_times_ms) > 1 else response_times_ms[0]
                max_time = max(response_times_ms)
                
                print(f"  {endpoint}:")
                print(f"    Total Requests: {stats['total_requests']}")
                print(f"    Success Rate: {success_rate:.1f}%")
                print(f"    Response Times (ms): min={min_time:.0f}, avg={avg_time:.0f}, p50={p50_time:.0f}, p90={p90_time:.0f}, max={max_time:.0f}")
                print(f"    Timeouts: {stats['timeout_failures']}")
                if stats['errors'] and len(set(stats['errors'])) <= 3:  # Show unique errors if not too many
                    unique_errors = list(set(stats['errors']))[:3]
                    print(f"    Sample Errors: {', '.join(unique_errors)}")
            else:
                print(f"  {endpoint}: No successful responses recorded")
        
        # Endpoint performance ranking
        print(f"\nENDPOINT PERFORMANCE RANKING (by avg response time):")
        endpoint_performance = []
        for endpoint, stats in self.endpoint_stats.items():
            if stats['response_times']:
                avg_time_ms = statistics.mean(stats['response_times']) * 1000
                success_rate = (stats['successful_requests'] / stats['total_requests']) * 100
                endpoint_performance.append((endpoint, avg_time_ms, success_rate))
        
        # Sort by average response time (slowest first)
        endpoint_performance.sort(key=lambda x: x[1], reverse=True)
        
        if endpoint_performance:
            print(f"  {'Endpoint':<20} {'Avg Time (ms)':<15} {'Success Rate':<12}")
            print(f"  {'-'*20} {'-'*15} {'-'*12}")
            for endpoint, avg_time, success_rate in endpoint_performance:
                endpoint_short = endpoint.replace('.asp', '')
                status_icon = "🐌" if avg_time > 1000 else "⚠️" if avg_time > 500 else "✅"
                print(f"  {status_icon} {endpoint_short:<18} {avg_time:<15.0f} {success_rate:<12.1f}%")
        
        # Overlapping cycles analysis
        max_concurrent = 0
        overlapping_periods = 0
        
        for i, cycle in enumerate(self.cycle_results):
            concurrent_count = sum(1 for other in self.cycle_results 
                                 if other['start_time'] < cycle['end_time'] and 
                                    other['end_time'] > cycle['start_time'] and
                                    other['cycle_id'] != cycle['cycle_id'])
            max_concurrent = max(max_concurrent, concurrent_count + 1)
            if concurrent_count > 0:
                overlapping_periods += 1
        
        print(f"\nOVERLAPPING CYCLES ANALYSIS:")
        print(f"  Max Concurrent Cycles: {max_concurrent}")
        print(f"  Cycles with Overlaps: {overlapping_periods}")
        print(f"  Overlap Rate: {(overlapping_periods / len(self.cycle_results)) * 100:.1f}%")
        
        # Configuration validation (like the plugin does)
        print(f"\nCONFIGURATION VALIDATION:")
        avg_cycle_time = statistics.mean(cycle_times)
        
        # Check collection timeout vs update interval
        if self.collection_timeout >= self.update_every:
            print(f"  ⚠️  WARNING: Collection timeout ({self.collection_timeout}s) >= update interval ({self.update_every}s)")
            print(f"      This can cause overlapping collections!")
            print(f"      Recommended: collection_timeout < update_every")
        else:
            print(f"  ✅ Good: Collection timeout ({self.collection_timeout}s) < update interval ({self.update_every}s)")
        
        # Check if serial mode timing fits within collection timeout
        if not self.parallel_collection:
            estimated_serial_time = (len(self.endpoints_to_test) * 
                                    (self.endpoint_timeout + self.inter_request_delay))
            if estimated_serial_time > self.collection_timeout:
                print(f"  ⚠️  WARNING: Estimated serial time ({estimated_serial_time:.1f}s) > collection timeout ({self.collection_timeout}s)")
                print(f"      Consider: increasing collection_timeout or reducing endpoint_timeout")
            else:
                print(f"  ✅ Good: Estimated serial time ({estimated_serial_time:.1f}s) fits in collection timeout")
        
        # Recommendations
        print(f"\nRECOMMENDATIONS:")
        
        if avg_cycle_time > self.update_every:
            print(f"  ⚠️  ISSUE: Average cycle time ({avg_cycle_time:.1f}s) > update interval ({self.update_every}s)")
            print(f"      This causes overlapping collections and resource contention!")
            print(f"      Recommended solutions:")
            if not self.parallel_collection:
                print(f"        1. Enable parallel collection (would reduce time by ~{len(self.endpoints_to_test)}x)")
            print(f"        2. Increase update_every to {int(avg_cycle_time * 1.2)}s")
            print(f"        3. Reduce endpoints (test with just dsinfo.asp + usinfo.asp)")
            print(f"        4. Increase endpoint_timeout to {int(self.endpoint_timeout * 1.5)}s")
        else:
            print(f"  ✅ Good: Cycle time ({avg_cycle_time:.1f}s) < update interval ({self.update_every}s)")
            if self.parallel_collection:
                print(f"      Parallel collection is working well!")
            else:
                print(f"      Serial collection is fast enough for this interval")
        
        if statistics.mean(success_rates) < 95:
            print(f"  ⚠️  ISSUE: Low success rate ({statistics.mean(success_rates):.1f}%)")
            print(f"      Consider: increasing endpoint_timeout, reducing max_retries, checking network")
        
        # Auto-calculated retries comparison
        calculated_retries = max(1, int(self.collection_timeout / self.endpoint_timeout))
        if self.max_retries != calculated_retries:
            print(f"  💡 INFO: Current max_retries ({self.max_retries}) differs from auto-calculated ({calculated_retries})")
            print(f"      Auto-calculation: collection_timeout ({self.collection_timeout}s) ÷ endpoint_timeout ({self.endpoint_timeout}s) = {calculated_retries}")
        
        self._create_visualization()
        self._save_detailed_csv()

    def _create_visualization(self):
        """Create comprehensive visualization of the simulation results."""
        if not self.cycle_results:
            return
            
        # Set up the plot
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(20, 12))
        
        # Extract data
        cycle_ids = [cr['cycle_id'] for cr in self.cycle_results]
        cycle_times = [cr['cycle_time'] for cr in self.cycle_results]
        success_rates = [(cr['successful_endpoints'] / cr['total_endpoints']) * 100 for cr in self.cycle_results]
        start_times = [datetime.fromtimestamp(cr['start_time']) for cr in self.cycle_results]
        
        # Plot 1: Cycle times over time
        ax1.plot(start_times, cycle_times, 'b-', linewidth=2, label='Cycle Time')
        ax1.axhline(y=self.update_every, color='r', linestyle='--', 
                   label=f'Update Interval ({self.update_every}s)')
        ax1.axhline(y=self.collection_timeout, color='orange', linestyle='--', 
                   label=f'Collection Timeout ({self.collection_timeout}s)')
        ax1.set_xlabel('Time')
        ax1.set_ylabel('Cycle Time (seconds)')
        ax1.set_title('Collection Cycle Times')
        ax1.legend()
        ax1.grid(True)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        
        # Plot 2: Success rates over time
        ax2.plot(start_times, success_rates, 'g-', linewidth=2, label='Success Rate')
        ax2.axhline(y=95, color='orange', linestyle='--', label='95% Target')
        ax2.set_xlabel('Time')
        ax2.set_ylabel('Success Rate (%)')
        ax2.set_title('Endpoint Success Rates')
        ax2.legend()
        ax2.grid(True)
        ax2.set_ylim(0, 105)
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        
        # Plot 3: Cycle time distribution
        ax3.hist(cycle_times, bins=20, alpha=0.7, color='blue', edgecolor='black')
        ax3.axvline(x=self.update_every, color='r', linestyle='--', 
                   label=f'Update Interval ({self.update_every}s)')
        ax3.axvline(x=self.collection_timeout, color='orange', linestyle='--', 
                   label=f'Collection Timeout ({self.collection_timeout}s)')
        ax3.set_xlabel('Cycle Time (seconds)')
        ax3.set_ylabel('Frequency')
        ax3.set_title('Cycle Time Distribution')
        ax3.legend()
        ax3.grid(True)
        
        # Plot 4: Endpoint response times
        endpoint_names = []
        endpoint_times = []
        for endpoint, stats in self.endpoint_stats.items():
            if stats['response_times']:
                endpoint_names.append(endpoint.replace('.asp', ''))
                endpoint_times.append([t * 1000 for t in stats['response_times']])
        
        if endpoint_times:
            ax4.boxplot(endpoint_times, labels=endpoint_names)
            ax4.set_ylabel('Response Time (ms)')
            ax4.set_title('Response Time Distribution by Endpoint')
            ax4.grid(True)
            plt.setp(ax4.get_xticklabels(), rotation=45)
        
        # Overall title
        method = "PARALLEL" if self.parallel_collection else "SERIAL"
        endpoint_timeout_display = f"{self.endpoint_timeout}s ({self.timeout_source})"
        collection_timeout_display = f"{self.collection_timeout}s ({self.collection_timeout_source})"
        fig.suptitle(f'Netdata Hitron Plugin Simulation - {method} Collection\n'
                    f'Update Every: {self.update_every}s, Endpoint Timeout: {endpoint_timeout_display}, '
                    f'Collection Timeout: {collection_timeout_display}, Endpoints: {len(self.endpoints_to_test)}', fontsize=14)
        
        plt.tight_layout()
        
        # Save with descriptive filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        method_str = "parallel" if self.parallel_collection else "serial"
        endpoint_timeout_str = f"{int(self.endpoint_timeout * 1000)}ms" if self.endpoint_timeout < 1 else f"{int(self.endpoint_timeout)}s"
        filename = (f'netdata_simulation_{timestamp}_{method_str}_'
                   f'interval{self.update_every}_etimeout{endpoint_timeout_str}_'
                   f'ctimeout{self.collection_timeout}_'
                   f'endpoints{len(self.endpoints_to_test)}.png')
        
        plt.savefig(filename, dpi=300, bbox_inches='tight')
        logger.info(f"Visualization saved as {filename}")
        plt.close()

    def _save_detailed_csv(self):
        """Save detailed results to CSV for further analysis."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        method_str = "parallel" if self.parallel_collection else "serial"
        endpoint_timeout_str = f"{int(self.endpoint_timeout * 1000)}ms" if self.endpoint_timeout < 1 else f"{int(self.endpoint_timeout)}s"
        filename = (f'netdata_simulation_data_{timestamp}_{method_str}_'
                   f'interval{self.update_every}_etimeout{endpoint_timeout_str}_'
                   f'ctimeout{self.collection_timeout}.csv')
        
        with open(filename, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([
                'Cycle ID', 'Method', 'Cycle Time (s)', 'Start Time', 'End Time',
                'Successful Endpoints', 'Total Endpoints', 'Success Rate (%)',
                'Endpoint', 'Endpoint Success', 'Response Time (ms)', 'Status Code'
            ])
            
            for cycle in self.cycle_results:
                success_rate = (cycle['successful_endpoints'] / cycle['total_endpoints']) * 100
                
                for result in cycle['results']:
                    writer.writerow([
                        cycle['cycle_id'],
                        cycle['method'],
                        f"{cycle['cycle_time']:.2f}",
                        datetime.fromtimestamp(cycle['start_time']),
                        datetime.fromtimestamp(cycle['end_time']),
                        cycle['successful_endpoints'],
                        cycle['total_endpoints'],
                        f"{success_rate:.1f}",
                        result['endpoint'],
                        result['success'],
                        f"{result['response_time'] * 1000:.1f}",
                        result['status_code']
                    ])
        
        logger.info(f"Detailed data saved as {filename}")

async def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Simulate Netdata Hitron CODA plugin behavior',
        epilog='''
Configuration Options (matching hitron_coda.chart.py):

Timeout Values:
  --endpoint-timeout 5s      # 5 seconds (per-endpoint timeout)
  --endpoint-timeout 1500ms  # 1.5 seconds (1500 milliseconds)
  --collection-timeout 54    # 54 seconds (overall collection timeout)

Collection Modes:
  --parallel-collection      # Enable parallel collection (default: serial)
  --inter-request-delay 2.0  # Delay between endpoints in serial mode

Examples:
  # Conservative serial mode (like plugin default)
  python netdata_simulator.py --endpoint-timeout 6s --collection-timeout 54 --inter-request-delay 1.0

  # High-performance parallel mode
  python netdata_simulator.py --endpoint-timeout 4s --parallel-collection --update-every 30

  # Ultra-conservative for sensitive modems
  python netdata_simulator.py --endpoint-timeout 10s --collection-timeout 180 --inter-request-delay 3.0 --update-every 300

  # Minimal endpoints for testing
  python netdata_simulator.py --minimal --parallel-collection --update-every 20
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('--host', default='https://192.168.100.1', 
                       help='Modem host URL')
    parser.add_argument('--update-every', type=int, default=20,
                       help='Update interval in seconds (like Netdata config)')
    parser.add_argument('--endpoint-timeout', type=str, 
                       help='Per-endpoint timeout: "5s", "1500ms", or numeric. Auto-calculated if not specified')
    parser.add_argument('--collection-timeout', type=int,
                       help='Overall collection timeout in seconds (auto-calculated if not specified)')
    parser.add_argument('--max-retries', type=int, default=3,
                       help='Maximum retry attempts')
    parser.add_argument('--duration', type=int, default=300,
                       help='Test duration in seconds')
    parser.add_argument('--parallel-collection', action='store_true',
                       help='Use parallel collection instead of serial (matches plugin config)')
    parser.add_argument('--inter-request-delay', type=float, default=0.0,
                       help='Delay between endpoints in serial mode (seconds)')
    parser.add_argument('--endpoints', nargs='+',
                       choices=['dsinfo.asp', 'usinfo.asp', 'dsofdminfo.asp', 
                               'usofdminfo.asp', 'getSysInfo.asp', 'getLinkStatus.asp'],
                       help='Specific endpoints to test (default: all)')
    parser.add_argument('--minimal', action='store_true',
                       help='Test only critical endpoints (dsinfo.asp, usinfo.asp)')
    
    args = parser.parse_args()
    
    # Handle endpoint selection
    if args.minimal:
        endpoints = ['dsinfo.asp', 'usinfo.asp']
    elif args.endpoints:
        endpoints = args.endpoints
    else:
        endpoints = None  # Use all endpoints
    
    simulator = NetdataModemSimulator(
        modem_host=args.host,
        update_every=args.update_every,
        endpoint_timeout=args.endpoint_timeout,
        collection_timeout=args.collection_timeout,
        max_retries=args.max_retries,
        test_duration=args.duration,
        parallel_collection=args.parallel_collection,
        inter_request_delay=args.inter_request_delay,
        endpoints_to_test=endpoints
    )
    
    # Set up signal handlers
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.info("Received interrupt signal, stopping simulation...")
        simulator.is_running = False
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)
    
    try:
        await simulator.run_simulation()
        
        # Wait for any remaining cycles to complete
        logger.info("Waiting for active cycles to complete...")
        while simulator.active_cycles:
            await asyncio.sleep(1)
            # Show final progress while waiting
            simulator._log_progress_update(force=True)
            
    except KeyboardInterrupt:
        logger.info("Simulation interrupted")
        # Show final progress on interrupt
        simulator._log_progress_update(force=True)
    finally:
        simulator.generate_report()

if __name__ == "__main__":
    asyncio.run(main())
