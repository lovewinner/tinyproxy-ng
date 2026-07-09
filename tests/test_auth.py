#!/usr/bin/env python3
"""
Auth functionality test script
Tests the proxy server's authentication enable/disable state
"""

import asyncio
import socket
import time
import base64
import sys

def build_proxy_request(host: str, port: int, path: str = "/", auth_header: str = None) -> bytes:
    """Build HTTP proxy request"""
    request = f"GET {path} HTTP/1.1\r\n"
    request += f"Host: {host}\r\n"
    if auth_header:
        request += f"Proxy-Authorization: {auth_header}\r\n"
    request += "User-Agent: test-client/1.0\r\n"
    request += "Connection: close\r\n\r\n"
    return request.encode('utf-8')

async def test_proxy(host: str, port: int, username: str = None, password: str = None) -> dict:
    """Test proxy connection and authentication"""
    result = {
        'host': host,
        'port': port,
        'connection': False,
        'auth_required': None,
        'auth_working': None,
        'error': None
    }

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=5
        )
        result['connection'] = True
        print(f"✅ 连接成功: {host}:{port}")

        # Step 1: Send request without auth
        req1 = build_proxy_request('httpbin.org', 80, '/ip')
        writer.write(req1)
        await writer.drain()
        response1 = await asyncio.wait_for(reader.read(4096), timeout=5)
        response1_str = response1.decode('utf-8', errors='ignore')
        print(f"\n📨 No-auth response (first 500 chars):")
        print(response1_str[:500])

        # Check if returns 407
        if '407' in response1_str.split('\n')[0]:
            result['auth_required'] = True
            print("✅ Auth required: Enabled (407)")
        elif '200' in response1_str.split('\n')[0]:
            result['auth_required'] = False
            print("⚠️  Auth required: Disabled (200)")
        else:
            print(f"❓ Unexpected status: {response1_str.split(chr(10))[0]}")

        # Step 2: If auth required, test with auth
        if result['auth_required'] and username and password:
            auth_str = base64.b64encode(f"{username}:{password}".encode()).decode()
            auth_header = f"Basic {auth_str}"
            req2 = build_proxy_request('httpbin.org', 80, '/ip', auth_header)

            # Need a new connection
            writer.close()
            await writer.wait_closed()
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=5
            )

            writer.write(req2)
            await writer.drain()
            response2 = await asyncio.wait_for(reader.read(4096), timeout=5)
            response2_str = response2.decode('utf-8', errors='ignore')
            print(f"\n📨 Auth response (first 500 chars):")
            print(response2_str[:500])

            if '200' in response2_str.split('\n')[0]:
                result['auth_working'] = True
                print("✅ Auth passed: 200")
            elif '407' in response2_str.split('\n')[0]:
                result['auth_working'] = False
                print("❌ Auth failed: 407 (wrong password or format)")
            else:
                print(f"❓ Unexpected status: {response2_str.split(chr(10))[0]}")

        writer.close()
        await writer.wait_closed()

    except asyncio.TimeoutError:
        result['error'] = 'Connection timeout'
        print(f"❌ Connection timeout: {host}:{port}")
    except ConnectionRefusedError:
        result['error'] = 'Connection refused'
        print(f"❌ Connection refused: {host}:{port}")
    except Exception as e:
        result['error'] = str(e)
        print(f"❌ Error: {e}")

    return result

async def main():
    print("=" * 60)
    print("Proxy Server Auth Test")
    print("=" * 60)

    host = input("Proxy address [default: 127.0.0.1]: ").strip() or "127.0.0.1"
    port_input = input("Proxy port [default: 8080]: ").strip()
    port = int(port_input) if port_input else 8080

    test_user = input("Test username [default: admin]: ").strip() or "admin"
    test_pass = input("Test password [default: password123]: ").strip() or "password123"

    print(f"\n🚀 Starting test: {host}:{port}")
    print(f"   Test account: {test_user}")
    print("-" * 60)

    result = await test_proxy(host, port, test_user, test_pass)

    print("\n" + "=" * 60)
    print("📊 Test Results Summary")
    print("=" * 60)
    print(f"Connection: {'✅' if result['connection'] else '❌'}")
    if result['auth_required'] is not None:
        print(f"Auth Required: {'✅ Enabled' if result['auth_required'] else '❌ Disabled (not recommended)'}")
    if result['auth_working'] is not None:
        print(f"Auth Working: {'✅ Yes' if result['auth_working'] else '❌ No'}")
    if result['error']:
        print(f"Error: {result['error']}")

    print("\n💡 Suggestions:")
    if not result['connection']:
        print("   - Check if the proxy server is running")
        print("   - Check firewall rules for the port")
    elif result['auth_required'] is False:
        print("   - Auth is disabled, set auth_enabled: true in config.yaml")
    elif result['auth_required'] and not result['auth_working']:
        print("   - Check if config.yaml username/password match client settings")
        print("   - Start proxy with --debug to see detailed logs")
        print("   - Ensure the client has the correct credentials")
    else:
        print("   - Auth is working properly!")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTest cancelled")
