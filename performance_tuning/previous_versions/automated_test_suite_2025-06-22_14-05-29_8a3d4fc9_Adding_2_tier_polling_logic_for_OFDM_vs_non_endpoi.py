#!/usr/bin/env python3
"""
Automated Test Suite for Netdata Hitron CODA Plugin Tiered Polling Optimization.

This script runs a multi-phase test suite to analyze endpoint performance and
determine the fastest stable polling configuration for the hitron_coda plugin's
tiered polling feature.

Version: 2.0.0
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
import pandas as pd
from typing import Dict, List, Any

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class TieredPollingTestSuite:
    """
    Runs a multi-phase test suite to find the optimal tiered polling configuration.
    """

    def __init__(self, simulator_script: str, host: str, quick_mode: bool = False):
        self.simulator_script = Path(simulator_script).resolve()
        if not self.simulator_script.exists():
            raise FileNotFoundError(f"Simulator script not found: {self.simulator_script}")

        self.host = host
        self.is_running = True
        self.test_results: List[Dict[str, Any]] = []
        
        # --- Test Matrix Definition ---
        # Defines the three phases of testing.
        self.test_matrix = self.build_test_matrix(quick_mode)

        # --- Results Directory ---
        self.start_time = datetime.now()
        self.results_dir = Path.cwd() / f"tiered_test_{self.start_time.strftime('%Y%m%d_%H%M%S')}"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        
        # Add a file handler for logging to the results directory
        log_file_handler = logging.FileHandler(self.results_dir / 'test_suite.log')
        log_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        logger.addHandler(log_file_handler)

        logger.info(f"Results will be saved in: {self.results_dir}")


    def build_test_matrix(self, quick_mode: bool) -> List[Dict[str, Any]]:
        """Builds the multi-phase test plan."""
        # Durations in seconds. Quick mode is shorter for rapid testing.
        duration_long = "1800" if quick_mode else "3600"  # 30 min / 1 hr
        duration_short = "900" if quick_mode else "1800" # 15 min / 30 min

        test_matrix = [
            # --- Phase 1: Baseline Individual Endpoint Performance ---
            # How do the fast and slow endpoints perform in isolation?
            {
                "phase": 1,
                "name": "Baseline Fast Endpoints",
                "args": [
                    '--duration', duration_short, '--update-every', '10', 
                    '--ofdm-poll-multiple', '9999', # Effectively disable OFDM
                    '--serial'
                ],
                "description": "Tests only the 4 fast endpoints at a high frequency."
            },
            {
                "phase": 1,
                "name": "Baseline Slow Endpoints",
                "args": [
                    '--duration', duration_short, '--update-every', '45',
                    '--ofdm-poll-multiple', '1', # Poll ONLY OFDM
                    '--serial'
                ],
                "description": "Tests only the 2 slow endpoints at a safe interval."
            },

            # --- Phase 2: Validate Tiered Polling Concept ---
            # Can we poll both tiers together stably at a conservative rate?
            {
                "phase": 2,
                "name": "Conservative Tiered Polling",
                "args": [
                    '--duration', duration_long, '--update-every', '30',
                    '--ofdm-poll-multiple', '5', # Poll OFDM every 2.5 mins
                    '--serial'
                ],
                "description": "A safe, stable configuration to prove the concept."
            },

            # --- Phase 3: Aggressive Optimization ---
            # Find the fastest stable polling rates.
            {
                "phase": 3,
                "name": "Aggressive - 20s Fast / 5x OFDM",
                "args": [
                    '--duration', duration_long, '--update-every', '20',
                    '--ofdm-poll-multiple', '5', # OFDM every 100s
                    '--serial'
                ],
                "description": "Fast polling for primary data, moderate for OFDM."
            },
            {
                "phase": 3,
                "name": "Aggressive - 15s Fast / 8x OFDM",
                "args": [
                    '--duration', duration_long, '--update-every', '15',
                    '--ofdm-poll-multiple', '8', # OFDM every 120s
                    '--serial'
                ],
                "description": "Very fast polling. Pushing the limits."
            },
             {
                "phase": 3,
                "name": "Hyper - 10s Fast / 12x OFDM",
                "args": [
                    '--duration', duration_long, '--update-every', '10',
                    '--ofdm-poll-multiple', '12', # OFDM every 120s
                    '--serial'
                ],
                "description": "Bleeding edge. High chance of instability."
            },
        ]
        return test_matrix

    async def run_all_tests(self):
        """Executes all tests defined in the test matrix."""
        logger.info(f"Starting test suite with {len(self.test_matrix)} tests...")
        for i, test in enumerate(self.test_matrix):
            if not self.is_running:
                logger.warning("Test suite stopped prematurely.")
                break
            
            logger.info(f"\n" + "="*80)
            logger.info(f"Running Test {i+1}/{len(self.test_matrix)}: [Phase {test['phase']}] {test['name']}")
            logger.info(f"Description: {test['description']}")
            logger.info(f"Arguments: {' '.join(test['args'])}")
            logger.info("="*80)
            
            await asyncio.sleep(2) # Short pause before starting

            result = await self.run_single_test(test)
            self.test_results.append(result)
            self.save_summary_results() # Save progress after each test

        logger.info("\nAll tests completed.")
        self.analyze_and_recommend()

    async def run_single_test(self, test_config: Dict[str, Any]) -> Dict[str, Any]:
        """Runs the simulator script as a subprocess for a single test."""
        command = [
            sys.executable, str(self.simulator_script),
            '--host', self.host,
        ] + test_config['args']

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"Test '{test_config['name']}' failed with return code {process.returncode}")
            logger.error(f"Stderr: {stderr.decode().strip()}")

        # Parse the JSON report from stdout
        report = {}
        try:
            # The report is the last JSON object in the output
            output_lines = stdout.decode().strip().split('\n')
            for line in reversed(output_lines):
                if line.startswith('{') and line.endswith('}'):
                    report = json.loads(line)
                    break
        except (json.JSONDecodeError, IndexError) as e:
            logger.error(f"Failed to parse JSON report for test '{test_config['name']}': {e}")
            logger.error(f"Full stdout: {stdout.decode()}")

        result = {
            "phase": test_config['phase'],
            "name": test_config['name'],
            "args": ' '.join(test_config['args']),
            "success_rate": report.get("cycle_success_rate", 0),
            "avg_collection_time": report.get("avg_collection_time", 0),
            "failed_cycles": report.get("failed_cycles", -1),
        }
        return result

    def save_summary_results(self):
        """Saves the high-level test results to a CSV file."""
        if not self.test_results:
            return
            
        filepath = self.results_dir / "summary_results.csv"
        with open(filepath, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=self.test_results[0].keys())
            writer.writeheader()
            writer.writerows(self.test_results)
        logger.info(f"Summary results updated at: {filepath}")

    def analyze_and_recommend(self):
        """Analyzes all test results and provides a final recommendation."""
        logger.info("\n\n" + "#"*80)
        logger.info("                 PERFORMANCE ANALYSIS AND RECOMMENDATION")
        logger.info("#"*80)

        if not self.test_results:
            logger.warning("No test results to analyze.")
            return

        df = pd.DataFrame(self.test_results)
        df = df.sort_values(by=['phase', 'name'])
        
        logger.info("\n--- Overall Test Results ---\n")
        print(df.to_string(index=False))
        
        # --- Recommendation Logic ---
        # Find the fastest test from Phase 3 that has a >= 99.5% success rate
        stable_tests = df[
            (df['phase'] == 3) & 
            (df['success_rate'] >= 99.5)
        ]

        recommendation = "No stable aggressive configuration found. Stick with the conservative settings."
        recommended_config = {}

        if not stable_tests.empty:
            # Find the best stable test. 'Best' is the one with the lowest update_every.
            # We extract update_every from the 'args' string for sorting.
            stable_tests['update_every'] = stable_tests['args'].str.extract(r'--update-every (\d+)').astype(int)
            best_test = stable_tests.sort_values(by='update_every', ascending=True).iloc[0]

            recommendation = (
                f"The best performing stable configuration is: '{best_test['name']}'\n"
                f"It achieved a {best_test['success_rate']:.2f}% success rate with an average collection time of "
                f"{best_test['avg_collection_time']:.3f}s."
            )
            
            # Extract config values for the final recommendation block
            ue = best_test['update_every']
            opm = int(best_test['args'].split('--ofdm-poll-multiple ')[1].split(' ')[0])
            recommended_config = {
                'update_every': ue,
                'ofdm_poll_multiple': opm,
                'ofdm_effective_interval': ue * opm,
            }

        else:
            # Fallback to conservative if no aggressive test was stable
             conservative_test = df[df['name'] == 'Conservative Tiered Polling'].iloc[0]
             if conservative_test['success_rate'] >= 99.5:
                 ue = int(conservative_test['args'].split('--update-every ')[1].split(' ')[0])
                 opm = int(conservative_test['args'].split('--ofdm-poll-multiple ')[1].split(' ')[0])
                 recommended_config = {
                    'update_every': ue,
                    'ofdm_poll_multiple': opm,
                    'ofdm_effective_interval': ue * opm,
                 }
                 recommendation = (
                    "No aggressive configuration was stable. Falling back to the proven conservative settings."
                 )


        logger.info("\n\n--- RECOMMENDATION ---")
        logger.info(recommendation)

        if recommended_config:
            logger.info("\n" + "-"*40)
            logger.info("  Recommended hitron_coda.conf settings:")
            logger.info(f"    update_every: {recommended_config['update_every']}")
            logger.info(f"    ofdm_poll_multiple: {recommended_config['ofdm_poll_multiple']}")
            logger.info("  (This will poll fast endpoints every "
                        f"{recommended_config['update_every']}s and slow OFDM endpoints every "
                        f"{recommended_config['ofdm_effective_interval']}s)")
            logger.info("-" * 40)
        
        logger.info(f"\nFull summary saved in {self.results_dir / 'summary_results.csv'}")


def main():
    parser = argparse.ArgumentParser(description="Automated test suite for Hitron CODA tiered polling optimization.")
    parser.add_argument('--simulator', default='netdata_simulator.py', help="Path to the netdata_simulator.py script.")
    parser.add_argument('--host', default='https://192.168.100.1', help="IP address of the Hitron modem.")
    parser.add_argument('--quick', action='store_true', help="Run shorter tests for a quick evaluation.")
    args = parser.parse_args()

    test_suite = TieredPollingTestSuite(
        simulator_script=args.simulator,
        host=args.host,
        quick_mode=args.quick
    )

    def signal_handler(signum, frame):
        logger.warning(f"Interrupt signal ({signum}) received. Shutting down gracefully...")
        test_suite.is_running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(test_suite.run_all_tests())
    except KeyboardInterrupt:
        logger.info("Test suite stopped by user.")
    finally:
        logger.info("Final analysis...")
        if not test_suite.test_results:
             logger.warning("No tests were completed.")
        elif len(test_suite.test_results) < len(test_suite.test_matrix):
             logger.warning("Test suite did not complete all tests. Analysis is based on partial results.")
             test_suite.analyze_and_recommend()


if __name__ == "__main__":
    import argparse
    main()

