#!/usr/bin/env python3
"""
Simple script to close all trades via API call.
"""

import requests
import json

def main():
    """Close all active trades via API."""
    api_base = "http://localhost:8000/api"
    
    # Check current positions
    try:
        resp = requests.get(f"{api_base}/positions")
        positions = resp.json()
        active_count = positions.get('count', 0)
        print(f"📊 Active positions found: {active_count}")
        
        if active_count == 0:
            print("✅ No active positions to close!")
            return
        
        # Send emergency stop
        print("🚨 Sending emergency stop command...")
        stop_resp = requests.post(f"{api_base}/emergency-stop")
        if stop_resp.status_code == 200:
            print("✅ Emergency stop command sent successfully!")
            print("🔴 All positions will be closed automatically.")
        else:
            print(f"❌ Error sending emergency stop: {stop_resp.status_code}")
            print(stop_resp.text)
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()