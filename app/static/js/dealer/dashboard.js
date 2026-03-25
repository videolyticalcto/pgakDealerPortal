        const API_BASE = '/api';
        const qrVideo = document.getElementById("qrVideo");
        const qrCanvas = document.createElement("canvas");
        const qrCtx = qrCanvas.getContext("2d", { willReadFrequently: true });
        const qrStatus = document.getElementById("qrStatus");
        const macHighlight = document.getElementById("macAddress");
        const macValue = document.getElementById("macAddressValue");
        const validationMessage = document.getElementById("validationMessage");
        const validationIcon = document.getElementById("validationIcon");
        const validationText = document.getElementById("validationText");
        const successMessage = document.getElementById("successMessage");
        const successText = document.getElementById("successText");
        const errorMessage = document.getElementById("errorMessage");
        const errorText = document.getElementById("errorText");
        const nextStepContainer = document.getElementById("nextStepContainer");

        let stream = null;
        let scanning = false;
        let rafId = null;
        let jsQRReady = false;
        let qrScanInProgress = false;
        let scanSuccessful = false;
        let discoveredDevices = [];
        let selectedOnvifSerial = null;
        let currentDevicesFilter = 'online'; // Default to online only
        let allDevicesData = {}; // Store all devices data
        let filteredDevicesData = {}; // Store filtered devices data


        const checkJsQR = setInterval(() => {
            if (typeof jsQR !== 'undefined') {
                jsQRReady = true;
                clearInterval(checkJsQR);
            }
        }, 100);

        let issueState = {
            currentType: null,
            currentUserType: null,
            currentUserId: null,
            currentUserData: null,
            lastScannedSerial: null
        };

        // ===== MODAL FUNCTIONS =====
        function openCredentialsModal() {
            document.getElementById('credentialsModal').classList.add('show');
            document.getElementById('modalUsername').focus();
        }

        function closeCredentialsModal() {
            document.getElementById('credentialsModal').classList.remove('show');
        }

        async function saveDevicesToAnalyticsAPI(devices, userId, userName, password) {
            try {
                console.log('📤 Starting to save devices to Analytics API...');
                console.log('Devices found:', devices.length);
               
                const rtspUrls = [];
               
                devices.forEach(device => {
                    console.log('Processing device:', device.device_info);
                    // Try rtsp_profiles array first
                    if (device.rtsp_profiles && Array.isArray(device.rtsp_profiles)) {
                        device.rtsp_profiles.forEach(profile => {
                            if (profile.rtsp_url) rtspUrls.push(profile.rtsp_url);
                        });
                    }
                    // Try cameras array
                    if (device.cameras && Array.isArray(device.cameras)) {
                        device.cameras.forEach(cam => {
                            const url = cam.rtsp_url || cam.rtsp;
                            if (url && !rtspUrls.includes(url)) rtspUrls.push(url);
                        });
                    }
                    // Try direct rtsp fields
                    const directRtsp = device.device_rtsp || device.rtsp || device.rtsp_url
                        || (device.device_info && device.device_info.rtsp);
                    if (directRtsp && !rtspUrls.includes(directRtsp)) rtspUrls.push(directRtsp);
                });

                // ✅ Validate all required fields before sending
                console.log('🔍 DEBUG payload values:',
                    '| user_id:', userId,
                    '| user_name:', userName,
                    '| password length:', (password||'').length,
                    '| rtsp_urls count:', rtspUrls.length,
                    '| rtsp_urls:', rtspUrls
                );

                if (!userId) {
                    anToast('❌ Customer select nahi kiya. Analytics modal mein customer choose karein.', '#991b1b');
                    return { success: false, message: 'user_id missing' };
                }
                if (!password) {
                    anToast('❌ Camera password missing. Pehle scan karein.', '#991b1b');
                    return { success: false, message: 'password missing' };
                }
                if (!rtspUrls || rtspUrls.length === 0) {
                    anToast('❌ Koi RTSP URL nahi mili devices mein. Check scan results.', '#991b1b');
                    return { success: false, message: 'rtsp_urls empty' };
                }

                const payload = {
                    user_id: String(userId),
                    user_name: userName || String(userId),
                    password: password,
                    rtsp_urls: rtspUrls
                };
                console.log('📤 Sending to Analytics API:', payload);
                const response = await fetch((window.PGAK_CONFIG && window.PGAK_CONFIG.EXTERNAL_DEVICES2) || 'https://api.pgak.co.in/analytics/devices2', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json'
                    },
                    body: JSON.stringify(payload),
                    mode: 'cors'
                });
                const result = await response.json();
               
                console.log('Analytics API Response:', result);
               
                if (response.ok) {
                    console.log('✅ Devices saved to Analytics API successfully');
                    anToast('✅ Devices successfully saved to analytics!', '#065f46');
                    return { success: true, message: 'Devices saved to analytics', data: result };
                } else {
                    console.error('❌ Analytics API error:', result);
                    const errMsg = result.message || 'Failed to save to analytics API';
                    anToast('❌ ' + errMsg, '#991b1b');
                    return { success: false, message: errMsg, data: result };
                }
            } catch (err) {
                console.error('❌ Error saving to Analytics API:', err);
                anToast('❌ Network error: ' + err.message, '#991b1b');
                return { success: false, message: 'Network error: ' + err.message, error: err };
            }
        }

        function toggleModalPassword() {
            const input = document.getElementById('modalPassword');
            const btn = event.target.closest('.toggle-password');
            const icon = btn.querySelector('i');
            if (input.type === 'password') {
                input.type = 'text';
                icon.classList.replace('fa-eye', 'fa-eye-slash');
            } else {
                input.type = 'password';
                icon.classList.replace('fa-eye-slash', 'fa-eye');
            }
        }
        const scanBtn = document.getElementById('scanBtn'); // modal scan button OR main button

        // OPTIONAL: if your UI also has a status line / count line (current code)
        // you can keep these, but code checks safely if they exist.
        const statusLine = document.getElementById("statusLine");
        const countLine  = document.getElementById("countLine");
        function setStatus(msg) {
        if (statusLine) statusLine.textContent = "Status: " + msg;
        }


        async function startNetworkScan() {
        const username = document.getElementById('modalUsername')?.value?.trim()
                        || document.getElementById("username")?.value?.trim()
                        || "";

        const password = document.getElementById('modalPassword')?.value?.trim()
                        || document.getElementById("password")?.value?.trim()
                        || "";

        const scanBtnEl = document.getElementById('scanBtn'); 
        if (!username || !password) {
            alert('Please enter both username and password');
            setStatus("enter username + password");
            return;
        }

        // UI: disable button + show spinner
        if (scanBtnEl) {
            scanBtnEl.disabled = true;
            scanBtnEl.innerHTML = '<span class="spinner"></span> Please Wait Searching For Camera In Network...';
        }
        setStatus("scanning...");

        if (countLine) {
            countLine.textContent = "Found Devices: 0";
            countLine.classList.remove("ok", "bad");
        }

        try {
            console.log('🔍 Starting network scan with credentials...');
            console.log('   Username:', username);
            console.log('   Sending to: /api/scan');

            const customerId = issueState.currentUserId || null;
            console.log('   Customer ID:', customerId);

            const res = await fetch(`/api/scan`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password, customer_id: customerId })
            });

            if (!res.ok) {
                throw new Error(`HTTP ${res.status}: ${res.statusText}`);
            }

            const out = await res.json();
            console.log('📥 Network scan result:', out);
            console.log('   Response keys:', Object.keys(out));

            // ✅ FIXED: Handle both response formats
            // Format 1 (New): { status: "ok", count: N, devices: [...], source: "socket_hub_async" }
            // Format 2 (Old): { ok: true, merged_count: N, merged_devices: [...] }
            
            let devices = [];
            let foundCount = 0;
            let isSuccess = false;

            // Check new format first (status: "ok")
            if (out.status === "ok") {
                devices = out.devices || [];
                foundCount = out.count || devices.length || 0;
                isSuccess = true;
                console.log('   ✅ Using NEW format (status: "ok")');
                console.log('   Source:', out.source);
            } 
            // Check old format (ok: true)
            else if (out.ok === true) {
                devices = out.merged_devices || out.devices || [];
                foundCount = Number(out.merged_count ?? out.count ?? devices.length ?? 0);
                isSuccess = true;
                console.log('   ✅ Using OLD format (ok: true)');
            }
            // Error response
            else {
                isSuccess = false;
                console.log('   ❌ Scan failed or error response');
            }

            console.log('   Found devices count:', foundCount);
            console.log('   Devices array length:', devices.length);
            
            // ✅ Log first device for debugging
            if (devices.length > 0) {
                console.log('   First device sample:', JSON.stringify(devices[0], null, 2));
            }

            if (isSuccess && foundCount > 0) {
                console.log('✅ Scan successful with', foundCount, 'devices');
                
                setStatus("scan completed");
                if (countLine) {
                    // Count devices with RTSP feeds (same filter as showDiscoveredDevicesInModal)
                    const rtspCount = devices.filter(d => d.rtsp_profiles && Array.isArray(d.rtsp_profiles) && d.rtsp_profiles.some(p => p.rtsp_url)).length;
                    countLine.textContent = "Found Devices: " + rtspCount + (rtspCount < foundCount ? ` (${foundCount - rtspCount} without RTSP hidden)` : "");
                    countLine.classList.add("ok");
                }

                if (typeof closeCredentialsModal === "function") {
                    closeCredentialsModal();
                }

                discoveredDevices = devices;
                showDiscoveredDevicesInModal(devices);

            } else {
                console.log('❌ Scan failed or no devices found');
                
                const msg = out.message || out.error || "No devices found";
                setStatus("scan failed: " + msg);
                
                if (countLine) {
                    countLine.textContent = "Found Devices: " + foundCount;
                    countLine.classList.add("bad");
                }
                
                let alertMsg = 'Scan failed: ' + msg;
                if (out.hint) {
                    alertMsg += '\n\nHint: ' + out.hint;
                }
                if (out.details) {
                    alertMsg += '\n\nDetails: ' + out.details;
                }
                
                alert(alertMsg);
            }

        } catch (err) {
            console.error('Error during network scan:', err);
            setStatus("scan error: " + err.message);
            if (countLine) {
            countLine.textContent = "Found Devices: 0";
            countLine.classList.add("bad");
            }
            alert('Error: ' + err.message);

        } finally {
            // restore button
            if (scanBtnEl) {
            scanBtnEl.disabled = false;
            scanBtnEl.innerHTML = '<i class="fas fa-search"></i> Scan Network';
            }
        }
        }


        // Load analytics first
        function showDiscoveredDevicesInModal(devices) {
            console.log("📱 Showing discovered devices...");
            
            // ✅ FILTER DEVICES: ONLY SHOW DEVICES WITH RTSP FEEDS
            const devicesWithRTSP = devices.filter(device => {
                const hasRtspProfiles = device.rtsp_profiles && Array.isArray(device.rtsp_profiles) && device.rtsp_profiles.length > 0;
                const hasRtspUrls = hasRtspProfiles && device.rtsp_profiles.some(profile => profile.rtsp_url);

                if (!hasRtspUrls) {
                    console.log("⏭️  Skipping device without RTSP feeds:", device.device_ip, "| rtsp_profiles:", JSON.stringify(device.rtsp_profiles));
                } else {
                    console.log("✅ Device has RTSP:", device.device_ip, "| profiles count:", device.rtsp_profiles.length);
                }
                return hasRtspUrls;
            });
            
            console.log(`✅ Filtered: ${devicesWithRTSP.length} device(s) with RTSP feeds out of ${devices.length} total devices`);
            
            // Store filtered devices and analytics data globally
            window.discoveredDevices = devicesWithRTSP || [];
            window.selectedDevicesWithAnalytics = [];
            window.analyticsDataCache = {};
            
            // Get user ID from issue state
            const userId = issueState.currentUserId;
            console.log("📌 User ID:", userId);
            
            macHighlight.classList.remove("show");
            validationMessage.classList.remove("show");
            nextStepContainer.classList.remove("show");

            // Create or get device list div
            let deviceListDiv = document.getElementById('discovered-devices-list');
            
            if (!deviceListDiv) {
                deviceListDiv = document.createElement('div');
                deviceListDiv.id = 'discovered-devices-list';
                const header = document.querySelector('.qr-modal-header');
                if (header && header.parentNode) {
                    header.parentNode.insertBefore(deviceListDiv, header.nextSibling);
                } else {
                    document.body.appendChild(deviceListDiv);
                }
            }

            // ✅ SHOW FILTERED DEVICE COUNT - "with RTSP"
            deviceListDiv.innerHTML = `
                <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 12px; padding: 16px 20px; border-top: 1px solid var(--gray-200); border-bottom: 1px solid var(--gray-200); background: linear-gradient(135deg, var(--gray-50) 0%, white 100%); position: sticky; top: 0; z-index: 10; border-radius: 8px 8px 0 0;">
                    <p style="font-size: 14px; color: var(--gray-700); margin: 0; font-weight: 600;">
                        <i class="fas fa-video" style="margin-right: 8px; color: var(--primary);"></i>
                        Found ${devicesWithRTSP.length} device(s) with RTSP
                    </p>
                    <button onclick="saveSelectedDevices()" 
                            style="padding: 10px 20px; background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%); color: white; border: none; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; white-space: nowrap; box-shadow: 0 2px 8px rgba(59, 130, 246, 0.3); transition: var(--transition);"
                            onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 4px 12px rgba(59, 130, 246, 0.4)'"
                            onmouseout="this.style.transform=''; this.style.boxShadow='0 2px 8px rgba(59, 130, 246, 0.3)'">
                        <i class="fas fa-save" style="margin-right: 6px;"></i>
                        Save Devices
                    </button>
                </div>
                <div id="devices-scroll-container" style="max-height: 70vh; overflow-y: auto; padding: 16px; background: var(--gray-50);">
                </div>
            `;

            // Load analytics first
            loadAnalyticsDataForDevices(userId).then(() => {
                // After analytics loaded, render ONLY FILTERED DEVICES
                devicesWithRTSP.forEach((device, filteredIndex) => {
                    // Find original index in full devices array for analytics lookup
                    const originalIndex = devices.indexOf(device);
                    const ip = d.device_ip || d.ip || d.ip_address || d.address || d.host || 'N/A';
                    const snapshot = device.screenshot_path || device.image_url || (device.device_ip || device.ip) ? `${(window.PGAK_CONFIG && window.PGAK_CONFIG.BASE_IMAGE_API) || 'https://dealer.pgak.co.in/images/'}${device.device_ip || device.ip}_jpg` : 'data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22100%22 height=%22100%22%3E%3Crect fill=%22%23f0f0f0%22 width=%22100%22 height=%22100%22/%3E%3Ctext x=%2250%25%22 y=%2250%25%22 text-anchor=%22middle%22 dy=%22.3em%22 fill=%22%23999%22 font-size=%2212%22%3ENo Image%3C/text%3E%3C/svg%3E';
                    const serial = device.device_info?.SerialNumber || `Device-${filteredIndex}`;
                    const deviceKey = `device-${filteredIndex}`;
                    
                    // Create HTML with beautiful horizontal layout
                    const deviceItem = document.createElement('div');
                    deviceItem.classList.add('device-item');
                    
                    deviceItem.innerHTML = `
                        <div class="device-item-container">
                            <!-- CHECKBOX -->
                            <div class="device-checkbox-wrapper">
                                <input type="checkbox" 
                                       class="device-checkbox" 
                                       value="${ip}" 
                                       data-serial="${serial}"
                                       data-index="${filteredIndex}"
                                       data-original-index="${originalIndex}"
                                       onchange="updateDeviceSelection()">
                            </div>
                            
                            <!-- DEVICE IP -->
                            <div class="device-ip-text">
                                <strong>Device IP</strong>
                                <div style="color: var(--gray-900); font-weight: 600;">${ip}</div>
                            </div>
                            
                            <!-- SNAPSHOT -->
                            ${snapshot && snapshot.includes('http') ? 
                                `<img src="${snapshot}" alt="Snapshot" class="device-snapshot">` : 
                                '<div class="device-snapshot-placeholder"><i class="fas fa-camera" style="font-size: 20px;"></i></div>'}
                            
                            <!-- ANALYTICS SECTION -->
                            <div class="device-analytics-wrapper">
                                <button class="analytics-select-btn" onclick="openAnalyticsModal(${filteredIndex})">
                                    <i class="fas fa-chart-line"></i>
                                    Select Analytics
                                </button>
                                
                            </div>
                        </div>
                    `;

                    // Append to scroll container
                    const scrollContainer = document.getElementById('devices-scroll-container');
                    if (scrollContainer) {
                        scrollContainer.appendChild(deviceItem);
                    } else {
                        deviceListDiv.appendChild(deviceItem);
                    }
                });
            });

            // Your old behavior: open QR scanner after scan (if required)
            if (typeof openQRScanner === "function") {
                openQRScanner();
            }

            // Optionally, add this to the device table if you want to show in the table as well:
            const devicesTableBody = document.getElementById('devicesRows');
            if (devicesTableBody) {
                devices.forEach(device => {
                    const row = document.createElement('tr');
                    row.innerHTML = `
                        <td>${device.device_ip}</td>
                        <td>${device.screenshot_path ? `<img src="${device.screenshot_path}" alt="Snapshot" style="width: 100px; height: 100px; object-fit: cover; border-radius: 8px;">` : '<div style="width: 100px; height: 100px; display: flex; align-items: center; justify-content: center; background: #f0f0f0; border-radius: 8px; color: #999; font-size: 12px;">No Image</div>'}</td>
                        <td><button class="details-btn" onclick="viewDeviceDetails(${device.device_ip})">Details</button></td>
                    `;
                    devicesTableBody.appendChild(row);
                });
            }
        }

        // ✅ NEW: Load analytics data for all devices
        async function loadAnalyticsDataForDevices(userId) {
            try {
                console.log("📊 Loading analytics for user:", userId);
                
                const response = await fetch(`/api/dealer/user-purchases2?user_id=${userId}`);
                const data = await response.json();
                
                console.log("✅ Analytics data loaded:", data);
                
                // Store analytics data globally
                window.analyticsDataCache = {
                    by_analytics: data.by_analytics || {},
                    total_camera_selections: data.total_camera_selections || 0,
                    total_orders: data.total_orders || 0,
                    unique_camera_count: data.unique_camera_count || 0,
                    user_id: userId
                };
                
                return window.analyticsDataCache;
            } catch (error) {
                console.error('❌ Error loading analytics:', error);
                window.analyticsDataCache = {
                    by_analytics: {},
                    total_camera_selections: 0,
                    total_orders: 0,
                    unique_camera_count: 0,
                    user_id: userId
                };
            }
        }

        // ========== SELECT ANALYTICS MODAL FUNCTIONS ==========
        // Global state for analytics selection
        let currentDeviceIndex = null;
        let deviceAnalyticsSelections = {}; // Store selections per device

        // ✅ Populate customer dropdown in analytics modal
        async function populateAnalyticsCustomerDropdown() {
            const dropdown = document.getElementById('analyticsCustomerDropdown');
            if (!dropdown) return;

            dropdown.innerHTML = '<option value="">⏳ Loading customers...</option>';
            dropdown.disabled = true;

            try {
                // Try cached first
                let customers = window.allCustomersDataForFilter || [];

                // If empty, fetch fresh from API
                if (!customers || customers.length === 0) {
                    const res = await fetch('/api/dealer/customers', { credentials: 'include' });
                    if (res.ok) {
                        const json = await res.json();
                        const raw = json.customers || json.data || json || [];
                        customers = raw.map(c => ({
                            user_id: c.user_id || c.id,
                            name: c.name || c.full_name || 'Unknown'
                        }));
                        window.allCustomersDataForFilter = customers;
                    }
                }

                dropdown.innerHTML = '<option value="">— Select a customer —</option>';
                dropdown.disabled = false;

                if (!customers || customers.length === 0) {
                    const opt = document.createElement('option');
                    opt.value = '';
                    opt.textContent = 'No customers found';
                    opt.disabled = true;
                    dropdown.appendChild(opt);
                    return;
                }

                customers.forEach(c => {
                    const uid = c.user_id || c.id || '';
                    const name = c.name || c.full_name || 'Unknown';
                    const opt = document.createElement('option');
                    opt.value = uid;
                    opt.textContent = name;
                    dropdown.appendChild(opt);
                });

            } catch (err) {
                dropdown.innerHTML = '<option value="">❌ Failed to load customers</option>';
                dropdown.disabled = false;
                console.error('Customer dropdown error:', err);
            }
        }

        // ✅ When customer is selected in analytics modal — fetch their analytics
        async function onAnalyticsCustomerChange(userId) {
            const checkboxList = document.getElementById('analyticsCheckboxList');
            const loader = document.getElementById('analyticsCustomerLoader');

            checkboxList.innerHTML = '';

            if (!userId) return;

            loader.style.display = 'block';

            try {
                const response = await fetch(`/api/dealer/user-purchases2?user_id=${userId}`);
                const data = await response.json();

                // Cache for this selected customer
                window.analyticsDataCache = {
                    by_analytics: data.by_analytics || {},
                    total_camera_selections: data.total_camera_selections || 0,
                    total_orders: data.total_orders || 0,
                    unique_camera_count: data.unique_camera_count || 0,
                    user_id: userId
                };

                loader.style.display = 'none';
                renderAnalyticsCheckboxes(currentDeviceIndex);

            } catch (err) {
                loader.style.display = 'none';
                checkboxList.innerHTML = '<p style="text-align:center;color:#ef4444;padding:20px;font-size:13px;"><i class="fas fa-exclamation-circle"></i> Failed to load analytics. Please try again.</p>';
                console.error('❌ Analytics fetch error:', err);
            }
        }

        // ✅ Render analytics checkboxes from cache
        // ✅ Get max camera limit from analytics cache
        function getAnalyticsCameraLimit() {
            const byAnalytics = window.analyticsDataCache?.by_analytics || {};
            let total = 0;
            Object.values(byAnalytics).forEach(v => { total += (v.count || 0); });
            return total;
        }

        // ✅ Update the limit counter bar UI
        function updateCameraLimitBar() {
            const limitBar   = document.getElementById('analyticsCameraLimitBar');
            const limitText  = document.getElementById('analyticsCameraLimitText');
            const limitFill  = document.getElementById('analyticsCameraLimitBar_fill');
            const limitWarn  = document.getElementById('analyticsCameraLimitWarning');
            if (!limitBar) return;

            const maxLimit = getAnalyticsCameraLimit();
            if (maxLimit === 0) { limitBar.style.display = 'none'; return; }

            const checkedCount = document.querySelectorAll('#analyticsCheckboxList input[type="checkbox"]:checked').length;
            const pct = Math.min(100, Math.round((checkedCount / maxLimit) * 100));

            limitBar.style.display = 'block';
            limitText.textContent  = checkedCount + ' / ' + maxLimit;
            limitFill.style.width  = pct + '%';

            if (checkedCount >= maxLimit) {
                limitFill.style.background = '#dc2626';
                limitBar.style.background  = '#fef2f2';
                limitBar.style.borderColor = '#fecaca';
                limitText.style.color      = '#dc2626';
                limitWarn.style.display    = 'block';
            } else {
                limitFill.style.background = '#3b82f6';
                limitBar.style.background  = '#eff6ff';
                limitBar.style.borderColor = '#bfdbfe';
                limitText.style.color      = '#1e40af';
                limitWarn.style.display    = 'none';
            }
        }

        function renderAnalyticsCheckboxes(deviceIndex) {
            const checkboxList = document.getElementById('analyticsCheckboxList');
            checkboxList.innerHTML = '';

            const analyticsData = window.analyticsDataCache?.by_analytics || {};

            if (Object.keys(analyticsData).length === 0) {
                checkboxList.innerHTML = '<p style="text-align: center; color: var(--gray-500); padding: 20px;">No analytics available</p>';
                document.getElementById('analyticsCameraLimitBar').style.display = 'none';
                return;
            }

            // Calculate max allowed cameras
            const maxLimit = getAnalyticsCameraLimit();

            const extractedAnalytics = {};
            Object.entries(analyticsData).forEach(([analyticsKey, analyticsValue]) => {
                const cameraIds = analyticsValue?.camera_ids || [];
                cameraIds.forEach((cameraId) => {
                    const analyticsMatch = cameraId.match(/^(.*?)\s*\(\d+\s*Cameras?\)$/);
                    if (analyticsMatch) {
                        const analyticsName = analyticsMatch[1].trim();
                        if (!extractedAnalytics[analyticsName]) extractedAnalytics[analyticsName] = [];
                        extractedAnalytics[analyticsName].push(cameraId);
                    } else {
                        const analyticsName = cameraId.trim();
                        if (!extractedAnalytics[analyticsName]) extractedAnalytics[analyticsName] = [];
                        extractedAnalytics[analyticsName].push(analyticsName);
                    }
                });
            });

            const selectedAnalytics = deviceAnalyticsSelections[deviceIndex] || [];

            Object.entries(extractedAnalytics).forEach(([analyticsName, cameras]) => {
                cameras.forEach((cameraId) => {
                    const isSelected = selectedAnalytics.some(item =>
                        item.analyticsType === analyticsName && item.cameraName === cameraId
                    );
                    const checkboxItem = document.createElement('div');
                    checkboxItem.className = 'analytics-checkbox-item';
                    const uniqueId = `analytics_${analyticsName.replace(/\s/g, '_')}_${cameraId.replace(/\s/g, '_')}_${deviceIndex}`;
                    checkboxItem.innerHTML = `
                        <input type="checkbox"
                               id="${uniqueId}"
                               value="${analyticsName}:${cameraId}"
                               ${isSelected ? 'checked' : ''}
                               data-analytics-type="${analyticsName}"
                               data-camera-name="${cameraId}"
                               onchange="onAnalyticsCheckboxChange(this, ${maxLimit})">
                        <label for="${uniqueId}" style="flex: 1; cursor: pointer;">
                            <div style="font-weight: 600; color: var(--gray-900);">${analyticsName}</div>
                            <div style="font-size: 12px; color: var(--gray-600); margin-top: 2px;">${cameraId}</div>
                        </label>
                    `;
                    checkboxList.appendChild(checkboxItem);
                });
            });

            // Show limit bar and initial count
            updateCameraLimitBar();
        }

        // ✅ Called on every checkbox change — enforces limit
        function onAnalyticsCheckboxChange(checkbox, maxLimit) {
            const allCheckboxes = document.querySelectorAll('#analyticsCheckboxList input[type="checkbox"]');
            const checkedCount  = document.querySelectorAll('#analyticsCheckboxList input[type="checkbox"]:checked').length;

            if (checkbox.checked && checkedCount > maxLimit) {
                // Uncheck it immediately
                checkbox.checked = false;

                // Flash warning
                const warn = document.getElementById('analyticsCameraLimitWarning');
                if (warn) {
                    warn.style.display = 'block';
                    warn.style.animation = 'none';
                    setTimeout(() => warn.style.animation = '', 10);
                }

                // Show toast
                anToast('❌ Maximum ' + maxLimit + ' cameras hi select kar sakte hain!', '#991b1b');
                updateCameraLimitBar();
                return;
            }

            // Disable all unchecked if at limit
            allCheckboxes.forEach(cb => {
                if (!cb.checked) {
                    cb.disabled = (checkedCount >= maxLimit);
                    cb.closest('.analytics-checkbox-item').style.opacity = (checkedCount >= maxLimit) ? '0.45' : '1';
                    cb.closest('.analytics-checkbox-item').style.cursor  = (checkedCount >= maxLimit) ? 'not-allowed' : '';
                }
            });

            updateCameraLimitBar();
        }

        // ✅ Open Analytics Modal
        async function openAnalyticsModal(deviceIndex) {
            currentDeviceIndex = deviceIndex;

            const modal = document.getElementById('analyticsModal');
            const checkboxList = document.getElementById('analyticsCheckboxList');
            const loader = document.getElementById('analyticsCustomerLoader');
            const dropdown = document.getElementById('analyticsCustomerDropdown');

            // Reset state
            checkboxList.innerHTML = '<p style="text-align: center; color: var(--gray-400); padding: 20px; font-size: 13px;">👆 Please select a customer above to load their analytics.</p>';
            if (loader) loader.style.display = 'none';

            modal.classList.add('show');

            // Populate customer dropdown (fetches from API if needed)
            await populateAnalyticsCustomerDropdown();

            // Reset dropdown selection
            if (dropdown) dropdown.value = '';
        }
        
        // ✅ Close Analytics Modal
        function closeAnalyticsModal() {
            const modal = document.getElementById('analyticsModal');
            modal.classList.remove('show');
            currentDeviceIndex = null;
        }
        
        // ✅ Apply Selected Analytics
        function applySelectedAnalytics() {
            if (currentDeviceIndex === null) return;

            const checkboxes = document.querySelectorAll('#analyticsCheckboxList input[type="checkbox"]:checked');

            // ✅ Final limit check before applying
            const maxLimit = getAnalyticsCameraLimit();
            if (maxLimit > 0 && checkboxes.length > maxLimit) {
                anToast('❌ Maximum ' + maxLimit + ' cameras hi select kar sakte hain! Abhi ' + checkboxes.length + ' selected hain.', '#991b1b');
                return;
            }

            const selections = Array.from(checkboxes).map(cb => ({
                analyticsType: cb.dataset.analyticsType,
                cameraName: cb.dataset.cameraName,
                rtsp_url: null // Will be filled during save
            }));

            // Store selections for this device
            deviceAnalyticsSelections[currentDeviceIndex] = selections;

            // Update the display
            updateDeviceAnalyticsDisplay(currentDeviceIndex, selections);

            // Update overall device selection
            updateDeviceSelection();

            closeAnalyticsModal();
        }
        
        // ✅ Update Device Analytics Display
        function updateDeviceAnalyticsDisplay(deviceIndex, selections) {
            const displayDiv = document.getElementById(`analytics-display-${deviceIndex}`);
            if (!displayDiv) return;
            
            displayDiv.innerHTML = '';
            
            if (selections.length === 0) {
                return;
            }
            
            selections.forEach((selection, index) => {
                const tag = document.createElement('div');
                tag.className = 'selected-analytics-tag';
                tag.innerHTML = `
                    <span>${selection.analyticsType}: ${selection.cameraName}</span>
                    <span class="remove-tag" onclick="event.stopPropagation(); removeAnalyticsTag(${deviceIndex}, ${index})">×</span>
                `;
                displayDiv.appendChild(tag);
            });
        }
        
        // ✅ Remove Analytics Tag
        function removeAnalyticsTag(deviceIndex, tagIndex) {
            if (!deviceAnalyticsSelections[deviceIndex]) return;
            
            deviceAnalyticsSelections[deviceIndex].splice(tagIndex, 1);
            updateDeviceAnalyticsDisplay(deviceIndex, deviceAnalyticsSelections[deviceIndex]);
            updateDeviceSelection();
        }
        
        // ✅ NEW: Populate analytics dropdown for a device
        // ✅ FIXED: Populate analytics dropdown - सिर्फ camera_ids से analytics fetch करो
        function populateAnalyticsDropdown(containerId) {
            const container = document.getElementById(containerId);
            if (!container) return;
            
            const analyticsData = window.analyticsDataCache?.by_analytics || {};
            
            console.log("📊 Analytics data:", analyticsData);
            
            // Extract all analytics from camera_ids directly
            const extractedAnalytics = {};
            
            Object.entries(analyticsData).forEach(([analyticsKey, analyticsValue]) => {
                const cameraIds = analyticsValue?.camera_ids || [];
                
                // Filter camera_ids that contain analytics info (text in parentheses)
                // Example: "Boundary Crossing (1 Cameras)" → extract "Boundary Crossing"
            //     cameraIds.forEach((cameraId) => {
            //         const analyticsMatch = cameraId.match(/^(.*?)\s*\(\d+\s*Cameras?\)$/);
            //         if (analyticsMatch) {
            //             const analyticsName = analyticsMatch[1].trim();
            //             if (!extractedAnalytics[analyticsName]) {
            //                 extractedAnalytics[analyticsName] = [];
            //             }
            //             extractedAnalytics[analyticsName].push(cameraId);
            //         }
            //     });
            // });
                cameraIds.forEach((cameraId) => {
                    const analyticsMatch = cameraId.match(/^(.*?)\s*\(\d+\s*Cameras?\)$/);
                    if (analyticsMatch) {
                        // Format: "Boundary Crossing (1 Cameras)"
                        const analyticsName = analyticsMatch[1].trim();
                        if (!extractedAnalytics[analyticsName]) {
                            extractedAnalytics[analyticsName] = [];
                        }
                        extractedAnalytics[analyticsName].push(cameraId);
                    } else {
                        // Format: plain name like "FRS" or "Line Crossing"
                        const analyticsName = cameraId.trim();
                        if (!extractedAnalytics[analyticsName]) {
                            extractedAnalytics[analyticsName] = [];
                        }
                        extractedAnalytics[analyticsName].push(analyticsName);
                    }
                });
            });
                
            console.log("📊 Extracted Analytics:", extractedAnalytics);
            
            if (Object.keys(extractedAnalytics).length === 0) {
                container.innerHTML += '<div style="font-size: 10px; color: var(--gray-500); padding: 4px;">No analytics available</div>';
                return;
            }
            
            // ✅ Create checkboxes for each analytics type
            Object.entries(extractedAnalytics).forEach(([analyticsName, cameras]) => {
                // Add group label
                const groupLabel = document.createElement('div');
                groupLabel.style.cssText = "font-size: 9px; color: var(--gray-700); font-weight: 600; margin-top: 4px; margin-bottom: 2px; padding: 2px 4px; background-color: var(--gray-50); border-radius: 3px;";
                groupLabel.textContent = `${analyticsName}`;
                container.appendChild(groupLabel);
                
                // Add checkbox for each camera
                cameras.forEach((cameraId) => {
                    const checkboxLabel = document.createElement('label');
                    checkboxLabel.style.cssText = "display: flex; align-items: center; gap: 6px; font-size: 10px; cursor: pointer; padding: 2px 4px; border-radius: 3px; transition: background-color 0.2s;";
                    checkboxLabel.onmouseover = () => checkboxLabel.style.backgroundColor = 'var(--gray-50)';
                    checkboxLabel.onmouseout = () => checkboxLabel.style.backgroundColor = 'transparent';
                    
                    const checkbox = document.createElement('input');
                    checkbox.type = 'checkbox';
                    checkbox.className = 'camera-checkbox';
                    checkbox.value = `${analyticsName}:${cameraId}`;
                    checkbox.dataset.analyticsType = analyticsName;
                    checkbox.dataset.cameraName = cameraId;
                    checkbox.style.cssText = "cursor: pointer; width: 14px; height: 14px; accent-color: var(--primary);";
                    checkbox.onchange = updateDeviceSelection;
                    
                    const cameraLabel = document.createElement('span');
                    cameraLabel.textContent = cameraId;
                    cameraLabel.style.cssText = "color: var(--gray-900); flex: 1; word-break: break-word;";
                    
                    checkboxLabel.appendChild(checkbox);
                    checkboxLabel.appendChild(cameraLabel);
                    container.appendChild(checkboxLabel);
                });
            });
            
            console.log("✅ Analytics checkboxes populated");
        }

        // ✅ Existing functions (unchanged)
        function updateDeviceSelection() {
            const deviceCheckboxes = document.querySelectorAll('.device-checkbox:checked');
            window.selectedDevicesWithAnalytics = Array.from(deviceCheckboxes).map(deviceCb => {
                const deviceIndex = deviceCb.dataset.index;
                
                // Get analytics selections for this device from deviceAnalyticsSelections
                const analytics = deviceAnalyticsSelections[deviceIndex] || [];
                
                // ✅ GET THE ORIGINAL DEVICE OBJECT
                const originalDevice = window.discoveredDevices ? window.discoveredDevices[deviceIndex] : null;
                
                // Add RTSP URLs to analytics
                const analyticsWithRtsp = analytics.map(analytic => {
                    let rtspUrl = null;
                    
                    // ✅ SEARCH FOR RTSP URL IN ORIGINAL DEVICE
                    if (originalDevice) {
                        // Search in rtsp_profiles
                        if (originalDevice.rtsp_profiles && Array.isArray(originalDevice.rtsp_profiles)) {
                            const profile = originalDevice.rtsp_profiles.find(p => 
                                p.name === analytic.cameraName || 
                                p.rtsp_url?.includes(analytic.cameraName)
                            );
                            if (profile) rtspUrl = profile.rtsp_url;
                        }
                        
                        // Search in cameras
                        if (!rtspUrl && originalDevice.cameras && Array.isArray(originalDevice.cameras)) {
                            const camera = originalDevice.cameras.find(c => 
                                c.name === analytic.cameraName || 
                                c.camera_name === analytic.cameraName
                            );
                            if (camera) rtspUrl = camera.rtsp_url || camera.rtsp;
                        }
                    }
                    
                    return {
                        ...analytic,
                        rtsp_url: rtspUrl,  // ✅ NOW INCLUDES RTSP URL!
                        rtsp: rtspUrl
                    };
                });
                
                return {
                    ip: deviceCb.value,
                    serial: deviceCb.dataset.serial,
                    deviceIndex: deviceIndex,
                    cameras: analyticsWithRtsp
                };
            });
            
            console.log("✅ Selected devices with RTSP URLs:", window.selectedDevicesWithAnalytics);
        }

        function saveSelectedDevices() {
            updateDeviceSelection();

            if (!window.selectedDevicesWithAnalytics || window.selectedDevicesWithAnalytics.length === 0) {
                alert('❌ Please select at least one device');
                return;
            }

            const devicesBatch = [];

            window.selectedDevicesWithAnalytics.forEach(device => {
                const ip = (device.ip || "").trim();
                if (!ip) {
                    console.warn('⚠️ Skipping device - no IP');
                    return;
                }

                const cameras = Array.isArray(device.cameras) ? device.cameras : [];
                if (cameras.length === 0) {
                    console.warn('⚠️ Skipping device - no cameras:', ip);
                    return;
                }

                // ✅ FIXED: Try multiple sources for device-level RTSP URL
                let deviceRtsp = null;
                const originalDevice = window.discoveredDevices[device.deviceIndex];
                
                if (originalDevice) {
                    // Try various possible locations for device RTSP URL
                    deviceRtsp = originalDevice.device_rtsp || 
                                originalDevice.rtsp || 
                                originalDevice.rtsp_url ||
                                (originalDevice.device_info && originalDevice.device_info.rtsp) ||
                                (originalDevice.rtsp_profiles && originalDevice.rtsp_profiles[0]?.rtsp_url) ||
                                (originalDevice.cameras && originalDevice.cameras[0]?.rtsp_url);
                }
                
                // Fallback to first camera's RTSP
                if (!deviceRtsp && cameras[0]) {
                    deviceRtsp = cameras[0].rtsp_url || cameras[0].rtsp;
                }

                const analytics = [];

                cameras.forEach(camera => {
                    const analyticsName = (camera.analyticsType || camera.analytics_name || "").trim();
                    
                    // ✅ FIXED: Use RTSP URL from camera object (now populated)
                    // ✅ FIXED: Use device_rtsp for rtsp_url (NOT camera RTSP which is null)
                    // Priority: deviceRtsp > camera.rtsp_url > camera.rtsp
                    const artsp = (deviceRtsp || camera.rtsp_url || camera.rtsp || "").trim() || null;
                    
                    if (!analyticsName) {
                        console.warn('⚠️ Skipping analytics - no name:', camera);
                        return;
                    }

                    console.log(`📌 Analytics: ${analyticsName}, RTSP: ${artsp}`);
                    
                    analytics.push({ 
                        analytics_name: analyticsName, 
                        rtsp_url: artsp 
                    });
                });

                if (analytics.length === 0) {
                    console.warn('⚠️ Skipping device - no valid analytics:', ip);
                    return;
                }

                console.log(`✅ Device added - IP: ${ip}, Device RTSP: ${deviceRtsp}, Analytics: ${analytics.length}`);
                
                devicesBatch.push({
                    ip_address: ip,
                    device_rtsp: deviceRtsp,
                    analytics: analytics
                });
            });

            if (devicesBatch.length === 0) {
                alert('❌ No valid devices/analytics selected');
                return;
            }

            const userId = (typeof issueState !== 'undefined' && issueState.currentUserId) ? issueState.currentUserId : null;
            console.log("📌 DEBUG user_id:", userId, "| issueState:", JSON.stringify(issueState));

            if (!userId) {
                alert('❌ No customer selected. user_id is missing.');
                return;
            }

            const payload = {
                user_id: userId,
                devices: devicesBatch
            };

            console.log("📤 Sending payload to /api/scan-db:", JSON.stringify(payload, null, 2));

            fetch('/api/scan-db', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            })
            .then(res => res.json())
            .then(data => {
                console.log("✅ /api/scan-db response:", data);

                if (data.ok) {
                    alert(`✅ Saved successfully! Remote status: ${data.remote_status_code || 'ok'}`);
                } else {
                    console.error("❌ Remote error details:", data.remote_body);
                    alert(`❌ Failed: ${data.message}\nRemote Status: ${data.remote_status_code}`);
                }
            })
            .catch(err => {
                console.error("❌ Fetch error:", err);
                alert("❌ Server error while saving");
            });
        }

        function submitSelectedDevices() {
            // Update selection before submit
            updateDeviceSelection();
            
            if (window.selectedDevicesWithAnalytics.length === 0) {
                alert('❌ Please select at least one device');
                return;
            }
            
            // Check if all selected devices have analytics
            const devicesWithoutAnalytics = window.selectedDevicesWithAnalytics.filter(d => !d.analytics);
            if (devicesWithoutAnalytics.length > 0) {
                alert('❌ Please select analytics for all selected devices');
                return;
            }
            
            console.log("📤 Submitting devices:", window.selectedDevicesWithAnalytics);
            
            alert(`✅ Selected ${window.selectedDevicesWithAnalytics.length} devices!\n\nDevices ready to submit to backend.`);
        }

        function selectDiscoveredDevice(index) {
            const device = discoveredDevices[index];
            if (!device || !device.device_info?.SerialNumber) {
                alert("Device does not have a valid serial number.");
                return;
            }

            selectedOnvifSerial = device.device_info.SerialNumber.trim();
            macValue.textContent = selectedOnvifSerial;
            macHighlight.classList.add("show");
            nextStepContainer.classList.add("show");
            showValidationMessage('✅', 'Device selected! Click "Proceed with Device Issuance" to continue.', 'success');
            
            stopScanning();
            qrVideo.style.display = 'none';
        }


        function submitSelectedDevices() {
            // Update selection before submit
            updateDeviceSelection();
            
            if (window.selectedDevicesWithAnalytics.length === 0) {
                alert('❌ Please select at least one device');
                return;
            }
            
            // Check if all selected devices have analytics
            const devicesWithoutAnalytics = window.selectedDevicesWithAnalytics.filter(d => !d.analytics);
            if (devicesWithoutAnalytics.length > 0) {
                alert('❌ Please select analytics for all selected devices');
                return;
            }
            
            console.log("📤 Submitting devices:", window.selectedDevicesWithAnalytics);
            
            alert(`✅ Selected ${window.selectedDevicesWithAnalytics.length} devices!\n\nDevices ready to submit to backend.`);
        }



        function selectDiscoveredDevice(index) {
            const device = discoveredDevices[index];
            if (!device || !device.device_info?.SerialNumber) {
                alert("Device does not have a valid serial number.");
                return;
            }

            selectedOnvifSerial = device.device_info.SerialNumber.trim();
            macValue.textContent = selectedOnvifSerial;
            macHighlight.classList.add("show");
            nextStepContainer.classList.add("show");
            showValidationMessage('✅', 'Device selected! Click "Proceed with Device Issuance" to continue.', 'success');
            
            stopScanning();
            qrVideo.style.display = 'none';
        }

        // ===== QR PRINT FUNCTIONS - REMOVED =====
        // All QR print functions have been removed as requested

        function _extractFirstIP(ipData) {
            if (!ipData) return '';
            if (typeof ipData === 'string') return ipData;
            if (typeof ipData === 'object') {
                for (const key in ipData) {
                    if (ipData[key]) return ipData[key];
                }
            }
            return '';
        }
        
        // ===== INITIALIZATION =====
        document.addEventListener('DOMContentLoaded', function() {
            setupMobileMenu();
            setupMenuItems();
            setupLogout();
            loadCurrentUser();
            loadDealerCode();
            setupMobileDetailBackButtons();
            setupResponsiveTable();

            // Detect current page and load appropriate data
            var currentPath = window.location.pathname;
            if (currentPath.indexOf('/customers-page') !== -1) {
                loadCustomersData();
            } else if (currentPath.indexOf('/devices-page') !== -1) {
                loadDevicesData();
            } else if (currentPath.indexOf('/discovery-page') !== -1) {
                // discovery page init if needed
            } else {
                loadDashboardData();
            }
        });
        
        // ✅ SETUP RESPONSIVE TABLE BEHAVIOR
        function setupResponsiveTable() {
            window.addEventListener('resize', handleResponsiveTables);
            handleResponsiveTables();
        }
        
        function handleResponsiveTables() {
            isMobileView = window.innerWidth <= 768;
            if (isMobileView) {
                setupMobileTables();
            } else {
                setupDesktopTables();
            }
        }
        
        function setupMobileTables() {
            // Hide search rows in mobile
            const searchRows = document.querySelectorAll('thead tr[style*="background: var(--gray-100)"]');
            searchRows.forEach(row => {
                row.style.display = 'none';
            });
        }
        
        function setupDesktopTables() {
            // Show search rows in desktop
            const searchRows = document.querySelectorAll('thead tr[style*="background: var(--gray-100)"]');
            searchRows.forEach(row => {
                row.style.display = '';
            });
        }
        
        // ✅ SETUP MOBILE DETAIL BACK BUTTONS
        function setupMobileDetailBackButtons() {
            const customerBackBtn = document.querySelector('#customerMobileDetailPage .mobile-detail-back-btn');
            const deviceBackBtn = document.querySelector('#deviceMobileDetailPage .mobile-detail-back-btn');
            
            if (customerBackBtn) {
                customerBackBtn.addEventListener('click', function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    closeCustomerDetailPage();
                });
            }
            
            if (deviceBackBtn) {
                deviceBackBtn.addEventListener('click', function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    closeDeviceDetailPage();
                });
            }
        }

        async function loadCurrentUser() {
            try {
                const response = await fetch('/api/current-user');
                
                if (!response.ok) {
                    const storedUser = localStorage.getItem('currentUser');
                    if (storedUser) {
                        const user = JSON.parse(storedUser);
                        displayUserProfile(user);
                        return;
                    }
                    throw new Error('Could not load user data');
                }

                const userData = await response.json();
                displayUserProfile(userData);
                localStorage.setItem('currentUser', JSON.stringify(userData));
            } catch (error) {
                console.error('Error loading current user:', error);
                displayUserProfile({
                    full_name: 'Dealer',
                    email: 'dealer@system.com',
                    user_type: 'dealer'
                });
            }
        }

        function displayUserProfile(user) {
            const getInitials = (name) => {
                if (!name) return 'D';
                return name.split(' ')
                    .map(n => n[0])
                    .join('')
                    .toUpperCase()
                    .substring(0, 2);
            };

            const initials = getInitials(user.full_name);
            const fullName = user.full_name || 'Dealer';
            const email = user.email || 'dealer@system.com';

            const avatarEl = document.getElementById('userAvatar');
            if (avatarEl) avatarEl.textContent = initials;

            const nameEl = document.getElementById('userName');
            if (nameEl) nameEl.textContent = fullName;

            const emailEl = document.getElementById('userEmail');
            if (emailEl) emailEl.textContent = email;
        }

        function setupMobileMenu() {
            const toggle = document.getElementById('mobileToggle');
            const sidebar = document.getElementById('sidebar');
            const overlay = document.getElementById('mobileOverlay');
            if (!toggle || !sidebar || !overlay) return;

            function closeSidebar() {
                sidebar.classList.remove('active');
                overlay.classList.remove('active');
            }

            toggle.addEventListener('click', () => {
                sidebar.classList.toggle('active');
                overlay.classList.toggle('active');
            });

            overlay.addEventListener('click', closeSidebar);

            document.querySelectorAll('.menu-item').forEach(item => {
                item.addEventListener('click', closeSidebar);
            });

            window.addEventListener('resize', () => {
                isMobileView = window.innerWidth <= 768;
                if (window.innerWidth > 768) {
                    closeSidebar();
                }
            });
        }

        let devicesRefreshInterval = null;

        async function loadDealerCode() {
            try {
                const response = await fetch('/api/dealer-code');
                
                if (!response.ok) {
                    throw new Error('Failed to load dealer code');
                }

                const data = await response.json();
                
                let dealerCode = 'N/A';
                
                if (data.users && Array.isArray(data.users) && data.users.length > 0) {
                    const dealerUser = data.users.find(user => (user.user_type || '').toLowerCase() === 'dealer');
                    if (dealerUser) {
                        dealerCode = dealerUser.dealer_code || dealerUser.code || 'N/A';
                    }
                } else if (data.dealer_code) {
                    dealerCode = data.dealer_code;
                } else if (data.code) {
                    dealerCode = data.code;
                }

                document.getElementById('dealerCode').textContent = dealerCode;
                sessionStorage.setItem('dealerCode', dealerCode);
                
            } catch (error) {
                console.error('Error loading dealer code:', error);
                document.getElementById('dealerCode').textContent = 'Error loading';
            }
        }

        function copyDealerCode() {
            const codeElement = document.getElementById('dealerCode');
            const code = codeElement.textContent;
            
            if (code === 'Loading...' || code === 'Error loading') {
                showNotification('error', 'Cannot copy - code not loaded');
                return;
            }

            navigator.clipboard.writeText(code).then(() => {
                showNotification('success', `✓ Copied: ${code}`);
                
                const btn = document.getElementById('copyCodeBtn');
                const originalText = btn.textContent;
                btn.textContent = '✓';
                btn.style.background = 'var(--success)';
                
                setTimeout(() => {
                    btn.textContent = originalText;
                    btn.style.background = '';
                }, 2000);
            }).catch(err => {
                console.error('Failed to copy:', err);
                showNotification('error', 'Failed to copy code');
            });
        }

        // ==================== SHOW NOTIFICATION ====================
        function showNotification(type, message) {
            const notification = document.createElement('div');
            notification.style.cssText = `
                position: fixed;
                top: 20px;
                right: 20px;
                padding: 16px 20px;
                border-radius: 8px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
                z-index: 3000;
                animation: slideIn 0.3s ease-out;
                max-width: 400px;
                word-wrap: break-word;
                font-weight: 600;
                font-size: 14px;
            `;

            const colors = {
                success: { bg: 'rgba(16, 185, 129, 0.95)', text: 'white' },
                error: { bg: 'rgba(239, 68, 68, 0.95)', text: 'white' },
                warning: { bg: 'rgba(245, 158, 11, 0.95)', text: 'white' }
            };

            const color = colors[type] || colors.success;
            notification.style.backgroundColor = color.bg;
            notification.style.color = color.text;
            notification.innerHTML = message;

            if (!document.querySelector('style[data-notification-animation]')) {
                const style = document.createElement('style');
                style.setAttribute('data-notification-animation', 'true');
                style.textContent = `
                    @keyframes slideIn {
                        from { transform: translateX(400px); opacity: 0; }
                        to { transform: translateX(0); opacity: 1; }
                    }
                    @keyframes slideOut {
                        from { transform: translateX(0); opacity: 1; }
                        to { transform: translateX(400px); opacity: 0; }
                    }
                `;
                document.head.appendChild(style);
            }

            document.body.appendChild(notification);

            setTimeout(() => {
                notification.style.animation = 'slideOut 0.3s ease-out';
                setTimeout(() => notification.remove(), 300);
            }, 3000);
        }

        function setupMenuItems() {
            // Menu items are now real <a href> links - let them navigate naturally.
            // Only set up non-navigation buttons if they exist on this page.
            var analyticsBtn = document.getElementById('analyticsMenuBtn');
            if (analyticsBtn) {
                analyticsBtn.addEventListener('click', function(e) {
                    e.preventDefault();
                    showAnalyticsView();
                });
            }
        }

        function setActiveMenu(element) {
            document.querySelectorAll('.menu-item').forEach(item => {
                item.classList.remove('active');
            });
            element.classList.add('active');
        }

        function showDashboardView() {
            document.getElementById('dashboardView').style.display = 'block';
            document.getElementById('customersView').style.display = 'none';
            document.getElementById('devicesView').style.display = 'none';
            document.getElementById('issuesView').style.display = 'none';
            document.getElementById('discoveryView').style.display = 'none';
            document.getElementById('pageTitle').textContent = '📊 Dashboard';
            document.getElementById('pageSubtitle').textContent = 'System Overview';
            
            if (devicesRefreshInterval) {
                clearInterval(devicesRefreshInterval);
                devicesRefreshInterval = null;
            }
        }

        function showCustomersView() {
            document.getElementById('dashboardView').style.display = 'none';
            document.getElementById('customersView').style.display = 'block';
            document.getElementById('devicesView').style.display = 'none';
            document.getElementById('issuesView').style.display = 'none';
            document.getElementById('discoveryView').style.display = 'none';
            document.getElementById('pageTitle').textContent = '👥 Customers';
            document.getElementById('pageSubtitle').textContent = 'All registered customers';
            
            loadCustomersData();
            
            if (devicesRefreshInterval) {
                clearInterval(devicesRefreshInterval);
                devicesRefreshInterval = null;
            }
        }

        function showDevicesView() {
            document.getElementById('dashboardView').style.display = 'none';
            document.getElementById('customersView').style.display = 'none';
            document.getElementById('devicesView').style.display = 'block';
            document.getElementById('issuesView').style.display = 'none';
            document.getElementById('discoveryView').style.display = 'none';
            document.getElementById('pageTitle').textContent = '🖥 Devices Management';
            document.getElementById('pageSubtitle').textContent = 'All registered devices';
            
            loadDevicesData();
            
            if (devicesRefreshInterval) {
                clearInterval(devicesRefreshInterval);
            }
            devicesRefreshInterval = setInterval(loadDevicesData, 5000);
        }

        function showAnalyticsView() {
            document.getElementById('dashboardView').style.display = 'none';
            document.getElementById('customersView').style.display = 'none';
            document.getElementById('devicesView').style.display = 'none';
            document.getElementById('issuesView').style.display = 'none';
            document.getElementById('discoveryView').style.display = 'none';
            document.getElementById('analyticsView').style.display = 'block';
            document.getElementById('pageTitle').textContent = '🤖 AI Analytics';
            document.getElementById('pageSubtitle').textContent = 'AI analytics from database';
            if (typeof devicesRefreshInterval !== 'undefined' && devicesRefreshInterval) {
                clearInterval(devicesRefreshInterval); devicesRefreshInterval = null;
            }
            loadAnalyticsData();
        }

                function showDiscoveryView() {
            document.getElementById('dashboardView').style.display = 'none';
            document.getElementById('customersView').style.display = 'none';
            document.getElementById('devicesView').style.display = 'none';
            document.getElementById('issuesView').style.display = 'none';
            document.getElementById('analyticsView').style.display = 'none';
            document.getElementById('discoveryView').style.display = 'block';
            document.getElementById('pageTitle').textContent = '🔍 Device Discovery';
            document.getElementById('pageSubtitle').textContent = 'Choose how you want to discover devices';

            if (devicesRefreshInterval) {
                clearInterval(devicesRefreshInterval);
                devicesRefreshInterval = null;
            }

            const entrySection = document.getElementById('deviceDiscoveryEntrySection');
            const actualSection = document.getElementById('deviceDiscoveryActualSection');
            const entryCard = document.querySelector('#deviceDiscoveryEntrySection .discovery-entry-card');
            if (entrySection) {
                entrySection.classList.add('active');
                entrySection.style.display = 'block';
            }
            if (actualSection) {
                actualSection.classList.remove('active');
                actualSection.style.display = 'none';
            }
            if (entryCard) {
                entryCard.classList.remove('static-ip-expanded');
            }

            // Reset form fields
            document.getElementById('dpv2Password').value = '';
            document.getElementById('dpv2ErrorBox').style.display = 'none';
            document.getElementById('dpv2StatusBox').style.display = 'none';
            document.getElementById('dpv2ResultsSection').style.display = 'none';
            document.getElementById('dpv2CredForm').style.display = 'block';
            document.getElementById('dpv2ScanBtn').disabled = false;
            document.getElementById('dpv2ScanBtn').innerHTML = '<i class="fas fa-search"></i> Discover Devices';
            document.getElementById('dpv2ProgressBar').style.width = '0%';
            // Load online devices dropdown
            window._dpv2SelectedDevice = null;
            if (typeof dpv2LoadOnlineDevices === 'function') dpv2LoadOnlineDevices();
        }

        function openDeviceDiscoveryLanding() {
            const entrySection = document.getElementById('deviceDiscoveryEntrySection');
            const actualSection = document.getElementById('deviceDiscoveryActualSection');

            if (entrySection) {
                entrySection.classList.add('active');
                entrySection.style.display = 'block';
            }

            if (actualSection) {
                actualSection.classList.remove('active');
                actualSection.style.display = 'none';
            }
        }

        function showScanWithDevicesSection() {
            const entrySection = document.getElementById('deviceDiscoveryEntrySection');
            const actualSection = document.getElementById('deviceDiscoveryActualSection');

            if (entrySection) {
                entrySection.classList.remove('active');
                entrySection.style.display = 'none';
            }

            if (actualSection) {
                actualSection.classList.add('active');
                actualSection.style.display = 'block';
            }
        }

        function handleStaticIpDiscovery() {
            alert('Search by static IP feature will open here.');
        }

        // ✅ NEW: Check if any device is online
        function hasOnlineDevices() {
            try {
                // ✅ FIXED: Get the tbody with id 'devicesRows'
                const devicesRows = document.getElementById('devicesRows');
                if (!devicesRows) {
                    console.warn('devicesRows tbody not found');
                    return false;
                }
                
                // Get all rows (excluding header and search rows)
                const rows = devicesRows.querySelectorAll('tr');
                
                if (rows.length === 0) {
                    console.warn('No device rows found');
                    return false;
                }
                
                // ✅ FIXED: Status is in 2nd column (index 1)
                // Columns: Hostname (0), Status (1), Last Seen (2), Details (3)
                for (let row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 2) {
                        const statusCell = cells[1]; // Status column is at index 1
                        if (statusCell) {
                            const statusText = statusCell.textContent.trim().toUpperCase();
                            console.log('Checking device status:', statusText);
                            if (statusText.includes('ONLINE')) {
                                console.log('✅ Found online device!');
                                return true;
                            }
                        }
                    }
                }
                
                console.warn('No online devices found');
                return false;
            } catch (error) {
                console.error('Error checking online devices:', error);
                return false;
            }
        }

        // ✅ NEW: Validation wrapper for Issue Devices
        function openIssueDevicesWithValidation() {
            if (!hasOnlineDevices()) {
                showNotification('warning', '⚠️ No online devices available. Device issue is only available when at least one device is online. Please ensure devices are powered on and connected to the network.');
                return;
            }
            
            // Proceed with showing issues view
            showIssueDevices();
        }

        // ✅ NEW FUNCTION: Show Issue Devices directly from Devices Management
        function showIssueDevices() {
            console.log("cal showIssueDevices.......................")
            var _dv = document.getElementById('dashboardView');
            var _cv = document.getElementById('customersView');
            var _devv = document.getElementById('devicesView');
            var _iv = document.getElementById('issuesView');
            if (_dv) _dv.style.display = 'none';
            if (_cv) _cv.style.display = 'none';
            if (_devv) _devv.style.display = 'none';
            if (_iv) _iv.style.display = 'block';
            var _pt = document.getElementById('pageTitle');
            var _ps = document.getElementById('pageSubtitle');
            if (_pt) _pt.textContent = '⚙️ Issue Device';
            if (_ps) _ps.textContent = 'Manage issues and track resolution';
            
            if (devicesRefreshInterval) {
                clearInterval(devicesRefreshInterval);
                devicesRefreshInterval = null;
            }

            goToIssueType();
        }

        function goBackToDevicesView() {
            // Hide Issues View
            var _issV = document.getElementById('issuesView');
            if (_issV) _issV.style.display = 'none';

            // Show Devices View
            var _devV = document.getElementById('devicesView');
            if (_devV) _devV.style.display = 'block';

            // Hide other views (safely)
            var _dashV = document.getElementById('dashboardView');
            if (_dashV) _dashV.style.display = 'none';
            const usersViewEl = document.getElementById('usersView');
            if (usersViewEl) usersViewEl.style.display = 'none';
            const customersViewEl = document.getElementById('customersView');
            if (customersViewEl) customersViewEl.style.display = 'none';
            
            // Update page title
            document.getElementById('pageTitle').textContent = '🖥 Device Management';
            document.getElementById('pageSubtitle').textContent = 'All registered devices';
            
            // Reload devices data
            loadDevicesData();
            
            // Restart auto-refresh interval
            if (devicesRefreshInterval) {
                clearInterval(devicesRefreshInterval);
            }
            devicesRefreshInterval = setInterval(loadDevicesData, 5000);
            
            // Update active menu (optional - if you want to highlight Devices menu)
            document.querySelectorAll('.menu-item').forEach(item => {
                item.classList.remove('active');
            });
            document.getElementById('devicesMenu').classList.add('active');
        }

        function selectIssueType(type) {
            issueState.currentType = type;
            issueState.currentUserType = type;

            const typeLabel = 'Customer';
            document.getElementById('userListTitle').textContent = `Select ${typeLabel} for Issue:`;
            document.getElementById('issueTypeSelection').style.display = 'none';
            document.getElementById('userListContainer').style.display = 'block';

            loadUsersByType(type);
        }

        function goToIssueType() {
            console.log('call goToIssueType........................')
            document.getElementById('issueTypeSelection').style.display = 'block';
            document.getElementById('userListContainer').style.display = 'none';
            
            issueState.currentType = null;
            issueState.currentUserType = null;
            issueState.currentUserId = null;
        }

        function goBackFromUserList() {
            goToIssueType();
        }

        // ===== CUSTOMERS TABLE - STORE DATA =====
        let allCustomersDataForFilter = [];
        let isMobileView = window.innerWidth <= 768;

        // ===== CUSTOMERS TABLE - COLUMN FILTERS =====
        function applyCustomerColumnFilters() {
            const fullNameFilter = document.getElementById('searchFullName').value.toLowerCase().trim();
            const addressFilter = document.getElementById('searchAddress').value.toLowerCase().trim();
            const emailFilter = document.getElementById('searchEmail').value.toLowerCase().trim();
            const phoneFilter = document.getElementById('searchPhoneNo').value.toLowerCase().trim();
            const companyFilter = document.getElementById('searchCompany').value.toLowerCase().trim();
            const userIdFilter = document.getElementById('searchCustomerId').value.toLowerCase().trim();
            const statusFilter = document.getElementById('searchStatus').value.toLowerCase().trim();

            const customersTable = document.getElementById('customersTable');
            
            // ✅ Filter data
            let filteredCustomers = allCustomersDataForFilter.filter(customer => {
                const matchFullName = !fullNameFilter || (customer.name || '').toLowerCase().includes(fullNameFilter);
                const matchAddress = !addressFilter || (customer.address || '').toLowerCase().includes(addressFilter);
                const matchEmail = !emailFilter || (customer.email || '').toLowerCase().includes(emailFilter);
                const matchPhone = !phoneFilter || (customer.phone_no || '').toLowerCase().includes(phoneFilter);
                const matchCompany = !companyFilter || (customer.company_name || '').toLowerCase().includes(companyFilter);
                const matchUserId = !userIdFilter || (customer.user_id || customer.id || '').toString().toLowerCase().includes(userIdFilter);
                const matchStatus = !statusFilter || (customer.status || '').toLowerCase().includes(statusFilter);

                return matchFullName && matchAddress && matchEmail && matchPhone && matchCompany && matchUserId && matchStatus;
            });

            customersTable.innerHTML = '';

            if (filteredCustomers.length === 0) {
                customersTable.innerHTML = `
                    <tr>
                        <td colspan="7" style="text-align: center; padding: 40px; color: var(--gray-500);">
                            No matching customers found
                        </td>
                    </tr>
                `;
                return;
            }

            filteredCustomers.forEach(customer => {
                const statusColor = (customer.status || '').toLowerCase() === 'approved' ? '#10B981' : '#F59E0B';
                const statusBg = (customer.status || '').toLowerCase() === 'approved' ? 'rgba(16, 185, 129, 0.1)' : 'rgba(245, 158, 11, 0.1)';
                
                const detailId = 'customer_details_' + customer.user_id;
                if (isMobileView) {
                    // ===== MOBILE VIEW FOR DEALERS: Show only Name, Status + CLICKABLE ROW WITH ARROW =====
                    const row = document.createElement('tr');
                    row.style.cursor = 'pointer';
                    
                    row.innerHTML = `
                        <td data-label="Full Name">${escapeHtml(customer.name)}</td>
                        <td data-label="Status">
                            <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                                <span class="status-dot" style="background: ${statusColor};"></span>
                                ${customer.status}
                            </span>
                        </td>
                        
                    `;
                    
                    // ✅ Add click handler to row to open mobile detail page
                    row.addEventListener('click', function(e) {
                        e.stopPropagation();
                        openCustomerDetailPage(customer.user_id || customer.id);
                    });
                    
                    customersTable.appendChild(row);
                } else {
                    const row = document.createElement('tr');
                    row.style.cursor = 'pointer';
                    row.addEventListener('click', function(e) {
                        // Only open detail page if not clicking on button
                        if (e.target.tagName !== 'BUTTON') {
                            openCustomerDetailPage(customer.user_id || customer.id);
                        }
                    });
                
                row.innerHTML = `
                    <td data-label="Full Name" style="font-weight: 600;">${escapeHtml(customer.name)}</td>
                    <td data-label="Address">${escapeHtml(customer.address)}</td>
                    <td data-label="Email">${escapeHtml(customer.email)}</td>
                    <td data-label="Phone No">${escapeHtml(customer.phone_no || 'N/A')}</td>
                    <td data-label="Company">${escapeHtml(customer.company_name || 'N/A')}</td>
                    <td data-label="User ID" style="display:none;">${escapeHtml(String(customer.user_id))}</td>
                    <td data-label="Status">
                        <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                            <span class="status-dot" style="background: ${statusColor};"></span>
                            ${customer.status}
                        </span>
                    </td>
                    <td data-label="Details">
                        <button class="details-btn" onclick="event.stopPropagation(); toggleCustomerDetails('${detailId}')" title="View details">
                            View Details
                        </button>
                        <div id="${detailId}" class="details-container" style="display: none;">
                            <div class="details-grid">
                                <div class="details-item"><strong>Full Name:</strong> <code>${escapeHtml(customer.name)}</code></div>
                                <div class="details-item"><strong>Address:</strong> <code>${escapeHtml(customer.address)}</code></div>
                                <div class="details-item"><strong>Email:</strong> <code>${escapeHtml(customer.email)}</code></div>
                                <div class="details-item"><strong>Phone:</strong> <code>${escapeHtml(customer.phone_no || 'N/A')}</code></div>
                                <div class="details-item"><strong>Company:</strong> <code>${escapeHtml(customer.company_name || 'N/A')}</code></div>
                                <div class="details-item" style="display:none;"><strong>User ID:</strong> <code>${escapeHtml(String(customer.user_id))}</code></div>
                                <div class="details-item"><strong>Status:</strong> <code>${escapeHtml(customer.status)}</code></div>
                            </div>
                        </div>
                    </td>
                `;
                customersTable.appendChild(row);
                }
            });
        }

        function clearCustomerColumnFilters() {
            document.getElementById('searchFullName').value = '';
            document.getElementById('searchAddress').value = '';
            document.getElementById('searchEmail').value = '';
            document.getElementById('searchPhoneNo').value = '';
            document.getElementById('searchCompany').value = '';
            document.getElementById('searchCustomerId').value = '';
            document.getElementById('searchStatus').value = '';
            
            applyCustomerColumnFilters();
        }

        // ===== ISSUE DEVICE TABLE - COLUMN FILTERS =====
        function applyIssueColumnFilters() {
            const fullNameFilter = document.getElementById('issueSearchFullName').value.toLowerCase().trim();
            const addressFilter = document.getElementById('issueSearchAddress').value.toLowerCase().trim();
            const emailFilter = document.getElementById('issueSearchEmail').value.toLowerCase().trim();
            const phoneFilter = document.getElementById('issueSearchPhoneNo').value.toLowerCase().trim();
            const companyFilter = document.getElementById('issueSearchCompany').value.toLowerCase().trim();
            const codeFilter = document.getElementById('issueSearchCode').value.toLowerCase().trim();
            const statusFilter = document.getElementById('issueSearchStatus').value.toLowerCase().trim();

            const issueUserTable = document.getElementById('issueUserTable');
            
            // ✅ Filter data
            let filteredUsers = allCustomersDataForFilter.filter(customer => {
                const matchFullName = !fullNameFilter || (customer.name || '').toLowerCase().includes(fullNameFilter);
                const matchAddress = !addressFilter || (customer.address || '').toLowerCase().includes(addressFilter);
                const matchEmail = !emailFilter || (customer.email || '').toLowerCase().includes(emailFilter);
                const matchPhone = !phoneFilter || (customer.phone_no || '').toLowerCase().includes(phoneFilter);
                const matchCompany = !companyFilter || (customer.company_name || '').toLowerCase().includes(companyFilter);
                const matchCode = !codeFilter || (customer.user_id || customer.id || '').toString().toLowerCase().includes(codeFilter);
                const matchStatus = !statusFilter || (customer.status || '').toLowerCase().includes(statusFilter);

                return matchFullName && matchAddress && matchEmail && matchPhone && matchCompany && matchCode && matchStatus;
            });

            issueUserTable.innerHTML = '';

            if (filteredUsers.length === 0) {
                issueUserTable.innerHTML = `
                    <tr>
                        <td colspan="7" style="text-align: center; padding: 40px; color: var(--gray-500);">
                            No matching customers found
                        </td>
                    </tr>
                `;
                return;
            }

            // ✅ CHECK SCREEN SIZE FOR RESPONSIVE DISPLAY
            const isMobile = window.innerWidth <= 768;

            filteredUsers.forEach((customer) => {
                const statusColor = (customer.status || '').toLowerCase() === 'approved' ? '#10B981' : '#F59E0B';
                const statusBg = (customer.status || '').toLowerCase() === 'approved' ? 'rgba(16, 185, 129, 0.1)' : 'rgba(245, 158, 11, 0.1)';

                if (isMobile) {
                    // ===== MOBILE VIEW - CLICKABLE CARD =====
                    const row = document.createElement('tr');
                    row.setAttribute('onclick', `selectUserForIssue('${customer.user_id}', '${escapeHtml(customer.name)}', 'customer', '${customer.status}')`);
                    row.style.cursor = 'pointer';
                    
                    row.innerHTML = `
                        <td data-label="Full Name" style="font-weight: 600;">${escapeHtml(customer.name)}</td>
                        <td data-label="Status">
                            <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                                <span class="status-dot" style="background: ${statusColor};"></span>
                                ${customer.status}
                            </span>
                        </td>
                    `;
                    
                    issueUserTable.appendChild(row);
                } else {
                    // ===== DESKTOP VIEW - FULL TABLE =====
                    const row = document.createElement('tr');
                    row.innerHTML = `
                        <td data-label="Full Name" style="font-weight: 600;">${escapeHtml(customer.name)}</td>
                        <td data-label="Address">${escapeHtml(customer.address)}</td>
                        <td data-label="Email">${escapeHtml(customer.email)}</td>
                        <td data-label="Phone No">${escapeHtml(customer.phone_no || 'N/A')}</td>
                        <td data-label="Company">${escapeHtml(customer.company_name || 'N/A')}</td>
                        <td data-label="ID/Code" style="display:none;">${escapeHtml(String(customer.user_id || customer.id))}</td>
                        <td data-label="Status">
                            <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                                <span class="status-dot" style="background: ${statusColor};"></span>
                                ${customer.status}
                            </span>
                        </td>
                        <td data-label="Action">
                            <button class="details-btn" onclick="selectUserForIssue('${customer.user_id}', '${escapeHtml(customer.name)}', 'customer', '${customer.status}')">
                                Select Customer
                            </button>
                        </td>
                    `;
                    issueUserTable.appendChild(row);
                }
            });
        }

        function clearIssueColumnFilters() {
            document.getElementById('issueSearchFullName').value = '';
            document.getElementById('issueSearchAddress').value = '';
            document.getElementById('issueSearchEmail').value = '';
            document.getElementById('issueSearchPhoneNo').value = '';
            document.getElementById('issueSearchCompany').value = '';
            document.getElementById('issueSearchCode').value = '';
            document.getElementById('issueSearchStatus').value = '';
            
            applyIssueColumnFilters();
        }
        // const PGAK_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJmcmVzaCI6ZmFsc2UsImlhdCI6MTc3MDAyNTg3NSwianRpIjoiMDdmM2Q2MGItOTFiNi00ZTZhLTllODUtMzdhYjljY2MzNDYzIiwidHlwZSI6ImFjY2VzcyIsInN1YiI6IjEyNiIsIm5iZiI6MTc3MDAyNTg3NSwiZXhwIjoxNzcwMDI5NDc1fQ.lFL7738RdKvxc1pmIjLBHZeOTj4iEsf9p00yjl1MrBo";

        // ===== LOAD CUSTOMERS DATA =====
        async function loadCustomersData() {
            try {
                // ✅ Call YOUR backend proxy endpoint (no token needed - uses session)
                const response = await fetch('/api/dealer/customers', {
                    method: 'GET',
                    credentials: 'include'  // Important: send session cookie
                });
                
                if (response.status === 401) {
                    // ✅ FIX: Session sach mein expire hua ya sirf API glitch
                    console.warn('⚠️ Got 401, verifying session...');
                    try {
                        const checkRes = await fetch('/api/current-user', { credentials: 'include' });
                        if (checkRes.status === 401) {
                            alert('Session expire ho gayi. Dobara login karein.');
                            window.location.href = '/login';
                            return;
                        }
                        // Session theek hai, sirf API issue tha
                        console.log('✅ Session valid hai, reload karo.');
                        location.reload();
                    } catch(e) {
                        window.location.href = '/login';
                    }
                    return;
                }
                
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                
                const data = await response.json();
                console.log('✅ API Response:', data);
                
                const customersTable = document.getElementById('customersTable');
                customersTable.innerHTML = '';

                let customers = [];
                if (data.customers && Array.isArray(data.customers)) {
                    customers = data.customers;
                    console.log(`✅ Found ${customers.length} customers`);
                }

                if (customers.length === 0) {
                    customersTable.innerHTML = `
                        <tr>
                            <td colspan="7" style="text-align: center; padding: 40px; color: var(--gray-500);">
                                No customers found
                            </td>
                        </tr>
                    `;
                    return;
                }

                allCustomersDataForFilter = customers.map(customer => ({
                    user_id: customer.user_id || customer.id,
                    id: customer.user_id || customer.id,
                    name: customer.name || 'N/A',
                    address: customer.address || 'N/A',
                    email: customer.email || 'N/A',
                    phone_no: customer.phone_no || 'N/A',
                    company_name: customer.company_name || customer.company || 'N/A',
                    status: customer.status || 'Approved'
                }));
                window.allCustomersDataForFilter = allCustomersDataForFilter; // expose globally for analytics modal

                console.log('✅ Processed customers:', allCustomersDataForFilter);
                clearCustomerColumnFilters();
                applyCustomerColumnFilters();
                
            } catch (error) {
                console.error('❌ Error loading customers:', error);
                document.getElementById('customersTable').innerHTML = `
                    <tr>
                        <td colspan="7" style="text-align: center; padding: 40px; color: var(--danger);">
                            Error loading customers
                        </td>
                    </tr>
                `;
            }
        }

        // ✅ UPDATE loadUsersByType similarly
        async function loadUsersByType(userType) {
            try {
                const issueUserTable = document.getElementById('issueUserTable');
                let filteredUsers = [];

                if ((userType || '').toLowerCase() === 'customer') {
                    // ✅ Use session-based authentication
                    const response = await fetch('/api/dealer/customers', {
                        method: 'GET',
                        credentials: 'include'
                    });
                    
                    if (response.status === 401) {
                        // ✅ FIX: Verify before logout
                        console.warn('⚠️ Got 401 on retry, checking session...');
                        try {
                            const verifyRes = await fetch('/api/current-user', { credentials: 'include' });
                            if (verifyRes.status === 401) {
                                alert('Session expire ho gayi. Dobara login karein.');
                                window.location.href = '/login';
                                return;
                            }
                            location.reload();
                        } catch(e) {
                            window.location.href = '/login';
                        }
                        return;
                    }
                    
                    const data = await response.json();
                    console.log('📋 API Response:', data);
                    
                    let customersArray = [];
                    if (data.customers && Array.isArray(data.customers)) {
                        customersArray = data.customers;
                    }
                    
                    filteredUsers = customersArray.map(user => ({
                        id: user.user_id || user.id || '',
                        user_id: user.user_id || user.id || '',
                        name: user.name || user.full_name || 'N/A',
                        address: user.address || 'N/A',
                        email: user.email || 'N/A',
                        phone_no: user.phone_no || user.phone || 'N/A',
                        company_name: user.company_name || user.company || 'N/A',
                        status: user.status || 'Approved',
                        user_type: 'customer'
                    }));
                }

                allCustomersDataForFilter = filteredUsers;
                issueUserTable.innerHTML = '';

                if (filteredUsers.length === 0) {
                    issueUserTable.innerHTML = `
                        <tr>
                            <td colspan="7" style="text-align: center; padding: 40px; color: var(--gray-500);">
                                No customers found
                            </td>
                        </tr>
                    `;
                    return;
                }

                clearIssueColumnFilters();
                applyIssueColumnFilters();
                
            } catch (error) {
                console.error('Error loading users:', error);
                document.getElementById('issueUserTable').innerHTML = `
                    <tr>
                        <td colspan="7" style="text-align: center; padding: 40px; color: var(--danger);">
                            Error loading customers
                        </td>
                    </tr>
                `;
            }
        }
        
        function selectUserForIssue(userId, userName, userType, userStatus) {
            // Trim and normalize the status
            const normalizedStatus = userStatus ? userStatus.trim() : '';
            
            // Check if user status is Approved
            if ((normalizedStatus || '').toLowerCase() !== 'approved') {
                showNotification('error', `❌ Cannot scan! User status is "${normalizedStatus}". Only "Approved" users can scan devices.`);
                return;
            }
            
            issueState.currentUserId = userId;
            issueState.currentUserType = userType;
            issueState.currentUserData = { 
                name: userName, 
                user_type: userType,
                customer_id: userId  // ✅ Store customer ID
            };
        
            console.log("✅ Selected for issue:", { userId, userType, userName, status: normalizedStatus });
        
            openQRScanner();
            startScanning();
        }

        function onDeviceStatusFilterChange() {
            const sel = document.getElementById('deviceStatusFilter');
            if (!sel) return;
            const v = (sel.value === 'all') ? 'all' : 'online';
            localStorage.setItem('deviceStatusFilter', v);
            updateDevicesSubtitle();
            loadDevicesData();
        }

        function normalizeDbDeviceForUI(row) {
            const info = (row && typeof row === 'object' && row.info && typeof row.info === 'object') ? row.info : {};
            if (row && row.ip_address && !info['IP Address']) {
                info['IP Address'] = { 'Primary': String(row.ip_address) };
            }
            return {
                status: (row && row.status) ? String(row.status) : '',
                last_seen: (row && (row.last_seen || row.last_change_at)) ? String(row.last_seen || row.last_change_at) : '',
                info: info,
                ip_address: (row && row.ip_address) ? String(row.ip_address) : '',
                hostname: (row && row.hostname) ? String(row.hostname) : '',
                serial_number: (row && row.serial_number) ? String(row.serial_number) : ''  // ✅ Primary unique key
            };
        }

        function normalizeDevicesPayload(payload) {
            // DB API shape: {status:'success', devices:[...]}
            if (payload && Array.isArray(payload.devices)) {
                // ✅ DEDUPLICATION: same serial_number ke liye sirf ek record rakho
                // Preference: hostname wala record > sirf serial wala record
                const seen = new Map();
                for (const r of payload.devices) {
                    const key = (r.serial_number && String(r.serial_number).trim()) ||
                                (r.hostname && String(r.hostname).trim()) ||
                                (r.ip_address && String(r.ip_address).trim()) ||
                                (r.id ? ('Device-' + r.id) : 'Device');

                    if (!seen.has(key)) {
                        // First time is record ko store karo
                        seen.set(key, r);
                    } else {
                        // Duplicate mila — hostname wale ko prefer karo
                        const existing = seen.get(key);
                        const existingHasHostname = existing.hostname && String(existing.hostname).trim() !== key;
                        const newHasHostname = r.hostname && String(r.hostname).trim() !== key;
                        if (!existingHasHostname && newHasHostname) {
                            // Naya record better hai (hostname hai) — replace karo
                            seen.set(key, r);
                        }
                        // Warna existing rakhte hain
                    }
                }
                return Array.from(seen.entries()).map(([key, r]) => [key, normalizeDbDeviceForUI(r)]);
            }
            // Legacy in-memory shape: {hostname: {status, last_seen, info}, ...}
            if (payload && typeof payload === 'object') {
                return Object.entries(payload);
            }
            return [];
        }

        function initDeviceStatusFilter() {
            const sel = document.getElementById('deviceStatusFilter');
            if (!sel) return;
            let v = localStorage.getItem('deviceStatusFilter');
            if (v !== 'all' && v !== 'online') v = 'online';
            sel.value = v;
        }

        function updateDevicesSubtitle() {
            const sel = document.getElementById('deviceStatusFilter');
            const v = sel ? sel.value : (localStorage.getItem('deviceStatusFilter') || 'online');
            const subtitle = (v === 'all') ? 'All devices (Online + Offline)' : 'Online devices only';
            const el = document.getElementById('pageSubtitle');
            if (el) el.textContent = subtitle;
        }

        function onDeviceStatusFilterChange() {
            const sel = document.getElementById('deviceStatusFilter');
            if (!sel) return;
            const v = (sel.value === 'all') ? 'all' : 'online';
            localStorage.setItem('deviceStatusFilter', v);
            updateDevicesSubtitle();
            loadDevicesData();
        }



        async function loadDashboardData() {
            try {
                // ✅ Fetch customers
                const customersRes = await fetch('/api/dealer/customers', {
                    method: 'GET',
                    credentials: 'include',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                });
                
                if (customersRes.status === 401) {
                    // ✅ FIX: Verify before logout
                    console.warn('⚠️ Got 401 on customers, verifying session...');
                    try {
                        const verifyRes = await fetch('/api/current-user', { credentials: 'include' });
                        if (verifyRes.status === 401) {
                            alert('Session expire ho gayi. Dobara login karein.');
                            window.location.href = '/login';
                            return;
                        }
                        location.reload();
                    } catch(e) {
                        window.location.href = '/login';
                    }
                    return;
                }
                
                if (!customersRes.ok) {
                    throw new Error(`HTTP ${customersRes.status}`);
                }
                
                const customersData = await customersRes.json();

                let customersCount = 0;
                if (customersData.customers && Array.isArray(customersData.customers)) {
                    customersCount = customersData.customers.length;
                }

                // ✅ Fetch dealer-specific devices count
                let dealerIdForDash = window._dealerId || null;
                if (!dealerIdForDash) {
                    try {
                        const uRes = await fetch('/api/current-user', { credentials: 'include' });
                        if (uRes.ok) { const ud = await uRes.json(); dealerIdForDash = ud.id || ud.user_id || null; window._dealerId = dealerIdForDash; }
                    } catch(e) {}
                }
                const devicesUrl = dealerIdForDash
                    ? `/dealer/devices?dealer_id=${encodeURIComponent(dealerIdForDash)}&filter=all`
                    : '/devices_status?filter=all';
                const devicesRes = await fetch(devicesUrl, { credentials: 'include' }).catch(() => ({ 
                    json: () => Promise.resolve({ devices: [] }) 
                }));
                const devicesData = await devicesRes.json();
                
                const deviceEntries = normalizeDevicesPayload(devicesData);
                let onlineCount = 0, offlineCount = 0;
                for (const [key, val] of deviceEntries) {
                    const st = (val && val.status) ? String(val.status).toUpperCase() : '';
                    if (st === 'ACTIVE' || st === 'ONLINE') onlineCount++;
                    else offlineCount++;
                }

                // ✅ Update dashboard stats
                var totalCustomersEl = document.getElementById('totalCustomers');
                var totalDevicesEl = document.getElementById('totalDevices');
                if (totalCustomersEl) totalCustomersEl.textContent = customersCount;
                if (totalDevicesEl) totalDevicesEl.textContent = deviceEntries.length;
                
                const onlineDevicesEl = document.getElementById('onlineDevices');
                const offlineDevicesEl = document.getElementById('offlineDevices');
                if (onlineDevicesEl) onlineDevicesEl.textContent = onlineCount;
                if (offlineDevicesEl) offlineDevicesEl.textContent = offlineCount;
                
            } catch (error) {
                console.error('Error loading dashboard data:', error);
                // ✅ Set default values on error
                var _tc = document.getElementById('totalCustomers');
                var _td = document.getElementById('totalDevices');
                var _on = document.getElementById('onlineDevices');
                var _of = document.getElementById('offlineDevices');
                if (_tc) _tc.textContent = '0';
                if (_td) _td.textContent = '0';
                if (_on) _on.textContent = '0';
                if (_of) _of.textContent = '0';
            }
        }

        function toggleCustomerDetails(detailId) {
            const el = document.getElementById(detailId);
            if(!el) return;
            el.style.display = el.style.display === "block" ? "none" : "block";
        }

        function escapeHtml(text) {
            const map = {
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#039;'
            };
            return String(text).replace(/[&<>"']/g, m => map[m]);
        }

        async function loadDevicesData() {
            try {
                const filterSel = document.getElementById('deviceStatusFilter');
                const filter = filterSel ? filterSel.value : (localStorage.getItem('deviceStatusFilter') || 'online');

                // ✅ Get dealer_id from cached value or current-user API
                let dealerId = window._dealerId || null;
                if (!dealerId) {
                    try {
                        const userResp = await fetch('/api/current-user', { credentials: 'include' });
                        if (userResp.ok) {
                            const userData = await userResp.json();
                            dealerId = userData.id || userData.user_id || null;
                            window._dealerId = dealerId;
                        }
                    } catch (e) {
                        console.warn('Could not fetch dealer ID:', e);
                    }
                }

                let data = null;
                try {
                    // ✅ Dealer-specific API: sirf is dealer ke issued devices dikhao
                    const url = dealerId
                        ? `/dealer/devices?dealer_id=${encodeURIComponent(dealerId)}&filter=${encodeURIComponent(filter)}`
                        : `/devices_status?filter=${encodeURIComponent(filter)}`;
                    const resp = await fetch(url, { credentials: 'include' });
                    data = await resp.json();
                } catch (e) {
                    data = null;
                }

                // If API is not available, fallback to legacy in-memory endpoint
                if (!data || !Array.isArray(data.devices)) {
                    const resp2 = await fetch('/devices');
                    data = await resp2.json();
                }

                const entries = normalizeDevicesPayload(data);
                const devicesRows = document.getElementById('devicesRows');
                if (entries.length === 0) {
                    devicesRows.innerHTML = `
                        <tr>
                            <td colspan="5" style="text-align: center; padding: 40px; color: var(--gray-500);">
                                📭 No devices found
                            </td>
                        </tr>
                    `;
                    return;
                }
                devicesRows.innerHTML = '';
                let totalDevices = 0, activeCount = 0, inactiveCount = 0;
                const savedOpenDetails = JSON.parse(localStorage.getItem('deviceDetailsOpen') || '{}');
                
                for(const [host, dRaw] of entries) {
                    const d = dRaw || {};

                    totalDevices++;
                    const st = (d.status || '').toString().toUpperCase();
                    const isOnline = (st === 'ACTIVE' || st === 'ONLINE');
                    const displayStatus = isOnline ? 'ONLINE' : 'OFFLINE';
                   
                    if (isOnline) activeCount++;
                    else inactiveCount++;
                    const info = d.info || {};
                    const did = 'device_details_' + host.replaceAll(' ', '_').replaceAll('.', '_').replaceAll('-', '_');
                    const statusColor = isOnline ? 'var(--success)' : 'var(--danger)';
                    const statusBadgeClass = isOnline ? 'status-active' : 'status-offline';
                    const isDetailsOpen = savedOpenDetails[did] || false;
                    
                    // Check if mobile view
                    const isMobile = window.innerWidth <= 768;
                    
                    if (isMobile) {
                        // Mobile view - card layout (similar to users)
                        const row = document.createElement('tr');
                        row.setAttribute('onclick', `openDeviceDetailPage('${host}', '${JSON.stringify(d).replace(/'/g, "\\'")}')`);
                        row.style.cursor = 'pointer';
                        
                        row.innerHTML = `
                            <td data-label="Hostname" style="font-weight: 600; color: var(--gray-900);">
                                ${escapeHtml(d.hostname || host)}
                                ${d.hostname && d.hostname !== host ? `<div style="font-size: 12px; font-weight: 400; color: var(--gray-500); margin-top: 2px;">${escapeHtml(host)}</div>` : ''}
                            </td>
                            <td data-label="Status">
                                <span class="status-badge ${statusBadgeClass}">
                                    <span class="status-dot" style="background: ${statusColor};"></span>
                                    ${displayStatus}
                                </span>
                            </td>
                            <td data-label="Last Seen" style="color: var(--gray-600);">${d.last_seen || 'Never'}</td>
                        `;
                        devicesRows.appendChild(row);
                    } else {
                        // Desktop view - full table with toggle button
                        const serial = info['Serial Number'] || info['Serial'] || info['serial_number'] || 'N/A';
                        const row = document.createElement('tr');
                        row.setAttribute('onclick', `toggleDeviceDetails('${did}', this)`);
                        row.style.cursor = 'pointer';
                        
                        row.innerHTML = `
                            <td data-label="Hostname" style="font-weight: 600; color: var(--gray-900);">
                                ${escapeHtml(d.hostname || host)}
                                ${d.hostname && d.hostname !== host ? `<div style="font-size: 12px; font-weight: 400; color: var(--gray-500); margin-top: 2px;">${escapeHtml(host)}</div>` : ''}
                            </td>
                            <td data-label="Status">
                                <span class="status-badge ${statusBadgeClass}">
                                    <span class="status-dot" style="background: ${statusColor};"></span>
                                    ${displayStatus}
                                </span>
                            </td>
                            <td data-label="Last Seen" style="color: var(--gray-600);">${d.last_seen || 'Never'}</td>
                            <td data-label="Details">
                                <button class="details-btn" onclick="event.stopPropagation(); toggleDetails(this, '${did}')">${isDetailsOpen ? 'Hide Details' : 'Show Details'}</button>
                            </td>
                            
                        `;
                        devicesRows.appendChild(row);
                        
                        const detailsContainer = document.createElement('tr');
                        detailsContainer.innerHTML = `
                            <td colspan="4">
                                <div id="${did}" class="details-container" style="display: ${isDetailsOpen ? 'block' : 'none'};">
                                    <div class="details-grid">
                                        <div class="details-item">
                                            <strong>OS</strong>
                                            <code>${escapeHtml(info['OS'] || 'N/A')}</code>
                                        </div>
                                        <div class="details-item">
                                            <strong>OS Version</strong>
                                            <code>${escapeHtml(info['OS Version'] || 'N/A')}</code>
                                        </div>
                                        <div class="details-item">
                                            <strong>Kernel</strong>
                                            <code>${escapeHtml(info['Kernel Version'] || 'N/A')}</code>
                                        </div>
                                        <div class="details-item">
                                            <strong>Processor</strong>
                                            <code>${escapeHtml(info['Processor'] || 'N/A')}</code>
                                        </div>
                                        <div class="details-item">
                                            <strong>Machine Type</strong>
                                            <code>${escapeHtml(info['Machine (OS Type)'] || info['Machine'] || 'N/A')}</code>
                                        </div>
                                        <div class="details-item">
                                            <strong>Make</strong>
                                            <code>${escapeHtml(info['Make'] || 'N/A')}</code>
                                        </div>
                                        <div class="details-item">
                                            <strong>Model</strong>
                                            <code>${escapeHtml(info['Model'] || 'N/A')}</code>
                                        </div>
                                        <div class="details-item">
                                            <strong>Serial Number</strong>
                                            <code>${escapeHtml(info['Serial Number'] || 'N/A')}</code>
                                        </div>
                                        <div style="grid-column: 1 / -1;">
                                            <strong style="display: block; margin-bottom: 8px; color: var(--primary);">MAC Addresses:</strong>
                                            <div style="background: white; padding: 12px; border-radius: 6px; border-left: 3px solid var(--primary);">
                                                ${formatMacAddresses(info['MAC Addresses'] || {})}
                                            </div>
                                        </div>
                                        <div style="grid-column: 1 / -1;">
                                            <strong style="display: block; margin-bottom: 8px; color: var(--primary);">IP Addresses:</strong>
                                            <div style="background: white; padding: 12px; border-radius: 6px; border-left: 3px solid var(--primary);">
                                                ${formatIpAddresses(info['IP Address'] || {})}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </td>
                        `;
                        devicesRows.appendChild(detailsContainer);
                    }
                }
                updateDevicesStats(totalDevices, activeCount, inactiveCount);
            } catch (error) {
                console.error('Error:', error);
                document.getElementById('devicesRows').innerHTML = `
                    <tr>
                        <td colspan="4" style="text-align: center; padding: 40px; color: var(--danger);">
                            ❌ Failed to load devices
                        </td>
                    </tr>
                `;
            }
        }

        function formatMacAddresses(macData) {
            if (!macData || Object.keys(macData).length === 0) {
                return '<span style="color: var(--gray-400);">No MAC addresses found</span>';
            }

            let html = '';

            if (Array.isArray(macData)) {
                macData.forEach(item => {
                    if (typeof item === 'string' && item.trim()) {
                        html += `<div style="margin-bottom: 8px;"><code style="background: var(--gray-100); padding: 4px 6px; border-radius: 4px; font-size: 12px;">${escapeHtml(item)}</code></div>`;
                    } else if (item && typeof item === 'object') {
                        const label = item.interface || item.name || '';
                        const addr = item.mac || item.address || '';
                        if (label && addr) {
                            html += `<div style="margin-bottom: 8px;"><code style="background: var(--gray-100); padding: 4px 6px; border-radius: 4px; font-size: 12px;">${escapeHtml(label)}: ${escapeHtml(addr)}</code></div>`;
                        }
                    }
                });
                if (html) return html;
            }

            if (typeof macData === 'object' && !Array.isArray(macData)) {
                let isSimpleKV = true;
                for (const value of Object.values(macData)) {
                    if (typeof value !== 'string' && !Array.isArray(value)) {
                        isSimpleKV = false;
                        break;
                    }
                }

                if (isSimpleKV) {
                    let hasData = false;
                    Object.entries(macData).forEach(([key, value]) => {
                        if (typeof value === 'string' && value.trim()) {
                            hasData = true;
                            html += `<div style="margin-bottom: 8px;"><code style="background: var(--gray-100); padding: 4px 6px; border-radius: 4px; font-size: 12px;">${escapeHtml(key)}: ${escapeHtml(value)}</code></div>`;
                        }
                    });
                    if (hasData) return html;
                }
            }

            if (typeof macData === 'object' && !Array.isArray(macData)) {
                Object.entries(macData).forEach(([category, items]) => {
                    if (Array.isArray(items) && items.length > 0) {
                        html += `<div style="margin-bottom: 12px;"><strong style="color: var(--gray-700); display: block; margin-bottom: 6px; font-size: 12px;">${escapeHtml(category)}</strong>`;
                        
                        items.forEach(item => {
                            if (typeof item === 'string' && item.trim()) {
                                html += `<div style="margin-left: 8px; margin-bottom: 6px;"><code style="background: var(--gray-100); padding: 4px 6px; border-radius: 4px; font-size: 12px;">${escapeHtml(item)}</code></div>`;
                            } else if (item && typeof item === 'object') {
                                const label = item.interface || item.name || '';
                                const addr = item.mac || item.address || '';
                                if (label || addr) {
                                    const text = label && addr ? `${label}: ${addr}` : (label || addr);
                                    html += `<div style="margin-left: 8px; margin-bottom: 6px;"><code style="background: var(--gray-100); padding: 4px 6px; border-radius: 4px; font-size: 12px;">${escapeHtml(text)}</code></div>`;
                                }
                            }
                        });
                        html += `</div>`;
                    } else if (typeof items === 'string' && items.trim()) {
                        html += `<div style="margin-bottom: 8px;"><strong style="color: var(--gray-700); display: block; margin-bottom: 4px; font-size: 12px;">${escapeHtml(category)}</strong><code style="background: var(--gray-100); padding: 4px 6px; border-radius: 4px; font-size: 12px;">${escapeHtml(items)}</code></div>`;
                    }
                });
                if (html) return html;
            }

            const fallback = JSON.stringify(macData);
            if (fallback && fallback !== '{}') {
                return `<code style="background: var(--gray-100); padding: 6px; border-radius: 4px; display: block; font-size: 12px; word-break: break-all;">${escapeHtml(fallback)}</code>`;
            }

            return '<span style="color: var(--gray-400);">No MAC addresses found</span>';
        }

        function formatIpAddresses(ipData) {
            if (!ipData || Object.keys(ipData).length === 0) {
                return '<span style="color: var(--gray-400);">No IP addresses found</span>';
            }

            let html = '';

            if (Array.isArray(ipData)) {
                ipData.forEach(item => {
                    if (typeof item === 'string' && item.trim()) {
                        html += `<div style="margin-bottom: 8px;"><code style="background: var(--gray-100); padding: 4px 6px; border-radius: 4px; font-size: 12px;">${escapeHtml(item)}</code></div>`;
                    } else if (item && typeof item === 'object') {
                        const label = item.interface || item.name || item.type || '';
                        const addr = item.address || item.ip || item.value || '';
                        if (label && addr) {
                            html += `<div style="margin-bottom: 8px;"><code style="background: var(--gray-100); padding: 4px 6px; border-radius: 4px; font-size: 12px;">${escapeHtml(label)}: ${escapeHtml(addr)}</code></div>`;
                        }
                    }
                });
                if (html) return html;
            }

            if (typeof ipData === 'object' && !Array.isArray(ipData)) {
                let isSimpleKV = true;
                
                for (const value of Object.values(ipData)) {
                    if (typeof value !== 'string' && !Array.isArray(value)) {
                        isSimpleKV = false;
                        break;
                    }
                }

                if (isSimpleKV) {
                    let hasData = false;
                    Object.entries(ipData).forEach(([key, value]) => {
                        if (typeof value === 'string' && value.trim()) {
                            hasData = true;
                            html += `<div style="margin-bottom: 8px;"><code style="background: var(--gray-100); padding: 4px 6px; border-radius: 4px; font-size: 12px;">${escapeHtml(key)}: ${escapeHtml(value)}</code></div>`;
                        }
                    });
                    if (hasData) return html;
                }
            }

            if (typeof ipData === 'object' && !Array.isArray(ipData)) {
                Object.entries(ipData).forEach(([category, items]) => {
                    if (Array.isArray(items) && items.length > 0) {
                        html += `<div style="margin-bottom: 12px;"><strong style="color: var(--gray-700); display: block; margin-bottom: 6px; font-size: 12px;">${escapeHtml(category)}</strong>`;
                        
                        items.forEach(item => {
                            if (typeof item === 'string' && item.trim()) {
                                html += `<div style="margin-left: 8px; margin-bottom: 6px;"><code style="background: var(--gray-100); padding: 4px 6px; border-radius: 4px; font-size: 12px;">${escapeHtml(item)}</code></div>`;
                            } else if (item && typeof item === 'object') {
                                const label = item.interface || item.name || '';
                                const addr = item.address || item.ip || '';
                                if (label || addr) {
                                    const text = label && addr ? `${label}: ${addr}` : (label || addr);
                                    html += `<div style="margin-left: 8px; margin-bottom: 6px;"><code style="background: var(--gray-100); padding: 4px 6px; border-radius: 4px; font-size: 12px;">${escapeHtml(text)}</code></div>`;
                                }
                            }
                        });
                        html += `div>`;
                    } else if (typeof items === 'string' && items.trim()) {
                        html += `<div style="margin-bottom: 8px;"><strong style="color: var(--gray-700); display: block; margin-bottom: 4px; font-size: 12px;">${escapeHtml(category)}</strong><code style="background: var(--gray-100); padding: 4px 6px; border-radius: 4px; font-size: 12px;">${escapeHtml(items)}</code></div>`;
                    }
                });
                if (html) return html;
            }

            const fallback = JSON.stringify(ipData);
            if (fallback && fallback !== '{}') {
                return `<code style="background: var(--gray-100); padding: 6px; border-radius: 4px; display: block; font-size: 12px; word-break: break-all;">${escapeHtml(fallback)}</code>`;
            }

            return '<span style="color: var(--gray-400);">No IP addresses found</span>';
        }
        
        // ✅ NEW FUNCTION: Open customer detail page (mobile)
        function openCustomerDetailPage(customerId) {
            try {
                const customer = allCustomersDataForFilter.find(c => c.user_id === customerId);
                if (!customer) {
                    alert('Customer not found');
                    return;
                }
                
                const detailPage = document.getElementById('customerMobileDetailPage');
                document.getElementById('customerMobileDetailTitle').textContent = customer.name;
                
                const detailContent = document.getElementById('customerMobileDetailContent');
                const statusColor = (customer.status || '').toLowerCase() === 'approved' ? '#10B981' : '#F59E0B';
                const statusBg = (customer.status || '').toLowerCase() === 'approved' ? 'rgba(16, 185, 129, 0.1)' : 'rgba(245, 158, 11, 0.1)';
                detailContent.innerHTML = `
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Full Name</div>
                    <div class="mobile-detail-value">${escapeHtml(customer.name)}</div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Address</div>
                    <div class="mobile-detail-value">${escapeHtml(customer.address)}</div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Email</div>
                    <div class="mobile-detail-value">${escapeHtml(customer.email)}</div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Phone Number</div>
                    <div class="mobile-detail-value">${escapeHtml(customer.phone_no || 'N/A')}</div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Company</div>
                    <div class="mobile-detail-value">${escapeHtml(customer.company_name || 'N/A')}</div>
                </div>
                <div class="mobile-detail-section" style="display:none;">
                        <div class="mobile-detail-label">Customer ID</div>
                        <div class="mobile-detail-value">${escapeHtml(customer.user_id)}</div>
                    </div>
                    <div class="mobile-detail-section">
                        <div class="mobile-detail-label">Status</div>
                        <div class="mobile-detail-value">
                            <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                                <span class="status-dot" style="background: ${statusColor};"></span>
                                ${customer.status}
                            </span>
                        </div>
                    </div>
                `;
                detailPage.classList.add('active');  
            } catch (error) {
                console.error('Error opening customer detail page:', error);
                alert('Error loading customer details');
            }
        }
        
        // ✅ NEW FUNCTION: Open device detail page (mobile)
        function openDeviceDetailPage(serialKey, deviceData) {
            try {
                const device = JSON.parse(deviceData);
                const info = device.info || {};
                const detailPage = document.getElementById('deviceMobileDetailPage');
                // ✅ Show serial number as title (same as admin dashboard)
                const displayTitle = info['Serial Number'] || device.serial_number || serialKey;
                document.getElementById('deviceMobileDetailTitle').textContent = displayTitle;
                
                const detailContent = document.getElementById('deviceMobileDetailContent');
                const isOnline = ((device.status || '').toUpperCase() === 'ACTIVE' || (device.status || '').toUpperCase() === 'ONLINE');
                const statusColor = isOnline ? '#10B981' : '#EF4444';
                const statusBg = isOnline ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)';
                
                detailContent.innerHTML = `
                    <div class="mobile-detail-section">
                        <div class="mobile-detail-label">Serial Number</div>
                        <div class="mobile-detail-value">${escapeHtml(info['Serial Number'] || device.serial_number || 'N/A')}</div>
                    </div>
                    <div class="mobile-detail-section">
                        <div class="mobile-detail-label">Hostname</div>
                        <div class="mobile-detail-value">${escapeHtml(device.hostname || serialKey)}</div>
                    </div>
                    <div class="mobile-detail-section">
                        <div class="mobile-detail-label">Status</div>
                        <div class="mobile-detail-value">
                            <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                                <span class="status-dot" style="background: ${statusColor};"></span>
                                ${isOnline ? 'ONLINE' : 'OFFLINE'}
                            </span>
                        </div>
                    </div>
                    <div class="mobile-detail-section">
                        <div class="mobile-detail-label">Last Seen</div>
                        <div class="mobile-detail-value">${device.last_seen || 'Never'}</div>
                    </div>
                    <div class="mobile-detail-section">
                        <div class="mobile-detail-label">OS</div>
                        <div class="mobile-detail-value">${escapeHtml(info['OS'] || 'N/A')}</div>
                    </div>
                    <div class="mobile-detail-section">
                        <div class="mobile-detail-label">OS Version</div>
                        <div class="mobile-detail-value">${escapeHtml(info['OS Version'] || 'N/A')}</div>
                    </div>
                    <div class="mobile-detail-section">
                        <div class="mobile-detail-label">Kernel Version</div>
                        <div class="mobile-detail-value">${escapeHtml(info['Kernel Version'] || 'N/A')}</div>
                    </div>
                    <div class="mobile-detail-section">
                        <div class="mobile-detail-label">Processor</div>
                        <div class="mobile-detail-value">${escapeHtml(info['Processor'] || 'N/A')}</div>
                    </div>
                    <div class="mobile-detail-section">
                        <div class="mobile-detail-label">Machine Type</div>
                        <div class="mobile-detail-value">${escapeHtml(info['Machine (OS Type)'] || 'N/A')}</div>
                    </div>
                    <div class="mobile-detail-section">
                        <div class="mobile-detail-label">Make</div>
                        <div class="mobile-detail-value">${escapeHtml(info['Make'] || 'N/A')}</div>
                    </div>
                    <div class="mobile-detail-section">
                        <div class="mobile-detail-label">Model</div>
                        <div class="mobile-detail-value">${escapeHtml(info['Model'] || 'N/A')}</div>
                    </div>
                    <div class="mobile-detail-section">
                        <div class="mobile-detail-label">MAC Addresses</div>
                        <div class="mobile-detail-value" style="font-size: 12px; color: var(--gray-600);">
                            ${formatMacAddressesForMobile(info['MAC Addresses'] || {})}
                        </div>
                    </div>
                    <div class="mobile-detail-section">
                        <div class="mobile-detail-label">IP Addresses</div>
                        <div class="mobile-detail-value" style="font-size: 12px; color: var(--gray-600);">
                            ${formatIpAddressesForMobile(info['IP Address'] || {})}
                        </div>
                    </div>
                `;
                detailPage.classList.add('active');
            } catch (error) {
                console.error('Error opening device detail page:', error);
                alert('Error loading device details');
            }
        }
        
        // ✅ NEW FUNCTION: Format MAC addresses for mobile view
        function formatMacAddressesForMobile(macData) {
            if (!macData || Object.keys(macData).length === 0) {
                return 'No MAC addresses found';
            }
            try {
                if (typeof macData === 'object') {
                    if (Array.isArray(macData)) {
                        return macData.map(item => escapeHtml(typeof item === 'string' ? item : JSON.stringify(item))).join('<br>');
                    } else {
                        return Object.entries(macData).map(([key, value]) => 
                            `${escapeHtml(key)}: ${escapeHtml(typeof value === 'string' ? value : JSON.stringify(value))}`
                        ).join('<br>');
                    }
                }
                return escapeHtml(String(macData));
            } catch (e) {
                return 'Error displaying MAC addresses';
            }
        }
        
        // ✅ NEW FUNCTION: Format IP addresses for mobile view
        function formatIpAddressesForMobile(ipData) {
            if (!ipData || Object.keys(ipData).length === 0) {
                return 'No IP addresses found';
            }
            try {
                if (typeof ipData === 'object') {
                    if (Array.isArray(ipData)) {
                        return ipData.map(item => escapeHtml(typeof item === 'string' ? item : JSON.stringify(item))).join('<br>');
                    } else {
                        return Object.entries(ipData).map(([key, value]) => 
                            `${escapeHtml(key)}: ${escapeHtml(typeof value === 'string' ? value : JSON.stringify(value))}`
                        ).join('<br>');
                    }
                }
                return escapeHtml(String(ipData));
            } catch (e) {
                return 'Error displaying IP addresses';
            }
        }
        
        // ✅ NEW FUNCTION: Close customer detail page
        function closeCustomerDetailPage() {
            const detailPage = document.getElementById('customerMobileDetailPage');
            detailPage.classList.remove('active');
        }
        
        // ✅ NEW FUNCTION: Close device detail page
        function closeDeviceDetailPage() {
            const detailPage = document.getElementById('deviceMobileDetailPage');
            detailPage.classList.remove('active');
        }
        
        // ✅ Desktop function for toggling device details
        function toggleDetails(button, detailsId) {
            event.stopPropagation(); // Prevent row click event
            const container = document.getElementById(detailsId);
            if (!container) return;
            const isVisible = container.style.display !== 'none';
            container.style.display = isVisible ? 'none' : 'block';
            button.textContent = isVisible ? 'Show Details' : 'Hide Details';
            const savedOpenDetails = JSON.parse(localStorage.getItem('deviceDetailsOpen') || '{}');
            savedOpenDetails[detailsId] = !isVisible;
            localStorage.setItem('deviceDetailsOpen', JSON.stringify(savedOpenDetails));
        }
        
        function updateDevicesStats(total, online, offline) {
            var el1 = document.getElementById('totalDevices');
            var el2 = document.getElementById('onlineDevices');
            var el3 = document.getElementById('offlineDevices');
            if (el1) el1.textContent = total;
            if (el2) el2.textContent = online;
            if (el3) el3.textContent = offline;
        }

        function openQRScanner() {
            qrScanInProgress = false;
            scanSuccessful = false;
            document.getElementById('qrModalOverlay').classList.add('active');
        
            nextStepContainer.classList.remove('show');
        
            macHighlight.classList.remove("show");
            validationMessage.classList.remove("show", "error", "success", "warning");
            successMessage.style.display = 'none';
            errorMessage.style.display = 'none';
            clearStatusMessage();  // ← NEW LINE ADDED
        
            macValue.textContent = '';
            validationText.textContent = '';
        
            qrStatus.textContent = "✓ Camera ready or device selected.";
            qrStatus.className = "qr-status success";
        }

        function closeQRScanner() {
            document.getElementById('qrModalOverlay').classList.remove('active');
            stopScanning();
            resetQRScanner();
        }

        function resetQRScanner() {
            qrScanInProgress = false;
            scanSuccessful = false;
            selectedOnvifSerial = null;
            issueState.lastScannedSerial = null;

            nextStepContainer.classList.remove('show');

            macHighlight.classList.remove("show");
            validationMessage.classList.remove("show", "error", "success", "warning");
            successMessage.style.display = 'none';
            errorMessage.style.display = 'none';
            clearStatusMessage();  // ← NEW LINE ADDED

            macValue.textContent = '';
            validationText.textContent = '';

            var manualInput = document.getElementById('manualSerialInput');
            if (manualInput) manualInput.style.display = 'none';
        
            qrStatus.textContent = "✓ Ready.";
            qrStatus.className = "qr-status success";
        
            const deviceListDiv = document.getElementById('discovered-devices-list');
            if (deviceListDiv) deviceListDiv.remove();
        }

        function submitManualSerial() {
            var field = document.getElementById('manualSerialField');
            if (!field) return;
            var serial = field.value.trim().toUpperCase();
            if (!serial) {
                field.style.borderColor = 'var(--danger)';
                field.focus();
                return;
            }
            field.style.borderColor = 'var(--gray-300)';
            handleQRDetected(serial);
        }

        function showManualSerialInput() {
            var manualInput = document.getElementById('manualSerialInput');
            if (manualInput) {
                manualInput.style.display = 'block';
                var field = document.getElementById('manualSerialField');
                if (field) { field.value = ''; field.focus(); }
            }
        }

        async function startScanning() {
            try {
                if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                    var isSecure = location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1';
                    showManualSerialInput();
                    return;
                }
                stream = await navigator.mediaDevices.getUserMedia({
                    video: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 } }
                });
                qrVideo.srcObject = stream;
                qrVideo.play();
                qrVideo.style.display = 'block';
                scanning = true;
                qrStatus.textContent = "✓ Camera active – point at a QR code.";
                qrStatus.className = "qr-status success";
                scanLoop();
            } catch (err) {
                console.error(err);
                showManualSerialInput();
            }
        }

        function stopScanning() {
            scanning = false;
            if (rafId) cancelAnimationFrame(rafId);
            rafId = null;
            if (stream) {
                stream.getTracks().forEach(track => track.stop());
                stream = null;
            }
            qrVideo.srcObject = null;
            qrVideo.style.display = 'none';
        }

        function scanLoop() {
            if (!scanning || qrVideo.readyState !== qrVideo.HAVE_ENOUGH_DATA) {
                rafId = requestAnimationFrame(scanLoop);
                return;
            }

            qrCanvas.width = qrVideo.videoWidth;
            qrCanvas.height = qrVideo.videoHeight;
            qrCtx.drawImage(qrVideo, 0, 0, qrCanvas.width, qrCanvas.height);

            const imageData = qrCtx.getImageData(0, 0, qrCanvas.width, qrCanvas.height);
            const code = jsQR(imageData.data, imageData.width, imageData.height, { inversionAttempts: "attemptBoth" });

            if (code && code.data && code.data.trim() !== '' && !qrScanInProgress) {
                qrScanInProgress = true;
                handleQRDetected(code.data);
            } else {
                rafId = requestAnimationFrame(scanLoop);
            }
        }

        function handleQRDetected(data) {
            if (!data || data.trim() === '') {
                console.log('Empty QR data, ignoring');
                return;
            }

            let serial = null;
            try {
                const json = JSON.parse(data);
                serial = json.serial || json.serialNumber || json.serial_number || json.mac || null;
            } catch (e) { }

            if (!serial) {
                const match = data.match(/[A-F0-9]{8,20}/gi);
                serial = match ? match[0] : data.substring(0, 50);
            }

            const displaySerial = serial || data;
            issueState.lastScannedSerial = displaySerial;
            
            macValue.textContent = displaySerial;
            macHighlight.classList.add("show");

            stopScanning();
            
            // ✅ HIDE network discovery initially (will show on success)
            nextStepContainer.style.display = 'none';
            nextStepContainer.classList.remove("show");
            
            validateAndSaveDevice(displaySerial);
        }

        async function validateAndSaveDevice(serialNumber) {
            try {
                console.log("🔍 Starting validation for serial:", serialNumber);
                console.log("Current user type:", issueState.currentUserType);
                console.log("Current user id:", issueState.currentUserId);
                
                // ✅ VALIDATION: Check if user selected a dealer/distributor first
                if (!issueState.currentUserType || !issueState.currentUserId) {
                    showErrorMessage(
                        "❌ Error",
                        "Please select a dealer or distributor first before scanning QR code."
                    );
                    return;
                }
                
                // STEP 1: VALIDATE SERIAL NUMBER
                console.log("📡 Calling validation API...");
                
                const validationResponse = await fetch('/api/validate-device-serial', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        serial_number: serialNumber
                    })
                });

                console.log("✅ Validation API Response Status:", validationResponse.status);
                const validationData = await validationResponse.json();
                console.log("📋 Validation Response Data:", validationData);

                // ✅ CHECK VALIDATION RESPONSE
                if (!validationResponse.ok || !validationData.success) {
                    console.log("❌ Validation failed!");
                    console.log("Error Code:", validationData.error_code);
                
                    showErrorMessage(
                        "❌ Invalid Device",
                        "This device does not exist in the system.\n\nPlease verify the QR code and try again."
                    );
                    return;
                }

                console.log("✅ Serial validation SUCCESSFUL!");
                
                // STEP 2: SAVE TO DEVICE MASTER (only if validation passed)
                console.log("💾 Proceeding to save device...");
                
                // ✅ GET user_type from the state
                const userType = issueState.currentUserType;
                const userId = issueState.currentUserId;

                console.log("📝 Saving with:", {
                    serial_number: serialNumber,
                    user_id: userId,
                    user_type: userType
                });

                // ✅ Build request body based on user type
                const requestBody = {
                    serial_number: serialNumber,
                    user_id: parseInt(userId),
                    user_type: userType.toLowerCase(),
                    qr_data: serialNumber
                };
                
                // ✅ If it's a customer, add customer_id to request
                // (no verification needed in backend, just save it)
                if (userType.toLowerCase() === 'customer') {
                    requestBody.customer_id = parseInt(userId);
                }

                const saveResponse = await fetch('/api/devices/save-from-qr-v2', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(requestBody)
                });

                console.log("✅ Save API Response Status:", saveResponse.status);
                const saveData = await saveResponse.json();
                console.log("📋 Save Response Data:", saveData);

                // ✅ CHECK SAVE RESPONSE
                if (!saveResponse.ok || !saveData.success) {
                    console.log("❌ Save failed!");
                    console.log("Error Details:", saveData);
                
                    // Check for duplicate - case insensitive
                    const errorCodeLower = (saveData.error_code || '').toLowerCase();
                    const messageLower = (saveData.message || '').toLowerCase();
                    const isDuplicate = errorCodeLower === 'duplicate_device' || messageLower.includes('assigned to');
                    
                    if (isDuplicate) {
                        showErrorMessage(
                            "⚠️ Device Already Issued",
                            "This device has already been issued.\n\nYou cannot issue the same device twice."
                        );
                    } else {
                        showErrorMessage(
                            "❌ Failed to Issue Device",
                            saveData.message || 'An error occurred while saving the device.'
                        );
                    }
                    return;
                }

                // ✅ SUCCESS: Device saved successfully
                console.log("✅ Device saved successfully!");
                
                const typeLabel = userType.toUpperCase();
                const userName = issueState.currentUserData?.full_name || `Customer ID: ${userId}`;
                showSuccessMessage(
                    `✅ ISSUED TO ${typeLabel} SUCCESSFULLY`,
                    `User: ${userName}`
                );

            } catch (error) {
                console.error('❌ Exception in validateAndSaveDevice:', error);
                showErrorMessage(
                    "❌ Error Processing Device",
                    'An unexpected error occurred. Please try again.'
                );
            }
        }
        
        // ✅ SHOW SUCCESS MESSAGE WITH ALERT
        function showSuccessMessage(title, details) {
                        console.log("🎉 Showing success message:", title);
            
            // DISPLAY IN POPUP - NO ALERT
            const messageDiv = document.getElementById('issueStatusMessage');
            if (messageDiv) {
                messageDiv.innerHTML = `
                    <div style="display: flex; gap: 12px; align-items: flex-start;">
                        <i class="fas fa-check-circle" style="color: var(--success); font-size: 20px; flex-shrink: 0; margin-top: 2px;"></i>
                        <div style="flex: 1;">
                            <div style="font-weight: 600; color: var(--success); margin-bottom: 4px;">${title}</div>
                            <div style="font-size: 13px; color: var(--gray-600);">${details}</div>
                        </div>
                    </div>
                `;
                messageDiv.style.display = 'block';
                messageDiv.style.background = 'rgba(16, 185, 129, 0.1)';
                messageDiv.style.border = '1px solid rgba(16, 185, 129, 0.3)';
                messageDiv.style.borderRadius = '8px';
                messageDiv.style.padding = '12px 16px';
                messageDiv.style.marginBottom = '16px';
            }
        
            successText.textContent = title;
            successMessage.style.display = 'block';
            errorMessage.style.display = 'none';
        
            macHighlight.classList.remove("show");
        
            // SHOW NETWORK DISCOVERY BUTTON ON SUCCESS
            nextStepContainer.style.display = 'block';
            nextStepContainer.classList.add("show");
        }

        function showErrorMessage(title, details) {
            console.log("⚠️ Showing error message:", title);
            console.log("Details:", details);
        
            const sanitizedDetails = String(details || '').replace(/undefined/g, '').trim();
        
            // DISPLAY IN POPUP - NO ALERT
            const messageDiv = document.getElementById('issueStatusMessage');
            if (messageDiv) {
                messageDiv.innerHTML = `
                    <div style="display: flex; gap: 12px; align-items: flex-start;">
                        <i class="fas fa-exclamation-circle" style="color: var(--danger); font-size: 20px; flex-shrink: 0; margin-top: 2px;"></i>
                        <div style="flex: 1;">
                            <div style="font-weight: 600; color: var(--danger); margin-bottom: 4px;">${title}</div>
                            <div style="font-size: 13px; color: var(--gray-600); white-space: pre-wrap;">${sanitizedDetails}</div>
                        </div>
                    </div>
                `;
                messageDiv.style.display = 'block';
                messageDiv.style.background = 'rgba(239, 68, 68, 0.1)';
                messageDiv.style.border = '1px solid rgba(239, 68, 68, 0.3)';
                messageDiv.style.borderRadius = '8px';
                messageDiv.style.padding = '12px 16px';
                messageDiv.style.marginBottom = '16px';
            }
        
            const html = `
                <div style="display: flex; align-items: flex-start; gap: 12px;">
                    <div style="font-size: 20px; flex-shrink: 0;">❌</div>
                    <div>
                        <strong style="display: block; margin-bottom: 8px;">${title}</strong>
                        <span style="font-size: 13px; color: var(--gray-600); white-space: pre-wrap;">${sanitizedDetails}</span>
                    </div>
                </div>
            `;
        
            errorText.innerHTML = html;
            errorMessage.style.display = 'block';
            successMessage.style.display = 'none';
        
            macHighlight.classList.remove("show");
        
            // HIDE NETWORK DISCOVERY ON ERROR
            nextStepContainer.style.display = 'none';
            nextStepContainer.classList.remove("show");
        
            qrScanInProgress = false;
        }

                // CLEAR STATUS MESSAGE
        function clearStatusMessage() {
            const messageDiv = document.getElementById('issueStatusMessage');
            if (messageDiv) {
                messageDiv.style.display = 'none';
                messageDiv.innerHTML = '';
            }
        }

        var _qrCloseBtn = document.getElementById("qrCloseBtn");
        if (_qrCloseBtn) _qrCloseBtn.addEventListener("click", closeQRScanner);
        var _qrOverlay = document.getElementById("qrModalOverlay");
        if (_qrOverlay) _qrOverlay.addEventListener("click", (e) => {
            if (e.target === _qrOverlay) closeQRScanner();
        });

        function setupLogout() {
            var btn = document.getElementById('logoutBtn');
            if (btn) btn.addEventListener('click', function() {
                window.location.href = '/logout';
            });
        }

        function _extractFirstIP(ipData) {
            if (!ipData) return '';
            if (typeof ipData === 'string') return ipData;
            if (typeof ipData === 'object') {
                for (const key in ipData) {
                    if (ipData[key]) return ipData[key];
                }
            }
            return '';
        }

        // Full Screen Image Modal Functions
        function openImageModal(imageSrc) {
            const modal = document.getElementById("imageModal");
            const modalImg = document.getElementById("modalImage");
            modal.classList.add("active");
            modalImg.src = imageSrc;
        }

        function closeImageModal() {
            const modal = document.getElementById("imageModal");
            modal.classList.remove("active");
        }

        // Close modal on click outside image
        var _imageModal = document.getElementById("imageModal");
        if (_imageModal) _imageModal.addEventListener("click", function(e) {
            if (e.target === this) {
                closeImageModal();
            }
        });

        // Close modal on ESC key
        document.addEventListener("keydown", function(e) {
            if (e.key === "Escape") {
                closeImageModal();
            }
        });

        // Use Event Delegation for double-click on images (works for dynamic content)
        document.addEventListener("dblclick", function(e) {
            const target = e.target;
            
            // Check if clicked element is an image we want to handle
            if (target.tagName === "IMG") {
                const isDeviceSnapshot = target.classList.contains("device-snapshot");
                const hasScreenshotInSrc = target.src && (target.src.includes("screenshot") || target.src.includes("images"));
                const isNotPlaceholder = target.src && !target.src.includes("data:image/svg");
                
                if ((isDeviceSnapshot || hasScreenshotInSrc) && isNotPlaceholder) {
                    e.preventDefault();
                    e.stopPropagation();
                    openImageModal(target.src);
                }
            }
        });

        // Add cursor pointer style to images on mouseover (event delegation)
        document.addEventListener("mouseover", function(e) {
            const target = e.target;
            if (target.tagName === "IMG") {
                const isDeviceSnapshot = target.classList.contains("device-snapshot");
                const hasScreenshotInSrc = target.src && (target.src.includes("screenshot") || target.src.includes("images"));
                const isNotPlaceholder = target.src && !target.src.includes("data:image/svg");
                
                if ((isDeviceSnapshot || hasScreenshotInSrc) && isNotPlaceholder) {
                    target.style.cursor = "pointer";
                }
            }
        });

    // ===================== DISCOVERY PANEL JS =====================

    function openDiscoveryPanel() {
        document.getElementById('discoveryPanel').classList.add('open');
        document.getElementById('discoveryOverlay').classList.add('open');
        // Reset state
        resetDiscoveryPanel();
    }

    function closeDiscoveryPanel() {
        document.getElementById('discoveryPanel').classList.remove('open');
        document.getElementById('discoveryOverlay').classList.remove('open');
    }

    function resetDiscoveryPanel() {
        document.getElementById('dpStatusBox').classList.remove('visible');
        document.getElementById('dpErrorBox').classList.remove('visible');
        document.getElementById('dpResultsSection').style.display = 'none';
        document.getElementById('dpProgressBar').style.width = '0%';
        document.getElementById('dpScanBtn').disabled = false;
        document.getElementById('dpScanBtn').innerHTML = '<i class="fas fa-search"></i> Discover Devices';
    }

    function toggleDpPassword() {
        const input = document.getElementById('dpPassword');
        const icon = document.getElementById('dpPwIcon');
        if (input.type === 'password') {
            input.type = 'text';
            icon.className = 'fas fa-eye-slash';
        } else {
            input.type = 'password';
            icon.className = 'fas fa-eye';
        }
    }

    async function startDiscoveryScan() {
        const username = document.getElementById('dpUsername').value.trim();
        const password = document.getElementById('dpPassword').value.trim();

        if (!username) { showDpError('Please enter a username.'); return; }
        if (!password) { showDpError('Please enter a password.'); return; }

        // Reset UI
        document.getElementById('dpErrorBox').classList.remove('visible');
        document.getElementById('dpResultsSection').style.display = 'none';
        document.getElementById('dpStatusBox').classList.add('visible');
        document.getElementById('dpScanBtn').disabled = true;
        document.getElementById('dpScanBtn').innerHTML = '<i class="fas fa-spinner fa-spin"></i> Please Wait Searching For Camera In Network...';

        // Animated progress bar while waiting
        let progress = 0;
        const statusMessages = [
            'Scanning network, please wait...',
            'Sending broadcast packets...',
            'Checking open ports...',
            'Authenticating devices...',
            'Collecting device info...',
            'Finalizing results...'
        ];
        let msgIndex = 0;
        const bar = document.getElementById('dpProgressBar');
        const statusText = document.getElementById('dpStatusText');
        bar.style.width = '0%';

        const progressInterval = setInterval(() => {
            progress += Math.random() * 10 + 3;
            if (progress > 92) progress = 92;
            bar.style.width = progress + '%';
            if (msgIndex < statusMessages.length - 1 && progress > (msgIndex + 1) * 15) {
                msgIndex++;
                statusText.textContent = statusMessages[msgIndex];
            }
        }, 500);

        try {
            console.log('🔍 [DiscoveryPanel] Starting /api/scan with:', username);

            const res = await fetch('/api/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password, customer_id: null })
            });

            clearInterval(progressInterval);
            bar.style.width = '100%';
            statusText.textContent = 'Discovery complete!';

            if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);

            const out = await res.json();
            console.log('📥 [DiscoveryPanel] Scan result:', out);

            // Support both response formats (same as existing startNetworkScan)
            let devices = [];
            let foundCount = 0;
            let isSuccess = false;

            if (out.status === 'ok') {
                devices = out.devices || [];
                foundCount = out.count || devices.length;
                isSuccess = true;
            } else if (out.ok === true) {
                devices = out.merged_devices || out.devices || [];
                foundCount = Number(out.merged_count ?? out.count ?? devices.length ?? 0);
                isSuccess = true;
            }

            setTimeout(() => {
                document.getElementById('dpStatusBox').classList.remove('visible');
                document.getElementById('dpScanBtn').disabled = false;
                document.getElementById('dpScanBtn').innerHTML = '<i class="fas fa-redo"></i> Scan Again';

                if (isSuccess && foundCount > 0) {
                    renderDiscoveredDevices(devices);
                } else {
                    const msg = out.message || out.error || 'No devices found on network.';
                    showDpError(msg + (out.hint ? ' Hint: ' + out.hint : ''));
                }
            }, 400);

        } catch (err) {
            clearInterval(progressInterval);
            console.error('[DiscoveryPanel] Scan error:', err);
            document.getElementById('dpStatusBox').classList.remove('visible');
            document.getElementById('dpScanBtn').disabled = false;
            document.getElementById('dpScanBtn').innerHTML = '<i class="fas fa-redo"></i> Try Again';
            showDpError('Error: ' + err.message);
        }
    }

    function renderDiscoveredDevices(devices) {
        const list = document.getElementById('dpDeviceList');
        const count = document.getElementById('dpResultsCount');
        const section = document.getElementById('dpResultsSection');

        // Filter only devices with RTSP (same logic as existing scanner)
        const rtspDevices = devices.filter(d =>
            d.rtsp_profiles && Array.isArray(d.rtsp_profiles) &&
            d.rtsp_profiles.some(p => p.rtsp_url)
        );

        const displayDevices = rtspDevices.length > 0 ? rtspDevices : devices;
        count.textContent = displayDevices.length;
        section.style.display = 'block';

        if (displayDevices.length === 0) {
            list.innerHTML = `
                <div class="dp-empty-state">
                    <div class="dp-empty-icon">📡</div>
                    No devices found. Check credentials and network.
                </div>`;
            return;
        }

        list.innerHTML = displayDevices.map(d => {
            const ip     = d.device_ip || d.ip || 'N/A';
            const mac    = d.mac_address || d.mac || '';
            const model  = d.model || d.device_model || '';
            const brand  = d.manufacturer || d.brand || '';
            const rtspCount = (d.rtsp_profiles || []).filter(p => p.rtsp_url).length;
            const isOnline = d.status === 'online' || d.reachable === true || d.online === true || true;

            return `
            <div class="dp-device-item">
                <div class="dp-device-icon">📷</div>
                <div class="dp-device-info">
                    <div class="dp-device-ip">${ip}</div>
                    <div class="dp-device-meta">
                        ${mac ? mac + ' &nbsp;·&nbsp; ' : ''}${brand || model || 'IP Camera'}
                        ${rtspCount > 0 ? ' &nbsp;·&nbsp; <span style="color:#42a5f5;">' + rtspCount + ' RTSP</span>' : ''}
                    </div>
                </div>
                <span class="dp-device-badge ${isOnline ? '' : 'offline'}">${isOnline ? 'online' : 'offline'}</span>
            </div>`;
        }).join('');
    }

    function showDpError(msg) {
        const box = document.getElementById('dpErrorBox');
        box.textContent = '⚠️ ' + msg;
        box.classList.add('visible');
    }


    // ========= DEVICE SELECTOR DROPDOWN FOR DEVICE DISCOVERY =========
    window._dpv2SelectedDevice = null;

    async function dpv2LoadOnlineDevices() {
        const dropdown = document.getElementById('dpv2DeviceDropdown');
        const statusDiv = document.getElementById('dpv2DeviceStatus');
        if (!dropdown) return;

        dropdown.innerHTML = '<option value="">⏳ Loading online devices...</option>';
        if (statusDiv) statusDiv.textContent = '';

        try {
            let dealerId = window._dealerId || null;
            if (!dealerId) {
                try {
                    const uRes = await fetch('/api/current-user', { credentials: 'include' });
                    if (uRes.ok) { const ud = await uRes.json(); dealerId = ud.id || ud.user_id || null; window._dealerId = dealerId; }
                } catch(e) {}
            }

            const url = dealerId
                ? `/dealer/devices?dealer_id=${encodeURIComponent(dealerId)}&filter=online`
                : `/devices_status?filter=online`;

            let data = null;
            try {
                const resp = await fetch(url, { credentials: 'include' });
                data = await resp.json();
            } catch(e) { data = null; }

            // Fallback
            if (!data || (!Array.isArray(data.devices) && !Array.isArray(data))) {
                try {
                    const resp2 = await fetch('/devices', { credentials: 'include' });
                    data = await resp2.json();
                } catch(e) {}
            }

            // Normalize
            let entries = [];
            if (typeof normalizeDevicesPayload === 'function') {
                entries = normalizeDevicesPayload(data);
            } else if (Array.isArray(data)) {
                entries = data.map(d => [d.hostname || d.device_ip || d.ip || 'Unknown', d]);
            } else if (data && Array.isArray(data.devices)) {
                entries = data.devices.map(d => [d.hostname || d.device_ip || d.ip || 'Unknown', d]);
            }

            // Filter only online
            const onlineEntries = entries.filter(([host, d]) => {
                const st = ((d && d.status) || '').toString().toUpperCase();
                return st === 'ACTIVE' || st === 'ONLINE';
            });

            dropdown.innerHTML = '';
            if (onlineEntries.length === 0) {
                dropdown.innerHTML = '<option value="">⚠️ No online devices found</option>';
                if (statusDiv) statusDiv.textContent = 'No devices are currently online.';
                window._dpv2SelectedDevice = null;
            } else {
                dropdown.innerHTML = '<option value="">— Select a device —</option>';
                onlineEntries.forEach(([host, d]) => {
                    const ip = (d && (d.device_ip || d.ip)) || host;
                    const label = ip !== host ? `${host} (${ip})` : host;
                    const opt = document.createElement('option');
                    opt.value = ip;
                    opt.textContent = '🟢 ' + label;
                    opt.dataset.host = host;
                    opt.dataset.ip = ip;
                    dropdown.appendChild(opt);
                });
                if (statusDiv) statusDiv.textContent = `✅ ${onlineEntries.length} device(s) online`;
            }
        } catch(err) {
            dropdown.innerHTML = '<option value="">❌ Failed to load devices</option>';
            if (statusDiv) statusDiv.textContent = 'Error: ' + err.message;
        }
    }

    function dpv2RefreshDeviceDropdown() {
        const icon = document.getElementById('dpv2RefreshIcon');
        if (icon) { icon.classList.add('fa-spin'); setTimeout(() => icon.classList.remove('fa-spin'), 1500); }
        dpv2LoadOnlineDevices();
    }

    function dpv2OnDeviceSelect(ip) {
        window._dpv2SelectedDevice = ip || null;
        const statusDiv = document.getElementById('dpv2DeviceStatus');
        if (statusDiv && ip) {
            statusDiv.innerHTML = `<span style="color:#059669;">✔ Selected: <strong>${ip}</strong> — fill credentials below and scan</span>`;
        }
    }

    // ========= FULL PAGE DISCOVERY (dpv2) FUNCTIONS =========

    function toggleDpv2Password() {
        const input = document.getElementById('dpv2Password');
        const icon  = document.getElementById('dpv2PwIcon');
        if (input.type === 'password') { input.type = 'text'; icon.className = 'fas fa-eye-slash'; }
        else                           { input.type = 'password'; icon.className = 'fas fa-eye'; }
    }

    async function startDpv2Scan() {
        const username = document.getElementById('dpv2Username').value.trim();
        const password = document.getElementById('dpv2Password').value.trim();

        if (!username) { dpv2ShowError('Please enter a username.'); return; }
        if (!password) { dpv2ShowError('Please enter a password.'); return; }

        // ✅ Store camera credentials globally so Save Devices can use them
        window._dpv2ScanUsername = username;
        window._dpv2ScanPassword = password;

        document.getElementById('dpv2ErrorBox').style.display = 'none';
        document.getElementById('dpv2ResultsSection').style.display = 'none';
        document.getElementById('dpv2StatusBox').style.display = 'block';
        document.getElementById('dpv2ScanBtn').disabled = true;
        document.getElementById('dpv2ScanBtn').innerHTML = '<i class="fas fa-spinner fa-spin"></i> Please Wait Searching For Camera In Network...';

        let progress = 0;
        const msgs = ['Scanning network, please wait...','Sending broadcast packets...','Checking open ports...','Authenticating devices...','Collecting device info...','Finalizing results...'];
        let mi = 0;
        const bar  = document.getElementById('dpv2ProgressBar');
        const stxt = document.getElementById('dpv2StatusText');
        bar.style.width = '0%';

        const iv = setInterval(() => {
            progress += Math.random() * 10 + 3;
            if (progress > 92) progress = 92;
            bar.style.width = progress + '%';
            if (mi < msgs.length - 1 && progress > (mi + 1) * 15) { mi++; stxt.textContent = msgs[mi]; }
        }, 500);

        try {
            const res = await fetch('/api/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password, customer_id: null })
            });
            clearInterval(iv);
            bar.style.width = '100%';
            stxt.textContent = 'Discovery complete!';

            if (!res.ok) throw new Error('HTTP ' + res.status + ': ' + res.statusText);
            const out = await res.json();

            let devices = [], foundCount = 0, isSuccess = false;
            if (out.status === 'ok')    { devices = out.devices || []; foundCount = out.count || devices.length; isSuccess = true; }
            else if (out.ok === true)   { devices = out.merged_devices || out.devices || []; foundCount = Number(out.merged_count ?? out.count ?? devices.length ?? 0); isSuccess = true; }

            setTimeout(() => {
                document.getElementById('dpv2StatusBox').style.display = 'none';
                document.getElementById('dpv2ScanBtn').disabled = false;
                document.getElementById('dpv2ScanBtn').innerHTML = '<i class="fas fa-redo"></i> Scan Again';
                if (isSuccess && foundCount > 0) { dpv2RenderDevices(devices); }
                else { dpv2ShowError((out.message || out.error || 'No devices found.') + (out.hint ? ' Hint: ' + out.hint : '')); }
            }, 400);

        } catch(err) {
            clearInterval(iv);
            document.getElementById('dpv2StatusBox').style.display = 'none';
            document.getElementById('dpv2ScanBtn').disabled = false;
            document.getElementById('dpv2ScanBtn').innerHTML = '<i class="fas fa-redo"></i> Try Again';
            dpv2ShowError('Error: ' + err.message);
        }
    }

    function dpv2RenderDevices(devices) {
        const rtspDevices = devices.filter(d => d.rtsp_profiles && Array.isArray(d.rtsp_profiles) && d.rtsp_profiles.some(p => p.rtsp_url));
        const display = rtspDevices.length > 0 ? rtspDevices : devices;

        document.getElementById('dpv2ResultsCount').textContent = display.length;
        document.getElementById('dpv2ResultsSection').style.display = 'block';

        if (display.length === 0) {
            document.getElementById('dpv2DeviceRows').innerHTML = '<tr><td colspan="5" style="text-align:center;padding:32px;color:#9ca3af;">📡 No devices found. Check credentials and network.</td></tr>';
            return;
        }

        document.getElementById('dpv2DeviceRows').innerHTML = display.map(d => {
            const ip        = d.device_ip || d.ip || 'N/A';
            const mac       = d.mac_address || d.mac || '—';
            const model     = d.model || d.device_model || '—';
            const brand     = d.manufacturer || d.brand || '';
            const rtspCount = (d.rtsp_profiles || []).filter(p => p.rtsp_url).length;
            const online    = d.status === 'online' || d.reachable === true || d.online === true;
            const badgeColor = online ? '#16a34a' : '#dc2626';
            const badgeBg   = online ? '#f0fdf4' : '#fef2f2';
            const badgeBorder = online ? '#bbf7d0' : '#fecaca';

            return `<tr>
                <td style="font-weight:600;">${ip}</td>
                <td style="font-family:monospace;font-size:12px;">${mac}</td>
                <td>${brand ? brand + (model !== '—' ? ' / ' + model : '') : model}</td>
                <td>${rtspCount > 0 ? '<span style="color:#2563eb;font-weight:600;">' + rtspCount + ' RTSP</span>' : '—'}</td>
                <td><span style="padding:3px 10px;border-radius:20px;font-size:11px;font-weight:700;text-transform:uppercase;background:' + badgeBg + ';color:' + badgeColor + ';border:1px solid ' + badgeBorder + ';">${online ? 'Online' : 'Offline'}</span></td>
            </tr>`;
        }).join('');
    }

    async function dpv2SaveSelectedDevices() {
    const devices = window.dpv2Devices || [];
    if (!devices.length) { anToast("Koi device nahi mili. Pehle scan karein.", "#991b1b"); return; }
    const selected = devices.filter((d, i) => { const chk = document.getElementById("dpcheck-" + i); return chk && chk.dataset.selected === "1"; });
    const toSave = selected.length > 0 ? selected : devices;
    const btn = document.getElementById("dpv2SaveBtn");
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
    try {
        // ✅ FIX: Use selected CUSTOMER's user_id from analytics dropdown, NOT dealer's ID
        const customerDropdown = document.getElementById('analyticsCustomerDropdown');
        let userId = customerDropdown ? customerDropdown.value : null;
        if (!userId && window.analyticsDataCache && window.analyticsDataCache.user_id) {
            userId = window.analyticsDataCache.user_id;
        }
        if (!userId) {
            anToast("❌ Pehle 'Select Analytics' mein customer select karein.", "#991b1b");
            return;
        }
        const customers = window.allCustomersDataForFilter || [];
        const selectedCustomer = customers.find(c => String(c.user_id || c.id) === String(userId));
        const userName = selectedCustomer ? (selectedCustomer.name || String(userId)) : String(userId);
        // ✅ Use camera scan password (stored when scan was done) - NOT empty string
        let password = window._dpv2ScanPassword || (document.getElementById("dpv2Password") ? document.getElementById("dpv2Password").value : "");
        if (!password) {
            anToast("❌ Camera password nahi mili. Pehle scan karein.", "#991b1b");
            return;
        }
        console.log(`📤 Saving devices for customer: ${userName} (user_id: ${userId})`);
        if (typeof saveDevicesToAnalyticsAPI === "function") {
            await saveDevicesToAnalyticsAPI(toSave, userId, userName, password);
        } else {
            const res = await fetch("/api/devices/save", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "include", body: JSON.stringify({ devices: toSave, user_id: userId }) });
            if (!res.ok) throw new Error("HTTP " + res.status);
            anToast("✅ " + toSave.length + " device(s) save ho gaye!", "#065f46");
        }
    } catch(e) { anToast("Save failed: " + e.message, "#991b1b"); }
    finally { btn.disabled = false; btn.innerHTML = orig; }
}

function dpv2ShowError(msg) {
        const box = document.getElementById('dpv2ErrorBox');
        box.textContent = '⚠️ ' + msg;
        box.style.display = 'block';
    }


// ============================================================
//  AI ANALYTICS  —  calls /api/db/analytics on agent (clienttt.py)
//  Dashboard → Server (/api/db/analytics) → Agent (clienttt.py) → PostgreSQL
//  Same socket pattern as /api/scan-db
// ============================================================
let _analyticsData = [];

/* ---------- LOAD (GET /api/db/analytics) ---------- */
async function loadAnalyticsData() {
    const tbody = document.getElementById('analyticsTableBody');
    const icon  = document.getElementById('analyticsRefreshIcon');
    const countEl = document.getElementById('analyticsCount');
    const errEl = document.getElementById('analyticsErrorBox');
    if (icon) icon.classList.add('fa-spin');
    if (errEl) errEl.style.display = 'none';
    if (countEl) { countEl.style.display = 'none'; countEl.textContent = ''; }
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:48px;color:#9ca3af;">
        <i class="fas fa-circle-notch fa-spin fa-lg"></i><br><br>Loading analytics...</td></tr>`;

    try {
        const res  = await fetch('/api/db/analytics');
        const data = await res.json();
        if (data.status === 'ok') {
            _analyticsData = data.analytics || [];
            if (countEl) { countEl.textContent = _analyticsData.length + ' record(s)'; countEl.style.display = 'inline-block'; }
            renderAnalyticsTable(_analyticsData);
        } else {
            if (errEl) { errEl.textContent = '⚠️ ' + (data.message || 'Failed to load'); errEl.style.display = 'block'; }
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:48px;color:#ef4444;">
                <i class="fas fa-exclamation-triangle fa-lg"></i><br><br>${data.message || 'Failed to load'}</td></tr>`;
        }
    } catch (e) {
        if (errEl) { errEl.textContent = '⚠️ Network error: ' + e.message; errEl.style.display = 'block'; }
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:48px;color:#ef4444;">
            <i class="fas fa-exclamation-triangle fa-lg"></i><br><br>Network error: ${e.message}</td></tr>`;
    } finally {
        if (icon) icon.classList.remove('fa-spin');
    }
}

/* ---------- SEARCH FILTER ---------- */
function filterAnalytics() {
    const q = (document.getElementById('analyticsSearch').value || '').toLowerCase();
    const filtered = _analyticsData.filter(a =>
        (a.cam_ip || '').toLowerCase().includes(q) ||
        (a.analytics_name || '').toLowerCase().includes(q) ||
        (a.camera_rtsp || '').toLowerCase().includes(q)
    );
    renderAnalyticsTable(filtered);
}

/* ---------- RENDER TABLE ---------- */
function renderAnalyticsTable(list) {
    const tbody = document.getElementById('analyticsTableBody');
    if (!list || list.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:48px;color:#9ca3af;">
            <i class="fas fa-robot fa-2x" style="margin-bottom:10px;display:block;"></i>No analytics records found</td></tr>`;
        return;
    }
    tbody.innerHTML = list.map((a, i) => `
        <tr id="anrow-${i}" style="transition:background 0.15s;"
            onmouseover="this.style.background='#f8fafc'" onmouseout="this.style.background=''">
            <td id="ancell-ip-${i}">
                <span style="background:#eff6ff;color:#1e40af;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600;">
                    ${a.cam_ip || '—'}
                </span>
            </td>
            <td id="ancell-name-${i}" style="font-weight:600;color:#374151;font-size:13px;">${a.analytics_name || '—'}</td>
            <td id="ancell-rtsp-${i}" style="color:#1f2937;font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                title="${a.camera_rtsp || ''}">${a.camera_rtsp || '—'}</td>
            <td style="text-align:center;" id="anaction-${i}">
                <div style="display:inline-flex;gap:6px;">
                    <button onclick="anEdit(${i})"
                        style="padding:6px 12px;background:#eff6ff;color:#1e40af;border:1px solid #bfdbfe;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;"
                        onmouseover="this.style.background='#dbeafe'" onmouseout="this.style.background='#eff6ff'">
                        <i class="fas fa-edit"></i> Edit
                    </button>
                    <button onclick="anDelete(${a.id},${i},this)"
                        style="padding:6px 12px;background:#fef2f2;color:#dc2626;border:1px solid #fecaca;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;"
                        onmouseover="this.style.background='#fee2e2'" onmouseout="this.style.background='#fef2f2'">
                        <i class="fas fa-trash-alt"></i> Delete
                    </button>
                </div>
            </td>
        </tr>`).join('');
}

/* ---------- INLINE EDIT ---------- */
function anEdit(i) {
    const a = _analyticsData[i];
    // Replace IP cell
    document.getElementById('ancell-ip-' + i).innerHTML =
        `<input id="anedit-ip-${i}" value="${a.cam_ip || ''}" placeholder="Cam IP"
            style="padding:5px 9px;border:1px solid #93c5fd;border-radius:7px;font-size:12px;outline:none;width:130px;"
            onfocus="this.style.borderColor='#2563eb'" onblur="this.style.borderColor='#93c5fd'">`;
    // Replace Name cell
    document.getElementById('ancell-name-' + i).innerHTML =
        `<input id="anedit-name-${i}" value="${a.analytics_name || ''}" placeholder="Analytics Name"
            style="padding:5px 9px;border:1px solid #93c5fd;border-radius:7px;font-size:12px;outline:none;width:150px;"
            onfocus="this.style.borderColor='#2563eb'" onblur="this.style.borderColor='#93c5fd'">`;
    // Replace RTSP cell
    document.getElementById('ancell-rtsp-' + i).innerHTML =
        `<input id="anedit-rtsp-${i}" value="${a.camera_rtsp || ''}" placeholder="Camera RTSP"
            style="padding:5px 9px;border:1px solid #93c5fd;border-radius:7px;font-size:12px;outline:none;width:170px;"
            onfocus="this.style.borderColor='#2563eb'" onblur="this.style.borderColor='#93c5fd'">`;
    // Replace action cell
    document.getElementById('anaction-' + i).innerHTML = `
        <div style="display:flex;flex-direction:column;align-items:center;gap:5px;">
            <div style="display:inline-flex;gap:5px;">
                <button onclick="anSave(${i})"
                    style="padding:6px 12px;background:#059669;color:#fff;border:none;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;">
                    <i class="fas fa-save"></i> Save
                </button>
                <button onclick="renderAnalyticsTable(_analyticsData)"
                    style="padding:6px 12px;background:#e5e7eb;color:#374151;border:none;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;">
                    Cancel
                </button>
            </div>
            <div id="anerr-${i}" style="display:none;color:#dc2626;font-size:11px;text-align:center;"></div>
        </div>`;
}

/* ---------- SAVE EDIT (PUT /api/db/analytics/<id>) ---------- */
async function anSave(i) {
    const a      = _analyticsData[i];
    const camIp  = (document.getElementById('anedit-ip-'   + i) || {}).value?.trim() || '';
    const name   = (document.getElementById('anedit-name-' + i) || {}).value?.trim() || '';
    const rtsp   = (document.getElementById('anedit-rtsp-' + i) || {}).value?.trim() || '';
    const errEl  = document.getElementById('anerr-' + i);

    if (errEl) errEl.style.display = 'none';
    if (!camIp || !name) {
        if (errEl) { errEl.textContent = 'Cam IP and Name required'; errEl.style.display = 'block'; }
        return;
    }

    try {
        const res  = await fetch('/api/db/analytics/' + a.id, {
            method:  'PUT',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ cam_ip: camIp, analytics_name: name, camera_rtsp: rtsp })
        });
        const data = await res.json();
        if (data.status === 'ok') {
            // Update local cache
            a.cam_ip = camIp; a.analytics_name = name; a.camera_rtsp = rtsp;
            renderAnalyticsTable(_analyticsData);
            anToast('✅ Analytics updated!', '#065f46');
        } else {
            if (errEl) { errEl.textContent = data.message || 'Update failed'; errEl.style.display = 'block'; }
        }
    } catch (e) {
        if (errEl) { errEl.textContent = 'Network error: ' + e.message; errEl.style.display = 'block'; }
    }
}

/* ---------- DELETE (DELETE /api/db/analytics/<id>) ---------- */
async function anDelete(analyticsId, i, btnEl) {
    const name = _analyticsData[i]?.analytics_name || '';
    if (!confirm(`Delete analytics #${analyticsId} (${name})?\n\nThis cannot be undone.`)) return;

    const orig = btnEl.innerHTML;
    btnEl.disabled = true;
    btnEl.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

    try {
        const res  = await fetch('/api/db/analytics/' + analyticsId, { method: 'DELETE' });
        const data = await res.json();
        if (data.status === 'ok') {
            // Fade out row then remove
            const row = document.getElementById('anrow-' + i);
            if (row) { row.style.opacity = '0'; row.style.transition = 'opacity 0.3s'; setTimeout(() => row.remove(), 300); }
            _analyticsData.splice(i, 1);
            const countEl = document.getElementById('analyticsCount');
            if (countEl) countEl.textContent = _analyticsData.length + ' record(s)';
            anToast('🗑️ Analytics deleted', '#991b1b');
        } else {
            anToast('Error: ' + (data.message || 'failed'), '#991b1b');
            btnEl.disabled = false; btnEl.innerHTML = orig;
        }
    } catch (e) {
        anToast('Network error: ' + e.message, '#991b1b');
        btnEl.disabled = false; btnEl.innerHTML = orig;
    }
}

/* ---------- TOAST ---------- */
function anToast(msg, bg) {
    const t = document.createElement('div');
    t.style.cssText = `position:fixed;bottom:28px;right:28px;z-index:99999;background:${bg};color:#fff;
        padding:12px 22px;border-radius:10px;font-size:13px;font-weight:600;
        box-shadow:0 8px 24px rgba(0,0,0,0.25);transition:opacity 0.4s;`;
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 400); }, 2600);
}
function toggleDpv2Password() {
    const input = document.getElementById('dpv2Password');
    const icon  = document.getElementById('dpv2PwIcon');
    if (input.type === 'password') { input.type = 'text'; icon.className = 'fas fa-eye-slash'; }
    else                           { input.type = 'password'; icon.className = 'fas fa-eye'; }
}

async function startDpv2Scan() {
    const username = document.getElementById('dpv2Username').value.trim();
    const password = document.getElementById('dpv2Password').value.trim();
    const selectedDevice = window._dpv2SelectedDevice || document.getElementById('dpv2DeviceDropdown')?.value || null;
    const selectedPort = window._dpv2SelectedPort || null;
    const scanTargetLabel = selectedPort ? `${selectedDevice}:${selectedPort}` : selectedDevice;

    if (!selectedDevice) { dpv2ShowError('Please select an online device from the dropdown above before scanning.'); return; }
    if (!username) { dpv2ShowError('Please enter a username.'); return; }
    if (!password) { dpv2ShowError('Please enter a password.'); return; }

    // ✅ Store camera credentials globally so Save Devices can use them
    window._dpv2ScanUsername = username;
    window._dpv2ScanPassword = password;

    document.getElementById('dpv2ErrorBox').style.display = 'none';
    document.getElementById('dpv2ResultsSection').style.display = 'none';
    document.getElementById('dpv2CredForm').style.display = 'none';
    document.getElementById('dpv2StatusBox').style.display = 'block';

    let progress = 0;
    const msgs = [`Scanning device ${scanTargetLabel}...`,'Sending broadcast packets...','Checking open ports...','Authenticating devices...','Collecting device info...','Finalizing results...'];
    let mi = 0;
    const bar  = document.getElementById('dpv2ProgressBar');
    const stxt = document.getElementById('dpv2StatusText');
    bar.style.width = '0%';
    stxt.textContent = msgs[0];

    const iv = setInterval(() => {
        progress += Math.random() * 10 + 3;
        if (progress > 92) progress = 92;
        bar.style.width = progress + '%';
        if (mi < msgs.length - 1 && progress > (mi + 1) * 15) { mi++; stxt.textContent = msgs[mi]; }
    }, 500);

    try {
        const scanPayload = { username, password, customer_id: null, device_ip: selectedDevice, target_ip: selectedDevice };
        if (selectedPort) {
            scanPayload.port = selectedPort;
            scanPayload.device_port = selectedPort;
            scanPayload.target_port = selectedPort;
        }
        const res = await fetch('/api/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(scanPayload)
        });
        clearInterval(iv);
        bar.style.width = '100%';
        stxt.textContent = 'Discovery complete!';

        if (!res.ok) throw new Error('HTTP ' + res.status + ': ' + res.statusText);
        const out = await res.json();

        let devices = [], foundCount = 0, isSuccess = false;
        if (out.status === 'ok')    { devices = out.devices || []; foundCount = out.count || devices.length; isSuccess = true; }
        else if (out.ok === true)   { devices = out.merged_devices || out.devices || []; foundCount = Number(out.merged_count ?? out.count ?? devices.length ?? 0); isSuccess = true; }

        setTimeout(() => {
            document.getElementById('dpv2StatusBox').style.display = 'none';
            if (isSuccess && foundCount > 0) { dpv2RenderDevices(devices); }
            else { 
                document.getElementById('dpv2CredForm').style.display = 'block';
                dpv2ShowError((out.message || out.error || 'No devices found.') + (out.hint ? ' Hint: ' + out.hint : '')); 
            }
        }, 400);

    } catch(err) {
        clearInterval(iv);
        document.getElementById('dpv2StatusBox').style.display = 'none';
        document.getElementById('dpv2CredForm').style.display = 'block';
        dpv2ShowError('Error: ' + err.message);
    }
}

function dpv2ShowCredForm() {
    document.getElementById('dpv2ResultsSection').style.display = 'none';
    document.getElementById('dpv2ErrorBox').style.display = 'none';
    document.getElementById('dpv2CredForm').style.display = 'block';
    document.getElementById('dpv2Password').value = '';
    document.getElementById('dpv2ProgressBar').style.width = '0%';
}

function dpv2RenderDevices(devices) {
    // Show ALL devices - same as old modal behavior
    const display = (devices && devices.length > 0) ? devices : [];

    // Store globally for analytics selection (same as old modal uses window.discoveredDevices)
    window.dpv2Devices = display;

    document.getElementById('dpv2ResultsCount').textContent = display.length;
    document.getElementById('dpv2ResultsSection').style.display = 'block';

    const container = document.getElementById('dpv2DeviceCards');

    if (display.length === 0) {
        container.innerHTML = '<div style="text-align:center;padding:32px;color:#9ca3af;">📡 No devices found. Check credentials and network.</div>';
        return;
    }

    container.innerHTML = '';

    display.forEach((d, i) => {
        const ip = d.device_ip || d.ip || 'N/A';

        // ✅ EXACT SAME URL as old modal - operator precedence ensures https:// always used
        const snapshot = d.screenshot_path || d.image_url || (d.device_ip || d.ip)
            ? `${(window.PGAK_CONFIG && window.PGAK_CONFIG.BASE_IMAGE_API) || 'https://dealer.pgak.co.in/images/'}${ip}_jpg`
            : null;

        const card = document.createElement('div');
        card.id = `dpcard-${i}`;
        card.style.cssText = 'background:#fff;border:1.5px solid #e5e7eb;border-radius:14px;padding:14px;display:flex;align-items:center;gap:14px;transition:border-color 0.2s,box-shadow 0.2s;';

        card.innerHTML = `
            <!-- Checkbox -->
            <div id="dpcheck-${i}" onclick="dpv2ToggleCard(${i})"
                style="width:22px;height:22px;border:2px solid #d1d5db;border-radius:5px;flex-shrink:0;display:flex;align-items:center;justify-content:center;transition:all 0.15s;cursor:pointer;"></div>

            <!-- IP Info -->
            <div style="min-width:110px;">
                <div style="font-size:11px;color:#9ca3af;font-weight:600;margin-bottom:2px;">Device IP</div>
                <div style="font-size:13px;font-weight:700;color:#1e40af;">${ip}</div>
            </div>

            <!-- ✅ Snapshot - uses same class as old modal so same CSS applies -->
            ${snapshot
                ? `<img src="${snapshot}" alt="Snapshot" class="device-snapshot"
                       style="width:90px;height:70px;object-fit:cover;border-radius:8px;border:1.5px solid #e5e7eb;flex-shrink:0;">`
                : `<div class="device-snapshot-placeholder" style="width:90px;height:70px;"></div>`
            }

            <!-- Spacer -->
            <div style="flex:1;"></div>

            <!-- ✅ Select Analytics button - same as old modal -->
            <button onclick="dpv2SelectAnalytics(${i})"
                style="background:linear-gradient(135deg,#7c3aed,#5b21b6);color:#fff;border:none;border-radius:8px;padding:10px 14px;font-size:13px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px;white-space:nowrap;box-shadow:0 2px 8px rgba(124,58,237,0.3);flex-shrink:0;">
                <i class="fas fa-chart-line"></i> Select Analytics
            </button>
        `;

        container.appendChild(card);
    });
}

function dpv2ToggleCard(i) {
    const check = document.getElementById('dpcheck-' + i);
    const card  = document.getElementById('dpcard-' + i);
    const selected = check.dataset.selected === '1';
    if (selected) {
        check.dataset.selected = '0';
        check.style.background = '';
        check.style.borderColor = '#d1d5db';
        check.innerHTML = '';
        card.style.borderColor = '#e5e7eb';
        card.style.boxShadow = '';
    } else {
        check.dataset.selected = '1';
        check.style.background = '#3b82f6';
        check.style.borderColor = '#3b82f6';
        check.innerHTML = '<svg viewBox="0 0 12 10" fill="none" style="width:12px;height:12px;"><polyline points="1,5 4.5,8.5 11,1" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        card.style.borderColor = '#3b82f6';
        card.style.boxShadow = '0 2px 10px rgba(59,130,246,0.18)';
    }
}

function dpv2SelectAnalytics(i) {
    // Map dpv2 device into window.discoveredDevices for compatibility with existing openAnalyticsModal
    if (!window.discoveredDevices) window.discoveredDevices = [];
    if (window.dpv2Devices && window.dpv2Devices[i]) {
        window.discoveredDevices[i] = window.dpv2Devices[i];
    }
    if (typeof openAnalyticsModal === 'function') {
        openAnalyticsModal(i);
    }
}

function dpv2CardHover(i) {
    const card  = document.getElementById('dpcard-' + i);
    const check = document.getElementById('dpcheck-' + i);
    if (check && check.dataset.selected !== '1') {
        card.style.borderColor = '#e5e7eb';
        card.style.boxShadow = '';
    }
}

async function dpv2SaveSelectedDevices() {
    const devices = window.dpv2Devices || [];
    if (!devices.length) { anToast("Koi device nahi mili. Pehle scan karein.", "#991b1b"); return; }
    const selected = devices.filter((d, i) => { const chk = document.getElementById("dpcheck-" + i); return chk && chk.dataset.selected === "1"; });
    const toSave = selected.length > 0 ? selected : devices;
    const btn = document.getElementById("dpv2SaveBtn");
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
    try {
        // ✅ FIX: Use selected CUSTOMER's user_id from analytics dropdown, NOT dealer's ID
        const customerDropdown = document.getElementById('analyticsCustomerDropdown');
        let userId = customerDropdown ? customerDropdown.value : null;
        if (!userId && window.analyticsDataCache && window.analyticsDataCache.user_id) {
            userId = window.analyticsDataCache.user_id;
        }
        if (!userId) {
            anToast("❌ Pehle 'Select Analytics' mein customer select karein.", "#991b1b");
            return;
        }
        const customers = window.allCustomersDataForFilter || [];
        const selectedCustomer = customers.find(c => String(c.user_id || c.id) === String(userId));
        const userName = selectedCustomer ? (selectedCustomer.name || String(userId)) : String(userId);
        // ✅ Use camera scan password (stored when scan was done) - NOT empty string
        let password = window._dpv2ScanPassword || (document.getElementById("dpv2Password") ? document.getElementById("dpv2Password").value : "");
        if (!password) {
            anToast("❌ Camera password nahi mili. Pehle scan karein.", "#991b1b");
            return;
        }
        console.log(`📤 Saving devices for customer: ${userName} (user_id: ${userId})`);
        if (typeof saveDevicesToAnalyticsAPI === "function") {
            await saveDevicesToAnalyticsAPI(toSave, userId, userName, password);
        } else {
            const res = await fetch("/api/devices/save", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "include", body: JSON.stringify({ devices: toSave, user_id: userId }) });
            if (!res.ok) throw new Error("HTTP " + res.status);
            anToast("✅ " + toSave.length + " device(s) save ho gaye!", "#065f46");
        }
    } catch(e) { anToast("Save failed: " + e.message, "#991b1b"); }
    finally { btn.disabled = false; btn.innerHTML = orig; }
}

function dpv2ShowError(msg) {
    const box = document.getElementById('dpv2ErrorBox');
    box.textContent = '⚠️ ' + msg;
    box.style.display = 'block';
}

function toggleStaticIpPassword() {
    const input = document.getElementById('staticIpPassword');
    const icon = document.getElementById('staticIpPwIcon');
    if (!input || !icon) return;
    if (input.type === 'password') {
        input.type = 'text';
        icon.className = 'fas fa-eye-slash';
    } else {
        input.type = 'password';
        icon.className = 'fas fa-eye';
    }
}

function showStaticIpError(message) {
    const box = document.getElementById('staticIpErrorBox');
    if (!box) return;
    box.textContent = '⚠️ ' + message;
    box.style.display = 'block';
}

function hideStaticIpError() {
    const box = document.getElementById('staticIpErrorBox');
    if (box) {
        box.style.display = 'none';
        box.textContent = '';
    }
}

function staticIpEscape(value) {
    return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function setStaticIpSearchLoading(isLoading) {
    const btn = document.getElementById('staticIpSearchBtn');
    if (!btn) return;
    btn.disabled = isLoading;
    btn.innerHTML = isLoading
        ? '<span class="static-ip-inline-loader"><i class="fas fa-spinner"></i> Discovering...</span>'
        : '<i class="fas fa-search"></i> Search Device';
}

function getStaticIpPrimaryRtsp(device) {
    if (!device || typeof device !== 'object') return '';
    return device.rtsp_sub
        || device.rtsp_url
        || device.rtsp_main
        || (Array.isArray(device.rtsp_profiles) ? (device.rtsp_profiles.find(p => p && p.rtsp_url) || {}).rtsp_url : '')
        || '';
}

function getStaticIpPreview(device, index) {
    const rtsp = getStaticIpPrimaryRtsp(device);
    if (!rtsp) return '';
    const params = new URLSearchParams({
        rtsp_url: rtsp,
        channel: String(device.channel || index + 1),
        _: String(Date.now())
    });
    return '/api/static-ip-thumbnail?' + params.toString();
}

function lazyLoadStaticIpThumbnails() {
    const wraps = document.querySelectorAll('.static-ip-preview-wrap');

    const loadPreview = (wrap) => {
        if (!wrap || wrap.dataset.loaded === '1') return;
        wrap.dataset.loaded = '1';

        const img = wrap.querySelector('.static-ip-preview-img');
        const loading = wrap.querySelector('.static-ip-preview-loading');
        const placeholder = wrap.querySelector('.static-ip-preview-placeholder');

        if (!img) {
            if (loading) loading.style.display = 'none';
            if (placeholder) placeholder.style.display = 'flex';
            return;
        }

        img.onload = function () {
            if (loading) loading.style.display = 'none';
            if (placeholder) placeholder.style.display = 'none';
            img.style.display = 'block';
        };

        img.onerror = function () {
            if (loading) loading.style.display = 'none';
            img.style.display = 'none';
            if (placeholder) placeholder.style.display = 'flex';
        };

        img.src = img.dataset.src;
    };

    if (!('IntersectionObserver' in window)) {
        wraps.forEach(loadPreview);
        return;
    }

    const observer = new IntersectionObserver((entries, obs) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                loadPreview(entry.target);
                obs.unobserve(entry.target);
            }
        });
    }, { rootMargin: '120px' });

    wraps.forEach(wrap => observer.observe(wrap));
}

function buildStaticIpPreviewHtml(device, index) {
    const previewUrl = getStaticIpPreview(device, index);

    if (!previewUrl) {
        return '<div class="static-ip-preview-placeholder">No Preview</div>';
    }

    return `
        <div class="static-ip-preview-wrap" data-preview-url="${previewUrl}">
            <div class="static-ip-preview-loading">Loading...</div>
            <img
                data-src="${previewUrl}"
                alt="Preview"
                class="static-ip-preview-img"
                style="display:none;"
            >
            <div class="static-ip-preview-placeholder" style="display:none;">No Preview</div>
        </div>
    `;
}



function showStaticIpDetectedPorts(onvifPort, rtspPort) {
    const meta = document.getElementById('staticIpScanMeta');
    const onvifInput = document.getElementById('staticDetectedOnvifPort');
    const rtspInput = document.getElementById('staticDetectedRtspPort');
    if (onvifInput && onvifPort) onvifInput.value = onvifPort;
    if (rtspInput && rtspPort) rtspInput.value = rtspPort;
    if (meta) meta.classList.add('active');
}

window.staticIpAssignmentState = {
    customers: [],
    selectedCustomer: null,
    analyticsRaw: null,
    analyticsCatalog: [],
    deviceSelections: {},
    discoveredDevices: []
};

function staticIpToast(message, color) {
    if (typeof anToast === 'function') {
        anToast(message, color || '#1d4ed8');
        return;
    }
    alert(message);
}

function staticIpNormalizeKey(value) {
    return String(value || '').trim().replace(/\s+/g, ' ').toLowerCase();
}

function staticIpDisplayName(value) {
    return String(value || '').trim().replace(/\s+/g, ' ').replace(/\w/g, ch => ch.toUpperCase());
}

function getStaticIpSelectedAnalyticsCount() {
    const selections = window.staticIpAssignmentState.deviceSelections || {};
    return Object.values(selections).reduce((sum, items) => sum + (Array.isArray(items) ? items.length : 0), 0);
}

function getStaticIpAnalyticsUsageMap() {
    const usage = {};
    const selections = window.staticIpAssignmentState.deviceSelections || {};
    Object.values(selections).forEach(items => {
        (items || []).forEach(item => {
            const key = item.value;
            usage[key] = (usage[key] || 0) + 1;
        });
    });
    return usage;
}

function buildStaticIpAnalyticsCatalog(byAnalytics) {
    const source = byAnalytics || {};
    const catalogMap = new Map();

    Object.entries(source).forEach(([analyticsKey, config]) => {
        const entry = config || {};
        const baseCount = Number(entry.count || 0) || 0;
        const cameraIds = Array.isArray(entry.camera_ids) ? entry.camera_ids : [];
        const normalizedKey = staticIpNormalizeKey(analyticsKey);

        if (normalizedKey === 'unknown') {
            const freq = {};
            cameraIds.forEach(cameraId => {
                const label = staticIpDisplayName(cameraId || 'Unknown');
                if (!label) return;
                freq[label] = (freq[label] || 0) + 1;
            });
            Object.entries(freq).forEach(([label, count]) => {
                const key = staticIpNormalizeKey(label);
                const current = catalogMap.get(key) || { value: key, label, limit: 0, sourceKey: 'unknown' };
                current.limit += count || 1;
                catalogMap.set(key, current);
            });
            return;
        }

        const label = staticIpDisplayName(analyticsKey);
        if (!label) return;
        const key = staticIpNormalizeKey(label);
        const current = catalogMap.get(key) || { value: key, label, limit: 0, sourceKey: analyticsKey };
        current.limit += baseCount > 0 ? baseCount : Math.max(1, cameraIds.length || 1);
        catalogMap.set(key, current);
    });

    return Array.from(catalogMap.values()).sort((a, b) => a.label.localeCompare(b.label));
}

function setStaticIpCustomerMeta(message, isError) {
    const meta = document.getElementById('staticIpCustomerMeta');
    if (!meta) return;
    meta.style.display = message ? 'block' : 'none';
    meta.style.color = isError ? '#b91c1c' : 'var(--gray-600)';
    meta.innerHTML = message || '';
}

async function ensureStaticIpCustomersLoaded(forceRefresh) {
    const state = window.staticIpAssignmentState;
    const loader = document.getElementById('staticIpCustomerLoader');
    const dropdown = document.getElementById('staticIpCustomerDropdown');

    if (!forceRefresh && Array.isArray(state.customers) && state.customers.length > 0) {
        renderStaticIpCustomerOptions(state.customers);
        return state.customers;
    }

    if (loader) loader.style.display = 'block';
    setStaticIpCustomerMeta('', false);

    try {
        const res = await fetch('/api/dealer/customers', { credentials: 'include' });
        const json = await res.json().catch(() => ({}));
        if (!res.ok || json.status === 'error') {
            throw new Error(json.message || ('HTTP ' + res.status));
        }

        const raw = Array.isArray(json.customers) ? json.customers : [];
        state.customers = raw.map(c => ({
            user_id: c.user_id || c.id || '',
            name: (c.name || c.full_name || '').trim(),
            raw: c
        })).filter(c => c.user_id && c.name).sort((a, b) => a.name.localeCompare(b.name));

        renderStaticIpCustomerOptions(state.customers);
        return state.customers;
    } catch (error) {
        console.error('Static IP customer load failed:', error);
        if (dropdown) dropdown.innerHTML = '<div class="static-ip-empty-state">Failed to load customers</div>';
        setStaticIpCustomerMeta('<i class="fas fa-exclamation-circle"></i> Failed to load customers.', true);
        return [];
    } finally {
        if (loader) loader.style.display = 'none';
    }
}

function renderStaticIpCustomerOptions(customers) {
    const dropdown = document.getElementById('staticIpCustomerDropdown');
    if (!dropdown) return;

    if (!customers || customers.length === 0) {
        dropdown.innerHTML = '<div class="static-ip-empty-state">No customers found</div>';
        return;
    }

    dropdown.innerHTML = customers.map(customer => {
        const name = staticIpEscape(customer.name);
        const userId = staticIpEscape(customer.user_id);
        return `<div class="static-ip-searchable-option" data-name="${name.toLowerCase()}" data-user-id="${userId}" onclick="selectStaticIpCustomer('${userId}')">${name}</div>`;
    }).join('');
}

function openStaticIpCustomerDropdown() {
    const dropdown = document.getElementById('staticIpCustomerDropdown');
    if (dropdown) dropdown.classList.add('active');
    ensureStaticIpCustomersLoaded(false);
}

function closeStaticIpCustomerDropdown() {
    const dropdown = document.getElementById('staticIpCustomerDropdown');
    if (dropdown) dropdown.classList.remove('active');
}

function toggleStaticIpCustomerDropdown() {
    const dropdown = document.getElementById('staticIpCustomerDropdown');
    if (!dropdown) return;
    if (dropdown.classList.contains('active')) closeStaticIpCustomerDropdown();
    else openStaticIpCustomerDropdown();
}

function filterStaticIpCustomerOptions(query) {
    const term = String(query || '').trim().toLowerCase();
    const dropdown = document.getElementById('staticIpCustomerDropdown');
    if (!dropdown) return;

    let visibleCount = 0;
    Array.from(dropdown.querySelectorAll('.static-ip-searchable-option')).forEach(option => {
        const show = !term || (option.dataset.name || '').includes(term);
        option.style.display = show ? 'block' : 'none';
        if (show) visibleCount += 1;
    });

    let empty = document.getElementById('staticIpCustomerNoMatch');
    if (!empty) {
        empty = document.createElement('div');
        empty.id = 'staticIpCustomerNoMatch';
        empty.className = 'static-ip-empty-state';
        empty.textContent = 'No matching customer';
        dropdown.appendChild(empty);
    }
    empty.style.display = visibleCount === 0 ? 'block' : 'none';
    openStaticIpCustomerDropdown();
}

async function selectStaticIpCustomer(userId) {
    const state = window.staticIpAssignmentState;
    const selected = (state.customers || []).find(item => String(item.user_id) === String(userId));
    const searchInput = document.getElementById('staticIpCustomerSearch');
    const hiddenInput = document.getElementById('staticIpSelectedCustomerId');

    state.selectedCustomer = selected || null;
    if (searchInput) searchInput.value = selected ? selected.name : '';
    if (hiddenInput) hiddenInput.value = selected ? selected.user_id : '';
    closeStaticIpCustomerDropdown();

    if (!selected) {
        setStaticIpCustomerMeta('', false);
        state.analyticsRaw = null;
        state.analyticsCatalog = [];
        state.deviceSelections = {};
        updateStaticIpResultsVisibility();
        renderStaticIpDiscoveryResults(state.discoveredDevices || []);
        return;
    }

    setStaticIpCustomerMeta(`<span class="static-ip-limit-pill"><i class="fas fa-user"></i>${staticIpEscape(selected.name)}</span>`, false);
    await loadStaticIpCustomerAnalytics(selected.user_id);
    renderStaticIpDiscoveryResults(state.discoveredDevices || []);
    updateStaticIpResultsVisibility();
}

async function loadStaticIpCustomerAnalytics(userId) {
    const state = window.staticIpAssignmentState;
    const loader = document.getElementById('staticIpCustomerLoader');
    const subtext = document.getElementById('staticIpResultsSubtext');

    if (loader) loader.style.display = 'block';
    if (subtext) subtext.textContent = 'Loading analytics rules for selected customer...';

    try {
        const res = await fetch(`/api/dealer/user-purchases2?user_id=${encodeURIComponent(userId)}`, { credentials: 'include' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status === 'error') throw new Error(data.message || ('HTTP ' + res.status));

        state.analyticsRaw = data;
        state.analyticsCatalog = buildStaticIpAnalyticsCatalog(data.by_analytics || {});
        state.deviceSelections = {};

        const total = Number(data.total_camera_selections || 0) || 0;
        const options = state.analyticsCatalog.length;
        setStaticIpCustomerMeta(
            `<span class="static-ip-limit-pill"><i class="fas fa-user"></i>${staticIpEscape(state.selectedCustomer?.name || '')}</span> ` +
            `<span class="static-ip-limit-pill"><i class="fas fa-layer-group"></i>${total} total selections</span> ` +
            `<span class="static-ip-limit-pill"><i class="fas fa-chart-line"></i>${options} analytics options</span>`,
            false
        );

        if (subtext) {
            subtext.textContent = total > 0 ? `Select analytics for each discovered stream. Global limit: ${total}` : 'No analytics quota found for the selected customer.';
        }
    } catch (error) {
        console.error('Static IP analytics load failed:', error);
        state.analyticsRaw = { by_analytics: {}, total_camera_selections: 0 };
        state.analyticsCatalog = [];
        state.deviceSelections = {};
        setStaticIpCustomerMeta('<i class="fas fa-exclamation-circle"></i> Failed to load analytics for selected customer.', true);
        if (subtext) subtext.textContent = 'Failed to load analytics rules.';
    } finally {
        if (loader) loader.style.display = 'none';
        updateStaticIpSaveState();
    }
}

function updateStaticIpResultsVisibility() {
    const results = document.getElementById('staticIpResultsSection');
    if (!results) return;
    const hasCustomer = !!window.staticIpAssignmentState.selectedCustomer;
    results.classList.toggle('active', hasCustomer);
}

function renderStaticIpSelectedTags(deviceIndex) {
    const container = document.getElementById(`staticIpSelectedTags-${deviceIndex}`);
    const selections = window.staticIpAssignmentState.deviceSelections[deviceIndex] || [];
    if (!container) return;
    container.innerHTML = selections.length ? selections.map(item => `<span class="static-ip-selected-tag">${staticIpEscape(item.label)}</span>`).join('') : '';
}

function getStaticIpTotalLimit() {
    return Number(window.staticIpAssignmentState.analyticsRaw?.total_camera_selections || 0) || 0;
}

function updateStaticIpSaveState() {
    const saveBtn = document.getElementById('staticIpSaveBtn');
    const state = window.staticIpAssignmentState;
    if (!saveBtn) return;
    saveBtn.disabled = !(state.selectedCustomer && (state.discoveredDevices || []).length > 0);
}

function toggleStaticIpAnalyticsMenu(deviceIndex) {
    const target = document.getElementById(`staticIpAnalyticsMenu-${deviceIndex}`);
    if (!target) return;
    document.querySelectorAll('.static-ip-multiselect-menu.active').forEach(menu => {
        if (menu !== target) menu.classList.remove('active');
    });
    target.classList.toggle('active');
}

function filterStaticIpAnalyticsOptions(deviceIndex, query) {
    const term = String(query || '').trim().toLowerCase();
    const list = document.getElementById(`staticIpAnalyticsOptions-${deviceIndex}`);
    if (!list) return;

    let visible = 0;
    Array.from(list.querySelectorAll('.static-ip-multiselect-option')).forEach(row => {
        const label = (row.dataset.label || '').toLowerCase();
        const show = !term || label.includes(term);
        row.style.display = show ? 'flex' : 'none';
        if (show) visible += 1;
    });

    const empty = document.getElementById(`staticIpAnalyticsEmpty-${deviceIndex}`);
    if (empty) empty.style.display = visible === 0 ? 'block' : 'none';
}

function updateStaticIpDropdownSummary(deviceIndex) {
    const label = document.getElementById(`staticIpAnalyticsLabel-${deviceIndex}`);
    const selections = window.staticIpAssignmentState.deviceSelections[deviceIndex] || [];
    if (label) label.textContent = selections.length ? `${selections.length} selected` : 'Select Analytics';
    renderStaticIpSelectedTags(deviceIndex);
}

function refreshStaticIpAnalyticsDisabledStates() {
    const usage = getStaticIpAnalyticsUsageMap();
    const totalLimit = getStaticIpTotalLimit();
    const totalSelected = getStaticIpSelectedAnalyticsCount();
    const catalog = window.staticIpAssignmentState.analyticsCatalog || [];

    document.querySelectorAll('.static-ip-analytics-option-checkbox').forEach(checkbox => {
        const optionKey = checkbox.dataset.optionValue;
        const option = catalog.find(item => item.value === optionKey);
        const used = usage[optionKey] || 0;
        const optionLimitReached = option ? used >= option.limit : false;
        const totalLimitReached = totalLimit > 0 && totalSelected >= totalLimit;
        const shouldDisable = !checkbox.checked && (optionLimitReached || totalLimitReached);
        checkbox.disabled = shouldDisable;
        const row = checkbox.closest('.static-ip-multiselect-option');
        if (row) row.classList.toggle('disabled', shouldDisable);
        const helper = row ? row.querySelector('.static-ip-option-limit') : null;
        if (helper && option) helper.textContent = `${used}/${option.limit} used`;
    });
}

function onStaticIpAnalyticsChange(deviceIndex, optionValue, checked) {
    const state = window.staticIpAssignmentState;
    const catalog = state.analyticsCatalog || [];
    const option = catalog.find(item => item.value === optionValue);
    const checkbox = document.querySelector(`.static-ip-analytics-option-checkbox[data-device-index="${deviceIndex}"][data-option-value="${optionValue}"]`);
    if (!option || !checkbox) return;

    const currentSelections = Array.isArray(state.deviceSelections[deviceIndex]) ? [...state.deviceSelections[deviceIndex]] : [];

    if (checked) {
        const totalLimit = getStaticIpTotalLimit();
        const totalSelected = getStaticIpSelectedAnalyticsCount();
        const usage = getStaticIpAnalyticsUsageMap();
        const usedForOption = usage[optionValue] || 0;

        if (totalLimit > 0 && totalSelected >= totalLimit) {
            checkbox.checked = false;
            staticIpToast(`❌ Maximum ${totalLimit} analytics selections allowed.`, '#991b1b');
            refreshStaticIpAnalyticsDisabledStates();
            return;
        }
        if (usedForOption >= option.limit) {
            checkbox.checked = false;
            staticIpToast(`❌ ${option.label} can be selected only ${option.limit} time(s).`, '#991b1b');
            refreshStaticIpAnalyticsDisabledStates();
            return;
        }
        if (!currentSelections.some(item => item.value === optionValue)) currentSelections.push({ value: option.value, label: option.label, sourceKey: option.sourceKey, limit: option.limit });
    } else {
        state.deviceSelections[deviceIndex] = currentSelections.filter(item => item.value !== optionValue);
        updateStaticIpDropdownSummary(deviceIndex);
        refreshStaticIpAnalyticsDisabledStates();
        return;
    }

    state.deviceSelections[deviceIndex] = currentSelections;
    updateStaticIpDropdownSummary(deviceIndex);
    refreshStaticIpAnalyticsDisabledStates();
}

function buildStaticIpAnalyticsDropdownHtml(deviceIndex) {
    const state = window.staticIpAssignmentState;
    const catalog = state.analyticsCatalog || [];
    const selected = state.deviceSelections[deviceIndex] || [];
    const disabled = !state.selectedCustomer || catalog.length === 0;

    if (disabled) {
        return `<div class="static-ip-multiselect"><button type="button" class="static-ip-multiselect-toggle" disabled><span id="staticIpAnalyticsLabel-${deviceIndex}">Select customer first</span><i class="fas fa-chevron-down"></i></button><div id="staticIpSelectedTags-${deviceIndex}" class="static-ip-selected-tags"></div></div>`;
    }

    return `
        <div class="static-ip-multiselect">
            <button type="button" class="static-ip-multiselect-toggle" onclick="toggleStaticIpAnalyticsMenu(${deviceIndex})">
                <span id="staticIpAnalyticsLabel-${deviceIndex}">${selected.length ? `${selected.length} selected` : 'Select Analytics'}</span>
                <i class="fas fa-chevron-down"></i>
            </button>
            <div id="staticIpAnalyticsMenu-${deviceIndex}" class="static-ip-multiselect-menu">
                <input type="text" class="static-ip-multiselect-search" placeholder="Search analytics" oninput="filterStaticIpAnalyticsOptions(${deviceIndex}, this.value)">
                <div id="staticIpAnalyticsOptions-${deviceIndex}" class="static-ip-multiselect-options">
                    ${catalog.map(option => {
                        const isSelected = selected.some(item => item.value === option.value);
                        return `<label class="static-ip-multiselect-option" data-label="${staticIpEscape(option.label)}"><input type="checkbox" class="static-ip-analytics-option-checkbox" data-device-index="${deviceIndex}" data-option-value="${staticIpEscape(option.value)}" ${isSelected ? 'checked' : ''} onchange="onStaticIpAnalyticsChange(${deviceIndex}, '${staticIpEscape(option.value)}', this.checked)"><span class="static-ip-option-text"><span class="static-ip-option-title">${staticIpEscape(option.label)}</span><span class="static-ip-option-limit">0/${option.limit} used</span></span></label>`;
                    }).join('')}
                </div>
                <div id="staticIpAnalyticsEmpty-${deviceIndex}" class="static-ip-empty-state" style="display:none;">No matching analytics</div>
            </div>
            <div id="staticIpSelectedTags-${deviceIndex}" class="static-ip-selected-tags"></div>
        </div>
    `;
}

function renderStaticIpDiscoveryResults(devices) {
    const results = document.getElementById('staticIpResultsSection');
    const tbody = document.getElementById('staticIpResultsTbody');
    const count = document.getElementById('staticIpResultsCount');
    const subtext = document.getElementById('staticIpResultsSubtext');
    const list = Array.isArray(devices) ? devices : [];

    window._staticIpDiscoveredDevices = list;
    window.staticIpAssignmentState.discoveredDevices = list;
    if (count) count.textContent = list.length + ' camera(s)';
    if (!tbody) return;

    if (!window.staticIpAssignmentState.selectedCustomer) {
        tbody.innerHTML = '<tr><td colspan="5" style="padding:18px; text-align:center; color:#94a3b8;">Select a customer to map analytics to discovered streams.</td></tr>';
        if (subtext) subtext.textContent = 'Select a customer to map analytics.';
        updateStaticIpResultsVisibility();
        updateStaticIpSaveState();
        return;
    }

    if (list.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="padding:18px; text-align:center; color:#94a3b8;">No cameras found for this static IP.</td></tr>';
        if (results) results.classList.add('active');
        updateStaticIpSaveState();
        return;
    }

    tbody.innerHTML = list.map((device, index) => {
        const channel = staticIpEscape(device.channel || (index + 1));
        const ip = staticIpEscape(device.address || device.device_ip || device.ip || '—');
        const previewHtml = buildStaticIpPreviewHtml(device, index);
        const analyticsHtml = buildStaticIpAnalyticsDropdownHtml(index);

        return `<tr><td class="static-ip-checkbox-cell"><input type="checkbox" class="static-ip-row-checkbox" id="staticIpRowCheck-${index}" onchange="updateStaticIpRowSelection()"></td><td>${previewHtml}</td><td><span class="static-ip-channel-badge">CH ${channel}</span></td><td class="static-ip-ip-cell">${ip}</td><td class="static-ip-analytics-cell">${analyticsHtml}</td></tr>`;
    }).join('');

    if (results) results.classList.add('active');
    lazyLoadStaticIpThumbnails();
    list.forEach((_, index) => updateStaticIpDropdownSummary(index));
    refreshStaticIpAnalyticsDisabledStates();
    updateStaticIpRowSelection();
    updateStaticIpSaveState();
}

function updateStaticIpRowSelection() {
    const rowChecks = Array.from(document.querySelectorAll('.static-ip-row-checkbox'));
    const allSelected = rowChecks.length > 0 && rowChecks.every(input => input.checked);
    const selectAll = document.getElementById('staticIpSelectAll');
    if (selectAll) selectAll.checked = allSelected;
}

function toggleAllStaticIpRows(checked) {
    document.querySelectorAll('.static-ip-row-checkbox').forEach(input => { input.checked = checked; });
}

async function saveStaticIpSelections() {
    const state = window.staticIpAssignmentState;
    const selectedRowIndexes = Array.from(document.querySelectorAll('.static-ip-row-checkbox:checked')).map(input => Number(input.id.split('-').pop()));

    if (!state.selectedCustomer) { staticIpToast('❌ Please select a customer first.', '#991b1b'); return; }
    if (!selectedRowIndexes.length) { staticIpToast('❌ Please select at least one row.', '#991b1b'); return; }

    // ── Only save rows that have analytics selected ──
    const rowsWithAnalytics = selectedRowIndexes.filter(index => {
        const selections = state.deviceSelections[index] || [];
        return selections.length > 0;
    });

    if (rowsWithAnalytics.length === 0) {
        staticIpToast('❌ No analytics selected. Please select analytics for at least one device.', '#991b1b');
        return;
    }

    const userId = state.selectedCustomer.user_id;
    const username = (document.getElementById('staticIpUsername')?.value || '').trim();
    const password = (document.getElementById('staticIpPassword')?.value || '').trim();
    const port = Number(document.getElementById('staticIpPort')?.value) || 554;

    // ── Build devices payload for /api/save-analytics ──
    const devPayload = rowsWithAnalytics.map(index => {
        const device = (state.discoveredDevices || [])[index] || {};
        const selections = state.deviceSelections[index] || [];
        const ip = device.address || device.device_ip || device.ip || '';
        const rtspUrl = device.rtsp_url || '';
        const substream = rtspUrl || (ip && username && password ? `rtsp://${username}:${password}@${ip}:${port}/stream1` : '');
        const mainstream = ip && username && password ? `rtsp://${username}:${password}@${ip}:${port}/stream0` : '';

        return {
            ip: ip,
            device_ip: ip,
            devices_id: device.devices_id || device.device_id || null,
            substream_rtsp: substream,
            mainstream_rtsp: mainstream,
            nick_Name: device.nick_Name || device.name || device.label || '',
            city_name: device.city_name || '',
            center_name: device.center_name || '',
            state_name: device.state_name || '',
            analytics: selections.map(sel => ({
                analyticsType: sel.label || sel.value,
                cameraName: sel.value,
                channel: device.channel || 'ch' + (index + 1)
            }))
        };
    });

    // ── Disable save button ──
    const btn = document.getElementById('staticIpSaveBtn');
    const orig = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...'; }

    try {
        console.log(`📤 Saving analytics for ${rowsWithAnalytics.length} device(s), user_id: ${userId}`);

        const response = await fetch('/api/save-analytics', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ user_id: userId, port: port, devices: devPayload })
        });

        const result = await response.json();

        if (response.ok && result.status === 'success') {
            staticIpToast('✅ ' + result.message, '#065f46');
            console.log('✅ Analytics saved:', result);
        } else if (result.status === 'partial') {
            staticIpToast('⚠️ ' + result.message, '#b45309');
            console.warn('⚠️ Partial save:', result);
        } else {
            staticIpToast('❌ ' + (result.message || 'Failed to save analytics'), '#991b1b');
            console.error('❌ Save error:', result);
        }
    } catch (e) {
        staticIpToast('Save failed: ' + e.message, '#991b1b');
        console.error('❌ Exception:', e);
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = orig; }
    }
}

function resetStaticIpDiscoveryForm() {
    hideStaticIpError();
    const wrap = document.getElementById('staticIpDiscoveryFormWrap');
    if (wrap) wrap.classList.remove('active');
    window._dpv2SelectedPort = null;
    window._staticIpDiscoveredDevices = [];
    window.staticIpAssignmentState = {
        customers: window.staticIpAssignmentState?.customers || [],
        selectedCustomer: null,
        analyticsRaw: null,
        analyticsCatalog: [],
        deviceSelections: {},
        discoveredDevices: []
    };

    const meta = document.getElementById('staticIpScanMeta');
    const results = document.getElementById('staticIpResultsSection');
    const tbody = document.getElementById('staticIpResultsTbody');
    const count = document.getElementById('staticIpResultsCount');
    const onvifInput = document.getElementById('staticDetectedOnvifPort');
    const rtspInput = document.getElementById('staticDetectedRtspPort');
    const searchBtn = document.getElementById('staticIpSearchBtn');
    const customerInput = document.getElementById('staticIpCustomerSearch');
    const customerIdInput = document.getElementById('staticIpSelectedCustomerId');
    const selectAll = document.getElementById('staticIpSelectAll');
    const subtext = document.getElementById('staticIpResultsSubtext');

    if (meta) meta.classList.remove('active');
    if (results) results.classList.remove('active');
    if (tbody) tbody.innerHTML = '<tr><td colspan="5" style="padding:18px; text-align:center; color:#94a3b8;">Run static IP discovery and select a customer to see results here.</td></tr>';
    if (count) count.textContent = '0 camera(s)';
    if (subtext) subtext.textContent = 'Select a customer to map analytics.';
    if (onvifInput) onvifInput.value = '';
    if (rtspInput) rtspInput.value = '';
    if (customerInput) customerInput.value = '';
    if (customerIdInput) customerIdInput.value = '';
    if (selectAll) selectAll.checked = false;
    setStaticIpCustomerMeta('', false);
    closeStaticIpCustomerDropdown();
    if (searchBtn) { searchBtn.disabled = false; searchBtn.innerHTML = '<i class="fas fa-search"></i> Search Device'; }
    updateStaticIpSaveState();
}

function hideStaticIpDiscoveryForm() {
    resetStaticIpDiscoveryForm();
    const card = document.querySelector('#deviceDiscoveryEntrySection .discovery-entry-card');
    if (card) card.classList.remove('static-ip-expanded');
}

function setDpv2DiscoveryMode(mode, ipValue, portValue) {
    window._dpv2DiscoveryMode = mode || 'agent';
    const deviceSection = document.getElementById('dpv2DeviceSelector');
    const banner = document.getElementById('dpv2ModeBanner');
    const formattedTarget = portValue ? (ipValue + ':' + portValue) : ipValue;

    if (deviceSection) deviceSection.style.display = mode === 'static' ? 'none' : 'block';

    if (banner) {
        if (mode === 'static' && ipValue) {
            banner.classList.add('active');
            banner.innerHTML = '<i class="fas fa-network-wired" style="margin-right:8px;"></i>Static IP mode active for <strong>' + formattedTarget + '</strong>. Discovery will run directly against this device.';
        } else {
            banner.classList.remove('active');
            banner.textContent = '';
        }
    }
}

function openDeviceDiscoveryLanding() {
    const entrySection = document.getElementById('deviceDiscoveryEntrySection');
    const actualSection = document.getElementById('deviceDiscoveryActualSection');
    const card = document.querySelector('#deviceDiscoveryEntrySection .discovery-entry-card');
    if (entrySection) { entrySection.classList.add('active'); entrySection.style.display = 'block'; }
    if (actualSection) { actualSection.classList.remove('active'); actualSection.style.display = 'none'; }
    if (card) card.classList.remove('static-ip-expanded');
    setDpv2DiscoveryMode('agent');
    window._dpv2SelectedDevice = null;
    window._dpv2SelectedPort = null;
    resetStaticIpDiscoveryForm();
}

function showScanWithDevicesSection() {
    const entrySection = document.getElementById('deviceDiscoveryEntrySection');
    const actualSection = document.getElementById('deviceDiscoveryActualSection');
    const card = document.querySelector('#deviceDiscoveryEntrySection .discovery-entry-card');
    if (entrySection) { entrySection.classList.remove('active'); entrySection.style.display = 'none'; }
    if (actualSection) { actualSection.classList.add('active'); actualSection.style.display = 'block'; }
    if (card) card.classList.remove('static-ip-expanded');
    setDpv2DiscoveryMode('agent');
    window._dpv2SelectedDevice = null;
    window._dpv2SelectedPort = null;
    resetStaticIpDiscoveryForm();
}

function handleStaticIpDiscovery() {
    resetStaticIpDiscoveryForm();
    const wrap = document.getElementById('staticIpDiscoveryFormWrap');
    const card = document.querySelector('#deviceDiscoveryEntrySection .discovery-entry-card');
    if (wrap) wrap.classList.add('active');
    if (card) card.classList.add('static-ip-expanded');
    hideStaticIpError();
    ensureStaticIpCustomersLoaded(false);
}

function isValidIpv4Address(value) {
    if (!value) return false;
    const ipRegex = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;
    return ipRegex.test(value.trim());
}

async function startStaticIpDiscovery() {
    const ip = (document.getElementById('staticIpAddress')?.value || '').trim();
    const portRaw = (document.getElementById('staticIpPort')?.value || '').trim();
    const username = (document.getElementById('staticIpUsername')?.value || '').trim();
    const password = (document.getElementById('staticIpPassword')?.value || '').trim();
    const port = portRaw ? Number(portRaw) : null;

    hideStaticIpError();

    if (!ip) { showStaticIpError('Please enter a static IP address.'); return; }
    if (!isValidIpv4Address(ip)) { showStaticIpError('Please enter a valid IPv4 address, for example 192.168.1.120.'); return; }
    if (portRaw && (!Number.isInteger(port) || port < 1 || port > 65535)) { showStaticIpError('Please enter a valid port between 1 and 65535.'); return; }
    if (!username) { showStaticIpError('Please enter a username.'); return; }
    if (!password) { showStaticIpError('Please enter a password.'); return; }

    setStaticIpSearchLoading(true);
    window.staticIpAssignmentState.deviceSelections = {};
    renderStaticIpDiscoveryResults([]);
    const resultsSection = document.getElementById('staticIpResultsSection');
    if (resultsSection) resultsSection.classList.remove('active');

    try {
        const res = await fetch('/api/static-ip-discovery', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ip, username, password, port })
        });

        const out = await res.json().catch(() => ({}));
        if (!res.ok || out.status !== 'ok') throw new Error(out.message || out.error || ('HTTP ' + res.status));

        const devices = Array.isArray(out.devices) ? out.devices : [];
        const detectedOnvif = out.onvif_port || port || '';
        const detectedRtsp = out.rtsp_port || 554;

        showStaticIpDetectedPorts(detectedOnvif, detectedRtsp);
        renderStaticIpDiscoveryResults(devices);

        window._dpv2SelectedDevice = ip;
        window._dpv2SelectedPort = detectedOnvif;
        window._dpv2ScanUsername = username;
        window._dpv2ScanPassword = password;
        window.dpv2Devices = devices;
        window.discoveredDevices = devices;
    } catch (err) {
        showStaticIpError(err.message || 'Static IP discovery failed.');
    } finally {
        setStaticIpSearchLoading(false);
        updateStaticIpSaveState();
    }
}

window.addEventListener('click', function (event) {
    const customerWrap = document.getElementById('staticIpCustomerSelectWrap');
    if (customerWrap && !customerWrap.contains(event.target)) closeStaticIpCustomerDropdown();

    document.querySelectorAll('.static-ip-multiselect').forEach(wrap => {
        if (!wrap.contains(event.target)) {
            const menu = wrap.querySelector('.static-ip-multiselect-menu');
            if (menu) menu.classList.remove('active');
        }
    });
});
