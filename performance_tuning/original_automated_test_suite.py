#!/usr/bin/env python
"""
Automated Test Suite for Netdata Hitron CODA Plugin Optimization
Runs comprehensive testing to find optimal configuration parameters.

FIXED VERSION - Resolves directory creation and path handling issues.
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
import shutil
from typing import Dict, List, Tuple

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('test_suite.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class NetdataTestSuite:
    """Automated test suite for finding optimal Netdata plugin configuration."""
    
    def __init__(self, simulator_script: str = "netdata_simulator.py", base_host: str = "https://192.168.100.1"):
        self.simulator_script = Path(simulator_script).resolve()
        self.base_host = base_host
        self.start_time = datetime.now()
        self.test_results = []
        self.is_running = True
        
        # Verify simulator script exists
        if not self.simulator_script.exists():
            raise FileNotFoundError(f"Simulator script not found: {self.simulator_script}")
        
        # Create results directory with absolute path
        self.results_dir = Path.cwd() / f"netdata_test_results_{self.start_time.strftime('%Y%m%d_%H%M%S')}"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Test suite initialized")
        logger.info(f"  Simulator script: {self.simulator_script}")
        logger.info(f"  Results directory: {self.results_dir}")
        logger.info(f"  Base host: {self.base_host}")
        
        # Define test matrix
        self.test_matrix = self._define_test_matrix()
        
    def _define_test_matrix(self) -> List[Dict]:
        """Define the comprehensive test matrix."""
        tests = [
            # Phase 1: Find the Breaking Point
            {
                "phase": "1_breaking_point",
                "name": "confirm_problem", 
                "description": "Confirm 5s interval causes problems",
                "args": ["--update-every", "5", "--timeout", "5", "--duration", "3600"],
                "expected": "High overlap rate, multiple concurrent cycles"
            },
            {
                "phase": "1_breaking_point",
                "name": "threshold_10s",
                "description": "Test 10s threshold",
                "args": ["--update-every", "10", "--timeout", "7", "--duration", "3600"],
                "expected": "Some overlaps possible"
            },
            {
                "phase": "1_breaking_point", 
                "name": "threshold_12s",
                "description": "Test 12s threshold",
                "args": ["--update-every", "12", "--timeout", "8", "--duration", "3600"],
                "expected": "Reduced overlaps"
            },
            {
                "phase": "1_breaking_point",
                "name": "safe_zone_20s",
                "description": "Confirm 20s works (current settings)",
                "args": ["--update-every", "20", "--timeout", "14", "--duration", "3600"],
                "expected": "No overlaps, high success rate"
            },
            
            # Phase 2: Test Parallel Optimization
            {
                "phase": "2_parallel_optimization",
                "name": "parallel_fix_5s",
                "description": "Test if parallel fixes 5s problem", 
                "args": ["--update-every", "5", "--timeout", "5", "--parallel", "--duration", "3600"],
                "expected": "Much better than serial 5s"
            },
            {
                "phase": "2_parallel_optimization",
                "name": "parallel_10s",
                "description": "Parallel at 10s interval",
                "args": ["--update-every", "10", "--parallel", "--duration", "3600"],
                "expected": "Should work very well"
            },
            {
                "phase": "2_parallel_optimization",
                "name": "parallel_15s",
                "description": "Parallel at 15s interval",
                "args": ["--update-every", "15", "--parallel", "--duration", "3600"],
                "expected": "Conservative parallel setting"
            },
            
            # Phase 3: Test Minimal Endpoints
            {
                "phase": "3_minimal_endpoints",
                "name": "minimal_serial_5s",
                "description": "Just critical endpoints (serial)",
                "args": ["--update-every", "5", "--minimal", "--duration", "3600"],
                "expected": "Faster than full endpoint set"
            },
            {
                "phase": "3_minimal_endpoints", 
                "name": "minimal_parallel_5s",
                "description": "Minimal endpoints + parallel",
                "args": ["--update-every", "5", "--minimal", "--parallel", "--duration", "3600"],
                "expected": "Best of both worlds"
            },
            {
                "phase": "3_minimal_endpoints",
                "name": "core_parallel_5s", 
                "description": "Core 3 endpoints + parallel",
                "args": ["--update-every", "5", "--parallel", "--endpoints", "dsinfo.asp", "usinfo.asp", "dsofdminfo.asp", "--duration", "3600"],
                "expected": "Good balance of data and speed"
            },
            
            # Phase 4: Stress Testing
            {
                "phase": "4_stress_testing",
                "name": "aggressive_3s",
                "description": "Push limits with 3s polling",
                "args": ["--update-every", "3", "--parallel", "--minimal", "--duration", "7200"],
                "expected": "Find absolute limits"
            },
            {
                "phase": "4_stress_testing",
                "name": "extended_reliability",
                "description": "Extended test of good settings",
                "args": ["--update-every", "10", "--parallel", "--endpoints", "dsinfo.asp", "usinfo.asp", "dsofdminfo.asp", "--duration", "14400"],
                "expected": "Long-term reliability proof"
            },
            
            # Phase 5: Real-World Scenarios
            {
                "phase": "5_real_world",
                "name": "network_stress_sim",
                "description": "Simulate network stress",
                "args": ["--update-every", "10", "--timeout", "3", "--max-retries", "2", "--parallel", "--duration", "10800"],
                "expected": "Performance under stress"
            },
            {
                "phase": "5_real_world",
                "name": "conservative_production",
                "description": "Safe production settings",
                "args": ["--update-every", "15", "--timeout", "10", "--parallel", "--duration", "10800"],
                "expected": "Production-ready reliability"
            },
            {
                "phase": "5_real_world", 
                "name": "ultra_fast_monitoring",
                "description": "Ultra-responsive monitoring",
                "args": ["--update-every", "5", "--timeout", "3", "--parallel", "--minimal", "--duration", "10800"],
                "expected": "Maximum responsiveness"
            }
        ]
        
        return tests
    
    async def run_single_test(self, test: Dict) -> Dict:
        """Run a single test and collect results."""
        test_id = f"{test['phase']}_{test['name']}"
        test_start = datetime.now()
        
        logger.info(f"🧪 Starting test: {test_id}")
        logger.info(f"   Description: {test['description']}")
        logger.info(f"   Expected: {test['expected']}")
        
        # Create test output directory
        test_output_dir = self.results_dir / test_id
        test_output_dir.mkdir(parents=True, exist_ok=True)
        
        # Prepare command with absolute paths
        cmd = [
            sys.executable,  # Use current Python interpreter
            str(self.simulator_script),
            "--host", self.base_host
        ] + test['args']
        
        logger.info(f"   Command: {' '.join(cmd)}")
        logger.info(f"   Working directory: {test_output_dir}")
        
        process = None
        try:
            # Run the test with proper working directory and real-time output
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # Merge stderr into stdout
                cwd=str(test_output_dir),
                preexec_fn=None if sys.platform == "win32" else os.setsid  # Create new process group on Unix
            )
            
            logger.info(f"   Process PID: {process.pid}")
            
            # Read output in real-time while monitoring for cancellation
            output_lines = []
            
            try:
                while True:
                    # Check if we should stop
                    if not self.is_running:
                        logger.info(f"Stopping test {test_id} due to user request...")
                        break
                    
                    # Read a line with timeout
                    try:
                        line_bytes = await asyncio.wait_for(
                            process.stdout.readline(), 
                            timeout=1.0
                        )
                        
                        if not line_bytes:  # EOF
                            break
                            
                        line = line_bytes.decode('utf-8').rstrip()
                        if line:
                            output_lines.append(line)
                            # Show important lines in real-time
                            if any(keyword in line for keyword in ['INFO', 'ERROR', 'Cycle', 'REPORT', 'SUCCESS', 'FAILED']):
                                logger.info(f"   [{test_id}] {line}")
                                
                    except asyncio.TimeoutError:
                        # Check if process is still running
                        if process.returncode is not None:
                            break
                        continue
                
                # Wait for process to complete
                await process.wait()
                
            except asyncio.CancelledError:
                logger.info(f"Test {test_id} cancelled - terminating subprocess (PID: {process.pid})...")
                if process and process.returncode is None:
                    try:
                        # Try graceful termination first
                        if hasattr(os, 'killpg'):
                            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                        else:
                            process.terminate()
                        
                        await asyncio.wait_for(process.wait(), timeout=5.0)
                        logger.info(f"Process {process.pid} terminated gracefully")
                    except asyncio.TimeoutError:
                        # Force kill if graceful termination doesn't work
                        logger.warning(f"Force killing subprocess {process.pid}")
                        if hasattr(os, 'killpg'):
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        else:
                            process.kill()
                        await process.wait()
                        logger.info(f"Process {process.pid} force killed")
                    except ProcessLookupError:
                        logger.info(f"Process {process.pid} already terminated")
                raise
            
            test_end = datetime.now()
            test_duration = (test_end - test_start).total_seconds()
            
            # Combine all output
            output_text = '\n'.join(output_lines)
            
            # Parse key metrics from output
            metrics = self._parse_test_output(output_text)
            
            # Collect generated files (with error handling)
            generated_files = []
            try:
                generated_files = [f.name for f in test_output_dir.iterdir() if f.is_file()]
            except Exception as e:
                logger.warning(f"Could not list generated files: {str(e)}")
            
            result = {
                "test_id": test_id,
                "phase": test['phase'],
                "name": test['name'],
                "description": test['description'],
                "expected": test['expected'],
                "command": ' '.join(cmd),
                "start_time": test_start.isoformat(),
                "end_time": test_end.isoformat(),
                "duration_seconds": test_duration,
                "return_code": process.returncode,
                "success": process.returncode == 0,
                "output_dir": str(test_output_dir),
                "generated_files": generated_files,
                "metrics": metrics,
                "stdout": output_text,
                "stderr": ""  # We merged stderr into stdout
            }
            
            if result['success']:
                logger.info(f"✅ Test {test_id} completed successfully in {test_duration:.1f}s")
                if metrics:
                    logger.info(f"   Key metrics: Overlap Rate: {metrics.get('overlap_rate', 'N/A')}, "
                              f"Success Rate: {metrics.get('success_rate', 'N/A')}, "
                              f"Avg Cycle Time: {metrics.get('avg_cycle_time', 'N/A')}")
            else:
                logger.error(f"❌ Test {test_id} failed with return code {process.returncode}")
                if output_text:
                    error_preview = output_text[-500:]  # Last 500 chars
                    logger.error(f"   Output preview: ...{error_preview}")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Test {test_id} failed with exception: {str(e)}")
            return {
                "test_id": test_id,
                "phase": test['phase'],
                "name": test['name'], 
                "description": test['description'],
                "success": False,
                "error": str(e),
                "start_time": test_start.isoformat(),
                "end_time": datetime.now().isoformat(),
                "command": ' '.join(cmd),
                "output_dir": str(test_output_dir)
            }
    
    def _parse_test_output(self, output: str) -> Dict:
        """Parse key metrics from test output."""
        metrics = {}
        
        try:
            lines = output.split('\n')
            
            for line in lines:
                line = line.strip()
                
                if "Overlap Rate:" in line:
                    try:
                        # Extract: "Overlap Rate: 75.2%"
                        rate = line.split("Overlap Rate:")[1].strip().replace('%', '')
                        metrics['overlap_rate'] = f"{float(rate):.1f}%"
                    except (ValueError, IndexError):
                        pass
                        
                elif "Average Success Rate:" in line:
                    try:
                        # Extract: "Average Success Rate: 98.5%"
                        rate = line.split("Average Success Rate:")[1].strip().replace('%', '')
                        metrics['success_rate'] = f"{float(rate):.1f}%"
                    except (ValueError, IndexError):
                        pass
                        
                elif "Average Cycle Time:" in line:
                    try:
                        # Extract: "Average Cycle Time: 18.3s"
                        time_str = line.split("Average Cycle Time:")[1].strip().replace('s', '')
                        metrics['avg_cycle_time'] = f"{float(time_str):.1f}s"
                    except (ValueError, IndexError):
                        pass
                        
                elif "Max Concurrent Cycles:" in line:
                    try:
                        count = line.split("Max Concurrent Cycles:")[1].strip()
                        metrics['max_concurrent'] = int(count)
                    except (ValueError, IndexError):
                        pass
                        
                elif "Completed Cycles:" in line:
                    try:
                        count = line.split("Completed Cycles:")[1].strip()
                        metrics['completed_cycles'] = int(count)
                    except (ValueError, IndexError):
                        pass
                        
                elif "successful responses:" in line:
                    try:
                        # Extract from "successful responses: 123"
                        count = line.split("successful responses:")[1].strip()
                        metrics['successful_responses'] = int(count)
                    except (ValueError, IndexError):
                        pass
        
        except Exception as e:
            logger.debug(f"Error parsing metrics: {str(e)}")
        
        return metrics
    
    async def run_all_tests(self):
        """Run the complete test suite."""
        total_tests = len(self.test_matrix)
        logger.info(f"🚀 Starting automated test suite with {total_tests} tests")
        logger.info(f"📁 Results directory: {self.results_dir}")
        
        # Track running tasks so we can cancel them
        current_task = None
        
        try:
            for i, test in enumerate(self.test_matrix, 1):
                if not self.is_running:
                    logger.info("Test suite stopped by user")
                    break
                    
                logger.info(f"\n📊 Test {i}/{total_tests} - Phase: {test['phase']}")
                
                # Create and track the test task
                current_task = asyncio.create_task(self.run_single_test(test))
                
                try:
                    result = await current_task
                    self.test_results.append(result)
                except asyncio.CancelledError:
                    logger.info(f"Test {i} was cancelled")
                    break
                except KeyboardInterrupt:
                    logger.info(f"Test {i} was interrupted")
                    break
                except Exception as e:
                    logger.error(f"Test {i} failed with exception: {str(e)}")
                    # Add a failed result
                    self.test_results.append({
                        "test_id": f"{test['phase']}_{test['name']}",
                        "phase": test['phase'],
                        "name": test['name'],
                        "success": False,
                        "error": str(e),
                        "start_time": datetime.now().isoformat()
                    })
                finally:
                    current_task = None
                
                # Save interim results after each test
                try:
                    self._save_summary()
                    logger.debug(f"Saved interim results after test {i}")
                except Exception as e:
                    logger.warning(f"Failed to save interim results: {str(e)}")
                
                # Brief pause between tests to be nice to the modem
                if i < total_tests and self.is_running:
                    logger.info("⏸️  Brief pause before next test...")
                    try:
                        for countdown in range(10, 0, -1):
                            if not self.is_running:
                                break
                            logger.info(f"   Starting next test in {countdown}s...")
                            await asyncio.sleep(1)
                    except (asyncio.CancelledError, KeyboardInterrupt):
                        logger.info("Pause interrupted")
                        break
            
            # Generate final report
            if self.test_results:
                self._generate_final_report()
            else:
                logger.warning("No test results to generate report from")
            
        except KeyboardInterrupt:
            logger.info("Test suite interrupted by user")
        except asyncio.CancelledError:
            logger.info("Test suite cancelled")
        except Exception as e:
            logger.error(f"Test suite failed: {str(e)}")
            raise
        finally:
            # Cancel any remaining running task
            if current_task and not current_task.done():
                logger.info("Cancelling current test...")
                current_task.cancel()
                try:
                    await current_task
                except (asyncio.CancelledError, KeyboardInterrupt):
                    pass
            
            try:
                self._save_summary()
            except Exception as e:
                logger.error(f"Failed to save final summary: {str(e)}")
    
    def _save_summary(self):
        """Save current test results summary."""
        try:
            summary_file = self.results_dir / "test_summary.json"
            
            summary = {
                "test_suite_info": {
                    "start_time": self.start_time.isoformat(),
                    "current_time": datetime.now().isoformat(),
                    "simulator_script": str(self.simulator_script),
                    "base_host": self.base_host,
                    "results_directory": str(self.results_dir)
                },
                "progress": {
                    "completed_tests": len(self.test_results),
                    "total_tests": len(self.test_matrix),
                    "completion_percentage": (len(self.test_results) / len(self.test_matrix)) * 100 if self.test_matrix else 0
                },
                "test_results": self.test_results
            }
            
            # Write to temporary file first, then move (atomic operation)
            temp_file = summary_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(summary, f, indent=2)
            
            temp_file.replace(summary_file)
            
        except Exception as e:
            logger.error(f"Error saving summary: {str(e)}")
            raise
    
    def _generate_final_report(self):
        """Generate comprehensive final report."""
        logger.info("\n" + "="*80)
        logger.info("🎯 GENERATING FINAL ANALYSIS REPORT")
        logger.info("="*80)
        
        try:
            # Generate CSV summary
            csv_file = self.results_dir / "test_results_summary.csv"
            
            with open(csv_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'Test ID', 'Phase', 'Description', 'Success', 'Duration (s)',
                    'Overlap Rate', 'Success Rate', 'Avg Cycle Time', 'Max Concurrent',
                    'Completed Cycles', 'Generated Files', 'Output Directory'
                ])
                
                for result in self.test_results:
                    metrics = result.get('metrics', {})
                    writer.writerow([
                        result['test_id'],
                        result['phase'], 
                        result['description'],
                        result['success'],
                        result.get('duration_seconds', 0),
                        metrics.get('overlap_rate', ''),
                        metrics.get('success_rate', ''),
                        metrics.get('avg_cycle_time', ''),
                        metrics.get('max_concurrent', ''),
                        metrics.get('completed_cycles', ''),
                        len(result.get('generated_files', [])),
                        result.get('output_dir', '')
                    ])
            
            # Generate recommendations
            recommendations = self._analyze_results()
            
            # Save recommendations
            rec_file = self.results_dir / "recommendations.md"
            with open(rec_file, 'w') as f:
                f.write("# Netdata Hitron Plugin Optimization Recommendations\n\n")
                f.write(f"Generated: {datetime.now()}\n\n")
                f.write("## Test Suite Results Summary\n\n")
                
                successful_tests = [r for r in self.test_results if r['success']]
                f.write(f"- **Total Tests**: {len(self.test_results)}\n")
                f.write(f"- **Successful Tests**: {len(successful_tests)}\n")
                f.write(f"- **Success Rate**: {(len(successful_tests)/len(self.test_results)*100):.1f}%\n")
                f.write(f"- **Test Duration**: {datetime.now() - self.start_time}\n\n")
                
                f.write("## Detailed Test Results\n\n")
                for result in self.test_results:
                    status = "✅ PASS" if result['success'] else "❌ FAIL"
                    f.write(f"### {result['test_id']} - {status}\n")
                    f.write(f"**Description**: {result['description']}\n\n")
                    f.write(f"**Expected**: {result['expected']}\n\n")
                    
                    if result['success'] and result.get('metrics'):
                        f.write("**Results**:\n")
                        for key, value in result['metrics'].items():
                            f.write(f"- {key.replace('_', ' ').title()}: {value}\n")
                    elif not result['success']:
                        f.write(f"**Error**: {result.get('error', 'Unknown error')}\n")
                    
                    f.write("\n")
                
                f.write("## Key Findings\n\n")
                for rec in recommendations:
                    f.write(f"### {rec['title']}\n")
                    f.write(f"{rec['description']}\n\n")
                    if 'configuration' in rec:
                        f.write("**Recommended Configuration:**\n```yaml\n")
                        for key, value in rec['configuration'].items():
                            f.write(f"{key}: {value}\n")
                        f.write("```\n\n")
            
            logger.info(f"📊 Final report saved to: {self.results_dir}")
            logger.info(f"📋 CSV summary: {csv_file}")
            logger.info(f"📝 Recommendations: {rec_file}")
            
            # Print top recommendations
            logger.info("\n🎯 TOP RECOMMENDATIONS:")
            for i, rec in enumerate(recommendations[:3], 1):
                logger.info(f"{i}. {rec['title']}")
                logger.info(f"   {rec['description']}")
                
        except Exception as e:
            logger.error(f"Error generating final report: {str(e)}")
    
    def _analyze_results(self) -> List[Dict]:
        """Analyze test results and generate recommendations."""
        recommendations = []
        
        successful_tests = [r for r in self.test_results if r['success'] and r.get('metrics')]
        failed_tests = [r for r in self.test_results if not r['success']]
        
        if not self.test_results:
            return [{"title": "No test results", "description": "No tests were completed"}]
        
        # Overall success analysis
        success_rate = len(successful_tests) / len(self.test_results) * 100
        recommendations.append({
            "title": f"Overall Test Success Rate: {success_rate:.1f}%",
            "description": f"Completed {len(successful_tests)} out of {len(self.test_results)} tests successfully. "
                         f"{'Excellent' if success_rate >= 90 else 'Good' if success_rate >= 70 else 'Poor'} overall results."
        })
        
        if not successful_tests:
            recommendations.append({
                "title": "No Successful Tests",
                "description": "All tests failed. Check network connectivity to modem and simulator script functionality."
            })
            return recommendations
        
        # Find best performing configurations
        fast_tests = []
        reliable_tests = []
        
        for test in successful_tests:
            metrics = test['metrics']
            
            # Parse overlap rate
            overlap_rate = 100  # Default to high if parsing fails
            if 'overlap_rate' in metrics:
                try:
                    overlap_rate = float(metrics['overlap_rate'].replace('%', ''))
                except:
                    pass
            
            # Parse success rate
            success_rate = 0  # Default to low if parsing fails
            if 'success_rate' in metrics:
                try:
                    success_rate = float(metrics['success_rate'].replace('%', ''))
                except:
                    pass
            
            # Identify reliable tests (low overlap, high success)
            if overlap_rate <= 10 and success_rate >= 95:
                reliable_tests.append(test)
            
            # Identify fast tests
            if 'avg_cycle_time' in metrics:
                try:
                    cycle_time = float(metrics['avg_cycle_time'].replace('s', ''))
                    if cycle_time <= 10:
                        fast_tests.append((test, cycle_time))
                except:
                    pass
        
        # Generate specific recommendations
        if reliable_tests:
            best_reliable = min(reliable_tests, 
                              key=lambda x: float(x['metrics'].get('avg_cycle_time', '999').replace('s', '')))
            recommendations.append({
                "title": "Most Reliable Configuration",
                "description": f"Test '{best_reliable['name']}' showed excellent reliability with "
                             f"{best_reliable['metrics'].get('overlap_rate', 'N/A')} overlap rate and "
                             f"{best_reliable['metrics'].get('success_rate', 'N/A')} success rate.",
                "test_id": best_reliable['test_id'],
                "configuration": self._extract_config_from_test(best_reliable)
            })
        
        if fast_tests:
            fastest = min(fast_tests, key=lambda x: x[1])
            recommendations.append({
                "title": "Fastest Configuration", 
                "description": f"Test '{fastest[0]['name']}' achieved the fastest cycle time of "
                             f"{fastest[0]['metrics'].get('avg_cycle_time', 'N/A')}.",
                "test_id": fastest[0]['test_id'],
                "configuration": self._extract_config_from_test(fastest[0])
            })
        
        # Analyze parallel vs serial
        parallel_tests = [t for t in successful_tests if '--parallel' in t.get('command', '')]
        serial_tests = [t for t in successful_tests if '--parallel' not in t.get('command', '')]
        
        if parallel_tests and serial_tests:
            recommendations.append({
                "title": "Parallel vs Serial Analysis",
                "description": f"Found {len(parallel_tests)} successful parallel tests and {len(serial_tests)} successful serial tests. "
                             f"Compare their performance to determine optimal collection method."
            })
        
        return recommendations
    
    def _extract_config_from_test(self, test: Dict) -> Dict:
        """Extract configuration parameters from test command."""
        config = {}
        cmd = test.get('command', '').split()
        
        try:
            for i, arg in enumerate(cmd):
                if arg == '--update-every' and i + 1 < len(cmd):
                    config['update_every'] = f"{cmd[i+1]}s"
                elif arg == '--timeout' and i + 1 < len(cmd):
                    config['timeout'] = f"{cmd[i+1]}s"
                elif arg == '--parallel':
                    config['collection_method'] = 'parallel'
                elif arg == '--minimal':
                    config['endpoints'] = 'minimal (dsinfo.asp, usinfo.asp)'
                elif arg == '--endpoints':
                    # Collect endpoint list
                    endpoints = []
                    j = i + 1
                    while j < len(cmd) and not cmd[j].startswith('--'):
                        endpoints.append(cmd[j])
                        j += 1
                    config['endpoints'] = ', '.join(endpoints)
        except Exception as e:
            logger.debug(f"Error extracting config: {str(e)}")
        
        if 'collection_method' not in config:
            config['collection_method'] = 'serial'
            
        return config

def signal_handler(test_suite):
    """Handle interrupt signals gracefully."""
    def handler(signum, frame):
        logger.info(f"Received signal {signum}, stopping test suite gracefully...")
        test_suite.is_running = False
        # Raise KeyboardInterrupt to properly cancel async operations
        raise KeyboardInterrupt("Signal received")
    return handler

async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Automated Netdata Plugin Test Suite')
    parser.add_argument('--simulator', default='netdata_simulator.py',
                       help='Path to the netdata_simulator.py script')
    parser.add_argument('--host', default='https://192.168.100.1',
                       help='Modem host URL')
    parser.add_argument('--quick', action='store_true',
                       help='Run shorter tests (reduce duration by 50%)')
    parser.add_argument('--test-phases', nargs='+', 
                       choices=['1_breaking_point', '2_parallel_optimization', '3_minimal_endpoints', '4_stress_testing', '5_real_world'],
                       help='Run only specific test phases')
    
    args = parser.parse_args()
    
    # Verify simulator script exists
    simulator_path = Path(args.simulator)
    if not simulator_path.exists():
        logger.error(f"Simulator script not found: {args.simulator}")
        logger.error("Please ensure netdata_simulator.py is in the current directory")
        logger.error(f"Current directory: {Path.cwd()}")
        logger.error(f"Files in current directory: {list(Path.cwd().glob('*.py'))}")
        sys.exit(1)
    
    try:
        test_suite = NetdataTestSuite(
            simulator_script=args.simulator,
            base_host=args.host
        )
        
        # Filter tests by phase if requested
        if args.test_phases:
            original_count = len(test_suite.test_matrix)
            test_suite.test_matrix = [t for t in test_suite.test_matrix if t['phase'] in args.test_phases]
            logger.info(f"Filtered to {len(test_suite.test_matrix)} tests from {original_count} (phases: {', '.join(args.test_phases)})")
        
        # If quick mode, reduce test durations
        if args.quick:
            logger.info("Quick mode enabled - reducing test durations by 50%")
            for test in test_suite.test_matrix:
                args_list = test['args']
                for i, arg in enumerate(args_list):
                    if arg == '--duration' and i + 1 < len(args_list):
                        current_duration = int(args_list[i + 1])
                        args_list[i + 1] = str(max(60, current_duration // 2))  # Minimum 60s
        
        # Set up signal handler
        handler = signal_handler(test_suite)
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
        
        try:
            await test_suite.run_all_tests()
        except KeyboardInterrupt:
            logger.info("Test suite interrupted by keyboard")
        except asyncio.CancelledError:
            logger.info("Test suite cancelled")
        
    except KeyboardInterrupt:
        logger.info("Test suite interrupted during initialization")
    except Exception as e:
        logger.error(f"Test suite failed: {str(e)}")
        raise
    
    logger.info(f"\n🎉 Test suite completed!")

if __name__ == "__main__":
    asyncio.run(main())
