#!/usr/bin/env python3
"""
Proxy server test tool
Verifies the proxy is working correctly
"""

import requests
import sys
import time

def test_proxy(proxy_host, proxy_port, proxy_user=None, proxy_pass=None, test_url="http://httpbin.org/ip"):
    """Test proxy connection"""
    import urllib.request

    # Build proxy URL
    if proxy_user and proxy_pass:
        proxy_url = f"http://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}"
    else:
        proxy_url = f"http://{proxy_host}:{proxy_port}"

    print(f"Testing proxy: {proxy_host}:{proxy_port}")
    print(f"Auth: {'Yes' if proxy_user else 'No'}")
    print(f"Test URL: {test_url}")
    print("-" * 50)

    # Configure proxy
    proxies = {
        'http': proxy_url,
        'https': proxy_url
    }

    try:
        start_time = time.time()
        response = requests.get(test_url, proxies=proxies, timeout=10)
        elapsed = time.time() - start_time

        print(f"✓ Connection successful!")
        print(f"Status: {response.status_code}")
        print(f"Response time: {elapsed:.2f}s")
        print(f"Content: {response.text[:200]}")

        return True

    except requests.exceptions.ProxyError as e:
        print(f"✗ Proxy error: {e}")
        return False
    except requests.exceptions.ConnectTimeout:
        print(f"✗ Connection timeout")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def main():
    print("=" * 50)
    print("Proxy Server Test Tool")
    print("=" * 50)
    print()

    # Default configuration
    host = "127.0.0.1"
    port = 8080
    username = "admin"
    password = "password123"

    # Load from config file
    try:
        import yaml
        with open('../config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            host = config.get('host', '127.0.0.1').replace('0.0.0.0', '127.0.0.1')
            port = config.get('port', 8080)
            if config.get('auth_enabled', True):
                username = config.get('username', 'admin')
                password = config.get('password', 'password123')
            print(f"Loaded settings from config file:")
            print(f"  Host: {host}")
            print(f"  Port: {port}")
            print(f"  Auth: {'Enabled' if config.get('auth_enabled', True) else 'Disabled'}")
            print()
    except Exception as e:
        print(f"Cannot load config file, using defaults: {e}")
        print()

    # Ask if user wants to modify
    print("Test with current configuration?")
    user_host = input(f"Host [{host}]: ").strip()
    user_port = input(f"Port [{port}]: ").strip()
    user_user = input(f"Username [{username}]: ").strip()
    user_pass = input(f"Password: ").strip()

    if user_host:
        host = user_host
    if user_port:
        port = int(user_port)
    if user_user:
        username = user_user
    if user_pass:
        password = user_pass

    # HTTPS test URLs (proxy must support HTTPS)
    test_urls = [
        "http://httpbin.org/ip",
        "https://httpbin.org/ip"
    ]

    print()
    print("=" * 50)
    print("Starting test...")
    print("=" * 50)
    print()

    all_passed = True

    for i, url in enumerate(test_urls, 1):
        print(f"Test {i}/{len(test_urls)}: {url}")
        success = test_proxy(
            host,
            port,
            username if username != 'admin' else None,  # Don't test password with default username
            password if username != 'admin' else None,
            url
        )
        all_passed = all_passed and success
        print()

    print("=" * 50)
    if all_passed:
        print("✓ All tests passed! Proxy server is working correctly.")
        print()
        print("Browser configuration reference:")
        print(f"  Proxy type: HTTP")
        print(f"  Server: {host if host != '0.0.0.0' else '127.0.0.1'}")
        print(f"  Port: {port}")
        if username != 'admin':
            print(f"  Auth: Enabled")
            print(f"  Username: {username}")
            print(f"  Password: {password}")
    else:
        print("✗ Some tests failed. Please check:")
        print("  1. Is the proxy server running?")
        print("  2. Does the firewall allow the port?")
        print("  3. Is the username/password correct?")
        print("  4. Is the network connection working?")
        print("  5. Check server logs for detailed errors")

    print("=" * 50)
    input("Press Enter to exit...")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nTest cancelled")
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("Run: pip install -r requirements.txt")