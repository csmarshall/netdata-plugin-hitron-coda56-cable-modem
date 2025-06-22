#!/usr/bin/env python
"""
Modem Stability Test Suite for Netdata Hitron CODA Plugin
Finds optimal polling interval ≤2 minutes that doesn't trigger reboots.
"""

import asyncio
import subprocess
import time
import json
import csv
from datetime import datetime, timedelta
from pathlib import Path
import logging
import sys
import signal
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('modem_stability_test.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ModemStabilityTestSuite:
    """Test suite focused on finding maximum sustainable polling without reboots."""
    
    def __init__(self, simulator_script: str = "netdata_simulator.py", base_host: str = "https://192.168.100.1"):
        self.simulator_script = Path(simulator_script).resolve()
        self.base_host = base_host
        self.start_time = datetime.now()
        self.test_results = []
        self.is_running = True
        
        # Create results directory
        self.results_dir = Path.cwd() / f"modem_stability_results_{self.start_time.strftime('%Y%m%d_%H%M%S')}"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Modem Stability Test Suite initialized")
        logger.info(f"  Simulator script: {self.simulator_script}")
        logger.info(f"  Results directory: {self.results_dir}")
        
        # Define reboot-focused test matrix
        self.test_matrix = self._define_stability_test_matrix()
        
    def _define_stability_test_matrix(self) -> list:
        """Define test matrix focused on sustainable polling rates."""
        tests = [
            {
                "phase": "conservative_baseline",
                "name": "ultra_conservative_120s", 
                "description": "2-minute polling - ultra conservative baseline",
                "args": ["--update-every", "120", "--endpoint-timeout", "8s", "--max-retries", "1", 
                        "--inter-request-delay", "3.0", "--duration", "7200"],
                "expected": "Should work without reboots - establishes safe baseline"
            },
            {
                "phase": "moderate_testing",
                "name": "moderate_90s",
                "description": "90-second polling - moderate conservative",
                "args": ["--update-every", "90", "--endpoint-timeout", "6s", "--max-retries", "1", 
                        "--inter-request-delay", "2.0", "--duration", "7200"],
                "expected": "Should be stable with good margin"
            },
            {
                "phase": "standard_monitoring", 
                "name": "standard_60s",
                "description": "1-minute polling - standard monitoring rate",
                "args": ["--update-every", "60", "--endpoint-timeout", "5s", "--max-retries", "1", 
                        "--inter-request-delay", "1.5", "--duration", "7200"],
                "expected": "May start showing stress signs"
            },
            {
                "phase": "aggressive_testing",
                "name": "fast_45s",
                "description": "45-second polling - faster monitoring",
                "args": ["--update-every", "45", "--endpoint-timeout", "4s", "--max-retries", "1", 
                        "--inter-request-delay", "1.0", "--duration", "7200"],
                "expected": "Likely to cause reboots"
            },
            {
                "phase": "aggressive_testing",
                "name": "aggressive_30s",
                "description": "30-second polling - aggressive monitoring", 
                "args": ["--update-every", "30", "--endpoint-timeout", "3s", "--max-retries", "0", 
                        "--inter-request-delay", "2.0", "--duration", "7200"],
                "expected": "High chance of reboots - no retries to reduce load"
            },
            {
                "phase": "balance_optimization",
                "name": "balanced_75s",
                "description": "75-second polling - balance optimization",
                "args": ["--update-every", "75", "--endpoint-timeout", "5s", "--max-retries", "1", 
                        "--inter-request-delay", "1.5", "--duration", "7200"],
                "expected": "Good production candidate"
            },
            {
                "phase": "limit_testing",
                "name": "limit_35s",
                "description": "35-second polling - pushing limits",
                "args": ["--update-every", "35", "--endpoint-timeout", "3s", "--max-retries", "0", 
                        "--inter-request-delay", "1.0", "--duration", "7200"],
                "expected": "Find reboot threshold"
            },
            {
                "phase": "optimal_hunt",
                "name": "optimal_50s", 
                "description": "50-second polling - optimal hunt",
                "args": ["--update-every", "50", "--endpoint-timeout", "4s", "--max-retries", "1", 
                        "--inter-request-delay", "1.0", "--duration", "7200"],
                "expected": "Potential sweet spot"
            }
        ]
        
        return tests
    
    async def run_single_test(self, test: dict) -> dict:
        """Run a single stability test."""
        test_id = f"{test['phase']}_{test['name']}"
        test_start = datetime.now()
        
        logger.info(f"🧪 Starting stability test: {test_id}")
        logger.info(f"   Description: {test['description']}")
        logger.info(f"   Duration: 2 hours (reboot detection)")
        
        # Create test output directory
        test_output_dir = self.results_dir / test_id
        test_output_dir.mkdir(parents=True, exist_ok=True)
        
        # Prepare command
        cmd = [
            sys.executable,
            str(self.simulator_script),
            "--host", self.base_host
        ] + test['args']
        
        logger.info(f"   Command: {' '.join(cmd)}")
        
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(test_output_dir),
                preexec_fn=None if sys.platform == "win32" else os.setsid
            )
            
            logger.info(f"   Process PID: {process.pid}")
            
            # Read output and monitor for reboots
            output_lines = []
            reboot_events = []
            last_success_time = None
            
            try:
                while True:
                    if not self.is_running:
                        logger.info(f"Stopping test {test_id} due to user request...")
                        break
                    
                    try:
                        line_bytes = await asyncio.wait_for(
                            process.stdout.readline(), 
                            timeout=1.0
                        )
                        
                        if not line_bytes:
                            break
                            
                        line = line_bytes.decode('utf-8').rstrip()
                        if line:
                            output_lines.append(line)
                            
                            # Monitor for success/failure patterns
                            if "✅" in line and "success" in line.lower():
                                last_success_time = datetime.now()
                            elif "❌" in line and ("timeout" in line.lower() or "failed" in line.lower()):
                                if last_success_time:
                                    time_since_success = (datetime.now() - last_success_time).total_seconds()
                                    if time_since_success > 300:  # 5+ minutes of failures = potential reboot
                                        reboot_events.append({
                                            'time': datetime.now(),
                                            'uptime_before_reboot': time_since_success
                                        })
                                        last_success_time = None
                            
                            # Log important lines
                            if any(keyword in line for keyword in ['INFO', 'ERROR', 'Cycle', 'PROGRESS', 'SUCCESS', 'FAILED']):
                                logger.info(f"   [{test_id}] {line}")
                                
                    except asyncio.TimeoutError:
                        if process.returncode is not None:
                            break
                        continue
                
                await process.wait()
                
            except asyncio.CancelledError:
                logger.info(f"Test {test_id} cancelled - terminating process...")
                if process and process.returncode is None:
                    try:
                        if hasattr(os, 'killpg'):
                            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                        else:
                            process.terminate()
                        await asyncio.wait_for(process.wait(), timeout=5.0)
                    except:
                        if hasattr(os, 'killpg'):
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        else:
                            process.kill()
                        await process.wait()
                raise
            
            test_end = datetime.now()
            test_duration = (test_end - test_start).total_seconds()
            
            # Analyze results
            output_text = '\n'.join(output_lines)
            metrics = self._analyze_stability_output(output_text, reboot_events)
            
            # Collect files
            generated_files = []
            try:
                generated_files = [f.name for f in test_output_dir.iterdir() if f.is_file()]
            except:
                pass
            
            result = {
                "test_id": test_id,
                "name": test['name'],
                "description": test['description'],
                "polling_interval": self._extract_interval(test['args']),
                "start_time": test_start.isoformat(),
                "end_time": test_end.isoformat(),
                "duration_hours": test_duration / 3600,
                "return_code": process.returncode,
                "success": process.returncode == 0,
                "reboot_events": len(reboot_events),
                "reboot_details": reboot_events,
                "stability_score": self._calculate_stability_score(metrics, reboot_events),
                "metrics": metrics,
                "generated_files": generated_files
            }
            
            if result['success'] and len(reboot_events) == 0:
                logger.info(f"✅ Test {test_id} - STABLE for {test_duration/3600:.1f}h")
            elif len(reboot_events) > 0:
                logger.info(f"⚠️  Test {test_id} - {len(reboot_events)} reboots detected")
            else:
                logger.error(f"❌ Test {test_id} failed")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Test {test_id} failed with exception: {str(e)}")
            return {
                "test_id": test_id,
                "name": test['name'],
                "success": False,
                "error": str(e),
                "start_time": test_start.isoformat()
            }
    
    def _extract_interval(self, args: list) -> int:
        """Extract polling interval from args."""
        try:
            idx = args.index("--update-every")
            return int(args[idx + 1])
        except:
            return 60
    
    def _analyze_stability_output(self, output: str, reboot_events: list) -> dict:
        """Analyze output for stability metrics."""
        metrics = {}
        lines = output.split('\n')
        
        for line in lines:
            if "Success Rate:" in line:
                try:
                    rate = line.split("Success Rate:")[1].strip().replace('%', '')
                    metrics['final_success_rate'] = float(rate)
                except:
                    pass
            elif "Overlap Rate:" in line:
                try:
                    rate = line.split("Overlap Rate:")[1].strip().replace('%', '')
                    metrics['overlap_rate'] = float(rate)
                except:
                    pass
            elif "Average Cycle Time:" in line:
                try:
                    time_str = line.split("Average Cycle Time:")[1].strip().replace('s', '')
                    metrics['avg_cycle_time'] = float(time_str)
                except:
                    pass
        
        return metrics
    
    def _calculate_stability_score(self, metrics: dict, reboot_events: list) -> float:
        """Calculate stability score (0-100, higher = more stable)."""
        score = 100.0
        
        # Penalize reboots heavily
        score -= len(reboot_events) * 25
        
        # Factor in success rate
        success_rate = metrics.get('final_success_rate', 0)
        if success_rate < 90:
            score -= (90 - success_rate)
        
        # Factor in overlap rate
        overlap_rate = metrics.get('overlap_rate', 0)
        if overlap_rate > 10:
            score -= (overlap_rate - 10) * 2
        
        return max(0, score)
    
    async def run_all_tests(self):
        """Run the complete stability test suite."""
        total_tests = len(self.test_matrix)
        logger.info(f"🚀 Starting Modem Stability Test Suite with {total_tests} tests")
        logger.info(f"📁 Results directory: {self.results_dir}")
        logger.info(f"⏱️  Estimated duration: {total_tests * 2} hours")
        
        for i, test in enumerate(self.test_matrix, 1):
            if not self.is_running:
                logger.info("Test suite stopped by user")
                break
                
            logger.info(f"\n📊 Test {i}/{total_tests} - {test['name']}")
            
            try:
                result = await self.run_single_test(test)
                self.test_results.append(result)
            except Exception as e:
                logger.error(f"Test {i} failed: {str(e)}")
                self.test_results.append({
                    "test_id": f"test_{i}",
                    "name": test['name'],
                    "success": False,
                    "error": str(e)
                })
            
            # Save interim results
            self._save_summary()
            
            if i < total_tests and self.is_running:
                logger.info("⏸️  Brief pause before next test...")
                await asyncio.sleep(30)  # Brief pause between tests
        
        # Generate final report
        self._generate_stability_report()
    
    def _save_summary(self):
        """Save current results."""
        summary_file = self.results_dir / "stability_summary.json"
        summary = {
            "test_suite_info": {
                "start_time": self.start_time.isoformat(),
                "current_time": datetime.now().isoformat(),
                "results_directory": str(self.results_dir)
            },
            "test_results": self.test_results
        }
        
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
    
    def _generate_stability_report(self):
        """Generate stability analysis report."""
        logger.info("\n" + "="*80)
        logger.info("🎯 MODEM STABILITY ANALYSIS REPORT")
        logger.info("="*80)
        
        # Find stable configurations
        stable_tests = [r for r in self.test_results if r.get('success', False) and r.get('reboot_events', 999) == 0]
        unstable_tests = [r for r in self.test_results if r.get('reboot_events', 0) > 0]
        
        logger.info(f"\n📊 STABILITY SUMMARY:")
        logger.info(f"  Total Tests: {len(self.test_results)}")
        logger.info(f"  Stable Configs: {len(stable_tests)}")
        logger.info(f"  Unstable Configs: {len(unstable_tests)}")
        
        if stable_tests:
            # Find fastest stable config
            fastest_stable = min(stable_tests, key=lambda x: x.get('polling_interval', 999))
            logger.info(f"\n🏆 FASTEST STABLE CONFIG:")
            logger.info(f"  {fastest_stable['name']}: {fastest_stable.get('polling_interval')}s intervals")
            logger.info(f"  Stability Score: {fastest_stable.get('stability_score', 0):.1f}/100")
            
            # Recommend production settings
            logger.info(f"\n💡 PRODUCTION RECOMMENDATION:")
            logger.info(f"  Optimal Polling: {fastest_stable.get('polling_interval')}s")
            logger.info(f"  This provides good responsiveness without reboots")
        
        # Save CSV report
        csv_file = self.results_dir / "stability_results.csv"
        with open(csv_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Test Name', 'Polling Interval', 'Duration (h)', 'Success', 'Reboots', 'Stability Score'])
            for result in self.test_results:
                writer.writerow([
                    result.get('name', ''),
                    result.get('polling_interval', ''),
                    result.get('duration_hours', 0),
                    result.get('success', False),
                    result.get('reboot_events', 0),
                    result.get('stability_score', 0)
                ])

def signal_handler(test_suite):
    """Handle interrupt signals."""
    def handler(signum, frame):
        logger.info(f"Received signal {signum}, stopping test suite...")
        test_suite.is_running = False
        raise KeyboardInterrupt("Signal received")
    return handler

async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Modem Stability Test Suite')
    parser.add_argument('--simulator', default='netdata_simulator.py',
                       help='Path to the netdata_simulator.py script')
    parser.add_argument('--host', default='https://192.168.100.1',
                       help='Modem host URL')
    
    args = parser.parse_args()
    
    try:
        test_suite = ModemStabilityTestSuite(
            simulator_script=args.simulator,
            base_host=args.host
        )
        
        # Set up signal handler
        handler = signal_handler(test_suite)
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
        
        await test_suite.run_all_tests()
        
    except KeyboardInterrupt:
        logger.info("Test suite interrupted by user")

if __name__ == "__main__":
    asyncio.run(main())
