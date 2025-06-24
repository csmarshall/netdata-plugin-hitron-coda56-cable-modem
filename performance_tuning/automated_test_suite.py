#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Automated Test Suite for Hitron CODA Plugin Optimal Speed Finding.

5 targeted configurations to find the fastest reliable polling based on actual response time data.
Each test runs for 2 hours to provide statistical confidence for aggressive settings.

Version: 3.0.1 - Fixed timeout parameter data types to match simulator expectations
"""

import asyncio
import sys
import json
import csv
import signal
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


class HitronSpeedTestSuite:
    """Speed optimization focused test suite for finding fastest reliable polling."""

    def __init__(self, simulator_script: str, host: str, quick_mode: bool = False):
        self.simulator_script = Path(simulator_script).resolve()
        if not self.simulator_script.exists():
            raise FileNotFoundError(f"Simulator not found: {self.simulator_script}")
        
        self.host = host
        self.quick_mode = quick_mode
        self.is_running = True
        self.test_results = []
        
        # Results directory
        self.start_time = datetime.now()
        timestamp = self.start_time.strftime('%Y%m%d_%H%M%S')
        self.results_dir = Path.cwd() / f"hitron_speed_test_{timestamp}"
        self.results_dir.mkdir(exist_ok=True)
        
        # File logging
        log_file = self.results_dir / 'speed_test_suite.log'
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(file_handler)
        
        # Build test matrix based on actual response time data analysis
        self.tests = self._create_speed_test_matrix()
        
        logger.info("Hitron CODA Speed Test Suite Initialized")
        logger.info(f"Results directory: {self.results_dir}")
        logger.info(f"Test count: {len(self.tests)}")
        logger.info(f"Focus: Finding fastest reliable polling with 2-hour stress tests")
        
        # Calculate total expected runtime
        total_hours = sum(t['duration'] for t in self.tests) / 3600
        logger.info(f"⏱️ Total expected runtime: {total_hours:.1f} hours")

    def _create_speed_test_matrix(self):
        """Create 5 targeted speed optimization tests based on response time analysis."""
        
        # Test duration - 2 hours each for statistical confidence
        test_duration = 7200 if not self.quick_mode else 3600  # 2 hours or 1 hour for quick mode
        
        # Based on actual data analysis:
        # P100 response time: 137ms
        # Individual endpoint estimate: ~274ms 
        # Safety margins: 100ms, 200ms, 300ms for different risk levels
        
        tests = [
            # TEST 1: Conservative Baseline - Proven safe timeouts with faster polling
            {
                "name": "Conservative 5s Baseline", 
                "description": "5s polling with safe 1s timeouts - baseline for speed testing",
                "duration": test_duration,
                "update_every": 5,
                "ofdm_multiple": 12,  # 60s OFDM
                "fast_timeout": 3,     # Changed from 1.0 to 3 (int, conservative)
                "ofdm_timeout": 3,     # Changed from 1.0 to 3 (int, conservative)
                "max_retries": 3,  # Manual override for safety
                "parallel_collection": False,
                "inter_request_delay": 0.1,
                "expected": "Establish 5s polling baseline with proven-safe timeouts"
            },
            
            # TEST 2: Optimized Response-Based - P100 + 300ms safety
            {
                "name": "Optimized 4s Response-Based",
                "description": "4s polling with timeouts based on P100 + 300ms safety margin",
                "duration": test_duration,
                "update_every": 4,
                "ofdm_multiple": 15,  # 60s OFDM  
                "fast_timeout": 1,    # Changed from 0.6 to 1 (int, ~600ms rounded up)
                "ofdm_timeout": 1,    # Changed from 0.6 to 1 (int, ~600ms rounded up)
                "max_retries": None,  # Auto-calculate
                "parallel_collection": False,
                "inter_request_delay": 0.1,
                "expected": "Test response-time optimized timeouts with 4s polling"
            },
            
            # TEST 3: Aggressive - P100 + 200ms safety with parallel collection
            {
                "name": "Aggressive 3s Parallel",
                "description": "3s polling with P100 + 200ms timeouts and parallel collection",
                "duration": test_duration,
                "update_every": 3,
                "ofdm_multiple": 20,  # 60s OFDM
                "fast_timeout": 1,    # Changed from 0.5 to 1 (int, ~500ms rounded up)
                "ofdm_timeout": 1,    # Changed from 0.5 to 1 (int, ~500ms rounded up)
                "max_retries": None,  # Auto-calculate
                "parallel_collection": True,  # Enable parallel for speed
                "inter_request_delay": 0.0,   # No delay in parallel mode
                "expected": "Test parallel collection with aggressive 3s polling"
            },
            
            # TEST 4: Ultra-Aggressive - P100 + 100ms safety (your requested config)
            {
                "name": "Ultra-Aggressive 3s Tight",
                "description": "3s polling with ultra-tight P100 + 100ms timeouts",
                "duration": test_duration,
                "update_every": 3,
                "ofdm_multiple": 13,  # 39s OFDM - more frequent
                "fast_timeout": 1,    # Changed from 0.4 to 1 (int, minimum safe timeout)
                "ofdm_timeout": 2,    # Changed from 0.4 to 2 (int, slightly higher for OFDM)
                "max_retries": None,  # Auto-calculate
                "parallel_collection": False,
                "inter_request_delay": 0.05,  # Minimal delay
                "expected": "Test ultra-tight timeouts with maximum speed"
            },
            
            # TEST 5: Maximum Speed - Push the absolute limits
            {
                "name": "Maximum Speed 2s",
                "description": "2s polling - absolute speed limit test with parallel collection",
                "duration": test_duration,
                "update_every": 2,
                "ofdm_multiple": 30,  # 60s OFDM
                "fast_timeout": 1,    # Changed from 0.4 to 1 (int, minimum timeout)
                "ofdm_timeout": 2,    # Changed from 0.4 to 2 (int, minimum for OFDM)
                "max_retries": 3,     # Limited retries for speed
                "parallel_collection": True,  # Required for 2s intervals
                "inter_request_delay": 0.0,
                "expected": "Test absolute maximum polling speed (likely to stress-test limits)"
            }
        ]
        
        return tests

    async def run_all_tests(self):
        """Execute all speed optimization tests."""
        logger.info(f"Starting speed optimization test suite: {len(self.tests)} tests")
        total_duration = sum(t['duration'] for t in self.tests)
        total_hours = total_duration / 3600
        
        logger.info(f"🚀 SPEED TEST PLAN:")
        logger.info(f"   Total runtime: {total_hours:.1f} hours")
        logger.info(f"   Test duration each: {self.tests[0]['duration']/3600:.1f} hours")
        logger.info(f"   Focus: Find fastest reliable polling configuration")
        logger.info(f"   Method: 5 targeted configurations with increasing aggressiveness")
        
        # Calculate expected completion time
        from datetime import datetime, timedelta
        completion_time = datetime.now() + timedelta(seconds=total_duration)
        logger.info(f"   Estimated completion: {completion_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        logger.info(f"\n📊 TEST PROGRESSION:")
        for i, test in enumerate(self.tests, 1):
            parallel_str = "Parallel" if test.get('parallel_collection', False) else "Serial"
            logger.info(f"   {i}. {test['name']:25} | {test['update_every']}s | {test['fast_timeout']}s TO | {parallel_str}")
        
        for i, test in enumerate(self.tests, 1):
            if not self.is_running:
                logger.warning("Test suite stopped by user")
                break
            
            # Calculate test metrics
            duration_hours = test['duration'] / 3600
            expected_cycles = int(test['duration'] / test['update_every'])
            
            # Calculate theoretical collection times
            if test.get('parallel_collection', False):
                est_collection_time = max(test['fast_timeout'], test['ofdm_timeout']) * 1000
                mode_desc = "Parallel (concurrent endpoints)"
            else:
                num_endpoints = 6  # Assume full cycles for estimation
                delays = num_endpoints * test.get('inter_request_delay', 0) * 1000
                est_collection_time = (num_endpoints * test['fast_timeout'] * 1000) + delays
                mode_desc = "Serial (sequential endpoints)"
            
            # Risk assessment
            if test['update_every'] <= 2:
                risk_level = "🔴 VERY HIGH"
            elif test['update_every'] <= 3:
                risk_level = "🟠 HIGH"  
            elif test['fast_timeout'] <= 1:
                risk_level = "🟡 MODERATE"
            else:
                risk_level = "🟢 LOW"
            
            logger.info(f"\n{'='*80}")
            logger.info(f"SPEED TEST {i}/{len(self.tests)}: {test['name']}")
            logger.info(f"Description: {test['description']}")
            logger.info(f"Expected: {test['expected']}")
            logger.info(f"⏱️ Duration: {duration_hours:.1f} hours")
            logger.info(f"🎯 Configuration:")
            logger.info(f"   Polling: Every {test['update_every']}s (OFDM every {test['ofdm_multiple']}x = {test['update_every'] * test['ofdm_multiple']}s)")
            logger.info(f"   Timeouts: Fast={test['fast_timeout']}s, OFDM={test['ofdm_timeout']}s")
            logger.info(f"   Mode: {mode_desc}")
            logger.info(f"   Est. collection time: {est_collection_time:.0f}ms")
            logger.info(f"📊 Analysis:")
            logger.info(f"   Expected cycles: {expected_cycles:,}")
            logger.info(f"   Risk level: {risk_level}")
            logger.info(f"   Efficiency: {(est_collection_time / (test['update_every'] * 1000) * 100):.1f}%")
            logger.info(f"{'='*80}")
            
            await asyncio.sleep(2)  # Brief pause
            
            result = await self._run_test(test)
            self.test_results.append(result)
            
            self._save_progress()
            self._analyze_result(result)
            
            # Show progress toward completion
            completed_tests = i
            remaining_tests = len(self.tests) - i
            if remaining_tests > 0:
                remaining_duration = sum(t['duration'] for t in self.tests[i:])
                remaining_hours = remaining_duration / 3600
                logger.info(f"📈 Progress: {completed_tests}/{len(self.tests)} tests completed")
                logger.info(f"⏱️ Remaining: {remaining_hours:.1f} hours ({remaining_tests} tests)")
            
            # Pause between tests
            if i < len(self.tests) and self.is_running:
                logger.info("📊 Analyzing results and preparing next test...")
                for countdown in range(10, 0, -1):
                    if not self.is_running:
                        break
                    if countdown % 3 == 0:
                        logger.info(f"Next test in {countdown}s...")
                    await asyncio.sleep(1)
        
        logger.info("\n🏁 Speed optimization test suite completed!")
        self._generate_final_analysis()

    async def _run_test(self, test):
        """Run a single speed test with enhanced logging."""
        start_time = time.time()
        
        # Build command with all parameters - ensuring all numeric values are properly formatted
        cmd = [
            sys.executable, str(self.simulator_script),
            '--host', self.host,
            '--duration', str(test['duration']),
            '--update-every', str(test['update_every']),
            '--ofdm-poll-multiple', str(test['ofdm_multiple']),
            '--fast-endpoint-timeout', str(test['fast_timeout']),  # Now guaranteed to be int
            '--ofdm-endpoint-timeout', str(test['ofdm_timeout']),  # Now guaranteed to be int
            '--inter-request-delay', str(test.get('inter_request_delay', 0.1))
        ]
        
        # Add collection mode
        if test.get('parallel_collection', False):
            cmd.append('--parallel')
        else:
            cmd.append('--serial')
            
        # Add manual max_retries if specified
        if test.get('max_retries') is not None:
            cmd.extend(['--max-retries', str(test['max_retries'])])
        
        logger.info(f"🚀 Command: {' '.join(cmd)}")
        print("\n📊 SPEED TEST OUTPUT:")
        print("=" * 60)
        
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            all_stdout_lines = []
            all_stderr_lines = []
            json_report = {}
            
            # Read stdout and stderr concurrently
            async def read_stdout():
                while True:
                    line_bytes = await process.stdout.readline()
                    if not line_bytes:
                        break
                    
                    line = line_bytes.decode('utf-8').rstrip()
                    all_stdout_lines.append(line)
                    
                    if line.strip():
                        # Focus on speed-related metrics
                        if line.startswith('{') and line.endswith('}'):
                            try:
                                json_report.update(json.loads(line))
                            except json.JSONDecodeError:
                                pass
                        elif any(keyword in line for keyword in ['Progress:', 'SUCCESS', 'FAILED', 'Success Rate:', 'Collection Time:', 'Consecutive Failures:']):
                            print(line)
                        elif 'SIMULATION FINAL REPORT' in line:
                            print("\n" + "="*40)
                            print(line)
                            print("="*40)
            
            async def read_stderr():
                while True:
                    line_bytes = await process.stderr.readline()
                    if not line_bytes:
                        break
                    
                    line = line_bytes.decode('utf-8').rstrip()
                    all_stderr_lines.append(line)
                    
                    if line.strip():
                        print(f"STDERR: {line}")
            
            # Read both streams concurrently
            await asyncio.gather(read_stdout(), read_stderr())
            await process.wait()
            
            duration = time.time() - start_time
            
            print("=" * 60)
            logger.info(f"📊 Speed test completed in {duration/3600:.2f} hours")
            
            # Enhanced success validation for speed tests
            success_indicators = [
                json_report.get('cycle_success_rate', 0) >= 90,  # Allow some failures for aggressive tests
                json_report.get('total_cycles', 0) >= test['duration'] / test['update_every'] * 0.75,  # Got most expected cycles
                process.returncode == 0,
                any('SIMULATION FINAL REPORT' in line for line in all_stdout_lines),
                not any('Failed to generate report' in line for line in all_stderr_lines)
            ]
            
            test_success = all(success_indicators)
            
            # Calculate speed metrics
            expected_cycles = int(test['duration'] / test['update_every'])
            actual_cycles = json_report.get('total_cycles', 0)
            completion_rate = (actual_cycles / expected_cycles * 100) if expected_cycles > 0 else 0
            
            result = {
                "name": test['name'],
                "description": test['description'],
                "config": {
                    "update_every": test['update_every'],
                    "ofdm_multiple": test['ofdm_multiple'],
                    "fast_timeout": test['fast_timeout'],
                    "ofdm_timeout": test['ofdm_timeout'],
                    "max_retries": test.get('max_retries'),
                    "parallel_collection": test.get('parallel_collection', False),
                    "inter_request_delay": test.get('inter_request_delay', 0.1)
                },
                "speed_metrics": {
                    "expected_cycles": expected_cycles,
                    "actual_cycles": actual_cycles,
                    "completion_rate": completion_rate,
                    "duration_hours": duration / 3600,
                    "polls_per_hour": actual_cycles / (duration / 3600) if duration > 0 else 0,
                    "theoretical_max_polls_per_hour": 3600 / test['update_every']
                },
                "command": ' '.join(cmd),
                "return_code": process.returncode,
                "success": test_success,
                "duration": duration,
                "simulator_report": json_report,
                "stdout": '\n'.join(all_stdout_lines),
                "stderr": '\n'.join(all_stderr_lines)
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Speed test failed with exception: {e}")
            return {
                "name": test['name'],
                "success": False,
                "error": str(e),
                "duration": time.time() - start_time,
                "speed_metrics": {
                    "expected_cycles": int(test['duration'] / test['update_every']),
                    "actual_cycles": 0,
                    "completion_rate": 0,
                    "duration_hours": (time.time() - start_time) / 3600
                }
            }

    def _analyze_result(self, result):
        """Analyze speed test results with focus on performance and reliability."""
        if not result['success']:
            logger.error(f"❌ {result['name']}: FAILED")
            if 'error' in result:
                logger.error(f"   Error: {result['error']}")
            elif result.get('return_code') != 0:
                logger.error(f"   Exit code: {result['return_code']}")
            return
        
        if not result.get('simulator_report'):
            logger.error(f"❌ {result['name']}: NO REPORT GENERATED")
            return
        
        report = result['simulator_report']
        config = result['config']
        speed_metrics = result['speed_metrics']
        
        # Key performance metrics
        success_rate = report.get('cycle_success_rate', 0)
        failed_cycles = report.get('failed_cycles', 0)
        max_consecutive_failures = report.get('max_consecutive_failures', 0)
        avg_collection_time = report.get('avg_collection_time', 0)
        
        # Speed-specific metrics
        actual_polls_per_hour = speed_metrics['polls_per_hour']
        theoretical_max = speed_metrics['theoretical_max_polls_per_hour']
        speed_efficiency = (actual_polls_per_hour / theoretical_max * 100) if theoretical_max > 0 else 0
        
        # Determine status with focus on speed + reliability
        if success_rate >= 99 and max_consecutive_failures <= 2:
            status = "🏆 EXCELLENT - Production Ready"
        elif success_rate >= 95 and max_consecutive_failures <= 5:
            status = "✅ GOOD - Acceptable for Production"
        elif success_rate >= 90 and max_consecutive_failures <= 10:
            status = "⚠️ MARGINAL - Monitor Closely"
        elif success_rate >= 80:
            status = "❌ POOR - Too Many Failures"
        else:
            status = "🔴 CRITICAL - Unusable"
        
        logger.info(f"📊 SPEED TEST ANALYSIS: {result['name']}")
        logger.info(f"   Status: {status}")
        logger.info(f"   Success Rate: {success_rate:.1f}%")
        logger.info(f"   Failed Cycles: {failed_cycles}")
        logger.info(f"   Max Consecutive Failures: {max_consecutive_failures}")
        logger.info(f"   Avg Collection Time: {avg_collection_time:.1f}ms")
        
        logger.info(f"🚀 SPEED METRICS:")
        logger.info(f"   Polling Interval: {config['update_every']}s")
        logger.info(f"   Actual Polls/Hour: {actual_polls_per_hour:.0f}")
        logger.info(f"   Theoretical Max: {theoretical_max:.0f}")
        logger.info(f"   Speed Efficiency: {speed_efficiency:.1f}%")
        
        # Calculate speedup vs 15s baseline
        baseline_polls_per_hour = 3600 / 15  # 240 polls/hour at 15s intervals
        speedup_factor = actual_polls_per_hour / baseline_polls_per_hour
        logger.info(f"   Speedup vs 15s baseline: {speedup_factor:.1f}x")
        
        # Timeout efficiency analysis
        collection_efficiency = (avg_collection_time / (config['update_every'] * 1000)) * 100
        logger.info(f"   Collection Efficiency: {collection_efficiency:.1f}%")
        
        # Speed recommendations
        if success_rate >= 95:
            if config['update_every'] >= 4:
                next_interval = max(2, config['update_every'] - 1)
                logger.info(f"   💡 OPTIMIZATION: Try {next_interval}s intervals for even more speed")
            elif collection_efficiency < 40:
                shorter_timeout = max(1, config['fast_timeout'] - 1)  # Keep as int
                logger.info(f"   💡 OPTIMIZATION: Try {shorter_timeout}s timeouts for better efficiency")
            else:
                logger.info(f"   🎯 OPTIMAL: This configuration appears well-tuned")
        else:
            if max_consecutive_failures > 5:
                longer_timeout = config['fast_timeout'] + 1  # Keep as int
                logger.info(f"   🔧 RECOMMENDATION: Increase timeouts to {longer_timeout}s")
            if success_rate < 90:
                longer_interval = config['update_every'] + 1
                logger.info(f"   🔧 RECOMMENDATION: Increase interval to {longer_interval}s")

    def _save_progress(self):
        """Save current progress with speed-focused metrics."""
        progress_file = self.results_dir / "speed_test_progress.json"
        
        # Calculate speed-focused summary statistics
        completed_tests = len(self.test_results)
        successful_tests = sum(1 for r in self.test_results if r['success'])
        
        # Analyze speed trends across tests
        speed_data = []
        for result in self.test_results:
            if result['success'] and result.get('simulator_report'):
                report = result['simulator_report']
                config = result['config']
                speed_metrics = result['speed_metrics']
                
                speed_data.append({
                    'name': result['name'],
                    'update_every': config['update_every'],
                    'success_rate': report.get('cycle_success_rate', 0),
                    'polls_per_hour': speed_metrics['polls_per_hour'],
                    'speedup_vs_15s': speed_metrics['polls_per_hour'] / (3600/15)
                })
        
        progress_data = {
            "start_time": self.start_time.isoformat(),
            "current_time": datetime.now().isoformat(),
            "completed": completed_tests,
            "total": len(self.tests),
            "successful_tests": successful_tests,
            "test_success_rate": (successful_tests / completed_tests * 100) if completed_tests > 0 else 0,
            "speed_summary": speed_data,
            "results": self.test_results
        }
        
        with open(progress_file, 'w') as f:
            json.dump(progress_data, f, indent=2)
        
        # Enhanced CSV summary focused on speed metrics
        if self.test_results:
            csv_file = self.results_dir / "speed_test_summary.csv"
            with open(csv_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'Test', 'Interval_s', 'Timeouts_s', 'Mode', 'Success_Rate_%', 
                    'Failed_Cycles', 'Max_Consecutive', 'Avg_Collection_ms', 
                    'Polls_Per_Hour', 'Speedup_vs_15s', 'Collection_Efficiency_%', 'Status'
                ])
                
                for result in self.test_results:
                    if result['success'] and result.get('simulator_report'):
                        report = result['simulator_report']
                        config = result.get('config', {})
                        speed_metrics = result.get('speed_metrics', {})
                        
                        success_rate = report.get('cycle_success_rate', 0)
                        max_consecutive = report.get('max_consecutive_failures', 0)
                        avg_collection = report.get('avg_collection_time', 0)
                        polls_per_hour = speed_metrics.get('polls_per_hour', 0)
                        speedup = polls_per_hour / (3600/15) if polls_per_hour > 0 else 0
                        collection_eff = (avg_collection / (config.get('update_every', 1) * 1000)) * 100
                        
                        # Status determination
                        if success_rate >= 99 and max_consecutive <= 2:
                            status = "EXCELLENT"
                        elif success_rate >= 95 and max_consecutive <= 5:
                            status = "GOOD"
                        elif success_rate >= 90:
                            status = "MARGINAL"
                        else:
                            status = "POOR"
                        
                        mode = "Parallel" if config.get('parallel_collection', False) else "Serial"
                        timeout_str = f"{config.get('fast_timeout', 0)}/{config.get('ofdm_timeout', 0)}"
                        
                        writer.writerow([
                            result['name'],
                            config.get('update_every', 0),
                            timeout_str,
                            mode,
                            f"{success_rate:.1f}",
                            report.get('failed_cycles', 0),
                            max_consecutive,
                            f"{avg_collection:.1f}",
                            f"{polls_per_hour:.0f}",
                            f"{speedup:.1f}",
                            f"{collection_eff:.1f}",
                            status
                        ])
                    else:
                        writer.writerow([
                            result['name'],
                            result.get('config', {}).get('update_every', 0),
                            'N/A', 'N/A', 'FAILED', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'FAILED'
                        ])

    def _generate_final_analysis(self):
        """Generate comprehensive speed optimization analysis and recommendations."""
        logger.info("\n" + "="*80)
        logger.info("FINAL SPEED OPTIMIZATION ANALYSIS")
        logger.info("="*80)
        
        if not self.test_results:
            logger.warning("No results to analyze")
            return
        
        successful = [r for r in self.test_results if r['success'] and r.get('simulator_report')]
        
        logger.info(f"📊 Speed Test Summary:")
        logger.info(f"   Total tests: {len(self.test_results)}")
        logger.info(f"   Successful tests: {len(successful)}")
        logger.info(f"   Test success rate: {len(successful)/len(self.test_results)*100:.1f}%")
        
        if not successful:
            logger.error("No successful tests to analyze")
            return
        
        # Sort by polling speed (fastest first)
        successful_by_speed = sorted(successful, key=lambda t: t['config']['update_every'])
        
        logger.info(f"\n🏁 SPEED TEST RESULTS:")
        logger.info(f"{'Test Name':25} | {'Interval':8} | {'Success':7} | {'Failures':8} | {'Polls/Hr':8} | {'Speedup':7}")
        logger.info("-" * 80)
        
        for test in successful_by_speed:
            report = test['simulator_report']
            config = test['config']
            speed_metrics = test['speed_metrics']
            
            success_rate = report.get('cycle_success_rate', 0)
            max_consecutive = report.get('max_consecutive_failures', 0)
            polls_per_hour = speed_metrics.get('polls_per_hour', 0)
            speedup = polls_per_hour / (3600/15)
            
            logger.info(f"{test['name']:25} | {config['update_every']:8}s | {success_rate:6.1f}% | {max_consecutive:8} | {polls_per_hour:8.0f} | {speedup:6.1f}x")
        
        # Find optimal configurations
        excellent = [t for t in successful if t['simulator_report'].get('cycle_success_rate', 0) >= 99 and t['simulator_report'].get('max_consecutive_failures', 0) <= 2]
        good = [t for t in successful if t['simulator_report'].get('cycle_success_rate', 0) >= 95 and t['simulator_report'].get('max_consecutive_failures', 0) <= 5]
        
        if excellent:
            fastest_excellent = min(excellent, key=lambda t: t['config']['update_every'])
            logger.info(f"\n🏆 FASTEST EXCELLENT CONFIG:")
            self._display_optimal_config(fastest_excellent, "PRODUCTION READY")
        
        if good:
            fastest_good = min(good, key=lambda t: t['config']['update_every'])
            if not excellent or fastest_good['config']['update_every'] < fastest_excellent['config']['update_every']:
                logger.info(f"\n🚀 FASTEST GOOD CONFIG:")
                self._display_optimal_config(fastest_good, "AGGRESSIVE PRODUCTION")
        
        # Failure analysis
        failed_tests = [r for r in self.test_results if not r['success'] or r.get('simulator_report', {}).get('cycle_success_rate', 0) < 90]
        if failed_tests:
            logger.info(f"\n❌ FAILED/POOR CONFIGURATIONS:")
            for test in failed_tests:
                config = test.get('config', {})
                if test.get('simulator_report'):
                    success_rate = test['simulator_report'].get('cycle_success_rate', 0)
                    logger.info(f"     {test['name']:25} | {config.get('update_every', '?')}s intervals | {success_rate:.1f}% success - TOO AGGRESSIVE")
                else:
                    logger.info(f"     {test['name']:25} | {config.get('update_every', '?')}s intervals | FAILED - UNUSABLE")
        
        # Speed comparison with baseline
        baseline_polls_per_hour = 3600 / 15  # 240 polls/hour
        
        logger.info(f"\n📈 SPEED IMPROVEMENTS VS 15s BASELINE:")
        for test in successful_by_speed:
            speed_metrics = test['speed_metrics']
            polls_per_hour = speed_metrics.get('polls_per_hour', 0)
            speedup = polls_per_hour / baseline_polls_per_hour
            efficiency_gain = speedup - 1
            
            logger.info(f"   {test['name']:25}: {speedup:.1f}x faster ({efficiency_gain*100:+4.0f}% improvement)")
        
        # Generate configuration files
        if excellent:
            self._generate_speed_config_files(excellent)
        elif good:
            self._generate_speed_config_files(good)
        
        logger.info(f"\n📁 Results saved in: {self.results_dir}")
        logger.info("="*80)

    def _display_optimal_config(self, test_result, category):
        """Display detailed optimal configuration."""
        config = test_result['config']
        report = test_result['simulator_report']
        speed_metrics = test_result['speed_metrics']
        
        success_rate = report.get('cycle_success_rate', 0)
        avg_collection = report.get('avg_collection_time', 0)
        polls_per_hour = speed_metrics.get('polls_per_hour', 0)
        speedup = polls_per_hour / (3600/15)
        
        logger.info(f"     Configuration: {test_result['name']}")
        logger.info(f"     Category: {category}")
        logger.info(f"     Polling: Every {config['update_every']}s")
        logger.info(f"     OFDM: Every {config['ofdm_multiple']}x ({config['update_every'] * config['ofdm_multiple']}s)")
        logger.info(f"     Timeouts: Fast={config['fast_timeout']}s, OFDM={config['ofdm_timeout']}s")
        logger.info(f"     Mode: {'Parallel' if config.get('parallel_collection', False) else 'Serial'}")
        logger.info(f"     Success Rate: {success_rate:.1f}%")
        logger.info(f"     Avg Collection: {avg_collection:.1f}ms")
        logger.info(f"     Speed: {polls_per_hour:.0f} polls/hour ({speedup:.1f}x faster)")

    def _generate_speed_config_files(self, successful_configs):
        """Generate ready-to-use configuration files for optimal speed settings."""
        
        # Find fastest and most reliable configs
        fastest = min(successful_configs, key=lambda t: t['config']['update_every'])
        most_reliable = max(successful_configs, key=lambda t: t['simulator_report'].get('cycle_success_rate', 0))
        
        # Generate fastest stable configuration
        fastest_conf = self.results_dir / "hitron_coda_fastest.conf"
        with open(fastest_conf, 'w') as f:
            config = fastest['config']
            report = fastest['simulator_report']
            
            f.write("# Hitron CODA Fastest Stable Configuration\n")
            f.write("# Generated from speed optimization test suite\n")
            f.write(f"# Based on test: {fastest['name']}\n")
            f.write(f"# Success rate: {report['cycle_success_rate']:.1f}%\n")
            f.write(f"# Speed improvement: {fastest['speed_metrics']['polls_per_hour'] / (3600/15):.1f}x faster than 15s baseline\n\n")
            
            f.write("localhost:\n")
            f.write("  name: 'hitron_coda'\n")
            f.write("  host: 'https://192.168.100.1'  # Update to your modem IP\n")
            f.write("  device_name: 'Hitron CODA Cable Modem'\n")
            f.write(f"  \n")
            f.write(f"  # 🚀 FASTEST STABLE CONFIGURATION\n")
            f.write(f"  update_every: {config['update_every']}\n")
            f.write(f"  ofdm_poll_multiple: {config['ofdm_multiple']}\n")
            f.write(f"  \n")
            f.write(f"  # ⚡ OPTIMIZED TIMEOUTS\n")
            f.write(f"  fast_endpoint_timeout: {config['fast_timeout']}\n")
            f.write(f"  ofdm_endpoint_timeout: {config['ofdm_timeout']}\n")
            if config.get('max_retries') is not None:
                f.write(f"  max_retries: {config['max_retries']}\n")
            f.write(f"  \n")
            f.write(f"  # 🔧 COLLECTION SETTINGS\n")
            f.write(f"  parallel_collection: {str(config.get('parallel_collection', False)).lower()}\n")
            f.write(f"  inter_request_delay: {config.get('inter_request_delay', 0.1)}\n")
        
        # Generate most reliable configuration  
        reliable_conf = self.results_dir / "hitron_coda_reliable_fast.conf"
        with open(reliable_conf, 'w') as f:
            config = most_reliable['config']
            report = most_reliable['simulator_report']
            
            f.write("# Hitron CODA Most Reliable Fast Configuration\n")
            f.write("# Generated from speed optimization test suite\n")
            f.write(f"# Based on test: {most_reliable['name']}\n")
            f.write(f"# Success rate: {report['cycle_success_rate']:.1f}%\n")
            f.write(f"# Speed improvement: {most_reliable['speed_metrics']['polls_per_hour'] / (3600/15):.1f}x faster than 15s baseline\n\n")
            
            f.write("localhost:\n")
            f.write("  name: 'hitron_coda'\n")
            f.write("  host: 'https://192.168.100.1'  # Update to your modem IP\n")
            f.write("  device_name: 'Hitron CODA Cable Modem'\n")
            f.write(f"  \n")
            f.write(f"  # 🎯 MOST RELIABLE FAST CONFIGURATION\n")
            f.write(f"  update_every: {config['update_every']}\n")
            f.write(f"  ofdm_poll_multiple: {config['ofdm_multiple']}\n")
            f.write(f"  \n")
            f.write(f"  # ⚡ PROVEN SAFE TIMEOUTS\n")
            f.write(f"  fast_endpoint_timeout: {config['fast_timeout']}\n")
            f.write(f"  ofdm_endpoint_timeout: {config['ofdm_timeout']}\n")
            if config.get('max_retries') is not None:
                f.write(f"  max_retries: {config['max_retries']}\n")
            f.write(f"  \n")
            f.write(f"  # 🔧 COLLECTION SETTINGS\n")
            f.write(f"  parallel_collection: {str(config.get('parallel_collection', False)).lower()}\n")
            f.write(f"  inter_request_delay: {config.get('inter_request_delay', 0.1)}\n")
        
        logger.info(f"📝 Generated optimized configuration files:")
        logger.info(f"   Fastest Stable: {fastest_conf}")
        logger.info(f"   Most Reliable: {reliable_conf}")


def signal_handler(test_suite):
    """Handle signals gracefully."""
    def handler(signum, frame):
        logger.info(f"Received signal {signum}, stopping...")
        test_suite.is_running = False
        raise KeyboardInterrupt()
    return handler


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Hitron CODA Speed Optimization Test Suite')
    parser.add_argument('--simulator', default='netdata_simulator.py',
                       help='Path to simulator script')
    parser.add_argument('--host', default='https://192.168.100.1',
                       help='Modem host URL')
    parser.add_argument('--quick', action='store_true',
                       help='Run 1-hour tests instead of 2-hour tests')
    
    args = parser.parse_args()
    
    if not Path(args.simulator).exists():
        logger.error(f"Simulator not found: {args.simulator}")
        sys.exit(1)
    
    try:
        suite = HitronSpeedTestSuite(args.simulator, args.host, args.quick)
        
        # Signal handling
        handler = signal_handler(suite)
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
        
        # Run tests
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(suite.run_all_tests())
        finally:
            loop.close()
            
    except KeyboardInterrupt:
        logger.info("Speed test suite interrupted")
    except Exception as e:
        logger.error(f"Speed test suite failed: {e}")
        sys.exit(1)
    
    logger.info("Speed optimization test suite completed!")


if __name__ == "__main__":
    main()
