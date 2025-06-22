#!/usr/bin/env python
"""
Simple Overnight Modem Stability Test
Find the fastest polling rate ≤ 2 minutes that doesn't cause API failures.

Direct connection = API failure = modem problem. Keep it simple.
All tests use SERIAL collection only (parallel causes reboots).
"""

import asyncio
import subprocess
import time
import json
import csv
from datetime import datetime
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
        logging.FileHandler('simple_overnight_test.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class SimpleOvernightTest:
    """Simple test suite to find fastest stable polling rate ≤ 2 minutes."""
    
    def __init__(self, simulator_script: str = "netdata_simulator.py", base_host: str = "https://192.168.100.1"):
        self.simulator_script = Path(simulator_script).resolve()
        self.base_host = base_host
        self.start_time = datetime.now()
        self.test_results = []
        self.is_running = True
        
        if not self.simulator_script.exists():
            raise FileNotFoundError(f"Simulator script not found: {self.simulator_script}")
        
        # Create results directory
        self.results_dir = Path.cwd() / f"simple_overnight_{self.start_time.strftime('%Y%m%d_%H%M%S')}"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Simple Overnight Test initialized")
        logger.info(f"  Goal: Find fastest polling ≤ 2min without API failures")
        logger.info(f"  Logic: Direct connection = API failure = modem reboot")
        logger.info(f"  Mode: SERIAL only (parallel causes reboots)")
        
        # Clean focused test matrix - SERIAL ONLY, ALL 6 ENDPOINTS
        self.test_matrix = [
            # Test 1: Absolute theoretical minimum (6 endpoints × 2s = 12s + overhead = 15s)
            {
                "name": "extreme_15s",
                "interval": 15,
                "args": ["--update-every", "15", "--endpoint-timeout", "2s", "--max-retries", "0", 
                        "--collection-timeout", "14", "--inter-request-delay", "0", "--duration", "3600"],
                "expected": "Theoretical minimum - will probably cause reboots"
            },
            
            # Test 2: Slightly more breathing room
            {
                "name": "very_aggressive_20s",
                "interval": 20,
                "args": ["--update-every", "20", "--endpoint-timeout", "2s", "--max-retries", "0",
                        "--collection-timeout", "18", "--inter-request-delay", "0.5", "--duration", "7200"],
                "expected": "20s with small delays - testing limits"
            },
            
            # Test 3: Allow 1 retry per endpoint
            {
                "name": "aggressive_30s",
                "interval": 30,
                "args": ["--update-every", "30", "--endpoint-timeout", "3s", "--max-retries", "1", 
                        "--collection-timeout", "27", "--inter-request-delay", "0.5", "--duration", "7200"],
                "expected": "30s with 1 retry - more realistic"
            },
            
            # Test 4: Conservative with retries
            {
                "name": "fast_45s",
                "interval": 45,
                "args": ["--update-every", "45", "--endpoint-timeout", "4s", "--max-retries", "1",
                        "--collection-timeout", "40", "--inter-request-delay", "1.0", "--duration", "7200"],
                "expected": "45s with retries - should work"
            },
            
            # Test 5: 1-minute target (good responsive rate)
            {
                "name": "target_60s",
                "interval": 60,
                "args": ["--update-every", "60", "--endpoint-timeout", "5s", "--max-retries", "1", 
                        "--collection-timeout", "54", "--inter-request-delay", "1.0", "--duration", "7200"],
                "expected": "1-minute - likely very stable"
            },
            
            # Test 6: 90-second safe option
            {
                "name": "safe_90s",
                "interval": 90,
                "args": ["--update-every", "90", "--endpoint-timeout", "6s", "--max-retries", "1", 
                        "--collection-timeout", "81", "--inter-request-delay", "1.5", "--duration", "7200"],
                "expected": "90s - should definitely work"
            },
            
            # Test 7: 2-minute absolute max (fallback)
            {
                "name": "max_120s",
                "interval": 120,
                "args": ["--update-every", "120", "--endpoint-timeout", "8s", "--max-retries", "1", 
                        "--collection-timeout", "108", "--inter-request-delay", "2.0", "--duration", "7200"],
                "expected": "2-minute max - must work"
            }
        ]
        
        total_hours = 1 + (len(self.test_matrix) - 1) * 2  # First test 1hr, rest 2hrs each
        logger.info(f"  Tests: {len(self.test_matrix)} tests = {total_hours} hours total")
    
    async def run_simple_test(self, test: dict, test_num: int) -> dict:
        """Run a single simple test with basic failure detection."""
        test_start = datetime.now()
        
        logger.info(f"\n🧪 Test {test_num}/{len(self.test_matrix)}: {test['name']}")
        logger.info(f"   Interval: {test['interval']}s (ALL 6 endpoints, SERIAL)")
        logger.info(f"   Expected: {test['expected']}")
        
        duration_hours = 1 if test['interval'] == 15 else 2
        logger.info(f"   Duration: {duration_hours} hours")
        
        # Create test directory
        test_dir = self.results_dir / test['name']
        test_dir.mkdir(exist_ok=True)
        
        # Build command
        cmd = [sys.executable, str(self.simulator_script), "--host", self.base_host] + test['args']
        logger.info(f"   Command: {' '.join(cmd)}")
        
        # Run the test
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(test_dir),
                preexec_fn=None if sys.platform == "win32" else os.setsid
            )
            
            # Simple monitoring
            output_lines = []
            last_success = test_start
            failure_periods = []
            current_failure_start = None
            
            logger.info(f"   Started PID {process.pid}")
            
            while True:
                if not self.is_running:
                    logger.info(f"   Stopping {test['name']} due to user request")
                    break
                
                try:
                    line_bytes = await asyncio.wait_for(process.stdout.readline(), timeout=1.0)
                    if not line_bytes:
                        break
                        
                    line = line_bytes.decode('utf-8').rstrip()
                    if line:
                        output_lines.append(line)
                        now = datetime.now()
                        
                        # Pass through ALL simulator output directly to console
                        print(line)
                        
                        # Simple background tracking for analysis (no logging spam)
                        if "✅" in line and "Cycle" in line:
                            last_success = now
                            if current_failure_start:
                                failure_duration = (now - current_failure_start).total_seconds()
                                failure_periods.append(failure_duration)
                                current_failure_start = None
                                
                        elif "❌" in line and ("timeout" in line.lower() or "failed" in line.lower()):
                            if not current_failure_start:
                                current_failure_start = now
                
                except asyncio.TimeoutError:
                    if process.returncode is not None:
                        break
                    continue
            
            await process.wait()
            test_end = datetime.now()
            
            # Simple analysis
            total_duration = (test_end - test_start).total_seconds()
            major_failures = [f for f in failure_periods if f > 180]  # >3 minutes = likely reboot
            
            # If still failing at end, count that too
            if current_failure_start:
                final_failure = (test_end - current_failure_start).total_seconds()
                if final_failure > 180:
                    major_failures.append(final_failure)
            
            # Simple verdict
            if len(major_failures) == 0:
                verdict = "STABLE"
                recommendation = "Good for production"
            elif len(major_failures) == 1:
                verdict = "MARGINAL" 
                recommendation = "Caution - 1 major failure detected"
            else:
                verdict = "UNSTABLE"
                recommendation = "Too aggressive - multiple failures"
            
            result = {
                "name": test['name'],
                "interval": test['interval'],
                "duration_hours": total_duration / 3600,
                "success": process.returncode == 0,
                "verdict": verdict,
                "recommendation": recommendation,
                "major_failures": len(major_failures),
                "failure_details": major_failures,
                "total_failure_periods": len(failure_periods)
            }
            
            # Log result
            if verdict == "STABLE":
                logger.info(f"   ✅ {test['name']}: {verdict} - No major API failures!")
            elif verdict == "MARGINAL":
                logger.warning(f"   ⚠️  {test['name']}: {verdict} - {len(major_failures)} major failure(s)")
            else:
                logger.error(f"   ❌ {test['name']}: {verdict} - {len(major_failures)} major failures")
            
            return result
            
        except Exception as e:
            logger.error(f"   ❌ {test['name']} failed: {str(e)}")
            return {"name": test['name'], "interval": test['interval'], "error": str(e)}
        
        finally:
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
    
    async def run_all_tests(self):
        """Run all simple tests."""
        logger.info(f"\n🚀 Starting Simple Overnight Test Suite")
        logger.info(f"📊 Goal: Find fastest polling ≤ 2 minutes without API failures")
        logger.info(f"🔄 Mode: SERIAL collection only (all 6 endpoints)")
        
        for i, test in enumerate(self.test_matrix, 1):
            if not self.is_running:
                break
                
            result = await self.run_simple_test(test, i)
            self.test_results.append(result)
            
            # Save progress
            self._save_results()
            
            # Short break between tests (let modem settle)
            if i < len(self.test_matrix) and self.is_running:
                logger.info(f"   ⏸️  5-minute break before next test...")
                await asyncio.sleep(300)
        
        self._generate_final_report()
    
    def _save_results(self):
        """Save current results."""
        results_file = self.results_dir / "simple_results.json"
        with open(results_file, 'w') as f:
            json.dump({
                "start_time": self.start_time.isoformat(),
                "current_time": datetime.now().isoformat(),
                "results": self.test_results
            }, f, indent=2)
    
    def _generate_final_report(self):
        """Generate simple final report."""
        logger.info("\n" + "="*60)
        logger.info("🎯 SIMPLE OVERNIGHT TEST RESULTS")
        logger.info("="*60)
        
        stable_tests = [r for r in self.test_results if r.get('verdict') == 'STABLE']
        
        logger.info(f"\n📊 SUMMARY:")
        logger.info(f"  Total Tests: {len(self.test_results)}")
        logger.info(f"  Stable: {len(stable_tests)}")
        logger.info(f"  Unstable: {len([r for r in self.test_results if r.get('verdict') == 'UNSTABLE'])}")
        
        if stable_tests:
            # Find fastest stable
            fastest = min(stable_tests, key=lambda x: x['interval'])
            logger.info(f"\n🏆 FASTEST STABLE POLLING:")
            logger.info(f"  ✅ {fastest['interval']} seconds ({fastest['interval']/60:.1f} minutes)")
            logger.info(f"  📊 Perfect for Netdata 5-minute graphs!")
            logger.info(f"  🛡️  No major API failures in {fastest['duration_hours']:.1f}h test")
            
            logger.info(f"\n💡 PRODUCTION RECOMMENDATION:")
            logger.info(f"  Use update_every: {fastest['interval']} in hitron_coda.conf")
            logger.info(f"  Keep parallel_collection: false (serial mode)")
            logger.info(f"  All 6 endpoints will be monitored")
        else:
            logger.warning(f"\n⚠️  NO STABLE CONFIGURATIONS FOUND!")
            logger.warning(f"  Try longer intervals (3+ minutes)")
        
        # Save CSV
        csv_file = self.results_dir / "simple_results.csv"
        with open(csv_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Test', 'Interval (s)', 'Verdict', 'Major Failures', 'Recommendation'])
            for r in self.test_results:
                writer.writerow([
                    r.get('name', ''),
                    r.get('interval', ''),
                    r.get('verdict', ''),
                    r.get('major_failures', ''),
                    r.get('recommendation', '')
                ])
        
        logger.info(f"\n📁 Results saved to: {self.results_dir}")

def signal_handler(test_suite):
    def handler(signum, frame):
        logger.info(f"Received signal {signum}, stopping...")
        test_suite.is_running = False
        raise KeyboardInterrupt()
    return handler

async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Simple Overnight Modem Stability Test')
    parser.add_argument('--simulator', default='netdata_simulator.py')
    parser.add_argument('--host', default='https://192.168.100.1')
    parser.add_argument('--quick', action='store_true', 
                       help='30-minute tests instead of 1-2 hours')
    
    args = parser.parse_args()
    
    test_suite = SimpleOvernightTest(args.simulator, args.host)
    
    # Quick mode
    if args.quick:
        logger.info("Quick mode: 30-minute tests")
        for test in test_suite.test_matrix:
            for i, arg in enumerate(test['args']):
                if arg == '--duration':
                    test['args'][i + 1] = '1800'  # 30 minutes
    
    # Set up signal handler  
    handler = signal_handler(test_suite)
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    
    try:
        await test_suite.run_all_tests()
    except KeyboardInterrupt:
        logger.info("Test interrupted")

if __name__ == "__main__":
    asyncio.run(main())
