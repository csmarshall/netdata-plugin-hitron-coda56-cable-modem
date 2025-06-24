#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Automated Test Suite for Hitron CODA Plugin Limit Finding.

Systematically tests polling limits to find the fastest stable configuration
for fast endpoints and optimal OFDM polling intervals.

UPDATED: Minimum 2-hour tests for statistically significant data.

Version: 2.1.0 - Statistical significance focused with 2+ hour minimums
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
    """Statistical significance focused test suite for Hitron CODA modems."""

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
        
        logger.info("Hitron CODA Test Suite Initialized - 18-24 Hour Optimized")
        logger.info(f"Results directory: {self.results_dir}")
        logger.info(f"Test count: {len(self.tests)}")
        logger.info(f"Quick mode: {quick_mode}")
        
        # Calculate total expected runtime
        total_hours = sum(t['duration'] for t in self.tests) / 3600
        logger.info(f"⏱️ Total expected runtime: {total_hours:.1f} hours")
        
        # Show phase breakdown
        phase_hours = {}
        for test in self.tests:
            phase = test['phase']
            if phase not in phase_hours:
                phase_hours[phase] = 0
            phase_hours[phase] += test['duration'] / 3600
        
        logger.info("📊 Phase breakdown:")
        for phase in sorted(phase_hours.keys()):
            hours = phase_hours[phase]
            logger.info(f"   Phase {phase}: {hours:.1f} hours")
        
        if total_hours > 30:
            logger.warning(f"⚠️ Runtime exceeds target ({total_hours:.1f} hours)")
        elif total_hours > 18:
            logger.info(f"✅ Runtime within 18-24 hour target ({total_hours:.1f} hours)")
        else:
            logger.info(f"✅ Efficient runtime ({total_hours:.1f} hours)")

    def _create_test_matrix(self):
        """Create the test matrix optimized for 18-24 hour completion with statistical significance."""
        
        # Test durations optimized for 18-24 hour total runtime
        if self.quick_mode:
            short = 7200    # 2 hours - for limit testing
            medium = 10800  # 3 hours - for validation
            long = 14400    # 4 hours - for stability confirmation
        else:
            short = 7200    # 2 hours - sufficient for failure detection
            medium = 10800  # 3 hours - good statistical confidence  
            long = 14400    # 4 hours - high confidence validation
        
        tests = [
            # PHASE 1: Baseline Establishment (6 hours total)
            {
                "phase": 1,
                "name": "Current 15s Baseline",
                "description": "Validate current 15s polling performance baseline",
                "duration": medium,  # 3 hours - sufficient for baseline
                "update_every": 15,
                "ofdm_multiple": 20,  # 5-minute OFDM
                "fast_timeout": 3,
                "ofdm_timeout": 8,
                "max_retries": 3,
                "expected": "Establish current performance baseline"
            },
            {
                "phase": 1,
                "name": "Optimized Timeout Baseline",
                "description": "Test current 15s with optimized timeouts",
                "duration": medium,  # 3 hours
                "update_every": 15,
                "ofdm_multiple": 20,  # 5-minute OFDM
                "fast_timeout": 1,
                "ofdm_timeout": 4,
                "max_retries": 4,
                "expected": "Validate timeout optimizations"
            },
            
            # PHASE 2: Aggressive Polling Tests (8 hours total)
            {
                "phase": 2,
                "name": "12s Aggressive Test",
                "description": "Test 12s polling for performance gains",
                "duration": medium,  # 3 hours - good sample size
                "update_every": 12,
                "ofdm_multiple": 25,  # 5-minute OFDM
                "fast_timeout": 1,
                "ofdm_timeout": 3,
                "max_retries": 3,
                "expected": "Determine if 12s is sustainable"
            },
            {
                "phase": 2,
                "name": "10s Performance Limit",
                "description": "Test 10s polling to find performance breaking point",
                "duration": medium,  # 3 hours
                "update_every": 10,
                "ofdm_multiple": 30,  # 5-minute OFDM
                "fast_timeout": 1,
                "ofdm_timeout": 3,
                "max_retries": 2,
                "expected": "Identify performance limits"
            },
            {
                "phase": 2,
                "name": "8s Breaking Point",
                "description": "Test 8s polling - designed to find absolute limits",
                "duration": short,   # 2 hours - failure detection
                "update_every": 8,
                "ofdm_multiple": 37,  # ~5-minute OFDM
                "fast_timeout": 1,
                "ofdm_timeout": 2,
                "max_retries": 2,
                "expected": "Document failure modes at aggressive settings"
            },
            
            # PHASE 3: OFDM Optimization (6 hours total)  
            {
                "phase": 3,
                "name": "OFDM 3-Minute Cycles",
                "description": "Test 3-minute OFDM cycles for optimal balance",
                "duration": medium,  # 3 hours
                "update_every": 15,
                "ofdm_multiple": 12,  # 3-minute OFDM
                "fast_timeout": 1,
                "ofdm_timeout": 4,
                "max_retries": 3,
                "expected": "Validate 3-minute OFDM as sweet spot"
            },
            {
                "phase": 3,
                "name": "OFDM 2-Minute Aggressive",
                "description": "Test aggressive 2-minute OFDM cycles",
                "duration": medium,  # 3 hours
                "update_every": 15,
                "ofdm_multiple": 8,   # 2-minute OFDM
                "fast_timeout": 1,
                "ofdm_timeout": 3,
                "max_retries": 2,
                "expected": "Test OFDM frequency limits"
            },
            
            # PHASE 4: Optimal Configuration Validation (4 hours total)
            {
                "phase": 4,
                "name": "Production Optimal Config",
                "description": "Extended validation of best performing configuration",
                "duration": long,    # 4 hours - high confidence validation
                "update_every": 12,  # Based on phase 2 results
                "ofdm_multiple": 15, # 3-minute OFDM
                "fast_timeout": 1,
                "ofdm_timeout": 3,
                "max_retries": 3,
                "expected": "Final validation of optimal settings"
            }
        ]
        
        return tests

    async def run_all_tests(self):
        """Execute all tests in the matrix optimized for 18-24 hour completion."""
        logger.info(f"Starting optimized test suite: {len(self.tests)} tests")
        total_duration = sum(t['duration'] for t in self.tests)
        total_hours = total_duration / 3600
        
        logger.info(f"📊 Test Plan Summary:")
        logger.info(f"   Total runtime: {total_hours:.1f} hours")
        logger.info(f"   Tests per phase: Phase 1 (2), Phase 2 (3), Phase 3 (2), Phase 4 (1)")
        logger.info(f"   Expected data points: {sum(int(t['duration'] / t['update_every']) for t in self.tests):,}")
        logger.info(f"   Target completion: {total_hours:.1f} hours from now")
        
        # Calculate expected completion time
        from datetime import datetime, timedelta
        completion_time = datetime.now() + timedelta(seconds=total_duration)
        logger.info(f"   Estimated completion: {completion_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        if total_hours > 25:
            logger.warning(f"⚠️ Runtime slightly over target ({total_hours:.1f} hours)")
        elif total_hours < 15:
            logger.warning(f"⚠️ Runtime might be too short for comprehensive analysis ({total_hours:.1f} hours)")
        else:
            logger.info(f"✅ Optimal runtime for comprehensive analysis ({total_hours:.1f} hours)")
        
        for i, test in enumerate(self.tests, 1):
            if not self.is_running:
                logger.warning("Test suite stopped by user")
                break
            
            # Calculate test metrics
            duration_hours = test['duration'] / 3600
            expected_cycles = int(test['duration'] / test['update_every'])
            
            # Estimate statistical confidence
            if expected_cycles >= 5000:
                confidence = "Very High"
            elif expected_cycles >= 2000:
                confidence = "High"
            elif expected_cycles >= 1000:
                confidence = "Good"
            else:
                confidence = "Moderate"
            
            logger.info(f"\n{'='*80}")
            logger.info(f"TEST {i}/{len(self.tests)}: {test['name']}")
            logger.info(f"Phase {test['phase']}: {test['description']}")
            logger.info(f"Expected: {test['expected']}")
            logger.info(f"⏱️ Duration: {duration_hours:.1f} hours")
            logger.info(f"📊 Analysis:")
            logger.info(f"   Expected cycles: {expected_cycles:,}")
            logger.info(f"   Statistical confidence: {confidence}")
            logger.info(f"   Polling: {test['update_every']}s fast, OFDM every {test['ofdm_multiple']}x")
            logger.info(f"   Timeouts: fast={test['fast_timeout']}s, OFDM={test['ofdm_timeout']}s")
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
        
        logger.info("\n🎯 18-24 hour test suite completed!")
        self._generate_final_analysis()

    async def _run_test(self, test):
        """Run a single test with enhanced statistical logging."""
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
        
        logger.info(f"🚀 Command: {' '.join(cmd)}")
        print("\n📊 SIMULATOR OUTPUT (Statistical Analysis):")
        print("=" * 80)
        
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            all_stdout_lines = []
            all_stderr_lines = []
            json_report = {}
            
            # Read stdout and stderr concurrently with statistical focus
            async def read_stdout():
                while True:
                    line_bytes = await process.stdout.readline()
                    if not line_bytes:
                        break
                    
                    line = line_bytes.decode('utf-8').rstrip()
                    all_stdout_lines.append(line)
                    
                    if line.strip():
                        # Handle different types of output - focus on statistical data
                        if line.startswith('{') and line.endswith('}'):
                            # JSON report - capture it
                            try:
                                json_report.update(json.loads(line))
                            except json.JSONDecodeError:
                                pass
                        elif any(keyword in line for keyword in ['Progress:', 'SUCCESS', 'FAILED', 'Success Rate:', 'Collection Time:']):
                            # Show progress and key statistics
                            print(line)
                        elif 'cycles' in line.lower() and any(stat in line for stat in ['%', 'ms', 'avg', 'max']):
                            # Show statistical summaries
                            print(line)
                        elif line.startswith('SIMULATION FINAL REPORT'):
                            print("\n" + "="*60)
                            print(line)
                            print("="*60)
            
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
            
            print("=" * 80)
            logger.info(f"📊 Test completed in {duration/3600:.2f} hours")
            
            # Enhanced success validation for statistical significance
            success_indicators = [
                json_report.get('cycle_success_rate', 0) > 0,  # Got meaningful data
                json_report.get('total_cycles', 0) >= test['duration'] / test['update_every'] * 0.8,  # Got most expected cycles
                process.returncode == 0,  # Clean exit
                any('SIMULATION FINAL REPORT' in line for line in all_stdout_lines),  # Report generated
                not any('Failed to generate report' in line for line in all_stderr_lines)  # No report errors
            ]
            
            test_success = all(success_indicators)
            
            # Calculate statistical metrics
            expected_cycles = int(test['duration'] / test['update_every'])
            actual_cycles = json_report.get('total_cycles', 0)
            completion_rate = (actual_cycles / expected_cycles * 100) if expected_cycles > 0 else 0
            
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
                "statistical_metrics": {
                    "expected_cycles": expected_cycles,
                    "actual_cycles": actual_cycles,
                    "completion_rate": completion_rate,
                    "duration_hours": duration / 3600,
                    "data_points_per_hour": int(3600 / test['update_every'])
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
                "duration": time.time() - start_time,
                "statistical_metrics": {
                    "expected_cycles": int(test['duration'] / test['update_every']),
                    "actual_cycles": 0,
                    "completion_rate": 0,
                    "duration_hours": (time.time() - start_time) / 3600
                }
            }

    def _analyze_result(self, result):
        """Analyze and display test result with statistical focus."""
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
        stats = result['statistical_metrics']
        
        # Key metrics
        success_rate = report.get('cycle_success_rate', 0)
        failed_cycles = report.get('failed_cycles', 0)
        max_consecutive_failures = report.get('max_consecutive_failures', 0)
        avg_time = report.get('avg_collection_time', 0)
        
        # Statistical significance indicators
        completion_rate = stats['completion_rate']
        data_points = stats['actual_cycles']
        duration_hours = stats['duration_hours']
        
        # Determine statistical confidence level
        if data_points >= 10000:
            confidence = "Very High (10k+ samples)"
        elif data_points >= 5000:
            confidence = "High (5k+ samples)"
        elif data_points >= 1000:
            confidence = "Good (1k+ samples)"
        elif data_points >= 500:
            confidence = "Moderate (500+ samples)"
        else:
            confidence = "Low (<500 samples)"
        
        # Performance assessment
        failures = []
        if failed_cycles >= 10:
            failures.append(f"{failed_cycles} failed cycles")
        if max_consecutive_failures >= 5:
            failures.append(f"{max_consecutive_failures} consecutive failures")
        if success_rate < 90:
            failures.append(f"low success rate ({success_rate:.1f}%)")
        if completion_rate < 90:
            failures.append(f"incomplete data ({completion_rate:.1f}%)")
        
        # Determine status with statistical context
        if failures:
            status = f"❌ FAILED ({', '.join(failures)})"
            logger.error(status)
        elif success_rate >= 99 and completion_rate >= 95:
            status = "✅ EXCELLENT"
            logger.info(status)
        elif success_rate >= 95 and completion_rate >= 90:
            status = "✅ GOOD"
            logger.info(status)
        elif success_rate >= 90 and completion_rate >= 85:
            status = "⚠️ MARGINAL"
            logger.warning(status)
        else:
            status = "❌ POOR"
            logger.error(status)
        
        # Display statistical analysis
        logger.info(f"📊 Statistical Analysis:")
        logger.info(f"   Success Rate: {success_rate:.2f}%")
        logger.info(f"   Data Completion: {completion_rate:.1f}% ({data_points:,}/{stats['expected_cycles']:,} cycles)")
        logger.info(f"   Statistical Confidence: {confidence}")
        logger.info(f"   Test Duration: {duration_hours:.2f} hours")
        logger.info(f"   Data Density: {stats['data_points_per_hour']} points/hour")
        logger.info(f"   Failed Cycles: {failed_cycles}")
        logger.info(f"   Max Consecutive Failures: {max_consecutive_failures}")
        logger.info(f"   Avg Collection Time: {avg_time:.2f}ms")
        
        # Performance efficiency analysis
        update_every = config['update_every']
        collection_efficiency = (avg_time / (update_every * 1000)) * 100 if update_every > 0 else 0
        theoretical_max_freq = 1000 / avg_time if avg_time > 0 else 0  # max polls per second
        actual_freq = 1 / update_every if update_every > 0 else 0
        
        logger.info(f"📈 Performance Analysis:")
        logger.info(f"   Collection Efficiency: {collection_efficiency:.1f}% (avg time vs interval)")
        logger.info(f"   Actual Frequency: {actual_freq:.3f} polls/sec")
        logger.info(f"   Theoretical Max: {theoretical_max_freq:.3f} polls/sec")
        
        # Statistical recommendations
        if data_points >= 5000 and success_rate >= 95:
            logger.info(f"   ✅ RECOMMENDATION: Configuration validated with high confidence")
        elif data_points >= 1000 and success_rate >= 90:
            logger.info(f"   ✅ RECOMMENDATION: Configuration acceptable for production")
        elif success_rate < 85:
            logger.info(f"   ❌ RECOMMENDATION: Configuration unsuitable - too many failures")
        elif completion_rate < 80:
            logger.info(f"   ⚠️ RECOMMENDATION: Test incomplete - results may not be reliable")
        else:
            logger.info(f"   ⚠️ RECOMMENDATION: More testing needed for confidence")
        
        # Optimization suggestions
        if collection_efficiency < 30 and success_rate > 95:
            suggested_interval = max(4, int(avg_time / 1000 * 2.5))
            improvement_factor = update_every / suggested_interval
            logger.info(f"   💡 OPTIMIZATION: Could poll every {suggested_interval}s ({improvement_factor:.1f}x faster)")
        
        if max_consecutive_failures == 0 and success_rate >= 99:
            logger.info(f"   💡 OPTIMIZATION: Perfect reliability - consider more aggressive settings")

    def _save_progress(self):
        """Save current progress with enhanced statistics."""
        progress_file = self.results_dir / "progress.json"
        
        # Calculate summary statistics
        completed_tests = len(self.test_results)
        successful_tests = sum(1 for r in self.test_results if r['success'])
        
        # Analyze trends across tests
        success_rates = []
        response_times = []
        for result in self.test_results:
            if result['success'] and result.get('simulator_report'):
                report = result['simulator_report']
                success_rates.append(report.get('cycle_success_rate', 0))
                response_times.append(report.get('avg_collection_time', 0))
        
        progress_data = {
            "start_time": self.start_time.isoformat(),
            "current_time": datetime.now().isoformat(),
            "completed": completed_tests,
            "total": len(self.tests),
            "successful_tests": successful_tests,
            "test_success_rate": (successful_tests / completed_tests * 100) if completed_tests > 0 else 0,
            "summary_statistics": {
                "avg_success_rate": sum(success_rates) / len(success_rates) if success_rates else 0,
                "avg_response_time": sum(response_times) / len(response_times) if response_times else 0,
                "best_success_rate": max(success_rates) if success_rates else 0,
                "worst_success_rate": min(success_rates) if success_rates else 0
            },
            "results": self.test_results
        }
        
        with open(progress_file, 'w') as f:
            json.dump(progress_data, f, indent=2)
        
        # Enhanced CSV summary
        if self.test_results:
            csv_file = self.results_dir / "summary.csv"
            with open(csv_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'Phase', 'Test', 'Success', 'Duration_Hours', 'Success_Rate_%', 
                    'Failed_Cycles', 'Max_Consecutive', 'Avg_Time_ms', 'Expected_Cycles',
                    'Actual_Cycles', 'Completion_%', 'Config', 'Recommendation'
                ])
                
                for result in self.test_results:
                    if result['success'] and result.get('simulator_report'):
                        report = result['simulator_report']
                        config = result.get('config', {})
                        stats = result.get('statistical_metrics', {})
                        
                        # Generate recommendation
                        success_rate = report.get('cycle_success_rate', 0)
                        completion_rate = stats.get('completion_rate', 0)
                        
                        if success_rate >= 95 and completion_rate >= 90:
                            recommendation = "EXCELLENT"
                        elif success_rate >= 90 and completion_rate >= 85:
                            recommendation = "GOOD"
                        elif success_rate >= 85:
                            recommendation = "MARGINAL"
                        else:
                            recommendation = "POOR"
                        
                        config_str = f"every={config.get('update_every', '?')}s, ofdm_mult={config.get('ofdm_multiple', '?')}"
                        
                        writer.writerow([
                            result['phase'],
                            result['name'],
                            'Yes',
                            f"{stats.get('duration_hours', 0):.2f}",
                            f"{success_rate:.1f}",
                            report.get('failed_cycles', 0),
                            report.get('max_consecutive_failures', 0),
                            f"{report.get('avg_collection_time', 0):.2f}",
                            stats.get('expected_cycles', 0),
                            stats.get('actual_cycles', 0),
                            f"{completion_rate:.1f}",
                            config_str,
                            recommendation
                        ])
                    else:
                        writer.writerow([
                            result['phase'],
                            result['name'],
                            'No',
                            f"{result.get('duration', 0) / 3600:.2f}",
                            'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A',
                            'FAILED',
                            'UNUSABLE'
                        ])

    def _generate_final_analysis(self):
        """Generate comprehensive final analysis and recommendations."""
        logger.info("\n" + "="*80)
        logger.info("FINAL ANALYSIS - OPTIMIZED 18-24 HOUR TEST SUITE")
        logger.info("="*80)
        
        if not self.test_results:
            logger.warning("No results to analyze")
            return
        
        successful = [r for r in self.test_results if r['success'] and r.get('simulator_report')]
        
        logger.info(f"📊 Test Suite Summary:")
        logger.info(f"   Total tests: {len(self.test_results)}")
        logger.info(f"   Successful tests: {len(successful)}")
        logger.info(f"   Test success rate: {len(successful)/len(self.test_results)*100:.1f}%")
        
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
        
        logger.info(f"\n📈 Results by Phase:")
        phase_names = {
            1: "Baseline Establishment",
            2: "Aggressive Polling Tests", 
            3: "OFDM Optimization",
            4: "Final Validation"
        }
        
        for phase in sorted(phases.keys()):
            tests = phases[phase]
            phase_name = phase_names.get(phase, f"Phase {phase}")
            logger.info(f"  {phase_name}: {len(tests)} successful tests")
            
            for test in tests:
                report = test['simulator_report']
            for test in tests:
                report = test['simulator_report']
                config = test['config']
                success_rate = report.get('cycle_success_rate', 0)
                avg_time = report.get('avg_collection_time', 0)
                stats = test.get('statistical_metrics', {})
                completion = stats.get('completion_rate', 0)
                
                logger.info(f"    {test['name']:25} | {success_rate:5.1f}% | {avg_time:5.1f}ms | {completion:5.1f}% | every={config['update_every']}s")
        
        # Find optimal configurations
        stable = [t for t in successful if t['simulator_report'].get('cycle_success_rate', 0) >= 95]
        
        if stable:
            # Sort by polling frequency (fastest first)
            stable_sorted = sorted(stable, key=lambda t: t['config']['update_every'])
            
            fastest = stable_sorted[0]
            most_reliable = max(stable, key=lambda t: t['simulator_report'].get('cycle_success_rate', 0))
            
            logger.info(f"\n🏆 Optimal Configurations Found:")
            
            logger.info(f"  🚀 FASTEST STABLE:")
            logger.info(f"     Configuration: {fastest['name']}")
            logger.info(f"     Polling: Every {fastest['config']['update_every']}s")
            logger.info(f"     OFDM: Every {fastest['config']['ofdm_multiple']}x ({fastest['config']['ofdm_multiple'] * fastest['config']['update_every']}s)")
            logger.info(f"     Success Rate: {fastest['simulator_report']['cycle_success_rate']:.1f}%")
            logger.info(f"     Avg Response: {fastest['simulator_report']['avg_collection_time']:.1f}ms")
            logger.info(f"     Timeouts: Fast={fastest['config']['fast_timeout']}s, OFDM={fastest['config']['ofdm_timeout']}s")
            
            logger.info(f"  🎯 MOST RELIABLE:")
            logger.info(f"     Configuration: {most_reliable['name']}")
            logger.info(f"     Polling: Every {most_reliable['config']['update_every']}s")
            logger.info(f"     OFDM: Every {most_reliable['config']['ofdm_multiple']}x ({most_reliable['config']['ofdm_multiple'] * most_reliable['config']['update_every']}s)")
            logger.info(f"     Success Rate: {most_reliable['simulator_report']['cycle_success_rate']:.1f}%")
            logger.info(f"     Avg Response: {most_reliable['simulator_report']['avg_collection_time']:.1f}ms")
            logger.info(f"     Timeouts: Fast={most_reliable['config']['fast_timeout']}s, OFDM={most_reliable['config']['ofdm_timeout']}s")
            
            # Production recommendations
            logger.info(f"\n💼 Production Recommendations:")
            
            if fastest['config']['update_every'] <= 12:
                logger.info(f"  ✅ AGGRESSIVE PRODUCTION: Use fastest stable config ({fastest['config']['update_every']}s polling)")
                logger.info(f"     - Excellent for high-frequency monitoring")
                logger.info(f"     - Monitor for long-term stability")
                logger.info(f"     - Consider fallback to conservative settings")
            
            if most_reliable['config']['update_every'] >= 15:
                logger.info(f"  ✅ CONSERVATIVE PRODUCTION: Use most reliable config ({most_reliable['config']['update_every']}s polling)")
                logger.info(f"     - Maximum stability for critical environments")
                logger.info(f"     - Lower resource usage")
                logger.info(f"     - Recommended for 24/7 operation")
            
            # Calculate performance gains
            baseline_15s = next((t for t in successful if t['config']['update_every'] == 15), None)
            if baseline_15s and fastest['config']['update_every'] < 15:
                improvement_factor = 15 / fastest['config']['update_every']
                logger.info(f"  📈 PERFORMANCE GAIN: {improvement_factor:.1f}x faster than 15s baseline")
        
        else:
            logger.warning("⚠️ No stable configurations found (success rate >= 95%)")
            # Show best available
            if successful:
                best = max(successful, key=lambda t: t['simulator_report'].get('cycle_success_rate', 0))
                logger.info(f"  📊 BEST AVAILABLE:")
                logger.info(f"     Configuration: {best['name']}")
                logger.info(f"     Success Rate: {best['simulator_report']['cycle_success_rate']:.1f}%")
                logger.info(f"     Recommendation: Needs optimization or longer testing")
        
        # Failure analysis
        failed_tests = [r for r in self.test_results if not r['success'] or r.get('simulator_report', {}).get('cycle_success_rate', 0) < 85]
        if failed_tests:
            logger.info(f"\n❌ Failed/Poor Configurations:")
            for test in failed_tests:
                config = test.get('config', {})
                if test.get('simulator_report'):
                    success_rate = test['simulator_report'].get('cycle_success_rate', 0)
                    logger.info(f"     {test['name']:25} | {success_rate:5.1f}% | every={config.get('update_every', '?')}s - TOO AGGRESSIVE")
                else:
                    logger.info(f"     {test['name']:25} | FAILED | every={config.get('update_every', '?')}s - UNUSABLE")
        
        # Save final configuration files
        if stable:
            self._generate_config_files(stable[0], most_reliable)
        
        logger.info(f"\n📁 Results saved in: {self.results_dir}")
        logger.info("="*80)

    def _generate_config_files(self, fastest_config, reliable_config):
        """Generate ready-to-use configuration files."""
        
        # Fastest stable configuration
        fastest_conf = self.results_dir / "hitron_coda_aggressive.conf"
        with open(fastest_conf, 'w') as f:
            f.write("# Hitron CODA Aggressive Configuration\n")
            f.write("# Generated from 18-24 hour test suite\n")
            f.write(f"# Based on test: {fastest_config['name']}\n")
            f.write(f"# Success rate: {fastest_config['simulator_report']['cycle_success_rate']:.1f}%\n\n")
            
            f.write("localhost:\n")
            f.write("  name: 'hitron_coda'\n")
            f.write("  host: 'https://192.168.100.1'  # Update to your modem IP\n")
            f.write("  device_name: 'Hitron CODA Cable Modem'\n")
            f.write(f"  update_every: {fastest_config['config']['update_every']}\n")
            f.write(f"  ofdm_poll_multiple: {fastest_config['config']['ofdm_multiple']}\n")
            f.write("  parallel_collection: false\n")
            f.write(f"  fast_endpoint_timeout: {fastest_config['config']['fast_timeout']}\n")
            f.write(f"  ofdm_endpoint_timeout: {fastest_config['config']['ofdm_timeout']}\n")
            f.write(f"  max_retries: {fastest_config['config']['max_retries']}\n")
        
        # Most reliable configuration  
        reliable_conf = self.results_dir / "hitron_coda_production.conf"
        with open(reliable_conf, 'w') as f:
            f.write("# Hitron CODA Production Configuration\n")
            f.write("# Generated from 18-24 hour test suite\n")
            f.write(f"# Based on test: {reliable_config['name']}\n")
            f.write(f"# Success rate: {reliable_config['simulator_report']['cycle_success_rate']:.1f}%\n\n")
            
            f.write("localhost:\n")
            f.write("  name: 'hitron_coda'\n")
            f.write("  host: 'https://192.168.100.1'  # Update to your modem IP\n")
            f.write("  device_name: 'Hitron CODA Cable Modem'\n")
            f.write(f"  update_every: {reliable_config['config']['update_every']}\n")
            f.write(f"  ofdm_poll_multiple: {reliable_config['config']['ofdm_multiple']}\n")
            f.write("  parallel_collection: false\n")
            f.write(f"  fast_endpoint_timeout: {reliable_config['config']['fast_timeout']}\n")
            f.write(f"  ofdm_endpoint_timeout: {reliable_config['config']['ofdm_timeout']}\n")
            f.write(f"  max_retries: {reliable_config['config']['max_retries']}\n")
        
        logger.info(f"📝 Generated configuration files:")
        logger.info(f"   Aggressive: {fastest_conf}")
        logger.info(f"   Production: {reliable_conf}")


def signal_handler(test_suite):
    """Handle signals gracefully."""
    def handler(signum, frame):
        logger.info(f"Received signal {signum}, stopping...")
        test_suite.is_running = False
        raise KeyboardInterrupt()
    return handler


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Hitron CODA 18-24 Hour Optimized Test Suite')
    parser.add_argument('--simulator', default='netdata_simulator.py',
                       help='Path to simulator script')
    parser.add_argument('--host', default='https://192.168.100.1',
                       help='Modem host URL')
    parser.add_argument('--quick', action='store_true',
                       help='Run slightly shorter tests (still 18+ hours total)')
    
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
    
    logger.info("18-24 hour test suite completed!")


if __name__ == "__main__":
    main()
