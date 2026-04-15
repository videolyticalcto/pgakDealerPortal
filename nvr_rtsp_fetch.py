import base64
import datetime as dt
import hashlib
import os
import re
import xml.etree.ElementTree as ET
from typing import Dict

import requests
from requests.auth import HTTPDigestAuth
from requests.exceptions import ConnectTimeout, ConnectionError, ReadTimeout


def _build_onvif_auth(password: str) -> tuple[str, str, str]:
    nonce = os.urandom(16)
    nonce_b64 = base64.b64encode(nonce).decode()
    created = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest_input = nonce + created.encode() + password.encode()
    password_digest = base64.b64encode(hashlib.sha1(digest_input).digest()).decode()
    return nonce_b64, created, password_digest


def _embed_rtsp_credentials(rtsp_url: str, username: str, password: str) -> str:
    if "@" in rtsp_url:
        return rtsp_url
    return rtsp_url.replace("rtsp://", f"rtsp://{username}:{password}@", 1)


def discover_prama_isapi_cameras(ip: str, port: int, username: str, password: str) -> Dict:
    result = {
        "success": False,
        "manufacturer": "Prama",
        "model": None,
        "cameras": [],
        "error": None,
        "rtsp_port": 554,
    }

    try:
        session = requests.Session()
        session.verify = False
        auth = HTTPDigestAuth(username, password)
        base_url = f"http://{ip}:{port}"

        try:
            resp = session.get(f"{base_url}/ISAPI/System/deviceInfo", auth=auth, timeout=10)
            if resp.ok:
                model_match = re.search(r"<model>([^<]+)</model>", resp.text)
                if model_match:
                    result["model"] = model_match.group(1)
        except Exception:
            pass

        rtsp_port = 554
        try:
            resp = session.get(f"{base_url}/ISAPI/Security/adminAccesses", auth=auth, timeout=10)
            if resp.ok:
                rtsp_match = re.search(r"<protocol>RTSP</protocol>\s*<portNo>(\d+)</portNo>", resp.text)
                if rtsp_match:
                    rtsp_port = int(rtsp_match.group(1))
                    result["rtsp_port"] = rtsp_port
        except Exception:
            pass

        cameras_found = []
        try:
            resp = session.get(f"{base_url}/ISAPI/ContentMgmt/InputProxy/channels", auth=auth, timeout=15)
            if resp.ok and "<InputProxyChannelList" in resp.text:
                root = ET.fromstring(resp.text)
                ns = {"ns": "http://www.isapi.org/ver20/XMLSchema"}

                for channel_elem in root.findall(".//ns:InputProxyChannel", ns):
                    try:
                        ch_id = channel_elem.find("ns:id", ns)
                        ch_name = channel_elem.find("ns:name", ns)
                        source_elem = channel_elem.find(".//ns:sourceInputPortDescriptor", ns)
                        channel_num = int(ch_id.text) if ch_id is not None else 0
                        label = ch_name.text if ch_name is not None else f"Channel {channel_num}"

                        cam_ip = ""
                        cam_model = ""
                        if source_elem is not None:
                            ip_elem = source_elem.find("ns:ipAddress", ns)
                            model_elem = source_elem.find("ns:model", ns)
                            if ip_elem is not None:
                                cam_ip = ip_elem.text or ""
                            if model_elem is not None:
                                cam_model = model_elem.text or ""

                        if cam_model:
                            label = cam_model

                        main_stream_id = channel_num * 100 + 1
                        sub_stream_id = channel_num * 100 + 2
                        cameras_found.append({
                            "channel": channel_num,
                            "label": label,
                            "address": cam_ip,
                            "model": cam_model,
                            "rtsp_main": f"rtsp://{username}:{password}@{ip}:{rtsp_port}/Streaming/Channels/{main_stream_id}",
                            "rtsp_sub": f"rtsp://{username}:{password}@{ip}:{rtsp_port}/Streaming/Channels/{sub_stream_id}",
                            "resolution": "",
                            "codec": "",
                        })
                    except Exception:
                        continue
        except Exception:
            pass

        if not cameras_found:
            try:
                resp = session.get(f"{base_url}/ISAPI/Streaming/channels", auth=auth, timeout=15)
                if resp.ok and "<StreamingChannelList" in resp.text:
                    channel_ids = re.findall(r"<id>(\d+)</id>", resp.text)
                    seen_channels = set()
                    for ch_id_str in channel_ids:
                        ch_num = int(ch_id_str) // 100
                        stream_type = int(ch_id_str) % 100
                        if ch_num in seen_channels or stream_type != 1:
                            continue
                        seen_channels.add(ch_num)
                        cameras_found.append({
                            "channel": ch_num,
                            "label": f"Channel {ch_num}",
                            "address": "",
                            "model": "",
                            "rtsp_main": f"rtsp://{username}:{password}@{ip}:{rtsp_port}/Streaming/Channels/{ch_num}01",
                            "rtsp_sub": f"rtsp://{username}:{password}@{ip}:{rtsp_port}/Streaming/Channels/{ch_num}02",
                            "resolution": "",
                            "codec": "",
                        })
            except Exception:
                pass

        for cam in cameras_found:
            try:
                ch_id_val = cam["channel"] * 100 + 1
                resp = session.get(f"{base_url}/ISAPI/Streaming/channels/{ch_id_val}", auth=auth, timeout=1)
                if resp.ok:
                    width_match = re.search(r"<videoResolutionWidth>(\d+)</videoResolutionWidth>", resp.text)
                    height_match = re.search(r"<videoResolutionHeight>(\d+)</videoResolutionHeight>", resp.text)
                    codec_match = re.search(r"<videoCodecType>([^<]+)</videoCodecType>", resp.text)
                    if width_match and height_match:
                        cam["resolution"] = f"{width_match.group(1)}x{height_match.group(1)}"
                    if codec_match:
                        cam["codec"] = codec_match.group(1)
            except Exception:
                pass

        result["cameras"] = cameras_found
        result["success"] = len(cameras_found) > 0
    except Exception as exc:
        result["error"] = str(exc)

    return result


def discover_onvif_cameras(ip: str, port: int, username: str, password: str, rtsp_port: int = 554) -> Dict:
    print('discover_onvif_cameras', ip, port, username, password, rtsp_port)
    result = {
        "success": False,
        "manufacturer": None,
        "model": None,
        "cameras": [],
        "error": None,
        "error_details": [],
        "rtsp_port": rtsp_port,
    }

    try:
        session = requests.Session()
        session.verify = False
        auth = HTTPDigestAuth(username, password)
        connection_tested = False
        auth_tested = False
        nvr_detected = False

        try:
            isapi_resp = session.get(f"http://{ip}:{port}/ISAPI/System/deviceInfo", auth=auth, timeout=5)
            connection_tested = True
            if isapi_resp.status_code == 401:
                result["error"] = "Authentication failed - wrong username or password"
                result["error_details"].append("ISAPI returned 401 Unauthorized")
                return result

            if isapi_resp.ok and "<DeviceInfo" in isapi_resp.text:
                auth_tested = True
                nvr_detected = True
                model_match = re.search(r"<model>([^<]+)</model>", isapi_resp.text)
                if model_match:
                    model = model_match.group(1)
                    if model.startswith("PT-") or "HiK" in isapi_resp.text or "hikvision" in isapi_resp.text.lower():
                        return discover_prama_isapi_cameras(ip, port, username, password)
        except ConnectTimeout:
            result["error_details"].append(f"ISAPI connection timeout to {ip}:{port}")
        except ConnectionError as exc:
            message = str(exc)
            if "Connection refused" in message or "refused" in message.lower():
                result["error_details"].append(f"Connection refused at {ip}:{port} - check IP and port")
            elif "No route to host" in message:
                result["error_details"].append(f"No route to host {ip} - check if IP is correct")
            elif "Network is unreachable" in message:
                result["error_details"].append(f"Network unreachable for {ip}")
            else:
                result["error_details"].append(f"ISAPI connection error: {message[:100]}")
        except ReadTimeout:
            result["error_details"].append("ISAPI read timeout - NVR not responding")
        except Exception as exc:
            result["error_details"].append(f"ISAPI check failed: {str(exc)[:100]}")

        nonce_b64, created, password_digest = _build_onvif_auth(password)
        device_envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
            xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
            xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
  <s:Header><wsse:Security><wsse:UsernameToken><wsse:Username>{username}</wsse:Username><wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{password_digest}</wsse:Password><wsse:Nonce>{nonce_b64}</wsse:Nonce><wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security></s:Header>
  <s:Body><tds:GetDeviceInformation/></s:Body>
</s:Envelope>"""

        try:
            resp = requests.post(
                f"http://{ip}:{port}/onvif/device_service",
                data=device_envelope,
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                timeout=10,
            )
            connection_tested = True
            if resp.status_code == 401 or "NotAuthorized" in resp.text or "Authentication" in resp.text:
                if not auth_tested:
                    result["error"] = "Authentication failed - wrong username or password"
                    result["error_details"].append("ONVIF returned authentication error")
                    return result

            if resp.status_code == 200:
                nvr_detected = True
                mfr_match = re.search(r"<tds:Manufacturer>([^<]+)</tds:Manufacturer>", resp.text)
                model_match = re.search(r"<tds:Model>([^<]+)</tds:Model>", resp.text)
                if mfr_match:
                    result["manufacturer"] = mfr_match.group(1)
                if model_match:
                    result["model"] = model_match.group(1)
        except ConnectTimeout:
            if not connection_tested:
                result["error_details"].append(f"ONVIF connection timeout to {ip}:{port}")
        except ConnectionError as exc:
            if not connection_tested:
                message = str(exc)
                if "Connection refused" in message or "refused" in message.lower():
                    result["error"] = f"Connection refused - check if IP ({ip}) and port ({port}) are correct"
                    result["error_details"].append(f"Connection refused at {ip}:{port}")
                    return result
                if "No route to host" in message:
                    result["error"] = f"Cannot reach host {ip} - check if IP address is correct"
                    result["error_details"].append(f"No route to host {ip}")
                    return result
        except Exception as exc:
            result["error_details"].append(f"ONVIF device info error: {str(exc)[:100]}")

        cameras_found = []
        try:
            profiles_envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
            xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
            xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
  <s:Header><wsse:Security><wsse:UsernameToken><wsse:Username>{username}</wsse:Username><wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{password_digest}</wsse:Password><wsse:Nonce>{nonce_b64}</wsse:Nonce><wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security></s:Header>
  <s:Body><trt:GetProfiles/></s:Body>
</s:Envelope>"""

            profiles_resp = requests.post(
                f"http://{ip}:{port}/onvif/media_service",
                data=profiles_envelope,
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                timeout=10,
            )
            if profiles_resp.status_code == 200 and "Profiles" in profiles_resp.text:
                profile_tokens = re.findall(r'<trt:Profiles[^>]*token="([^"]+)"', profiles_resp.text)
                if not profile_tokens:
                    profile_tokens = re.findall(r'token="([^"]+)"', profiles_resp.text)

                channel_num = 0
                for token in profile_tokens:
                    is_sub = any(part in token.lower() for part in ["sub", "_1", "profile_2", "profile2"])
                    if is_sub and channel_num == 0:
                        continue
                    channel_num += 1

                    stream_uri_envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
            xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
            xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
            xmlns:tt="http://www.onvif.org/ver10/schema">
  <s:Header><wsse:Security><wsse:UsernameToken><wsse:Username>{username}</wsse:Username><wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{password_digest}</wsse:Password><wsse:Nonce>{nonce_b64}</wsse:Nonce><wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security></s:Header>
  <s:Body><trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream><tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup><trt:ProfileToken>{token}</trt:ProfileToken></trt:GetStreamUri></s:Body>
</s:Envelope>"""

                    try:
                        uri_resp = requests.post(
                            f"http://{ip}:{port}/onvif/media_service",
                            data=stream_uri_envelope,
                            headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                            timeout=5,
                        )
                        if uri_resp.status_code != 200:
                            continue
                        uri_match = re.search(r"<tt:Uri>([^<]+)</tt:Uri>", uri_resp.text)
                        if not uri_match:
                            continue
                        rtsp_url = _embed_rtsp_credentials(uri_match.group(1), username, password)
                        existing = next((cam for cam in cameras_found if cam["channel"] == channel_num), None)
                        if existing:
                            if is_sub:
                                existing["rtsp_sub"] = rtsp_url
                            else:
                                existing["rtsp_main"] = rtsp_url
                        else:
                            cameras_found.append({
                                "channel": channel_num,
                                "label": f"Channel {channel_num}",
                                "address": "",
                                "model": "",
                                "rtsp_main": "" if is_sub else rtsp_url,
                                "rtsp_sub": rtsp_url if is_sub else "",
                            })
                    except Exception:
                        continue
        except Exception:
            pass

        if not cameras_found:
            try:
                api_resp = session.get(
                    f"http://{ip}:{port}/cgi-bin/configManager.cgi?action=getConfig&name=RemoteDevice",
                    auth=auth,
                    timeout=10,
                    allow_redirects=True,
                )
                connection_tested = True
                if api_resp.status_code == 401:
                    result["error"] = "Authentication failed - wrong username or password"
                    result["error_details"].append("HTTP API returned 401 Unauthorized")
                    return result

                if api_resp.status_code == 200 and "Enable=true" in api_resp.text:
                    nvr_detected = True
                    camera_info = {}
                    for line in api_resp.text.splitlines():
                        if ".Enable=true" in line:
                            match = re.search(r"NETCAMERA_INFO_(\d+)\.Enable", line)
                            if match:
                                idx = match.group(1)
                                camera_info.setdefault(idx, {"enabled": True, "channel": int(idx) + 1})
                        if ".Address=" in line and "192.168.0.0" not in line:
                            match = re.search(r"NETCAMERA_INFO_(\d+)\.Address=(.+)", line)
                            if match:
                                idx, addr = match.group(1), match.group(2).strip()
                                camera_info.setdefault(idx, {"channel": int(idx) + 1})
                                camera_info[idx]["address"] = addr
                        if ".Name=" in line:
                            match = re.search(r"NETCAMERA_INFO_(\d+)\.Name=(.+)", line)
                            if match:
                                idx, name_val = match.group(1), match.group(2).strip()
                                if name_val and idx in camera_info:
                                    camera_info[idx]["name"] = name_val
                        if ".VideoInputs[0].Name=" in line:
                            match = re.search(r"NETCAMERA_INFO_(\d+)\.VideoInputs\[0\]\.Name=(.+)", line)
                            if match:
                                idx, vname = match.group(1), match.group(2).strip()
                                if vname and idx in camera_info:
                                    camera_info[idx]["video_name"] = vname

                    for idx, info in camera_info.items():
                        if not info.get("enabled") and not info.get("address"):
                            continue
                        channel = info.get("channel", int(idx) + 1)
                        label = info.get("video_name") or info.get("name") or f"Channel {channel}"
                        cameras_found.append({
                            "channel": channel,
                            "label": label,
                            "address": info.get("address", ""),
                            "model": info.get("name", ""),
                            "rtsp_main": f"rtsp://{username}:{password}@{ip}:{rtsp_port}/cam/realmonitor?channel={channel}&subtype=0",
                            "rtsp_sub": f"rtsp://{username}:{password}@{ip}:{rtsp_port}/cam/realmonitor?channel={channel}&subtype=1",
                        })
            except ConnectTimeout:
                result["error_details"].append("HTTP API connection timeout")
            except ConnectionError as exc:
                if not connection_tested and ("Connection refused" in str(exc) or "refused" in str(exc).lower()):
                    result["error"] = f"Connection refused - check if IP ({ip}) and port ({port}) are correct"
                    return result
                result["error_details"].append(f"HTTP API connection error: {str(exc)[:100]}")
            except Exception as exc:
                result["error_details"].append(f"HTTP API error: {str(exc)[:100]}")

        if not cameras_found:
            if not connection_tested:
                result["error"] = f"Cannot connect to {ip}:{port} - check IP address and port"
                if result["error_details"]:
                    result["error"] += f" ({'; '.join(result['error_details'][:2])})"
                return result
            if not nvr_detected:
                result["error"] = f"Connected to {ip}:{port} but could not detect NVR - may be wrong port or unsupported device"
                if result["error_details"]:
                    result["error"] += f" ({'; '.join(result['error_details'][:2])})"
                return result

            mfr = (result.get("manufacturer") or "").upper()
            use_hikvision_format = any(name in mfr for name in ["CPPLUS", "CP PLUS", "HIKVISION", "HIK"])
            for ch in range(1, 17):
                if use_hikvision_format:
                    main_url = f"rtsp://{username}:{password}@{ip}:{rtsp_port}/Streaming/Channels/{ch}01"
                    sub_url = f"rtsp://{username}:{password}@{ip}:{rtsp_port}/Streaming/Channels/{ch}02"
                else:
                    main_url = f"rtsp://{username}:{password}@{ip}:{rtsp_port}/cam/realmonitor?channel={ch}&subtype=0"
                    sub_url = f"rtsp://{username}:{password}@{ip}:{rtsp_port}/cam/realmonitor?channel={ch}&subtype=1"
                cameras_found.append({
                    "channel": ch,
                    "label": f"Channel {ch}",
                    "address": "",
                    "model": "",
                    "rtsp_main": main_url,
                    "rtsp_sub": sub_url,
                })

        result["cameras"] = cameras_found
        result["success"] = True
    except ConnectTimeout:
        result["error"] = f"Connection timeout - cannot reach {ip}:{port}"
    except ConnectionError as exc:
        message = str(exc)
        if "Connection refused" in message or "refused" in message.lower():
            result["error"] = f"Connection refused - check if IP ({ip}) and port ({port}) are correct"
        elif "No route to host" in message:
            result["error"] = f"Cannot reach host {ip} - check if IP address is correct"
        elif "Network is unreachable" in message:
            result["error"] = "Network unreachable - check your network connection"
        else:
            result["error"] = f"Connection error: {message[:100]}"
    except Exception as exc:
        result["error"] = f"Discovery failed: {str(exc)[:100]}"

    return result



if __name__ == "__main__":
    res=discover_onvif_cameras("202.160.134.130", "8082", "admin", "Central@0001", "1082")
    print(res)