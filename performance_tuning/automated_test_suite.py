#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Automated Test Suite for Hitron CODA Plugin Limit Finding.

Systematically tests polling limits to find the fastest stable configuration
for fast endpoints and optimal OFDM polling intervals.

Version: 2.0.1 - Fixed real-time output handling and JSON parsing
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


class HitronTestSuite:
    """Aggressive limit-finding test suite for Hitron CODA modems."""

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
        self.results_dir = Path.cwd() / f"hitron_limits_test_{timestamp}"
        self.results_dir.mkdir(exist_ok=True)
        
        # File logging
        log_file = self.results_dir / 'test_suite.log'
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(file_handler)
        
        # Build test matrix
        self.tests = self._create_test_matrix()
        
        logger.info("Hitron CODA Test Suite Initialized")
        logger.info(f"Results directory: {self.results_dir}")
        logger.info(f"Test count: {len(self.tests)}")
        logger.info(f"Quick mode: {quick_mode}")

    def _create_test_matrix(self):
        """Create the test matrix for aggressive limit finding with timeout optimization."""
        
        # Test durations
        if self.quick_mode:
            short = 600   # 10 min
            medium = 900  # 15 min  
            long = 1200   # 20 min
        else:
            short = 1800  # 30 min
            medium = 3600 # 1 hour
            long = 7200   # 2 hours
        
        tests = [
            # PHASE 1: Baseline and Timeout Optimization
            {
                "phase": 1,
                "name": "Current Config Baseline",
                "description": "Validate current 15s works with conservative timeouts",
                "duration": short,
                "update_every": 15,
                "ofdm_multiple": 9999,  # Fast endpoints only
                "fast_timeout": 3,
                "ofdm_timeout": 8,
                "max_retries": 1,
                "expected": "Should work - proven config"
            },
            {
                "phase": 1,
                "name": "Optimized Timeouts",
                "description": "Same 15s interval but optimized timeouts",
                "duration": short,
                "update_every": 15,
                "ofdm_multiple": 9999,
                "fast_timeout": 1,      # 10x faster timeout
                "ofdm_timeout": 2,      # 4x faster timeout
                "max_retries": 2,       # More retries since timeouts are tighter
                "expected": "Should work with much faster response"
            },
            
            # PHASE 2: Aggressive Polling with Optimized Timeouts
            {
                "phase": 2,
                "name": "Aggressive 10s + Fast Timeouts",
                "description": "10s polling with optimized 1s timeouts",
                "duration": medium,
                "update_every": 10,
                "ofdm_multiple": 9999,
                "fast_timeout": 1,
                "ofdm_timeout": 2,
                "max_retries": 3,
                "expected": "Should be very responsive"
            },
            {
                "phase": 2,
                "name": "Very Aggressive 8s",
                "description": "8s polling - approaching theoretical limits",
                "duration": medium,
                "update_every": 8,
                "ofdm_multiple": 9999,
                "fast_timeout": 1,
                "ofdm_timeout": 2,
                "max_retries": 2,
                "expected": "Testing near-optimal speed"
            },
            {
                "phase": 2,
                "name": "Ultra Aggressive 6s",
                "description": "6s polling - 3x collection time",
                "duration": short,
                "update_every": 6,
                "ofdm_multiple": 9999,
                "fast_timeout": 1,
                "ofdm_timeout": 2,
                "max_retries": 2,
                "expected": "May hit limits"
            },
            {
                "phase": 2,
                "name": "Extreme 5s",
                "description": "5s polling - theoretical minimum",
                "duration": short,
                "update_every": 5,
                "ofdm_multiple": 9999,
                "fast_timeout": 1,
                "ofdm_timeout": 2,
                "max_retries": 1,
                "expected": "Likely too aggressive"
            },
            {
                "phase": 2,
                "name": "Breaking Point 4s",
                "description": "4s polling - beyond safe limits",
                "duration": short,
                "update_every": 4,
                "ofdm_multiple": 9999,
                "fast_timeout": 1,
                "ofdm_timeout": 2,
                "max_retries": 1,
                "expected": "Designed to break"
            },
            
            # PHASE 3: OFDM Polling with Optimized Timeouts
            {
                "phase": 3,
                "name": "OFDM Every 60s",
                "description": "OFDM polling with fast timeouts",
                "duration": short,
                "update_every": 60,
                "ofdm_multiple": 1,     # OFDM every cycle
                "fast_timeout": 1,
                "ofdm_timeout": 2,      # Much faster than 8s
                "max_retries": 2,
                "expected": "Should be very fast"
            },
            {
                "phase": 3,
                "name": "OFDM Every 45s",
                "description": "Faster OFDM with optimized timeouts",
                "duration": medium,
                "update_every": 45,
                "ofdm_multiple": 1,
                "fast_timeout": 1,
                "ofdm_timeout": 2,
                "max_retries": 2,
                "expected": "Testing OFDM limits with fast timeouts"
            },
            {
                "phase": 3,
                "name": "OFDM Every 30s",
                "description": "Very aggressive OFDM polling",
                "duration": short,
                "update_every": 30,
                "ofdm_multiple": 1,
                "fast_timeout": 1,
                "ofdm_timeout": 2,
                "max_retries": 1,
                "expected": "May be too fast for OFDM endpoints"
            },
            
            # PHASE 4: Optimal Tiered Configurations
            {
                "phase": 4,
                "name": "Optimal Tiered 10s/5min",
                "description": "10s fast polling, 5min OFDM with optimized timeouts",
                "duration": long,
                "update_every": 10,
                "ofdm_multiple": 30,    # 10s * 30 = 5min OFDM
                "fast_timeout": 1,
                "ofdm_timeout": 2,
                "max_retries": 2,
                "expected": "Optimal production config"
            },
            {
                "phase": 4,
                "name": "Ultra Tiered 8s/5min",
                "description": "8s fast polling, 5min OFDM - maximum performance",
                "duration": long,
                "update_every": 8,
                "ofdm_multiple": 37,    # 8s * 37 = ~5min OFDM
                "fast_timeout": 1,
                "ofdm_timeout": 2,
                "max_retries": 2,
                "expected": "Maximum sustainable performance"
            },
            {
                "phase": 4,
                "name": "Extended Validation",
                "description": "Long-term stability test with optimal settings",
                "duration": long if self.quick_mode else long * 2,
                "update_every": 12,     # Slightly conservative for long-term
                "ofdm_multiple": 25,    # 12s * 25 = 5min OFDM
                "fast_timeout": 1,
                "ofdm_timeout": 2,
                "max_retries": 3,       # Extra retries for stability
                "expected": "Sustained optimal performance"
            }
        ]
        
        return tests

    async def run_all_tests(self):
        """Execute all tests in the matrix."""
        logger.info(f"Starting test suite: {len(self.tests)} tests")
        total_duration = sum(t['duration'] for t in self.tests) / 60
        logger.info(f"Estimated duration: {total_duration:.0f} minutes")
        
        for i, test in enumerate(self.tests, 1):
            if not self.is_running:
                logger.warning("Test suite stopped by user")
                break
            
            # Calculate test estimates
            update_every = test['update_every']
            duration_min = test['duration'] / 60
            expected_cycles = int(test['duration'] / update_every)
            
            # Estimate collection time based on endpoint count
            if test['ofdm_multiple'] >= 999:  # Fast endpoints only
                estimated_collection_time = 1.3  # 5 endpoints, based on your data
                endpoint_description = "5 fast endpoints only"
            else:
                estimated_collection_time = 1.8  # All endpoints
                endpoint_description = "5 fast + 2 OFDM endpoints"
            
            collection_efficiency = (estimated_collection_time / update_every) * 100
            idle_time = 100 - collection_efficiency
            
            logger.info(f"\n{'='*60}")
            logger.info(f"TEST {i}/{len(self.tests)}: {test['name']}")
            logger.info(f"Phase {test['phase']}: {test['description']}")
            logger.info(f"Expected: {test['expected']}")
            logger.info(f"Duration: {duration_min:.0f} minutes")
            logger.info(f"📊 Test Estimates:")
            logger.info(f"   Polling interval: {update_every}s ({endpoint_description})")
            logger.info(f"   Expected cycles: {expected_cycles} cycles")
            logger.info(f"   Est. collection time: {estimated_collection_time:.1f}s")
            logger.info(f"   Collection efficiency: {collection_efficiency:.1f}% (idle: {idle_time:.1f}%)")
            logger.info(f"   Timeouts: fast={test['fast_timeout']}s, OFDM={test['ofdm_timeout']}s")
            logger.info(f"{'='*60}")
            
            await asyncio.sleep(2)  # Brief pause
            
            result = await self._run_test(test)
            self.test_results.append(result)
            
            self._save_progress()
            self._analyze_result(result)
            
            # Pause between tests
            if i < len(self.tests) and self.is_running:
                logger.info("Brief pause before next test...")
                for countdown in range(10, 0, -1):
                    if not self.is_running:
                        break
                    logger.info(f"Next test in {countdown}s...")
                    await asyncio.sleep(1)
        
        logger.info("\nAll tests completed!")
        self._generate_final_analysis()

    async def _run_test(self, test):
        """Run a single test with improved output handling."""
        start_time = time.time()
        
        # Build command
        cmd = [
            sys.executable, str(self.simulator_script),
            '--host', self.host,
            '--duration', str(test['duration']),
            '--update-every', str(test['update_every']),
            '--ofdm-poll-multiple', str(test['ofdm_multiple']),
            '--fast-endpoint-timeout', str(test['fast_timeout']),
            '--ofdm-endpoint-timeout', str(test['ofdm_timeout']),
            '--max-retries', str(test['max_retries']),
            '--serial'
        ]
        
        logger.info(f"Command: {' '.join(cmd)}")
        print("\n🔄 SIMULATOR OUTPUT:")
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
                        # Handle different types of output
                        if line.startswith('{') and line.endswith('}'):
                            # This is the JSON report - capture it
                            try:
                                json_report.update(json.loads(line))
                            except json.JSONDecodeError:
                                pass
                        elif any(keyword in line for keyword in ['Progress:', 'INFO', 'Enhanced Simulator', 'Collection mode:', 'SIMULATION FINAL REPORT']):
                            # Show informational and progress lines
                            print(line)
                        elif 'Configuration:' in line or 'Cycle Results:' in line or 'Assessment:' in line:
                            # Show report section headers
                            print(line)
                        elif any(result in line for result in ['Success Rate:', 'Collection Time:', 'Overall Rating:']):
                            # Show key results
                            print(line)
            
            async def read_stderr():
                while True:
                    line_bytes = await process.stderr.readline()
                    if not line_bytes:
                        break
                    
                    line = line_bytes.decode('utf-8').rstrip()
                    all_stderr_lines.append(line)
                    
                    # Show actual error lines immediately
                    if line.strip():
                        print(f"STDERR: {line}")
            
            # Read both streams concurrently
            await asyncio.gather(read_stdout(), read_stderr())
            await process.wait()
            
            duration = time.time() - start_time
            
            print("=" * 60)
            logger.info(f"Test completed in {duration:.1f}s")
            
            # Check for common failure patterns
            success_indicators = [
                json_report,  # Got JSON report
                process.returncode == 0,  # Clean exit
                any('SIMULATION FINAL REPORT' in line for line in all_stdout_lines),  # Report generated
                not any('Failed to generate report' in line for line in all_stderr_lines)  # No report errors
            ]
            
            test_success = all(success_indicators)
            
            result = {
                "phase": test['phase'],
                "name": test['name'],
                "description": test['description'],
                "config": {
                    "update_every": test['update_every'],
                    "ofdm_multiple": test['ofdm_multiple'],
                    "fast_timeout": test['fast_timeout'],
                    "ofdm_timeout": test['ofdm_timeout'],
                    "max_retries": test['max_retries']
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
            logger.error(f"Test failed with exception: {e}")
            return {
                "phase": test['phase'],
                "name": test['name'],
                "success": False,
                "error": str(e),
                "duration": time.time() - start_time
            }

    def _analyze_result(self, result):
        """Analyze and display test result with polling calculations."""
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
        success_rate = report.get('cycle_success_rate', 0)
        failed_cycles = report.get('failed_cycles', 0)
        max_consecutive_failures = report.get('max_consecutive_failures', 0)
        avg_time = report.get('avg_collection_time', 0)
        total_cycles = report.get('total_cycles', 0)
        
        # Calculate polling efficiency and estimates
        update_every = config['update_every']
        test_duration = result['duration']
        expected_cycles = int(test_duration / update_every)
        actual_cycles = total_cycles
        completion_rate = (actual_cycles / expected_cycles * 100) if expected_cycles > 0 else 0
        
        # Calculate collection efficiency
        collection_efficiency = (avg_time / update_every * 100) if update_every > 0 else 0
        idle_time_percent = 100 - collection_efficiency
        
        # Theoretical maximum polling frequency (3x collection time for safety)
        min_safe_interval = avg_time * 3
        max_theoretical_frequency = 1 / min_safe_interval if min_safe_interval > 0 else 0
        
        # Failure detection
        failures = []
        if failed_cycles >= 2:
            failures.append(f"{failed_cycles} failed cycles")
        
        # Calculate unavailability (consecutive failures * interval)
        unavailable_time = max_consecutive_failures * update_every
        if unavailable_time >= 30:
            failures.append(f"{unavailable_time}s unavailable")
        
        if success_rate < 85:
            failures.append(f"low success rate ({success_rate:.1f}%)")
        
        # Determine status
        if failures:
            status = f"❌ FAILED ({', '.join(failures)})"
            logger.error(status)
        elif success_rate >= 99:
            status = "✅ EXCELLENT"
            logger.info(status)
        elif success_rate >= 95:
            status = "✅ GOOD"
            logger.info(status)
        elif success_rate >= 90:
            status = "⚠️ MARGINAL"
            logger.warning(status)
        else:
            status = "❌ POOR"
            logger.error(status)
        
        # Display results with polling calculations and emojis
        logger.info(f"   📊 Success Rate: {success_rate:.1f}%")
        logger.info(f"   ❌ Failed Cycles: {failed_cycles}")
        logger.info(f"   🔄 Max Consecutive Failures: {max_consecutive_failures}")
        logger.info(f"   ⏱️ Avg Collection Time: {avg_time:.2f}s")
        logger.info(f"   📈 Collection Efficiency: {collection_efficiency:.1f}% (💤 idle: {idle_time_percent:.1f}%)")
        logger.info(f"   🔢 Polling Stats: {actual_cycles}/{expected_cycles} cycles ({completion_rate:.1f}% completion)")
        
        if max_theoretical_frequency > 0:
            logger.info(f"   🚀 Theoretical Max Frequency: {max_theoretical_frequency:.2f} polls/sec (every {min_safe_interval:.1f}s)")
        
        # Performance recommendations with emojis
        if success_rate >= 95 and collection_efficiency < 50:
            recommended_interval = max(4, int(avg_time * 2.5))  # 2.5x safety margin
            improvement_factor = update_every / recommended_interval
            logger.info(f"   💡 Recommendation: Could poll every {recommended_interval}s ({improvement_factor:.1f}x faster)")
        elif success_rate >= 95 and collection_efficiency < 80:
            logger.info(f"   ✨ Performance: Excellent efficiency, minor optimization possible")
        elif success_rate < 95:
            logger.warning(f"   ⚠️ Recommendation: Increase interval or timeouts for stability")

    def _save_progress(self):
        """Save current progress."""
        progress_file = self.results_dir / "progress.json"
        with open(progress_file, 'w') as f:
            json.dump({
                "start_time": self.start_time.isoformat(),
                "current_time": datetime.now().isoformat(),
                "completed": len(self.test_results),
                "total": len(self.tests),
                "results": self.test_results
            }, f, indent=2)
        
        # CSV summary
        if self.test_results:
            csv_file = self.results_dir / "summary.csv"
            with open(csv_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Phase', 'Test', 'Success', 'Success Rate %', 'Failed Cycles', 'Max Consecutive', 'Avg Time', 'Config'])
                
                for result in self.test_results:
                    if result['success'] and result.get('simulator_report'):
                        report = result['simulator_report']
                        config = result.get('config', {})
                        config_str = f"every={config.get('update_every', '?')}s, ofdm_mult={config.get('ofdm_multiple', '?')}"
                        writer.writerow([
                            result['phase'],
                            result['name'],
                            'Yes',
                            f"{report.get('cycle_success_rate', 0):.1f}",
                            report.get('failed_cycles', 0),
                            report.get('max_consecutive_failures', 0),
                            f"{report.get('avg_collection_time', 0):.2f}",
                            config_str
                        ])
                    else:
                        writer.writerow([
                            result['phase'],
                            result['name'],
                            'No',
                            'N/A',
                            'N/A',
                            'N/A',
                            'N/A',
                            'FAILED'
                        ])

    def _generate_final_analysis(self):
        """Generate final analysis and recommendations."""
        logger.info("\n" + "="*60)
        logger.info("FINAL ANALYSIS")
        logger.info("="*60)
        
        if not self.test_results:
            logger.warning("No results to analyze")
            return
        
        successful = [r for r in self.test_results if r['success'] and r.get('simulator_report')]
        
        logger.info(f"Total tests: {len(self.test_results)}")
        logger.info(f"Successful tests: {len(successful)}")
        
        if not successful:
            logger.error("No successful tests to analyze")
            return
        
        # Phase analysis
        phases = {}
        for test in successful:
            phase = test['phase']
            if phase not in phases:
                phases[phase] = []
            phases[phase].append(test)
        
        logger.info("\nPhase Results:")
        for phase in sorted(phases.keys()):
            tests = phases[phase]
            logger.info(f"  Phase {phase}: {len(tests)} successful tests")
            for test in tests:
                report = test['simulator_report']
                config = test['config']
                success_rate = report.get('cycle_success_rate', 0)
                avg_time = report.get('avg_collection_time', 0)
                logger.info(f"    {test['name']:20} | {success_rate:5.1f}% | {avg_time:5.2f}s | every={config['update_every']}s")
        
        # Find optimal configs
        stable = [t for t in successful if t['simulator_report'].get('cycle_success_rate', 0) >= 95]
        
        if stable:
            fastest = min(stable, key=lambda t: t['config']['update_every'])
            most_reliable = max(stable, key=lambda t: t['simulator_report'].get('cycle_success_rate', 0))
            
            logger.info(f"\nOptimal Configurations:")
            logger.info(f"  Fastest Stable: {fastest['name']}")
            logger.info(f"    Update Every: {fastest['config']['update_every']}s")
            logger.info(f"    Success: {fastest['simulator_report']['cycle_success_rate']:.1f}%")
            logger.info(f"    Time: {fastest['simulator_report']['avg_collection_time']:.2f}s")
            
            logger.info(f"  Most Reliable: {most_reliable['name']}")
            logger.info(f"    Update Every: {most_reliable['config']['update_every']}s")
            logger.info(f"    Success: {most_reliable['simulator_report']['cycle_success_rate']:.1f}%")
            logger.info(f"    Time: {most_reliable['simulator_report']['avg_collection_time']:.2f}s")
        
        logger.info(f"\nResults saved in: {self.results_dir}")


def signal_handler(test_suite):
    """Handle signals gracefully."""
    def handler(signum, frame):
        logger.info(f"Received signal {signum}, stopping...")
        test_suite.is_running = False
        raise KeyboardInterrupt()
    return handler


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Hitron CODA Aggressive Limit Testing')
    parser.add_argument('--simulator', default='netdata_simulator.py',
                       help='Path to simulator script')
    parser.add_argument('--host', default='https://192.168.100.1',
                       help='Modem host URL')
    parser.add_argument('--quick', action='store_true',
                       help='Run shorter tests')
    
    args = parser.parse_args()
    
    if not Path(args.simulator).exists():
        logger.error(f"Simulator not found: {args.simulator}")
        sys.exit(1)
    
    try:
        suite = HitronTestSuite(args.simulator, args.host, args.quick)
        
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
        logger.info("Test suite interrupted")
    except Exception as e:
        logger.error(f"Test suite failed: {e}")
        sys.exit(1)
    
    logger.info("Test suite completed!")


if __name__ == "__main__":
    main()
