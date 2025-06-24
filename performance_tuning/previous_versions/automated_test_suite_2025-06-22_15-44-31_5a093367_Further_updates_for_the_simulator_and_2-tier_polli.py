#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Automated Test Suite for Hitron CODA Plugin Limit Finding.

Systematically tests polling limits to find the fastest stable configuration
for fast endpoints and optimal OFDM polling intervals.

Version: 2.0.0 - Fixed real-time output display
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
        """Create the test matrix for aggressive limit finding."""
        
        # Test durations
        if self.quick_mode:
            short = 600   # 10 min
            medium = 1200 # 20 min  
            long = 1800   # 30 min
        else:
            short = 1800  # 30 min
            medium = 3600 # 1 hour
            long = 7200   # 2 hours
        
        tests = [
            # PHASE 1: Fast Endpoint Limit Finding
            {
                "phase": 1,
                "name": "Baseline 15s",
                "description": "Validate 15s works (proven from your data)",
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
                "name": "Aggressive 10s",
                "description": "10s fast polling - pushing limits",
                "duration": medium,
                "update_every": 10,
                "ofdm_multiple": 9999,
                "fast_timeout": 2,
                "ofdm_timeout": 8,
                "max_retries": 1,
                "expected": "May show stress"
            },
            {
                "phase": 1,
                "name": "Extreme 8s",
                "description": "8s polling - near theoretical limit",
                "duration": medium,
                "update_every": 8,
                "ofdm_multiple": 9999,
                "fast_timeout": 2,
                "ofdm_timeout": 8,
                "max_retries": 0,  # No retries
                "expected": "Likely marginal"
            },
            {
                "phase": 1,
                "name": "Ultra 6s",
                "description": "6s polling - beyond safe limits",
                "duration": short,
                "update_every": 6,
                "ofdm_multiple": 9999,
                "fast_timeout": 1,
                "ofdm_timeout": 8,
                "max_retries": 0,
                "expected": "Likely to fail"
            },
            {
                "phase": 1,
                "name": "Breaking Point 5s",
                "description": "5s polling - absolute limit test",
                "duration": short,
                "update_every": 5,
                "ofdm_multiple": 9999,
                "fast_timeout": 1,
                "ofdm_timeout": 8,
                "max_retries": 0,
                "expected": "Designed to break"
            },
            
            # PHASE 2: OFDM Endpoint Testing
            {
                "phase": 2,
                "name": "OFDM Baseline 60s",
                "description": "Conservative OFDM-only polling",
                "duration": short,
                "update_every": 60,
                "ofdm_multiple": 1,  # OFDM every cycle
                "fast_timeout": 3,
                "ofdm_timeout": 8,
                "max_retries": 1,
                "expected": "Should be stable"
            },
            {
                "phase": 2,
                "name": "OFDM Aggressive 45s",
                "description": "Faster OFDM polling",
                "duration": medium,
                "update_every": 45,
                "ofdm_multiple": 1,
                "fast_timeout": 3,
                "ofdm_timeout": 8,
                "max_retries": 1,
                "expected": "Testing OFDM limits"
            },
            {
                "phase": 2,
                "name": "OFDM Breaking 30s",
                "description": "Very aggressive OFDM",
                "duration": short,
                "update_every": 30,
                "ofdm_multiple": 1,
                "fast_timeout": 3,
                "ofdm_timeout": 8,
                "max_retries": 1,
                "expected": "Likely too fast for OFDM"
            },
            
            # PHASE 3: Optimal Combinations
            {
                "phase": 3,
                "name": "Optimal Tiered",
                "description": "Best fast polling + smart OFDM scheduling",
                "duration": long,
                "update_every": 10,  # Adjust based on Phase 1
                "ofdm_multiple": 30, # 10s * 30 = 5min OFDM
                "fast_timeout": 2,
                "ofdm_timeout": 8,
                "max_retries": 1,
                "expected": "Optimal performance"
            },
            {
                "phase": 3,
                "name": "Extended Validation",
                "description": "Long-term stability test",
                "duration": long if self.quick_mode else long * 2,
                "update_every": 12,  # Conservative
                "ofdm_multiple": 25, # 12s * 25 = 5min OFDM
                "fast_timeout": 2,
                "ofdm_timeout": 8,
                "max_retries": 1,
                "expected": "Sustained stability"
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
            
            logger.info(f"\n{'='*60}")
            logger.info(f"TEST {i}/{len(self.tests)}: {test['name']}")
            logger.info(f"Phase {test['phase']}: {test['description']}")
            logger.info(f"Expected: {test['expected']}")
            logger.info(f"Duration: {test['duration']/60:.0f} minutes")
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
        """Run a single test with proper real-time output handling."""
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
                stderr=asyncio.subprocess.STDOUT  # Merge stderr into stdout
            )
            
            all_lines = []
            json_report = {}
            
            # Read all output in real-time
            while True:
                line_bytes = await process.stdout.readline()
                if not line_bytes:
                    break
                
                line = line_bytes.decode('utf-8').rstrip()
                all_lines.append(line)
                
                if line.strip():
                    # Check for progress indicators and display them immediately
                    if any(indicator in line for indicator in ['[█', 'Cycles:', '%', 'ms', 'Remaining:']):
                        # This is a progress line - show it with carriage return
                        clean_line = line.replace('\r', '').strip()
                        if clean_line:
                            print(f"\r{clean_line}", end='', flush=True)
                    elif 'INFO' in line and ('Starting' in line or 'Target end time' in line or 'Press Ctrl+C' in line):
                        # Show important startup messages
                        print(f"{line}")
                    elif 'Enhanced Simulator initialized:' in line:
                        print(f"{line}")
                    elif any(config in line for config in ['Collection mode:', 'Update interval:', 'OFDM poll multiple:', 'timeout:', 'Max retries:']):
                        # Show configuration lines
                        print(f"{line}")
                    elif 'SIMULATION FINAL REPORT' in line or 'Configuration:' in line or 'Cycle Results:' in line:
                        # Show final report headers
                        print(f"\n{line}")
                    elif any(result in line for result in ['Success Rate:', 'Collection Time:', 'Assessment:']):
                        # Show final results
                        print(f"{line}")
                
                # Try to capture JSON report
                if line.startswith('{') and line.endswith('}'):
                    try:
                        json_report.update(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            
            await process.wait()
            
            duration = time.time() - start_time
            
            print()  # New line after output
            print("=" * 60)
            logger.info(f"Test completed in {duration:.1f}s")
            
            result = {
                "phase": test['phase'],
                "name": test['name'],
                "description": test['description'],
                "command": ' '.join(cmd),
                "return_code": process.returncode,
                "success": process.returncode == 0,
                "duration": duration,
                "simulator_report": json_report,
                "output": '\n'.join(all_lines)
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Test failed: {e}")
            return {
                "phase": test['phase'],
                "name": test['name'],
                "success": False,
                "error": str(e),
                "duration": time.time() - start_time
            }

    def _analyze_result(self, result):
        """Analyze and display test result."""
        if not result['success'] or not result.get('simulator_report'):
            logger.error(f"❌ {result['name']}: FAILED")
            return
        
        report = result['simulator_report']
        success_rate = report.get('cycle_success_rate', 0)
        failed_cycles = report.get('failed_cycles', 0)
        consecutive_failures = report.get('consecutive_failures', 0)
        avg_time = report.get('avg_collection_time', 0)
        
        # Failure detection
        failures = []
        if failed_cycles >= 2:
            failures.append(f"{failed_cycles} failed cycles")
        
        # Calculate unavailability (consecutive failures * interval)
        update_every = self._extract_update_every(result['command'])
        unavailable_time = consecutive_failures * update_every
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
        
        logger.info(f"   Success Rate: {success_rate:.1f}%")
        logger.info(f"   Failed Cycles: {failed_cycles}")
        logger.info(f"   Consecutive Failures: {consecutive_failures}")
        logger.info(f"   Avg Collection Time: {avg_time:.2f}s")

    def _extract_update_every(self, command):
        """Extract update_every from command string."""
        parts = command.split()
        try:
            idx = parts.index('--update-every')
            return int(parts[idx + 1])
        except (ValueError, IndexError):
            return 60

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
                writer.writerow(['Phase', 'Test', 'Success', 'Success Rate %', 'Failed Cycles', 'Avg Time'])
                
                for result in self.test_results:
                    if result['success'] and result.get('simulator_report'):
                        report = result['simulator_report']
                        writer.writerow([
                            result['phase'],
                            result['name'],
                            'Yes',
                            f"{report.get('cycle_success_rate', 0):.1f}",
                            report.get('failed_cycles', 0),
                            f"{report.get('avg_collection_time', 0):.2f}"
                        ])
                    else:
                        writer.writerow([
                            result['phase'],
                            result['name'],
                            'No',
                            'N/A',
                            'N/A',
                            'N/A'
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
                success_rate = report.get('cycle_success_rate', 0)
                avg_time = report.get('avg_collection_time', 0)
                logger.info(f"    {test['name']:20} | {success_rate:5.1f}% | {avg_time:5.2f}s")
        
        # Find optimal configs
        stable = [t for t in successful if t['simulator_report'].get('cycle_success_rate', 0) >= 95]
        
        if stable:
            fastest = min(stable, key=lambda t: t['simulator_report'].get('avg_collection_time', float('inf')))
            most_reliable = max(stable, key=lambda t: t['simulator_report'].get('cycle_success_rate', 0))
            
            logger.info(f"\nOptimal Configurations:")
            logger.info(f"  Fastest Stable: {fastest['name']}")
            logger.info(f"    Success: {fastest['simulator_report']['cycle_success_rate']:.1f}%")
            logger.info(f"    Time: {fastest['simulator_report']['avg_collection_time']:.2f}s")
            
            logger.info(f"  Most Reliable: {most_reliable['name']}")
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
