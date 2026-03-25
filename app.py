"""
ONVIF Camera Discovery and Snapshot Flask Application
Discovers cameras on network via WS-Discovery and fetches snapshots
"""

import socket
import uuid
import re
import base64
import io
import threading
import struct
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify, Response
import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth
import xml.etree.ElementTree as ET
import urllib3

# Suppress SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# WS-Discovery probe message template
WS_DISCOVERY_PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
               xmlns:wsd="http://schemas.xmlsoap.org/ws/2005/04/discovery"
               xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
    <soap:Header>
        <wsa:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</wsa:Action>
        <wsa:MessageID>uuid:{message_id}</wsa:MessageID>
        <wsa:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</wsa:To>
    </soap:Header>
    <soap:Body>
        <wsd:Probe>
            <wsd:Types>dn:NetworkVideoTransmitter</wsd:Types>
        </wsd:Probe>
    </soap:Body>
</soap:Envelope>"""

# ONVIF GetCapabilities request
GET_CAPABILITIES = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
    <soap:Body>
        <tds:GetCapabilities>
            <tds:Category>All</tds:Category>
        </tds:GetCapabilities>
    </soap:Body>
</soap:Envelope>"""

# ONVIF GetProfiles request
GET_PROFILES = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
    <soap:Body>
        <trt:GetProfiles/>
    </soap:Body>
</soap:Envelope>"""

# ONVIF GetSnapshotUri request
GET_SNAPSHOT_URI = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
    <soap:Body>
        <trt:GetSnapshotUri>
            <trt:ProfileToken>{profile_token}</trt:ProfileToken>
        </trt:GetSnapshotUri>
    </soap:Body>
</soap:Envelope>"""


def get_local_ips():
    """Get all local IP addresses from network interfaces"""
    local_ips = []

    # Method 1: Use socket to get primary IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ips.append(s.getsockname()[0])
        s.close()
    except:
        pass

    # Method 2: Parse ipconfig output on Windows
    try:
        result = subprocess.run(['ipconfig'], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split('\n'):
            if 'IPv4' in line:
                match = re.search(r'(\d+\.\d+\.\d+\.\d+)', line)
                if match:
                    ip = match.group(1)
                    if not ip.startswith('127.') and ip not in local_ips:
                        local_ips.append(ip)
    except:
        pass

    # Method 3: Get all IPs from hostname
    try:
        hostname = socket.gethostname()
        ips = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for ip_info in ips:
            ip = ip_info[4][0]
            if not ip.startswith('127.') and ip not in local_ips:
                local_ips.append(ip)
    except:
        pass

    if not local_ips:
        local_ips.append("192.168.1.100")  # Fallback

    print(f"Detected local IPs: {local_ips}")
    return local_ips


def ws_discovery_probe(timeout=5):
    """
    Send WS-Discovery multicast probe to find ONVIF devices
    Returns list of discovered device addresses
    """
    devices = []
    seen_ips = set()

    # WS-Discovery multicast address and port
    MULTICAST_IP = "239.255.255.250"
    MULTICAST_PORT = 3702

    # Create probe message with unique ID
    message_id = str(uuid.uuid4())
    probe_message = WS_DISCOVERY_PROBE.format(message_id=message_id)

    local_ips = get_local_ips()
    print(f"Local IPs found: {local_ips}")

    for local_ip in local_ips:
        try:
            # Create UDP socket for this interface
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)

            # Bind to specific interface
            sock.bind((local_ip, 0))
            sock.settimeout(timeout)

            print(f"Sending WS-Discovery probe from {local_ip}...")

            # Send probe to multicast address
            sock.sendto(probe_message.encode('utf-8'), (MULTICAST_IP, MULTICAST_PORT))

            # Collect responses
            start_time = __import__('time').time()
            while __import__('time').time() - start_time < timeout:
                try:
                    data, addr = sock.recvfrom(65535)
                    response = data.decode('utf-8', errors='ignore')

                    print(f"Got response from {addr[0]}")

                    # Extract XAddrs from response
                    xaddrs = extract_xaddrs(response)
                    for xaddr in xaddrs:
                        if addr[0] not in seen_ips:
                            seen_ips.add(addr[0])
                            device_info = {
                                'ip': addr[0],
                                'xaddr': xaddr,
                                'raw_response': response
                            }
                            devices.append(device_info)
                            print(f"Found device: {addr[0]} -> {xaddr}")

                except socket.timeout:
                    break
                except Exception as e:
                    print(f"Receive error: {e}")
                    break

        except Exception as e:
            print(f"Discovery error on {local_ip}: {e}")
        finally:
            try:
                sock.close()
            except:
                pass

    return devices


def extract_xaddrs(xml_response):
    """Extract XAddrs (service endpoints) from WS-Discovery response"""
    xaddrs = []

    # Try regex pattern matching for XAddrs
    pattern = r'<[^:]*:?XAddrs[^>]*>([^<]+)</[^:]*:?XAddrs>'
    matches = re.findall(pattern, xml_response)

    for match in matches:
        # XAddrs can contain multiple URLs separated by spaces
        urls = match.strip().split()
        for url in urls:
            if url.startswith('http'):
                xaddrs.append(url)

    return xaddrs


def create_wsse_header(username, password):
    """Create WS-Security header for ONVIF authentication"""
    import hashlib
    import datetime

    nonce = uuid.uuid4().bytes
    created = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')

    # Calculate digest
    digest_input = nonce + created.encode('utf-8') + password.encode('utf-8')
    digest = base64.b64encode(hashlib.sha1(digest_input).digest()).decode('utf-8')
    nonce_b64 = base64.b64encode(nonce).decode('utf-8')

    return f"""
    <soap:Header>
        <wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
                       xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
            <wsse:UsernameToken>
                <wsse:Username>{username}</wsse:Username>
                <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password>
                <wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</wsse:Nonce>
                <wsu:Created>{created}</wsu:Created>
            </wsse:UsernameToken>
        </wsse:Security>
    </soap:Header>"""


def get_media_service_url(device_url, username, password):
    """Get the media service URL from device capabilities"""
    try:
        # Add WSSE header to request
        headers = {'Content-Type': 'application/soap+xml; charset=utf-8'}

        response = requests.post(
            device_url,
            data=GET_CAPABILITIES,
            headers=headers,
            auth=HTTPDigestAuth(username, password),
            timeout=10
        )

        # Parse response to find media service URL
        pattern = r'<[^:]*:?Media[^>]*>.*?<[^:]*:?XAddr[^>]*>([^<]+)</[^:]*:?XAddr>'
        match = re.search(pattern, response.text, re.DOTALL)

        if match:
            return match.group(1)

        # Fallback: construct media URL from device URL
        base_url = re.match(r'(https?://[^/]+)', device_url)
        if base_url:
            return f"{base_url.group(1)}/onvif/media_service"

    except Exception as e:
        print(f"Error getting media service: {e}")

    return None


def get_profile_token(media_url, username, password):
    """Get first available profile token from camera"""
    try:
        headers = {'Content-Type': 'application/soap+xml; charset=utf-8'}

        response = requests.post(
            media_url,
            data=GET_PROFILES,
            headers=headers,
            auth=HTTPDigestAuth(username, password),
            timeout=10
        )

        # Extract first profile token
        pattern = r'<[^:]*:?Profiles[^>]*token="([^"]+)"'
        match = re.search(pattern, response.text)

        if match:
            return match.group(1)

    except Exception as e:
        print(f"Error getting profiles: {e}")

    return None


def get_snapshot_uri(media_url, profile_token, username, password):
    """Get snapshot URI for a profile"""
    try:
        headers = {'Content-Type': 'application/soap+xml; charset=utf-8'}
        request_body = GET_SNAPSHOT_URI.format(profile_token=profile_token)

        response = requests.post(
            media_url,
            data=request_body,
            headers=headers,
            auth=HTTPDigestAuth(username, password),
            timeout=10
        )

        # Extract snapshot URI
        pattern = r'<[^:]*:?Uri[^>]*>([^<]+)</[^:]*:?Uri>'
        match = re.search(pattern, response.text)

        if match:
            return match.group(1)

    except Exception as e:
        print(f"Error getting snapshot URI: {e}")

    return None


def fetch_snapshot(snapshot_url, username, password):
    """Fetch actual snapshot image from camera - try multiple auth methods"""
    auth_methods = [
        HTTPDigestAuth(username, password),
        HTTPBasicAuth(username, password),
        None  # No auth
    ]

    for auth in auth_methods:
        try:
            response = requests.get(
                snapshot_url,
                auth=auth,
                timeout=15,
                stream=True,
                verify=False
            )

            if response.status_code == 200 and len(response.content) > 1000:
                # Convert to base64 for embedding in HTML
                img_data = base64.b64encode(response.content).decode('utf-8')
                content_type = response.headers.get('Content-Type', 'image/jpeg')
                return f"data:{content_type};base64,{img_data}"

        except Exception as e:
            print(f"Snapshot fetch error with auth {type(auth)}: {e}")
            continue

    return None


def discover_and_snapshot(username, password, timeout=5):
    """Main function to discover cameras and get snapshots"""
    results = []

    # Step 1: Discover devices
    print("Starting WS-Discovery probe...")
    devices = ws_discovery_probe(timeout=timeout)
    print(f"Found {len(devices)} devices")

    if not devices:
        return results

    # Step 2: Process each device in parallel
    def process_device(device):
        device_result = {
            'ip': device['ip'],
            'xaddr': device['xaddr'],
            'status': 'unknown',
            'snapshot': None,
            'error': None
        }

        try:
            # Get media service URL
            media_url = get_media_service_url(device['xaddr'], username, password)
            if not media_url:
                device_result['status'] = 'error'
                device_result['error'] = 'Could not get media service URL'
                return device_result

            # Get profile token
            profile_token = get_profile_token(media_url, username, password)
            if not profile_token:
                device_result['status'] = 'error'
                device_result['error'] = 'Could not get profile token'
                return device_result

            # Get snapshot URI
            snapshot_uri = get_snapshot_uri(media_url, profile_token, username, password)
            if not snapshot_uri:
                device_result['status'] = 'error'
                device_result['error'] = 'Could not get snapshot URI'
                return device_result

            # Fetch snapshot
            snapshot = fetch_snapshot(snapshot_uri, username, password)
            if snapshot:
                device_result['status'] = 'online'
                device_result['snapshot'] = snapshot
            else:
                device_result['status'] = 'error'
                device_result['error'] = 'Could not fetch snapshot'

        except Exception as e:
            device_result['status'] = 'error'
            device_result['error'] = str(e)

        return device_result

    # Process devices in parallel
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(process_device, device): device for device in devices}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)

    return results


@app.route('/')
def index():
    """Render main page"""
    return render_template('index.html')


@app.route('/discover', methods=['POST'])
def discover():
    """API endpoint to discover cameras and get snapshots"""
    data = request.get_json()
    username = data.get('username', 'admin')
    password = data.get('password', 'admin')
    timeout = data.get('timeout', 5)

    results = discover_and_snapshot(username, password, timeout)

    return jsonify({
        'success': True,
        'cameras': results,
        'total': len(results)
    })


@app.route('/scan-range', methods=['POST'])
def scan_range():
    """Scan a specific IP range for ONVIF cameras"""
    data = request.get_json()
    username = data.get('username', 'admin')
    password = data.get('password', 'admin')
    ip_range = data.get('ip_range', '192.168.1')  # e.g., "192.168.1"
    start = data.get('start', 1)
    end = data.get('end', 254)

    results = []
    found_ips = set()

    # Common ONVIF and camera ports
    common_ports = [80, 8080, 554, 8000, 8899, 81, 8081, 37777, 34567]

    # Common snapshot URLs for different camera brands
    snapshot_paths = [
        "/cgi-bin/snapshot.cgi",
        "/snap.jpg",
        "/snapshot.jpg",
        "/image/jpeg.cgi",
        "/jpg/image.jpg",
        "/ISAPI/Streaming/channels/101/picture",  # Hikvision
        "/cgi-bin/api.cgi?cmd=Snap&channel=0",    # Reolink
        "/webcapture.jpg?command=snap&channel=1", # Dahua
        "/onvif-http/snapshot",
        "/capture/snapshot.cgi",
        "/image.jpg",
        "/video.jpg",
        "/Streaming/channels/1/picture",
        "/tmpfs/auto.jpg",
        "/cgi-bin/images_cgi?channel=0"
    ]

    def check_camera(ip):
        # First try common snapshot URLs directly (faster)
        for port in [80, 8080, 554, 8000]:
            for path in snapshot_paths:
                try:
                    url = f"http://{ip}:{port}{path}"
                    for auth in [HTTPDigestAuth(username, password), HTTPBasicAuth(username, password), None]:
                        try:
                            response = requests.get(url, auth=auth, timeout=2, verify=False)
                            if response.status_code == 200 and len(response.content) > 1000:
                                # Check if it's actually an image
                                content_type = response.headers.get('Content-Type', '')
                                if 'image' in content_type or response.content[:3] == b'\xff\xd8\xff':
                                    img_data = base64.b64encode(response.content).decode('utf-8')
                                    return {
                                        'ip': ip,
                                        'xaddr': url,
                                        'status': 'online',
                                        'snapshot': f"data:image/jpeg;base64,{img_data}",
                                        'port': port
                                    }
                        except:
                            continue
                except:
                    continue

        # Then try ONVIF discovery
        for port in common_ports:
            try:
                device_url = f"http://{ip}:{port}/onvif/device_service"

                response = requests.post(
                    device_url,
                    data=GET_CAPABILITIES,
                    headers={'Content-Type': 'application/soap+xml'},
                    auth=HTTPDigestAuth(username, password),
                    timeout=3,
                    verify=False
                )

                if response.status_code in [200, 401, 403] or 'Capabilities' in response.text or 'ONVIF' in response.text:
                    # Found ONVIF device
                    print(f"Found ONVIF at {ip}:{port}")

                    # Try to get snapshot via ONVIF
                    media_url = get_media_service_url(device_url, username, password)
                    if media_url:
                        profile_token = get_profile_token(media_url, username, password)
                        if profile_token:
                            snapshot_uri = get_snapshot_uri(media_url, profile_token, username, password)
                            if snapshot_uri:
                                snapshot = fetch_snapshot(snapshot_uri, username, password)
                                if snapshot:
                                    return {
                                        'ip': ip,
                                        'xaddr': device_url,
                                        'status': 'online',
                                        'snapshot': snapshot,
                                        'port': port
                                    }

                    return {
                        'ip': ip,
                        'xaddr': device_url,
                        'status': 'found',
                        'snapshot': None,
                        'port': port
                    }

            except Exception:
                continue

        return None

    # Scan in parallel
    with ThreadPoolExecutor(max_workers=100) as executor:
        ips = [f"{ip_range}.{i}" for i in range(start, end + 1)]
        futures = {executor.submit(check_camera, ip): ip for ip in ips}

        for future in as_completed(futures):
            result = future.result()
            if result and result['ip'] not in found_ips:
                found_ips.add(result['ip'])
                results.append(result)
                print(f"Camera found: {result['ip']}")

    return jsonify({
        'success': True,
        'cameras': results,
        'total': len(results)
    })


@app.route('/test-ip', methods=['POST'])
def test_single_ip():
    """Test a single IP address for camera"""
    data = request.get_json()
    ip = data.get('ip', '')
    username = data.get('username', 'admin')
    password = data.get('password', '')

    results = []

    # XMeye and common Chinese camera ports and paths
    test_configs = [
        # XMeye specific
        (80, "/webcapture.jpg?command=snap&channel=0"),
        (80, "/cgi-bin/snapshot.cgi?chn=0&u={user}&p={pwd}"),
        (80, "/snapshot.cgi?user={user}&pwd={pwd}&chn=0"),
        (80, "/snap.jpg?chn=0"),
        (80, "/tmpfs/auto.jpg"),
        (80, "/tmpfs/snap.jpg"),
        (80, "/images/snapshot.jpg"),
        (80, "/cgi-bin/hi3510/snap.cgi?&-getstream&-chn=0"),
        (80, "/capture/0/snap.jpg"),

        # Port 8080
        (8080, "/webcapture.jpg?command=snap&channel=0"),
        (8080, "/snapshot.cgi"),
        (8080, "/snap.jpg"),

        # Port 34567 web (some XMeye)
        (34567, "/snap.jpg"),

        # Generic
        (80, "/cgi-bin/snapshot.cgi"),
        (80, "/snap.jpg"),
        (80, "/image.jpg"),
        (80, "/jpg/image.jpg"),
        (80, "/Streaming/channels/1/picture"),
        (80, "/onvif-http/snapshot"),

        # RTSP snapshot via HTTP
        (554, "/snapshot"),
    ]

    for port, path in test_configs:
        # Replace placeholders
        url_path = path.replace("{user}", username).replace("{pwd}", password)
        url = f"http://{ip}:{port}{url_path}"

        for auth in [None, HTTPBasicAuth(username, password), HTTPDigestAuth(username, password)]:
            try:
                print(f"Testing: {url}")
                response = requests.get(url, auth=auth, timeout=3, verify=False)

                result = {
                    'url': url,
                    'status_code': response.status_code,
                    'content_length': len(response.content),
                    'content_type': response.headers.get('Content-Type', ''),
                    'auth': str(type(auth).__name__) if auth else 'None'
                }

                # Check if it's an image
                if response.status_code == 200:
                    content = response.content
                    is_jpeg = content[:3] == b'\xff\xd8\xff'
                    is_png = content[:4] == b'\x89PNG'

                    if is_jpeg or is_png or 'image' in result['content_type']:
                        result['is_image'] = True
                        result['snapshot'] = f"data:image/jpeg;base64,{base64.b64encode(content).decode('utf-8')}"
                        results.append(result)
                        print(f"SUCCESS: {url}")
                        # Return immediately on success
                        return jsonify({
                            'success': True,
                            'camera': {
                                'ip': ip,
                                'xaddr': url,
                                'status': 'online',
                                'snapshot': result['snapshot'],
                                'port': port
                            },
                            'all_results': results
                        })
                    else:
                        result['is_image'] = False

                results.append(result)

            except requests.exceptions.ConnectTimeout:
                results.append({'url': url, 'error': 'Connection timeout'})
            except requests.exceptions.ConnectionError:
                results.append({'url': url, 'error': 'Connection refused'})
            except Exception as e:
                results.append({'url': url, 'error': str(e)})

    # Also try ONVIF
    try:
        device_url = f"http://{ip}:80/onvif/device_service"
        response = requests.post(
            device_url,
            data=GET_CAPABILITIES,
            headers={'Content-Type': 'application/soap+xml'},
            auth=HTTPDigestAuth(username, password),
            timeout=5,
            verify=False
        )
        results.append({
            'url': device_url,
            'type': 'ONVIF',
            'status_code': response.status_code,
            'response_preview': response.text[:500]
        })
    except Exception as e:
        results.append({'url': device_url, 'type': 'ONVIF', 'error': str(e)})

    return jsonify({
        'success': False,
        'message': 'No snapshot found',
        'tested': len(results),
        'results': results[:20]  # Limit response size
    })


@app.route('/network-info', methods=['GET'])
def network_info():
    """Get local network information for auto-fill"""
    local_ips = get_local_ips()
    networks = []

    for ip in local_ips:
        parts = ip.rsplit('.', 1)
        if len(parts) == 2:
            networks.append({
                'ip': ip,
                'base': parts[0]
            })

    return jsonify({
        'success': True,
        'networks': networks
    })


if __name__ == '__main__':
    print("=" * 50)
    print("Pgak Cameras Discovery Tool")
    print("=" * 50)
    print("Open http://localhost:5000 in your browser")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5001, debug=True)
