#!/usr/bin/env python
"""
Comprehensive Hitron CODA modem endpoint analyzer.
Queries all discovered endpoints and analyzes their JSON structure.
"""

import json
import requests
import urllib3
import sys
from datetime import datetime
import time

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuration
MODEM_HOST = 'https://192.168.100.1'
TIMEOUT = 10

class ModemAnalyzer:
    def __init__(self, host=MODEM_HOST):
        self.host = host
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9'
        }
        self.results = {}
        
    def test_endpoint(self, endpoint, description=""):
        """Test a specific endpoint and return structured results."""
        print(f"Testing: {endpoint}")
        
        try:
            response = self.session.get(
                f'{self.host}/data/{endpoint}',
                timeout=TIMEOUT
            )
            
            result = {
                'endpoint': endpoint,
                'description': description,
                'status_code': response.status_code,
                'success': False,
                'data_type': None,
                'data': None,
                'structure_analysis': {},
                'error': None
            }
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    result['success'] = True
                    result['data'] = data
                    result['data_type'] = 'json'
                    result['structure_analysis'] = self.analyze_structure(data, endpoint)
                    
                except json.JSONDecodeError:
                    # Try to extract useful info from non-JSON
                    content = response.text.strip()
                    if content:
                        result['data'] = content[:500]  # First 500 chars
                        result['data_type'] = 'text'
                        result['structure_analysis'] = {
                            'content_length': len(content),
                            'contains_json': 'json' in content.lower(),
                            'contains_xml': '<' in content and '>' in content
                        }
                    
            else:
                result['error'] = f"HTTP {response.status_code}"
                
        except requests.exceptions.RequestException as e:
            result['error'] = str(e)
        except Exception as e:
            result['error'] = f"Unexpected error: {str(e)}"
            
        self.results[endpoint] = result
        return result
    
    def analyze_structure(self, data, endpoint):
        """Analyze the structure of JSON data to understand its format."""
        analysis = {
            'type': type(data).__name__,
            'key_fields': [],
            'data_categories': [],
            'channel_info': {},
            'sample_record': None
        }
        
        if isinstance(data, list):
            analysis['array_length'] = len(data)
            if data and isinstance(data[0], dict):
                analysis['key_fields'] = list(data[0].keys())
                analysis['sample_record'] = data[0]
                
                # Analyze what type of data this might be
                analysis['data_categories'] = self.categorize_data_fields(data[0].keys(), endpoint)
                
                # Look for channel information
                analysis['channel_info'] = self.analyze_channel_data(data)
                
        elif isinstance(data, dict):
            analysis['key_fields'] = list(data.keys())
            analysis['sample_record'] = data
            analysis['data_categories'] = self.categorize_data_fields(data.keys(), endpoint)
            
        return analysis
    
    def categorize_data_fields(self, keys, endpoint):
        """Categorize fields based on common cable modem data types."""
        categories = []
        key_list = [k.lower() for k in keys]
        
        # Power/Signal categories
        if any(term in key_list for term in ['signalstrength', 'power', 'rxpower', 'txpower']):
            categories.append('power_levels')
            
        # Frequency categories  
        if any(term in key_list for term in ['frequency', 'freq']):
            categories.append('frequency_data')
            
        # Modulation categories
        if any(term in key_list for term in ['modtype', 'modulation', 'qam', 'profile']):
            categories.append('modulation_data')
            
        # Error/Quality categories
        if any(term in key_list for term in ['snr', 'correcteds', 'uncorrect', 'errors']):
            categories.append('error_statistics')
            
        # Channel categories
        if any(term in key_list for term in ['channelid', 'channel', 'portid']):
            categories.append('channel_info')
            
        # OFDM categories
        if any(term in key_list for term in ['ofdm', 'subcarrier', 'profile', 'plc']) or 'ofdm' in endpoint:
            categories.append('ofdm_data')
            
        # Bandwidth categories
        if any(term in key_list for term in ['bandwidth', 'bw', 'width']):
            categories.append('bandwidth_data')
            
        # System categories
        if any(term in key_list for term in ['uptime', 'status', 'version', 'model']):
            categories.append('system_info')
            
        return categories
    
    def analyze_channel_data(self, data):
        """Analyze channel-specific information."""
        if not isinstance(data, list) or not data:
            return {}
            
        channel_info = {
            'total_channels': len(data),
            'channel_ids': [],
            'frequency_range': {},
            'power_range': {},
            'unique_modulations': set()
        }
        
        for item in data:
            if isinstance(item, dict):
                # Collect channel IDs
                for id_field in ['channelId', 'Channel', 'portId']:
                    if id_field in item:
                        try:
                            channel_info['channel_ids'].append(int(item[id_field]))
                        except (ValueError, TypeError):
                            pass
                
                # Collect frequency info
                for freq_field in ['frequency', 'Frequency']:
                    if freq_field in item:
                        try:
                            freq = float(item[freq_field])
                            if 'min' not in channel_info['frequency_range']:
                                channel_info['frequency_range']['min'] = freq
                                channel_info['frequency_range']['max'] = freq
                            else:
                                channel_info['frequency_range']['min'] = min(channel_info['frequency_range']['min'], freq)
                                channel_info['frequency_range']['max'] = max(channel_info['frequency_range']['max'], freq)
                        except (ValueError, TypeError):
                            pass
                
                # Collect power info
                for power_field in ['signalStrength', 'power', 'rxPower', 'txPower']:
                    if power_field in item:
                        try:
                            power = float(item[power_field])
                            if 'min' not in channel_info['power_range']:
                                channel_info['power_range']['min'] = power
                                channel_info['power_range']['max'] = power
                            else:
                                channel_info['power_range']['min'] = min(channel_info['power_range']['min'], power)
                                channel_info['power_range']['max'] = max(channel_info['power_range']['max'], power)
                        except (ValueError, TypeError):
                            pass
                
                # Collect modulation types
                for mod_field in ['modtype', 'modulation', 'profileId']:
                    if mod_field in item and item[mod_field]:
                        channel_info['unique_modulations'].add(str(item[mod_field]))
        
        # Convert set to list for JSON serialization
        channel_info['unique_modulations'] = list(channel_info['unique_modulations'])
        channel_info['channel_ids'] = sorted(list(set(channel_info['channel_ids'])))
        
        return channel_info
    
    def generate_report(self):
        """Generate a comprehensive analysis report."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        report = {
            'analysis_timestamp': timestamp,
            'modem_host': self.host,
            'summary': {
                'total_endpoints': len(self.results),
                'successful_endpoints': len([r for r in self.results.values() if r['success']]),
                'json_endpoints': len([r for r in self.results.values() if r['data_type'] == 'json']),
                'failed_endpoints': len([r for r in self.results.values() if not r['success']])
            },
            'endpoint_categories': {
                'downstream_qam': [],
                'downstream_ofdm': [], 
                'upstream_qam': [],
                'upstream_ofdm': [],
                'system_info': [],
                'other': []
            },
            'detailed_results': self.results
        }
        
        # Categorize endpoints
        for endpoint, result in self.results.items():
            if not result['success']:
                continue
                
            endpoint_lower = endpoint.lower()
            categories = result['structure_analysis'].get('data_categories', [])
            
            if 'dsofdm' in endpoint_lower:
                report['endpoint_categories']['downstream_ofdm'].append(endpoint)
            elif 'usofdm' in endpoint_lower:
                report['endpoint_categories']['upstream_ofdm'].append(endpoint)
            elif 'dsinfo' in endpoint_lower or (endpoint_lower.startswith('ds') and 'ofdm' not in endpoint_lower):
                report['endpoint_categories']['downstream_qam'].append(endpoint)
            elif 'usinfo' in endpoint_lower or (endpoint_lower.startswith('us') and 'ofdm' not in endpoint_lower):
                report['endpoint_categories']['upstream_qam'].append(endpoint)
            elif 'system_info' in categories or any(term in endpoint_lower for term in ['sys', 'status', 'info']):
                report['endpoint_categories']['system_info'].append(endpoint)
            else:
                report['endpoint_categories']['other'].append(endpoint)
        
        return report
    
    def print_summary(self):
        """Print a human-readable summary of findings."""
        report = self.generate_report()
        
        print(f"\n{'='*80}")
        print(f"HITRON CODA MODEM ANALYSIS COMPLETE")
        print(f"Timestamp: {report['analysis_timestamp']}")
        print(f"{'='*80}")
        
        print(f"\nSUMMARY:")
        print(f"  Total endpoints tested: {report['summary']['total_endpoints']}")
        print(f"  Successful responses: {report['summary']['successful_endpoints']}")
        print(f"  JSON endpoints: {report['summary']['json_endpoints']}")
        print(f"  Failed endpoints: {report['summary']['failed_endpoints']}")
        
        print(f"\nENDPOINT CATEGORIES:")
        for category, endpoints in report['endpoint_categories'].items():
            if endpoints:
                print(f"  {category.upper()}: {len(endpoints)} endpoints")
                for ep in endpoints:
                    result = self.results[ep]
                    channels = result['structure_analysis'].get('channel_info', {}).get('total_channels', 0)
                    print(f"    📊 {ep} - {channels} channels")
        
        print(f"\nKEY FINDINGS:")
        
        # Find the best endpoints for each data type
        best_endpoints = {
            'downstream_qam': None,
            'downstream_ofdm': None,
            'upstream_qam': None, 
            'upstream_ofdm': None
        }
        
        for category, endpoints in report['endpoint_categories'].items():
            if endpoints and category in best_endpoints:
                # Pick the endpoint with the most data
                best = None
                max_channels = 0
                for ep in endpoints:
                    channels = self.results[ep]['structure_analysis'].get('channel_info', {}).get('total_channels', 0)
                    if channels > max_channels:
                        max_channels = channels
                        best = ep
                best_endpoints[category] = best
        
        for data_type, endpoint in best_endpoints.items():
            if endpoint:
                result = self.results[endpoint]
                channel_info = result['structure_analysis'].get('channel_info', {})
                categories = result['structure_analysis'].get('data_categories', [])
                
                print(f"\n  🎯 {data_type.upper()}: {endpoint}")
                print(f"     Channels: {channel_info.get('total_channels', 0)}")
                print(f"     Data types: {', '.join(categories)}")
                
                if 'frequency_range' in channel_info and channel_info['frequency_range']:
                    freq_min = channel_info['frequency_range'].get('min', 0) / 1000000
                    freq_max = channel_info['frequency_range'].get('max', 0) / 1000000
                    print(f"     Frequency range: {freq_min:.1f} - {freq_max:.1f} MHz")
                
                if 'power_range' in channel_info and channel_info['power_range']:
                    power_min = channel_info['power_range'].get('min', 0)
                    power_max = channel_info['power_range'].get('max', 0)
                    print(f"     Power range: {power_min:.1f} - {power_max:.1f} dBmV")
        
        print(f"\n{'='*80}")
        print("Use save_detailed_report() to save full JSON analysis")
        print(f"{'='*80}")
    
    def save_detailed_report(self, filename=None):
        """Save detailed analysis to JSON file."""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"hitron_analysis_{timestamp}.json"
        
        report = self.generate_report()
        
        with open(filename, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        
        print(f"Detailed analysis saved to: {filename}")
        return filename

def main():
    """Main analysis function."""
    print("Hitron CODA Comprehensive Endpoint Analyzer")
    print(f"Target: {MODEM_HOST}")
    print("=" * 80)
    
    analyzer = ModemAnalyzer()
    
    # All endpoints from your screenshot
    endpoints = [
        ('dsinfo.asp', 'Standard downstream channel info'),
        ('dsofdminfo.asp', 'OFDM downstream channel info'),
        ('getCmDocsisWan.asp', 'DOCSIS WAN information'),
        ('getMenu.asp', 'Menu/navigation data'),
        ('getSubMenu.asp', 'Submenu data'),
        ('getViewInfo.asp', 'View information'),
        ('system_model.asp', 'System model information'),
        ('usinfo.asp', 'Upstream channel info - CONFIRMED WORKING'),
        ('usofdminfo.asp', 'OFDM upstream channel info'),
    ]
    
    print("Testing all endpoints...")
    for endpoint, description in endpoints:
        result = analyzer.test_endpoint(endpoint, description)
        status = "✅" if result['success'] else "❌"
        data_info = f"({result['data_type']})" if result['success'] else f"({result['error']})"
        print(f"  {status} {endpoint} {data_info}")
        time.sleep(0.5)  # Be nice to the modem
    
    print("\nAnalyzing results...")
    analyzer.print_summary()
    
    # Save detailed report
    print(f"\nSaving detailed analysis...")
    filename = analyzer.save_detailed_report()
    
    print(f"\n🎯 NEXT STEPS:")
    print(f"1. Review the analysis above to identify the best endpoints")
    print(f"2. Check {filename} for complete JSON structures")
    print(f"3. Use the identified endpoints to update your Netdata plugin")
    print(f"4. Focus on endpoints with the most comprehensive data")

if __name__ == '__main__':
    main()
