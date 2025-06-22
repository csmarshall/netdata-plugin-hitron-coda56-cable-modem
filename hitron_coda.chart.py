#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Enhanced Netdata plugin for monitoring Hitron CODA cable modems.

Key Features:
- Tiered Polling: Polls fast endpoints frequently and slow OFDM endpoints less often for stability.
- Serial collection by default for maximum modem compatibility.
- Smart timeout management and dynamic retry calculation.
- Comprehensive monitoring of over 50 metrics.

Version: 2.0.0
"""

import sys
import json
import time
import asyncio
import aiohttp
import ssl
import requests
import urllib3
import logging
from bases.FrameworkServices.SimpleService import SimpleService

# --- Basic Plugin Configuration ---
NAME = 'hitron_coda'
UPDATE_EVERY = 60

# --- Setup ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


class Service(SimpleService):
    """
    Netdata service for Hitron CODA modems with tiered polling logic.
    """

    # --- Endpoint Categories ---
    # Fast endpoints are polled every cycle.
    FAST_ENDPOINTS = ['dsinfo.asp', 'usinfo.asp', 'getCmDocsisWan.asp', 'getViewInfo.asp']
    # Slow endpoints are polled less frequently to avoid modem instability.
    SLOW_ENDPOINTS = ['dsofdminfo.asp', 'usofdminfo.asp']
    ALL_ENDPOINTS = FAST_ENDPOINTS + SLOW_ENDPOINTS

    def __init__(self, configuration=None, name=None):
        """Initializes the service with configuration and tiered polling logic."""
        SimpleService.__init__(self, configuration=configuration, name=name)
        
        # --- Load Configuration ---
        if self.configuration is None:
            self.configuration = {}
        if NAME in self.configuration:
            self.configuration = self.configuration[NAME]
            
        self.host = self.configuration.get('host')
        if not self.host:
            logger.critical(f"[{self.name}] 'host' is not defined in the configuration. Plugin cannot start.")
            raise ValueError("'host' must be specified in hitron_coda.conf")

        self.order = []
        self.definitions = {}
        
        # --- Core Settings ---
        self.update_every = int(self.configuration.get('update_every', UPDATE_EVERY))
        self.device_name = self.configuration.get('device_name', "Hitron CODA Modem")
        
        # --- Collection Strategy ---
        # Default to serial collection for maximum stability.
        self.parallel_collection = self.configuration.get('parallel_collection', False)
        self.inter_request_delay = float(self.configuration.get('inter_request_delay', 0.2))

        # --- Tiered Polling Logic ---
        self.run_counter = 0
        ofdm_update_every = int(self.configuration.get('ofdm_update_every', 0))
        default_ofdm_multiple = 5  # Poll OFDM endpoints every 5th cycle by default.
        self.ofdm_poll_multiple = int(self.configuration.get('ofdm_poll_multiple', default_ofdm_multiple))

        # If a specific 'ofdm_update_every' is set, it overrides the multiple.
        if ofdm_update_every > 0:
            if ofdm_update_every < self.update_every:
                self.ofdm_poll_multiple = 1 # Poll every time if interval is faster.
            else:
                self.ofdm_poll_multiple = max(1, round(ofdm_update_every / self.update_every))
        
        logger.info(f"[{self.name}] Tiered polling enabled. OFDM endpoints will be polled every {self.ofdm_poll_multiple} cycles.")

        # --- Timeout and Retry Logic ---
        self.endpoint_timeout = int(self.configuration.get('endpoint_timeout', 8)) # Generous default for serial.
        self.collection_timeout = int(self.configuration.get('collection_timeout', int(self.update_every * 0.9)))
        self.max_retries = self.configuration.get('max_retries')
        
        # Auto-calculate max_retries if not explicitly set.
        if self.max_retries is None:
            if self.endpoint_timeout > 0:
                self.max_retries = max(1, int(self.collection_timeout / self.endpoint_timeout))
            else:
                self.max_retries = 1
        
        # SSL context for async requests (if parallel mode is enabled).
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    def check(self):
        """Confirms the service can connect to the modem before starting."""
        try:
            url = f"{self.host}/data/getViewInfo.asp"
            response = requests.get(url, verify=False, timeout=10)
            if response.status_code == 200 and 'SysDesc' in response.text:
                logger.info(f"[{self.name}] Successfully connected to modem at {self.host}.")
                return True
            else:
                logger.error(f"[{self.name}] Failed to connect to modem at {self.host}. Status: {response.status_code}, Response: {response.text[:100]}")
                return False
        except requests.RequestException as e:
            logger.error(f"[{self.name}] Connection check failed: {e}")
            return False

    def _get_data(self):
        """Main data collection method called by the Netdata agent."""
        start_time = time.time()
        self.run_counter += 1

        # --- Determine which endpoints to poll in this cycle ---
        # The first run always polls everything to initialize all charts.
        if self.run_counter == 1 or (self.ofdm_poll_multiple > 0 and self.run_counter % self.ofdm_poll_multiple == 0):
            endpoints_to_poll = self.ALL_ENDPOINTS
            logger.debug(f"Cycle {self.run_counter}: Polling ALL endpoints.")
        else:
            endpoints_to_poll = self.FAST_ENDPOINTS
            logger.debug(f"Cycle {self.run_counter}: Polling FAST endpoints only.")

        # --- Fetch Data ---
        try:
            if self.parallel_collection:
                # Use asyncio for parallel fetching.
                loop = asyncio.get_event_loop()
                data = loop.run_until_complete(self._fetch_parallel(endpoints_to_poll))
            else:
                # Use requests for serial fetching.
                data = self._fetch_serial(endpoints_to_poll)
        except Exception as e:
            logger.error(f"[{self.name}] Unhandled exception during data collection: {e}")
            return None

        if not data:
            logger.warning(f"[{self.name}] No data was collected in this cycle.")
            return None

        # --- Process Data ---
        self._process_and_define_charts(data)
        
        # Add plugin performance metrics.
        collection_time = time.time() - start_time
        self.data['plugin_collection_time'] = int(collection_time * 1000)
        return self.data

    def _fetch_serial(self, endpoints):
        """Fetches data from endpoints sequentially (serially)."""
        data = {}
        with requests.Session() as session:
            session.verify = False
            for endpoint in endpoints:
                try:
                    url = f"{self.host}/data/{endpoint}"
                    response = session.get(url, timeout=self.endpoint_timeout)
                    response.raise_for_status()
                    data[endpoint] = response.json()
                except requests.RequestException as e:
                    logger.warning(f"[{self.name}] Serial fetch failed for {endpoint}: {e}")
                
                # Delay between requests to be gentle on the modem.
                if len(endpoints) > 1:
                    time.sleep(self.inter_request_delay)
        return data

    async def _fetch_parallel(self, endpoints):
        """Fetches data from endpoints concurrently (in parallel)."""
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=self.ssl_context)) as session:
            tasks = [self._fetch_endpoint_async(session, endpoint) for endpoint in endpoints]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        
        data = {endpoint: result for endpoint, result in zip(endpoints, results) if not isinstance(result, Exception) and result is not None}
        return data

    async def _fetch_endpoint_async(self, session, endpoint):
        """Async helper to fetch a single endpoint with retries."""
        url = f"{self.host}/data/{endpoint}"
        for attempt in range(self.max_retries):
            try:
                async with session.get(url, timeout=self.endpoint_timeout) as response:
                    response.raise_for_status()
                    return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.debug(f"[{self.name}] Attempt {attempt + 1}/{self.max_retries} for {endpoint} failed: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(1)
        
        logger.error(f"[{self.name}] Failed to fetch {endpoint} after {self.max_retries} attempts.")
        return None

    def _process_and_define_charts(self, api_data):
        """Parses collected data and populates self.data for Netdata."""
        # Reset data for this cycle.
        self.data = {}
        
        # --- Downstream Channels (DOCSIS 3.0) ---
        if 'dsinfo.asp' in api_data and 'dsinfo' in api_data['dsinfo.asp']:
            for ch in api_data['dsinfo.asp']['dsinfo']:
                ch_id = f"ds_{ch['channelid']}"
                self.data[f'{ch_id}_power'] = int(float(ch['power']) * 10)
                self.data[f'{ch_id}_snr'] = int(float(ch['snr']) * 100)
                self.data[f'{ch_id}_corrected'] = int(ch['correcteds'])
                self.data[f'{ch_id}_uncorrected'] = int(ch['uncorrectables'])

        # --- Upstream Channels (DOCSIS 3.0) ---
        if 'usinfo.asp' in api_data and 'usinfo' in api_data['usinfo.asp']:
            for ch in api_data['usinfo.asp']['usinfo']:
                ch_id = f"us_{ch['channelid']}"
                self.data[f'{ch_id}_power'] = int(float(ch['power']) * 100)

        # --- Downstream OFDM (DOCSIS 3.1) ---
        if 'dsofdminfo.asp' in api_data and 'dsofdminfo' in api_data['dsofdminfo.asp']:
            dsofdm = api_data['dsofdminfo.asp']['dsofdminfo'][0]
            self.data['dsofdm_power'] = int(float(dsofdm['power']) * 10)
            self.data['dsofdm_snr'] = int(float(dsofdm['snr']) * 100)
            self.data['dsofdm_corrected'] = int(dsofdm['correcteds'])
            self.data['dsofdm_uncorrected'] = int(dsofdm['uncorrectables'])

        # --- Upstream OFDMA (DOCSIS 3.1) ---
        if 'usofdminfo.asp' in api_data and 'usofdminfo' in api_data['usofdminfo.asp']:
            usofdm = api_data['usofdminfo.asp']['usofdminfo'][0]
            self.data['usofdm_power'] = int(float(usofdm['power']) * 100)
        
        # --- System & WAN Info ---
        if 'getViewInfo.asp' in api_data and 'getViewInfo' in api_data['getViewInfo.asp']:
            self.data['system_uptime'] = int(api_data['getViewInfo.asp']['getViewInfo'][0].get('SysUpTime', 0))
        if 'getCmDocsisWan.asp' in api_data and 'getCmDocsisWan' in api_data['getCmDocsisWan.asp']:
            wan = api_data['getCmDocsisWan.asp']['getCmDocsisWan'][0]
            self.data['wan_ipv4_online'] = 1 if wan.get('IPv4Status') == 'Online' else 0
            self.data['wan_ipv6_online'] = 1 if wan.get('IPv6Status') == 'Online' else 0

        # --- Create chart definitions on the first successful run ---
        if not self.order and self.data:
            self._create_definitions()

    def _create_definitions(self):
        """Dynamically creates all chart definitions for Netdata."""
        self.order, self.definitions = [], {}

        def add_chart(chart_id, title, units, family, context, chart_type='line'):
            self.order.append(chart_id)
            self.definitions[chart_id] = {'options': [None, title, units, family, context, chart_type], 'lines': []}
        
        def add_line(chart_id, dim_id, name, algorithm='absolute', multiplier=1, divisor=1):
            self.definitions[chart_id]['lines'].append([dim_id, name, algorithm, multiplier, divisor])

        # --- Plugin Health Chart ---
        add_chart('plugin_health', 'Plugin Performance', 'ms', 'plugin', f'{self.name}.plugin_health')
        add_line('plugin_health', 'plugin_collection_time', 'Collection Time', 'absolute')

        # --- System Status ---
        add_chart('system_status', 'System Status', 'status', 'system', f'{self.name}.system')
        add_line('system_status', 'system_uptime', 'Uptime', 'absolute')
        add_line('system_status', 'wan_ipv4_online', 'IPv4 Online', 'absolute')
        add_line('system_status', 'wan_ipv6_online', 'IPv6 Online', 'absolute')

        # --- Signal Quality Charts ---
        add_chart('downstream_power', 'Downstream Power', 'dBmV', 'power', f'{self.name}.downstream_power')
        add_chart('downstream_snr', 'Downstream SNR', 'dB', 'snr', f'{self.name}.downstream_snr')
        add_chart('upstream_power', 'Upstream Power', 'dBmV', 'power', f'{self.name}.upstream_power')

        # --- Error Charts ---
        add_chart('downstream_corrected', 'Downstream Corrected Errors', 'errors/s', 'errors', f'{self.name}.downstream_corrected', 'stacked')
        add_chart('downstream_uncorrected', 'Downstream Uncorrected Errors', 'errors/s', 'errors', f'{self.name}.downstream_uncorrected', 'stacked')

        # --- OFDM (DOCSIS 3.1) Charts ---
        if 'dsofdm_power' in self.data:
            add_chart('ofdm_power', 'OFDM Power', 'dBmV', 'power', f'{self.name}.ofdm_power')
            add_line('ofdm_power', 'dsofdm_power', 'Downstream', divisor=10)
            if 'usofdm_power' in self.data:
                add_line('ofdm_power', 'usofdm_power', 'Upstream', divisor=100)
        
        if 'dsofdm_snr' in self.data:
            add_chart('ofdm_snr', 'OFDM SNR', 'dB', 'snr', f'{self.name}.ofdm_snr')
            add_line('ofdm_snr', 'dsofdm_snr', 'Downstream', divisor=100)
            
        if 'dsofdm_corrected' in self.data:
            add_chart('ofdm_errors', 'OFDM Errors', 'errors/s', 'errors', f'{self.name}.ofdm_errors', 'stacked')
            add_line('ofdm_errors', 'dsofdm_corrected', 'Corrected', 'incremental')
            add_line('ofdm_errors', 'dsofdm_uncorrected', 'Uncorrected', 'incremental')

        # --- Add lines for individual channels dynamically ---
        for i in range(1, 32): # DS channels
            if f'ds_{i}_power' in self.data:
                add_line('downstream_power', f'ds_{i}_power', f'Ch {i}', divisor=10)
                add_line('downstream_snr', f'ds_{i}_snr', f'Ch {i}', divisor=100)
                add_line('downstream_corrected', f'ds_{i}_corrected', f'Ch {i}', 'incremental')
                add_line('downstream_uncorrected', f'ds_{i}_uncorrected', f'Ch {i}', 'incremental')

        for i in range(1, 9): # US channels
            if f'us_{i}_power' in self.data:
                add_line('upstream_power', f'us_{i}_power', f'Ch {i}', divisor=100)

