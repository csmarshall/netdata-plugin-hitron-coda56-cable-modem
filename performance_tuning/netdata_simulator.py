#!/usr/bin/env python3

"""
Netdata Modem Simulator for Stability Testing.

This script simulates the behavior of the `hitron_coda.chart.py` plugin,
including the new tiered polling logic, to help determine the fastest
stable polling rates for a Hitron CODA modem.

Version: 2.0.0
"""

import asyncio
import aiohttp
import ssl
import time
from datetime import datetime, timedelta
import signal
import sys
import argparse
import logging
import statistics
import json

# --- Basic Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class NetdataModemSimulator:
    """
    Simulates the Netdata plugin with tiered polling for stability testing.
    """

    # Mirror endpoint categories from the plugin
    FAST_ENDPOINTS = ['dsinfo.asp', 'usinfo.asp', 'getCmDocsisWan.asp', 'getViewInfo.asp']
    SLOW_ENDPOINTS = ['dsofdminfo.asp', 'usofdminfo.asp']
    ALL_ENDPOINTS = FAST_ENDPOINTS + SLOW_ENDPOINTS

    def __init__(self, **kwargs):
        self.modem_host = kwargs.get('modem_host')
        self.update_every = kwargs.get('update_every')
        self.test_duration = kwargs.get('test_duration')
        
        # --- Collection Strategy ---
        self.parallel_collection = kwargs.get('parallel_collection', False) # Default to serial
        self.inter_request_delay = kwargs.get('inter_request_delay', 0.2)

        # --- Tiered Polling Config ---
        self.run_counter = 0
        ofdm_update_every = kwargs.get('ofdm_update_every', 0)
        self.ofdm_poll_multiple = kwargs.get('ofdm_poll_multiple', 5)

        if ofdm_update_every > 0:
            self.ofdm_poll_multiple = max(1, round(ofdm_update_every / self.update_every))
        
        # --- Timeout and Retry Logic ---
        self.endpoint_timeout = kwargs.get('endpoint_timeout', 8)
        self.collection_timeout = kwargs.get('collection_timeout')
        if self.collection_timeout is None:
            self.collection_timeout = int(self.update_every * 0.9)
            
        self.max_retries = kwargs.get('max_retries')
        if self.max_retries is None:
            self.max_retries = max(1, int(self.collection_timeout / self.endpoint_timeout)) if self.endpoint_timeout > 0 else 1

        # --- SSL and State ---
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        
        self.is_running = True
        self.results = {
            "total_cycles": 0, "successful_cycles": 0, "failed_cycles": 0,
            "total_requests": 0, "successful_requests": 0,
            "collection_times": []
        }
        logger.info(f"Simulator configured. Mode: {'Parallel' if self.parallel_collection else 'Serial'}. OFDM poll multiple: {self.ofdm_poll_multiple}.")

    async def run_simulation(self):
        """Main simulation loop."""
        start_time = datetime.now()
        end_time = start_time + timedelta(seconds=self.test_duration)
        logger.info(f"Starting simulation for {self.test_duration} seconds... Press Ctrl+C to stop.")

        while self.is_running and datetime.now() < end_time:
            cycle_start_time = time.monotonic()
            
            # --- Determine endpoints for this cycle ---
            self.run_counter += 1
            if self.run_counter == 1 or (self.ofdm_poll_multiple > 0 and self.run_counter % self.ofdm_poll_multiple == 0):
                endpoints_to_poll = self.ALL_ENDPOINTS
                cycle_type = "FULL"
            else:
                endpoints_to_poll = self.FAST_ENDPOINTS
                cycle_type = "FAST"

            logger.debug(f"--- Cycle {self.results['total_cycles'] + 1} ({cycle_type}) ---")
            
            success, collection_time, num_success = await self._run_collection_cycle(endpoints_to_poll)
            
            self._update_stats(success, collection_time, len(endpoints_to_poll), num_success)
            self._log_progress_update()

            # Wait for the next cycle, accounting for the time this cycle took.
            elapsed = time.monotonic() - cycle_start_time
            await asyncio.sleep(max(0, self.update_every - elapsed))

        logger.info("\nSimulation finished.")

    async def _run_collection_cycle(self, endpoints):
        """Runs a single data collection cycle."""
        start_time = time.monotonic()
        try:
            if self.parallel_collection:
                results = await self._fetch_parallel(endpoints)
            else:
                results = await self._fetch_serial(endpoints)
            
            collection_time = time.monotonic() - start_time
            successful_requests = sum(1 for r in results if r is not None)
            
            is_cycle_success = successful_requests == len(endpoints)
            return is_cycle_success, collection_time, successful_requests
            
        except Exception as e:
            logger.error(f"Cycle failed with an unexpected exception: {e}")
            return False, time.monotonic() - start_time, 0

    async def _fetch_serial(self, endpoints):
        """Simulates serial fetching."""
        results = []
        for endpoint in endpoints:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=self.ssl_context)) as session:
                results.append(await self._fetch_endpoint(session, endpoint))
            await asyncio.sleep(self.inter_request_delay)
        return results

    async def _fetch_parallel(self, endpoints):
        """Simulates parallel fetching."""
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=self.ssl_context)) as session:
            tasks = [self._fetch_endpoint(session, ep) for ep in endpoints]
            return await asyncio.gather(*tasks)

    async def _fetch_endpoint(self, session, endpoint):
        """Helper to fetch a single endpoint with retries."""
        url = f"{self.modem_host}/data/{endpoint}"
        for attempt in range(self.max_retries):
            try:
                async with session.get(url, timeout=self.endpoint_timeout) as response:
                    if response.status == 200:
                        return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
        return None

    def _update_stats(self, success, collection_time, num_reqs, num_success):
        """Updates statistics after each collection cycle."""
        self.results['total_cycles'] += 1
        self.results['collection_times'].append(collection_time)
        self.results['total_requests'] += num_reqs
        self.results['successful_requests'] += num_success
        
        if success:
            self.results['successful_cycles'] += 1
        else:
            self.results['failed_cycles'] += 1

    def _log_progress_update(self):
        """Logs a single line of progress to the console."""
        if not self.results['total_cycles']: return
        
        success_rate = (self.results['successful_cycles'] / self.results['total_cycles'] * 100)
        avg_time = statistics.mean(self.results['collection_times'])
        
        progress_str = (
            f"Cycles: {self.results['total_cycles']} | "
            f"Success: {success_rate:.1f}% ({self.results['successful_cycles']}/{self.results['total_cycles']}) | "
            f"Avg Time: {avg_time:.2f}s"
        )
        sys.stdout.write(f"\r{progress_str}   ")
        sys.stdout.flush()

    def generate_report(self):
        """Prints a final summary report of the simulation results."""
        print("\n\n" + "="*50)
        print("          SIMULATION FINAL REPORT")
        print("="*50)

        total_cycles = self.results['total_cycles']
        if total_cycles == 0:
            print("No cycles were completed.")
            return

        success_rate = (self.results['successful_cycles'] / total_cycles * 100)
        req_success_rate = (self.results['successful_requests'] / self.results['total_requests'] * 100) if self.results['total_requests'] else 0
        avg_time = statistics.mean(self.results['collection_times'])
        max_time = max(self.results['collection_times'])

        print(f"  Test Duration:        {self.test_duration}s")
        print(f"  Update Interval:      {self.update_every}s")
        print(f"  OFDM Poll Multiple:   {self.ofdm_poll_multiple}")
        print(f"  Collection Mode:      {'Parallel' if self.parallel_collection else 'Serial'}")
        print("-" * 50)
        print(f"  Cycle Success Rate:   {success_rate:.2f}% ({self.results['successful_cycles']}/{total_cycles})")
        print(f"  Request Success Rate: {req_success_rate:.2f}% ({self.results['successful_requests']}/{self.results['total_requests']})")
        print(f"  Avg Collection Time:  {avg_time:.3f}s")
        print(f"  Max Collection Time:  {max_time:.3f}s")
        print("="*50)

def main():
    parser = argparse.ArgumentParser(description="Netdata Hitron CODA Modem Stability Simulator")
    parser.add_argument('--host', default='https://192.168.100.1', help="Modem IP address.")
    parser.add_argument('--duration', type=int, default=300, help="Test duration in seconds.")
    parser.add_argument('--update-every', type=int, default=30, help="Base polling interval for fast endpoints.")
    parser.add_argument('--serial', action='store_true', default=True, help="Use serial collection (default).")
    parser.add_argument('--parallel', action='store_false', dest='serial', help="Use parallel collection.")
    
    # --- Tiered Polling Arguments ---
    parser.add_argument('--ofdm-poll-multiple', type=int, default=5, help="Poll slow OFDM endpoints every N cycles.")
    parser.add_argument('--ofdm-update-every', type=int, default=0, help="Set a specific OFDM poll interval in seconds (overrides --ofdm-poll-multiple).")

    # --- Fine-tuning Arguments ---
    parser.add_argument('--endpoint-timeout', type=int, default=8, help="Timeout for each individual HTTP request.")
    parser.add_argument('--collection-timeout', type=int, default=None, help="Overall timeout for one collection cycle.")
    parser.add_argument('--max-retries', type=int, default=None, help="Max retries per endpoint. Auto-calculated if not set.")
    parser.add_argument('--inter-request-delay', type=float, default=0.2, help="Delay between requests in serial mode.")

    args = parser.parse_args()

    sim_args = {
        'modem_host': args.host,
        'test_duration': args.duration,
        'update_every': args.update_every,
        'parallel_collection': not args.serial,
        'ofdm_poll_multiple': args.ofdm_poll_multiple,
        'ofdm_update_every': args.ofdm_update_every,
        'endpoint_timeout': args.endpoint_timeout,
        'collection_timeout': args.collection_timeout,
        'max_retries': args.max_retries,
        'inter_request_delay': args.inter_request_delay
    }
    
    simulator = NetdataModemSimulator(**sim_args)
    
    loop = asyncio.get_event_loop()

    def stop_simulation():
        if simulator.is_running:
            print("\nStopping simulation gracefully...")
            simulator.is_running = False

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_simulation)

    try:
        loop.run_until_complete(simulator.run_simulation())
    finally:
        simulator.generate_report()
        # Allow pending tasks to complete before closing loop
        loop.run_until_complete(asyncio.sleep(0.5))
        loop.close()

if __name__ == "__main__":
    main()


