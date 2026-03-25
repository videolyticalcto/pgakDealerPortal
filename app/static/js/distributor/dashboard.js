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
        let allDealersDataForFilter = [];
        let currentDevicesFilter = 'online'; // Default to online only
        let allDevicesData = {}; // Store all devices data
        let filteredDevicesData = {}; // Store filtered devices data

        // NEW: Mobile view state
        let isMobileView = window.innerWidth <= 768;
        let devicesRefreshInterval = null;
        let onlineDevicesData = []; // Store online devices from assets table

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

        // ===== INITIALIZATION =====
        document.addEventListener('DOMContentLoaded', function() {
            setupMobileMenu();
            setupMenuItems();
            setupLogout();
            loadCurrentUser();
            loadDistributorCode();
            loadDashboardData();
            setupMobileDetailBackButtons();
            handleResponsiveTables();
            
            // Listen for window resize
            window.addEventListener('resize', function() {
                isMobileView = window.innerWidth <= 768;
                handleResponsiveTables();
            });
        });
        
        // ✅ SETUP RESPONSIVE TABLE BEHAVIOR
        function handleResponsiveTables() {
            if (isMobileView) {
                setupMobileTables();
            } else {
                setupDesktopTables();
            }
        }
        
        function setupMobileTables() {
            // Mobile styles are handled by CSS
        }
        
        function setupDesktopTables() {
            // Reset to normal table layout
        }
        
        // ===== MOBILE DETAIL PAGE FUNCTIONS =====
        function setupMobileDetailBackButtons() {
            const dealerBackBtn = document.getElementById('mobileDealerDetailBackBtn');
            const deviceBackBtn = document.getElementById('mobileDeviceDetailBackBtn');
            
            if (dealerBackBtn) {
                dealerBackBtn.addEventListener('click', closeDealerDetailPage);
            }
            if (deviceBackBtn) {
                deviceBackBtn.addEventListener('click', closeDeviceDetailPage);
            }
        }
        
        // ✅ OPEN DEALER DETAIL PAGE (MOBILE)
        function openDealerDetailPage(dealerId, dealerName) {
            const dealer = allDealersDataForFilter.find(d => d.user_id == dealerId || d.id == dealerId);
            if (!dealer) return;
            
            const detailPage = document.getElementById('mobileDealerDetailPage');
            document.getElementById('mobileDealerDetailTitle').textContent = dealerName || dealer.full_name;
            
            const detailContent = document.getElementById('mobileDealerDetailContent');
            const statusColor = dealer.status === 'Approved' ? '#10B981' : dealer.status === 'Pending' ? '#F59E0B' : '#EF4444';
            const statusBg = dealer.status === 'Approved' ? 'rgba(16, 185, 129, 0.1)' : dealer.status === 'Pending' ? 'rgba(245, 158, 11, 0.1)' : 'rgba(239, 68, 68, 0.1)';
            
            detailContent.innerHTML = `
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Full Name</div>
                    <div class="mobile-detail-value">${escapeHtml(dealer.full_name)}</div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Address</div>
                    <div class="mobile-detail-value">${escapeHtml(dealer.address)}</div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Email</div>
                    <div class="mobile-detail-value">${escapeHtml(dealer.email)}</div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Phone Number</div>
                    <div class="mobile-detail-value">${escapeHtml(dealer.phone_number || 'N/A')}</div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Company</div>
                    <div class="mobile-detail-value">${escapeHtml(dealer.company_name || 'N/A')}</div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Type</div>
                    <div class="mobile-detail-value">
                        <span class="type-badge">Dealer</span>
                    </div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Status</div>
                    <div class="mobile-detail-value">
                        <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                            <span class="status-dot" style="background: ${statusColor};"></span>
                            ${dealer.status}
                        </span>
                    </div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Distributor Code</div>
                    <div class="mobile-detail-value">${escapeHtml(dealer.distributor_code || 'N/A')}</div>
                </div>
            `;
            detailPage.classList.add('active');
        }
        
        // ✅ CLOSE DEALER DETAIL PAGE
        function closeDealerDetailPage() {
            const detailPage = document.getElementById('mobileDealerDetailPage');
            detailPage.classList.remove('active');
        }
        
        // ✅ OPEN DEVICE DETAIL PAGE (MOBILE)
        function openDeviceDetailPage(hostname, deviceData) {
            try {
                const device = typeof deviceData === 'string' ? JSON.parse(deviceData) : deviceData;
                const info = device.info || {};
                const detailPage = document.getElementById('mobileDeviceDetailPage');
                document.getElementById('mobileDeviceDetailTitle').textContent = hostname;
                
                const detailContent = document.getElementById('mobileDeviceDetailContent');
                const isOnline = (device.status === 'ACTIVE' || device.status === 'ONLINE');
                const statusColor = isOnline ? '#10B981' : '#EF4444';
                const statusBg = isOnline ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)';
                
                detailContent.innerHTML = `
                    <div class="mobile-detail-section">
                        <div class="mobile-detail-label">Hostname</div>
                        <div class="mobile-detail-value">${escapeHtml(hostname)}</div>
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
                        <div class="mobile-detail-label">Serial Number</div>
                        <div class="mobile-detail-value">${escapeHtml(info['Serial Number'] || 'N/A')}</div>
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
        
        // ✅ CLOSE DEVICE DETAIL PAGE
        function closeDeviceDetailPage() {
            const detailPage = document.getElementById('mobileDeviceDetailPage');
            detailPage.classList.remove('active');
        }
        
        // ✅ FORMAT MAC ADDRESSES FOR MOBILE VIEW
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
        
        // ✅ FORMAT IP ADDRESSES FOR MOBILE VIEW
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
                    if (device.rtsp_profiles && Array.isArray(device.rtsp_profiles)) {
                        device.rtsp_profiles.forEach(profile => {
                            if (profile.rtsp_url) {
                                rtspUrls.push(profile.rtsp_url);
                            }
                        });
                    }
                });
                const payload = {
                    user_id: userId,
                    user_name: userName,
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
                    return {
                        success: true,
                        message: 'Devices saved to analytics',
                        data: result
                    };
                } else {
                    console.error('❌ Analytics API error:', result);
                    return {
                        success: false,
                        message: result.message || 'Failed to save to analytics API',
                        data: result
                    };
                }
            } catch (err) {
                console.error('❌ Error saving to Analytics API:', err);
                return {
                    success: false,
                    message: 'Network error: ' + err.message,
                    error: err
                };
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

        const scanBtnEl = document.getElementById('scanBtn'); // your scan button in modal
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

            const res = await fetch(`/api/scan`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password })
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
                    countLine.textContent = "Found Devices: " + foundCount;
                    countLine.classList.add("ok");
                }

                if (typeof closeCredentialsModal === "function") {
                    closeCredentialsModal();
                }

                discoveredDevices = devices;

                if (typeof saveDevicesToAnalyticsAPI === "function") {
                    console.log('📤 Calling saveDevicesToAnalyticsAPI with', devices.length, 'devices...');
                    const saveResult = await saveDevicesToAnalyticsAPI(
                        devices,
                        issueState?.currentUserId,
                        issueState?.currentUserData?.full_name || username,
                        password
                    );

                    if (saveResult && saveResult.success) {
                        console.log('✅ Devices saved successfully to Analytics API');
                        showDiscoveredDevicesInModal(devices);
                    } else {
                        console.error('❌ Failed to save devices to Analytics API:', saveResult?.message);
                        alert('Devices discovered but failed to save to analytics: ' + (saveResult?.message || "unknown error"));
                        showDiscoveredDevicesInModal(devices);
                    }
                } else {
                    console.log('ℹ️ saveDevicesToAnalyticsAPI not found, showing devices directly');
                    showDiscoveredDevicesInModal(devices);
                }

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

        function showDiscoveredDevicesInModal(devices) {
            console.log("📱 Showing discovered devices...");
            
            // ✅ FILTER DEVICES: ONLY SHOW DEVICES WITH RTSP FEEDS
            const devicesWithRTSP = devices.filter(device => {
                const hasRtspProfiles = device.rtsp_profiles && Array.isArray(device.rtsp_profiles) && device.rtsp_profiles.length > 0;
                const hasRtspUrls = hasRtspProfiles && device.rtsp_profiles.some(profile => profile.rtsp_url);
                
                if (!hasRtspUrls) {
                    console.log("⏭️  Skipping device without RTSP feeds:", device.device_ip);
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
                <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 12px; padding: 12px; border-top: 1px solid var(--gray-200); border-bottom: 1px solid var(--gray-200); background: white; position: sticky; top: 0; z-index: 10;">
                    <p style="font-size: 13px; color: var(--gray-600); margin: 0; font-weight: 500;">
                        Found ${devicesWithRTSP.length} device(s) with RTSP. Click to select:
                    </p>
                    <button onclick="saveSelectedDevices()" 
                            style="padding: 8px 16px; background-color: var(--primary); color: white; border: none; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; white-space: nowrap;">
                        💾 Save Devices
                    </button>
                </div>
                <div id="devices-scroll-container" style="max-height: 400px; overflow-y: auto;">
                </div>
            `;

            // Load analytics first
            loadAnalyticsDataForDevices(userId).then(() => {
                // After analytics loaded, render ONLY FILTERED DEVICES
                devicesWithRTSP.forEach((device, filteredIndex) => {
                    // Find original index in full devices array for analytics lookup
                    const originalIndex = devices.indexOf(device);
                    const ip = device.device_ip || device.ip || 'N/A';
                    const snapshot = device.screenshot_path || device.image_url || (device.device_ip || device.ip) ? `https://20.198.16.57:5000/images/${device.device_ip || device.ip}_jpg` : 'data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22100%22 height=%22100%22%3E%3Crect fill=%22%23f0f0f0%22 width=%22100%22 height=%22100%22/%3E%3Ctext x=%2250%25%22 y=%2250%25%22 text-anchor=%22middle%22 dy=%22.3em%22 fill=%22%23999%22 font-size=%2212%22%3ENo Image%3C/text%3E%3C/svg%3E';
                    const serial = device.device_info?.SerialNumber || `Device-${filteredIndex}`;
                    const deviceKey = `device-${filteredIndex}`;
                    
                    // Create HTML: ALL ON SAME LINE (LEFT TO RIGHT)
                    // ☑ Device IP: 192.168.1.110  [📷 Snapshot]  [Dropdown ▼]
                    const deviceItem = document.createElement('div');
                    deviceItem.classList.add('device-item');
                    
                    deviceItem.innerHTML = `
                        <div style="display: flex; align-items: center; gap: 10px; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--gray-100); flex-wrap: nowrap;">
                            
                            <!-- CHECKBOX (LEFT) -->
                            <input type="checkbox" 
                                   class="device-checkbox" 
                                   value="${ip}" 
                                   data-serial="${serial}"
                                   data-index="${filteredIndex}"
                                   data-original-index="${originalIndex}"
                                   onchange="updateDeviceSelection()"
                                   style="width: 18px; height: 18px; cursor: pointer; flex-shrink: 0; accent-color: var(--primary);">
                            
                            <!-- DEVICE IP TEXT -->
                            <span style="font-size: 13px; color: var(--gray-900); flex-shrink: 0; white-space: nowrap;">
                                <strong>Device IP:</strong> ${ip}
                            </span>
                            
                            <!-- SNAPSHOT IMAGE -->
                            ${snapshot ? `<img src="${snapshot}" 
                                 alt="Snapshot" 
                                 style="width: 70px; height: 70px; object-fit: cover; border-radius: 6px; flex-shrink: 0;">` : 
                                 '<div style="width: 70px; height: 70px; display: flex; align-items: center; justify-content: center; background: #f0f0f0; border-radius: 6px; flex-shrink: 0; color: #999; font-size: 10px;">No Image</div>'}
                            
                            <!-- ANALYTICS CHECKBOXES (RIGHT) - MULTIPLE SELECT -->
                            <div id="analytics-device-${filteredIndex}" 
                                 class="device-analytics-container"
                                 data-index="${filteredIndex}"
                                 style="display: flex; flex-direction: column; gap: 4px; padding: 6px; border: 1px solid var(--gray-300); border-radius: 4px; background-color: white; flex-shrink: 0; min-width: 200px; max-height: 150px; overflow-y: auto; margin-left: auto;">
                                <div style="font-size: 10px; color: var(--gray-600); font-weight: 600; margin-bottom: 4px; padding-bottom: 4px; border-bottom: 1px solid var(--gray-200);">
                                    Select Cameras:
                                </div>
                            </div>
                        </div>
                    `;

                    // Append to scroll container instead
                    const scrollContainer = document.getElementById('devices-scroll-container');
                    if (scrollContainer) {
                        scrollContainer.appendChild(deviceItem);
                    } else {
                        deviceListDiv.appendChild(deviceItem);
                    }
                    
                    // Populate camera dropdown for this device
                    populateAnalyticsDropdown(`analytics-device-${filteredIndex}`);
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
                
                const response = await fetch(`${(window.PGAK_CONFIG && window.PGAK_CONFIG.EXTERNAL_USER_PURCHASES) || 'https://api.pgak.co.in/analytics/user-purchases2'}?user_id=${userId}`);
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
                cameraIds.forEach((cameraId) => {
                    const analyticsMatch = cameraId.match(/^(.*?)\s*\(\d+\s*Cameras?\)$/);
                    if (analyticsMatch) {
                        const analyticsName = analyticsMatch[1].trim();
                        if (!extractedAnalytics[analyticsName]) {
                            extractedAnalytics[analyticsName] = [];
                        }
                        extractedAnalytics[analyticsName].push(cameraId);
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
                const container = document.getElementById(`analytics-device-${deviceIndex}`);
                
                const cameraCheckboxes = container ? Array.from(container.querySelectorAll('.camera-checkbox:checked')) : [];
                
                // ✅ GET THE ORIGINAL DEVICE OBJECT
                const originalDevice = window.discoveredDevices ? window.discoveredDevices[deviceIndex] : null;
                
                const cameras = cameraCheckboxes.map(cameraCb => {
                    let rtspUrl = null;
                    
                    // ✅ SEARCH FOR RTSP URL IN ORIGINAL DEVICE
                    if (originalDevice) {
                        // Search in rtsp_profiles
                        if (originalDevice.rtsp_profiles && Array.isArray(originalDevice.rtsp_profiles)) {
                            const profile = originalDevice.rtsp_profiles.find(p => 
                                p.name === cameraCb.dataset.cameraName || 
                                p.rtsp_url?.includes(cameraCb.dataset.cameraName)
                            );
                            if (profile) rtspUrl = profile.rtsp_url;
                        }
                        
                        // Search in cameras
                        if (!rtspUrl && originalDevice.cameras && Array.isArray(originalDevice.cameras)) {
                            const camera = originalDevice.cameras.find(c => 
                                c.name === cameraCb.dataset.cameraName || 
                                c.camera_name === cameraCb.dataset.cameraName
                            );
                            if (camera) rtspUrl = camera.rtsp_url || camera.rtsp;
                        }
                    }
                    
                    return {
                        analyticsType: cameraCb.dataset.analyticsType,
                        cameraName: cameraCb.dataset.cameraName,
                        rtsp_url: rtspUrl,  // ✅ NOW INCLUDES RTSP URL!
                        rtsp: rtspUrl
                    };
                });
                
                return {
                    ip: deviceCb.value,
                    serial: deviceCb.dataset.serial,
                    deviceIndex: deviceIndex,
                    cameras: cameras
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

            const payload = {
                user_id: (window.issueState && issueState.currentUserId) ? issueState.currentUserId : null,
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
                    full_name: 'Distributor',
                    email: 'distributor@system.com',
                    user_type: 'distributor'
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
            const fullName = user.full_name || 'Distributor';
            const email = user.email || 'distributor@system.com';

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
                if (window.innerWidth > 768) {
                    closeSidebar();
                }
            });
        }

        // ==================== LOAD DISTRIBUTOR CODE ====================
        async function loadDistributorCode() {
            try {
                const response = await fetch('/api/distributor-code');
                
                if (!response.ok) {
                    throw new Error('Failed to load distributor code');
                }

                const data = await response.json();
                
                let distributorCode = 'N/A';
                
                if (data.users && Array.isArray(data.users) && data.users.length > 0) {
                    const distributorUser = data.users.find(user => user.user_type === 'distributor');
                    if (distributorUser) {
                        distributorCode = distributorUser.distributor_code || distributorUser.code || 'N/A';
                    }
                } else if (data.distributor_code) {
                    distributorCode = data.distributor_code;
                } else if (data.code) {
                    distributorCode = data.code;
                }

                document.getElementById('distributorCode').textContent = distributorCode;
                sessionStorage.setItem('distributorCode', distributorCode);
                
            } catch (error) {
                console.error('Error loading distributor code:', error);
                document.getElementById('distributorCode').textContent = 'Error loading';
            }
        }

        // ==================== COPY DISTRIBUTOR CODE ====================
        function copyDistributorCode() {
            const codeElement = document.getElementById('distributorCode');
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
        }

        function setActiveMenu(element) {
            document.querySelectorAll('.menu-item').forEach(item => {
                item.classList.remove('active');
            });
            element.classList.add('active');
        }

        function showDashboardView() {
            document.getElementById('dashboardView').style.display = 'block';
            document.getElementById('dealersView').style.display = 'none';
            document.getElementById('devicesView').style.display = 'none';
            document.getElementById('issuesView').style.display = 'none';
            document.getElementById('pageTitle').textContent = '📊 Dashboard';
            document.getElementById('pageSubtitle').textContent = 'System Overview';
            
            if (devicesRefreshInterval) {
                clearInterval(devicesRefreshInterval);
                devicesRefreshInterval = null;
            }
        }

        function showDealersView() {
            document.getElementById('dashboardView').style.display = 'none';
            document.getElementById('dealersView').style.display = 'block';
            document.getElementById('devicesView').style.display = 'none';
            document.getElementById('issuesView').style.display = 'none';
            document.getElementById('pageTitle').textContent = '👥 Dealers Management';
            document.getElementById('pageSubtitle').textContent = 'All registered dealers';
            
            loadDealersData();
            
            if (devicesRefreshInterval) {
                clearInterval(devicesRefreshInterval);
                devicesRefreshInterval = null;
            }
        }

        function showDevicesView() {
            document.getElementById('dashboardView').style.display = 'none';
            document.getElementById('dealersView').style.display = 'none';
            document.getElementById('devicesView').style.display = 'block';
            document.getElementById('issuesView').style.display = 'none';
            document.getElementById('pageTitle').textContent = '🖥 Devices Management';
            document.getElementById('pageSubtitle').textContent = 'All registered devices';
            
            loadDevicesData();
            
            if (devicesRefreshInterval) {
                clearInterval(devicesRefreshInterval);
            }
            devicesRefreshInterval = setInterval(loadDevicesData, 5000);
        }

        // NEW: Show Issues View within Devices Management
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
            showIssuesView();
        }

        function showIssuesView() {
            document.getElementById('dashboardView').style.display = 'none';
            document.getElementById('dealersView').style.display = 'none';
            document.getElementById('devicesView').style.display = 'none';
            document.getElementById('issuesView').style.display = 'block';
            document.getElementById('pageTitle').textContent = '⚙️ Issue Device';
            document.getElementById('pageSubtitle').textContent = 'Manage issues and track resolution';
            
            if (devicesRefreshInterval) {
                clearInterval(devicesRefreshInterval);
                devicesRefreshInterval = null;
            }

            goToIssueType();
        }

        // NEW: Go back to devices view from issues
        function goBackToDevicesView() {
            document.getElementById('dashboardView').style.display = 'none';
            document.getElementById('dealersView').style.display = 'none';
            document.getElementById('devicesView').style.display = 'block';
            document.getElementById('issuesView').style.display = 'none';
            document.getElementById('pageTitle').textContent = '🖥 Devices Management';
            document.getElementById('pageSubtitle').textContent = 'All registered devices';
            
            loadDevicesData();
            
            if (devicesRefreshInterval) {
                clearInterval(devicesRefreshInterval);
            }
            devicesRefreshInterval = setInterval(loadDevicesData, 5000);
        }

        function selectIssueType(type) {
            issueState.currentType = type;
            issueState.currentUserType = type;

            const typeLabel = type === 'dealer' ? 'Dealer' : 'Customer';
            document.getElementById('userListTitle').textContent = `Select ${typeLabel} for Issue:`;
            document.getElementById('issueTypeSelection').style.display = 'none';
            document.getElementById('userListContainer').style.display = 'block';

            loadUsersByType(type);
        }

        function goToIssueType() {
            document.getElementById('issueTypeSelection').style.display = 'block';
            document.getElementById('userListContainer').style.display = 'none';
            
            issueState.currentType = null;
            issueState.currentUserType = null;
            issueState.currentUserId = null;
        }

        function goBackFromUserList() {
            goToIssueType();
        }

        // ===== DEALERS TABLE - COLUMN FILTERS =====
        function applyDealerColumnFilters() {
            const fullNameFilter = document.getElementById('searchFullName').value.toLowerCase().trim();
            const addressFilter = document.getElementById('searchAddress').value.toLowerCase().trim();
            const emailFilter = document.getElementById('searchEmail').value.toLowerCase().trim();
            const phoneFilter = document.getElementById('searchPhoneNo').value.toLowerCase().trim();
            const companyFilter = document.getElementById('searchCompany').value.toLowerCase().trim();
            const codeFilter = document.getElementById('searchCode').value.toLowerCase().trim();
            const statusFilter = document.getElementById('searchStatus').value.toLowerCase().trim();

            const dealersTable = document.getElementById('dealersTable');
            
            // ✅ Filter data
            let filteredDealers = allDealersDataForFilter.filter(dealer => {
                const matchFullName = !fullNameFilter || (dealer.full_name || '').toLowerCase().includes(fullNameFilter);
                const matchAddress = !addressFilter || (dealer.address || '').toLowerCase().includes(addressFilter);
                const matchEmail = !emailFilter || (dealer.email || '').toLowerCase().includes(emailFilter);
                const matchPhone = !phoneFilter || (dealer.phone_number || '').toLowerCase().includes(phoneFilter);
                const matchCompany = !companyFilter || (dealer.company_name || '').toLowerCase().includes(companyFilter);
                const matchCode = !codeFilter || (dealer.distributor_code || '').toLowerCase().includes(codeFilter);
                const matchStatus = !statusFilter || (dealer.status || '').toLowerCase().includes(statusFilter);

                return matchFullName && matchAddress && matchEmail && matchPhone && matchCompany && matchCode && matchStatus;
            });

            dealersTable.innerHTML = '';

            if (filteredDealers.length === 0) {
                dealersTable.innerHTML = `
                    <tr>
                        <td colspan="7" style="text-align: center; padding: 40px; color: var(--gray-500);">
                            No matching dealers found
                        </td>
                    </tr>
                `;
                return;
            }

            filteredDealers.forEach(dealer => {
                const statusColor = dealer.status === 'Approved' ? '#10B981' : dealer.status === 'Pending' ? '#F59E0B' : '#EF4444';
                const statusBg = dealer.status === 'Approved' ? 'rgba(16, 185, 129, 0.1)' : dealer.status === 'Pending' ? 'rgba(245, 158, 11, 0.1)' : 'rgba(239, 68, 68, 0.1)';
                
                const dealerId = 'dealer_details_' + dealer.user_id;
                
                // Check if mobile view
                if (isMobileView) {
                    // ===== MOBILE VIEW FOR DEALERS: Show only Name, Status + CLICKABLE ROW WITH ARROW =====
                    const row = document.createElement('tr');
                    row.style.cursor = 'pointer';
                    
                    row.innerHTML = `
                        <td data-label="Full Name">${escapeHtml(dealer.full_name)}</td>
                        <td data-label="Status">
                            <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                                <span class="status-dot" style="background: ${statusColor};"></span>
                                ${dealer.status}
                            </span>
                        </td>
                        
                    `;
                    
                    // ✅ Add click handler to row to open mobile detail page
                    row.addEventListener('click', function(e) {
                        e.stopPropagation();
                        openDealerDetailPage(dealer.user_id || dealer.id, dealer.full_name);
                    });
                    
                    dealersTable.appendChild(row);
                } else {
                    // ===== DESKTOP VIEW: FULL TABLE WITH ALL COLUMNS =====
                    const row = document.createElement('tr');
                    row.style.cursor = 'pointer';
                    
                    row.innerHTML = `
                        <td data-label="Full Name" style="font-weight: 600;">${escapeHtml(dealer.full_name)}</td>
                        <td data-label="Address">${escapeHtml(dealer.address)}</td>
                        <td data-label="Email">${escapeHtml(dealer.email)}</td>
                        <td data-label="Phone No">${escapeHtml(dealer.phone_number || 'N/A')}</td>
                        <td data-label="Company">${escapeHtml(dealer.company_name || 'N/A')}</td>
                        <td data-label="Code">${escapeHtml(dealer.distributor_code || 'N/A')}</td>
                        <td data-label="Status">
                            <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                                <span class="status-dot" style="background: ${statusColor};"></span>
                                ${dealer.status}
                            </span>
                        </td>
                        <td data-label="Details">
                            <button class="details-btn" onclick="event.stopPropagation(); toggleDealerDetails('${dealerId}')" title="View details">
                                View Details
                            </button>
                            <div id="${dealerId}" class="details-container" style="display: none;">
                                <div class="details-grid">
                                    <div class="details-item"><strong>Full Name:</strong> <code>${escapeHtml(dealer.full_name)}</code></div>
                                    <div class="details-item"><strong>Address:</strong> <code>${escapeHtml(dealer.address)}</code></div>
                                    <div class="details-item"><strong>Email:</strong> <code>${escapeHtml(dealer.email)}</code></div>
                                    <div class="details-item"><strong>Phone:</strong> <code>${escapeHtml(dealer.phone_number || 'N/A')}</code></div>
                                    <div class="details-item"><strong>Company:</strong> <code>${escapeHtml(dealer.company_name || 'N/A')}</code></div>
                                    <div class="details-item"><strong>Status:</strong> <code>${escapeHtml(dealer.status)}</code></div>
                                    <div class="details-item"><strong>GST:</strong> <code>${escapeHtml(dealer.gst_no || 'N/A')}</code></div>
                                    <div class="details-item"><strong>Pincode:</strong> <code>${escapeHtml(dealer.pincode || 'N/A')}</code></div>
                                    <div class="details-item"><strong>Code:</strong> <code>${escapeHtml(dealer.distributor_code || 'N/A')}</code></div>
                                </div>
                            </div>
                        </td>
                    `;
                    dealersTable.appendChild(row);
                }
            });
        }

        function clearDealerColumnFilters() {
            document.getElementById('searchFullName').value = '';
            document.getElementById('searchAddress').value = '';
            document.getElementById('searchEmail').value = '';
            document.getElementById('searchPhoneNo').value = '';
            document.getElementById('searchCompany').value = '';
            document.getElementById('searchCode').value = '';
            document.getElementById('searchStatus').value = '';
            
            applyDealerColumnFilters();
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
            let filteredUsers = allDealersDataForFilter.filter(user => {
                const matchFullName = !fullNameFilter || (user.full_name || '').toLowerCase().includes(fullNameFilter);
                const matchAddress = !addressFilter || (user.address || '').toLowerCase().includes(addressFilter);
                const matchEmail = !emailFilter || (user.email || '').toLowerCase().includes(emailFilter);
                const matchPhone = !phoneFilter || (user.phone_number || '').toLowerCase().includes(phoneFilter);
                const matchCompany = !companyFilter || (user.company_name || '').toLowerCase().includes(companyFilter);
                const matchCode = !codeFilter || (user.code || user.distributor_code || '').toLowerCase().includes(codeFilter);
                const matchStatus = !statusFilter || (user.status || '').toLowerCase().includes(statusFilter);

                return matchFullName && matchAddress && matchEmail && matchPhone && matchCompany && matchCode && matchStatus;
            });

            issueUserTable.innerHTML = '';

            if (filteredUsers.length === 0) {
                issueUserTable.innerHTML = `
                    <tr>
                        <td colspan="7" style="text-align: center; padding: 40px; color: var(--gray-500);">
                            No matching users found
                        </td>
                    </tr>
                `;
                return;
            }

            filteredUsers.forEach((user) => {
                const row = document.createElement('tr');
                const statusColor = user.status === 'Approved' ? '#10B981' : user.status === 'Pending' ? '#F59E0B' : '#EF4444';
                const statusBg = user.status === 'Approved' ? 'rgba(16, 185, 129, 0.1)' : user.status === 'Pending' ? 'rgba(245, 158, 11, 0.1)' : 'rgba(239, 68, 68, 0.1)';

                const buttonLabel = 'Select Dealer';
                
                // Check if mobile view
                if (isMobileView) {
                    // Mobile view - clickable card layout
                    row.setAttribute('onclick', `selectUserForIssue('${user.id}', '${escapeHtml(user.full_name)}', 'dealer' , '${user.status}')`);
                    row.style.cursor = 'pointer';
                    
                    row.innerHTML = `
                        <td data-label="Full Name" style="font-weight: 600;">${escapeHtml(user.full_name)}</td>
                        <td data-label="Status">
                            <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                                <span class="status-dot" style="background: ${statusColor};"></span>
                                ${user.status}
                            </span>
                        </td>
                    `;
                } else {
                    // Desktop view
                    row.innerHTML = `
                        <td data-label="Full Name" style="font-weight: 600;">${escapeHtml(user.full_name)}</td>
                        <td data-label="Address">${escapeHtml(user.address)}</td>
                        <td data-label="Email">${escapeHtml(user.email)}</td>
                        <td data-label="Phone No">${escapeHtml(user.phone_number || 'N/A')}</td>
                        <td data-label="Company">${escapeHtml(user.company_name || 'N/A')}</td>
                        <td data-label="ID/Code">${escapeHtml(String(user.code || user.id || user.distributor_code))}</td>
                        <td data-label="Status">
                            <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                                <span class="status-dot" style="background: ${statusColor};"></span>
                                ${user.status}
                            </span>
                        </td>
                        <td data-label="Action">
                            <button class="details-btn" onclick="selectUserForIssue('${user.id}', '${escapeHtml(user.full_name)}', 'dealer', '${user.status}')">
                                ${buttonLabel}
                            </button>
                        </td>
                    `;
                }
                issueUserTable.appendChild(row);
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

        // ===== UPDATE loadDealersData FUNCTION =====
        async function loadDealersData() {
            try {
                const response = await fetch('/distributor/dealers');
                const data = await response.json();
                const dealersTable = document.getElementById('dealersTable');
                dealersTable.innerHTML = '';

                if (data.status !== 'success' || !data.dealers || data.dealers.length === 0) {
                    dealersTable.innerHTML = `
                        <tr>
                            <td colspan="7" style="text-align: center; padding: 40px; color: var(--gray-500);">
                                No dealers found
                            </td>
                        </tr>
                    `;
                    return;
                }

                // ✅ Store data for filtering
                allDealersDataForFilter = data.dealers.map(d => ({
                    ...d,
                    id: d.user_id,
                    code: d.distributor_code
                }));

                // ✅ Clear filters
                clearDealerColumnFilters();
                
                // ✅ Apply filters and render
                applyDealerColumnFilters();
                
            } catch (error) {
                console.error('Error loading dealers:', error);
            }
        }

        // ===== UPDATE loadUsersByType FUNCTION =====
        async function loadUsersByType(userType) {
            try {
                const token = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJmcmVzaCI6ZmFsc2UsImlhdCI6MTc3MDYzMjc1NiwianRpIjoiMzRkNmM1ODUtODkxMS00ZTE4LWFmZWEtOTdiZWJhYTQ2N2RiIiwidHlwZSI6ImFjY2VzcyIsInN1YiI6IjE0NCIsIm5iZiI6MTc3MDYzMjc1NiwiZXhwIjoxNzcwNjM2MzU2fQ.jjRC4qdBIrPlWB5xACAMGOVJrxcsYCnldhY49sDcBxo';
                const endpoint = userType === 'dealer' ? '/distributor/dealers' : ((window.PGAK_CONFIG && window.PGAK_CONFIG.EXTERNAL_DEALER_CUSTOMERS) || 'https://api.pgak.co.in/auth/dealer/customers');
                
                const fetchOptions = {
                    method: 'GET',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                };

                if (userType === 'customer') {
                    fetchOptions.headers['Authorization'] = `Bearer ${token}`;
                }

                const response = await fetch(endpoint, fetchOptions);
                
                let filteredUsers = [];
                let userData;

                if (userType === 'dealer') {
                    userData = await response.json();
                    if (userData.dealers && Array.isArray(userData.dealers)) {
                        filteredUsers = userData.dealers.map((dealer, index) => ({
                            id: dealer.user_id || dealer.id || index,
                            full_name: dealer.full_name,
                            address: dealer.address,
                            email: dealer.email,
                            company_name: dealer.company_name,
                            code: dealer.distributor_code,
                            status: dealer.status,
                            user_type: 'dealer'
                        }));
                    }
                } else {
                    userData = await response.json();
                    // ✅ अब userData.customers directly से निकालता है
                    if (userData.customers && Array.isArray(userData.customers)) {
                        filteredUsers = userData.customers.map((customer, index) => ({
                            // ✅ सभी field combinations को handle करता है
                            id: customer.user_id || customer.id || customer.customer_id || index,
                            user_id: customer.user_id || customer.id || customer.customer_id || index,
                            full_name: customer.full_name || customer.name || 'N/A',
                            name: customer.full_name || customer.name || 'N/A',
                            address: customer.address || 'N/A',
                            email: customer.email || 'N/A',
                            phone_no: customer.phone_no || customer.phone || 'N/A',
                            company_name: customer.company_name || customer.company || 'N/A',
                            code: customer.customer_id || customer.user_id || customer.id || index,
                            status: customer.status || 'Approved',
                            user_type: 'customer'
                        }));
                    }
                }

                const issueUserTable = document.getElementById('issueUserTable');
                issueUserTable.innerHTML = '';

                if (filteredUsers.length === 0) {
                    issueUserTable.innerHTML = `
                        <tr>
                            <td colspan="6" style="text-align: center; padding: 40px; color: var(--gray-500);">
                                No ${userType}s found
                            </td>
                        </tr>
                    `;
                    return;
                }

                filteredUsers.forEach((user) => {
                    const row = document.createElement('tr');
                    const statusColor = user.status === 'Approved' ? '#10B981' : user.status === 'Pending' ? '#F59E0B' : '#EF4444';
                    const statusBg = user.status === 'Approved' ? 'rgba(16, 185, 129, 0.1)' : user.status === 'Pending' ? 'rgba(245, 158, 11, 0.1)' : 'rgba(239, 68, 68, 0.1)';

                    row.innerHTML = `
                        <td data-label="Full Name" style="font-weight: 600;">${escapeHtml(user.full_name)}</td>
                        <td data-label="Address">${escapeHtml(user.address)}</td>
                        <td data-label="Email">${escapeHtml(user.email)}</td>
                        <td data-label="Phone No">${escapeHtml(user.phone_no || 'N/A')}</td>
                        <td data-label="Company">${escapeHtml(user.company_name)}</td>
                        <td data-label="ID / Code"><code style="background: var(--gray-100); padding: 4px 6px; border-radius: 4px; font-size: 12px;">${escapeHtml(String(user.code || user.id))}</code></td>
                        <td data-label="Status">
                            <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                                <span class="status-dot" style="background: ${statusColor};"></span>
                                ${user.status}
                            </span>
                        </td>
                        <td data-label="Action">
                            <button class="details-btn" onclick="selectUserForIssue('${user.id}', '${escapeHtml(user.full_name)}', '${userType}', '${user.status}')">
                                Select
                            </button>
                        </td>
                    `;
                    issueUserTable.appendChild(row);
                });
            } catch (error) {
                console.error('Error loading users:', error);
                document.getElementById('issueUserTable').innerHTML = `
                    <tr>
                        <td colspan="6" style="text-align: center; padding: 40px; color: var(--danger);">
                            Error loading users
                        </td>
                    </tr>
                `;
            }
        }

        function selectUserForIssue(userId, userName, userType, userStatus) {
            // Trim and normalize the status
            const normalizedStatus = userStatus ? userStatus.trim() : '';
            
            // Check if user status is Approved
            if (normalizedStatus !== 'Approved') {
                showNotification('error', `❌ Cannot scan! User status is "${normalizedStatus}". Only "Approved" users can scan devices.`);
                return;
            }
            
            issueState.currentUserId = userId;
            issueState.currentUserType = userType;
            issueState.currentUserData = { full_name: userName, name: userName, user_type: userType };
        
            console.log("✅ Selected for issue:", { userId, userType, userName, status: normalizedStatus });
        
            openQRScanner();
            startScanning();
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
                hostname: (row && row.hostname) ? String(row.hostname) : ''
            };
        }

        function normalizeDevicesPayload(payload) {
            // DB API shape: {status:'success', devices:[...]}
            if (payload && Array.isArray(payload.devices)) {
                return payload.devices.map(r => {
                    const host = (r.hostname && String(r.hostname).trim()) || (r.ip_address && String(r.ip_address).trim()) || (r.id ? ('Device-' + r.id) : 'Device');
                    return [host, normalizeDbDeviceForUI(r)];
                });
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
                // ✅ Get distributor_id from current-user API
                let distributorId = window._distributorId || null;
                if (!distributorId) {
                    try {
                        const uRes = await fetch('/api/current-user', { credentials: 'include' });
                        if (uRes.ok) {
                            const ud = await uRes.json();
                            distributorId = ud.id || ud.user_id || null;
                            window._distributorId = distributorId;
                        }
                    } catch(e) { console.warn('Could not fetch distributor ID:', e); }
                }

                // ✅ Distributor-specific devices URL
                const devicesUrl = distributorId
                    ? `/distributor/devices?distributor_id=${encodeURIComponent(distributorId)}&filter=all`
                    : '/devices_status?filter=all';

                const [dealersRes, devicesRes] = await Promise.all([
                    fetch('/distributor/dealers').then(r => r.json()).catch(() => ({})),
                    fetch(devicesUrl, { credentials: 'include' }).then(r => r.json()).catch(() => ({devices: []}))
                ]);
                
                let dealers = 0, approved = 0, pending = 0;
                if (dealersRes.status === 'success' && dealersRes.dealers) {
                    dealers = dealersRes.dealers.length;
                    dealersRes.dealers.forEach(d => {
                        if (d.status === 'Approved') approved++;
                        if (d.status === 'Pending') pending++;
                    });
                }
                
                const deviceEntries = normalizeDevicesPayload(devicesRes);
                let onlineCount = 0, offlineCount = 0;
                for (const [key, val] of deviceEntries) {
                    const st = (val && val.status) ? String(val.status).toUpperCase() : '';
                    if (st === 'ACTIVE' || st === 'ONLINE') onlineCount++;
                    else offlineCount++;
                }

                document.getElementById('totalDealers').textContent = dealers;
                document.getElementById('approvedDealers').textContent = approved;
                document.getElementById('pendingDealers').textContent = pending;
                document.getElementById('totalDevices').textContent = deviceEntries.length;
                document.getElementById('onlineDevices').textContent = onlineCount;
                document.getElementById('offlineDevices').textContent = offlineCount;
            } catch (error) {
                console.error('Error loading dashboard data:', error);
            }
        }
        function toggleDealerDetails(dealerId) {
            const el = document.getElementById(dealerId);
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

                // ✅ Get distributor_id from cached value or current-user API
                let distributorId = window._distributorId || null;
                if (!distributorId) {
                    try {
                        const uRes = await fetch('/api/current-user', { credentials: 'include' });
                        if (uRes.ok) {
                            const ud = await uRes.json();
                            distributorId = ud.id || ud.user_id || null;
                            window._distributorId = distributorId;
                        }
                    } catch (e) { console.warn('Could not fetch distributor ID:', e); }
                }

                let data = null;
                try {
                    // ✅ Distributor-specific API: sirf is distributor ke issued devices dikhao
                    const url = distributorId
                        ? `/distributor/devices?distributor_id=${encodeURIComponent(distributorId)}&filter=${encodeURIComponent(filter)}`
                        : `/devices_status?filter=${encodeURIComponent(filter)}`;
                    const resp = await fetch(url, { credentials: 'include' });
                    data = await resp.json();
                } catch (e) {
                    data = null;
                }

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
                            <td data-label="Hostname" style="font-weight: 600; color: var(--gray-900);">${escapeHtml(host)}</td>
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
                            <td data-label="Hostname" style="font-weight: 600; color: var(--gray-900);">${escapeHtml(host)}</td>
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

        // ✅ Desktop function for toggling device details
        function toggleDeviceDetails(detailsId, rowElement) {
            const container = document.getElementById(detailsId);
            if (!container) return;
            const isVisible = container.style.display !== 'none';
            container.style.display = isVisible ? 'none' : 'block';
            
            // Update the button text in the same row
            const button = rowElement.querySelector('.details-btn');
            if (button) {
                button.textContent = isVisible ? 'Show Details' : 'Hide Details';
            }
            
            const savedOpenDetails = JSON.parse(localStorage.getItem('deviceDetailsOpen') || '{}');
            savedOpenDetails[detailsId] = !isVisible;
            localStorage.setItem('deviceDetailsOpen', JSON.stringify(savedOpenDetails));
        }
        
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
                                if (label && addr) {
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

            // ✅ CHECK IF USER IS SELECTED FIRST
            if (!issueState.currentUserId) {
                showErrorMessage(
                    "❌ No User Selected",
                    "Please select a dealer or distributor BEFORE scanning the QR code."
                );
                qrScanInProgress = false;
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

                const saveResponse = await fetch('/api/devices/save-from-qr-v2', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        serial_number: serialNumber,
                        user_id: parseInt(userId),
                        user_type: userType.toLowerCase(),
                        qr_data: serialNumber
                    })
                });

                console.log("✅ Save API Response Status:", saveResponse.status);
                const saveData = await saveResponse.json();
                console.log("📋 Save Response Data:", saveData);

                // ✅ CHECK SAVE RESPONSE
                if (!saveResponse.ok || !saveData.success) {
                    console.log("❌ Save failed!");
                    console.log("Error Details:", saveData);
                
                    // Check for duplicate - case insensitive for error_code
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
                const userName = issueState.currentUserData?.full_name || `User ID: ${userId}`;
                const userIdDisplay = userType.toLowerCase() === 'customer' ? `Customer ID: ${userId}` : `${typeLabel} ID: ${userId}`;
                
                showSuccessMessage(
                    `✅ ISSUED TO ${typeLabel} SUCCESSFULLY`,
                    `User: ${userName}`,
                    userType.toLowerCase() === 'customer'  // Only show device discovery for customers
                );

            } catch (error) {
                console.error('❌ Exception in validateAndSaveDevice:', error);
                showErrorMessage(
                    "❌ Error Processing Device",
                    'An unexpected error occurred. Please try again.'
                );
            }
        }

        // CLEAR STATUS MESSAGE
        function clearStatusMessage() {
            const messageDiv = document.getElementById('issueStatusMessage');
            if (messageDiv) {
                messageDiv.style.display = 'none';
                messageDiv.innerHTML = '';
            }
        }
        
        
        // ✅ SHOW SUCCESS MESSAGE WITH ALERT
        function showSuccessMessage(title, details, showDiscovery = false) {
            console.log("🎉 Showing success message:", title);
            
            // DISPLAY IN POPUP - NO ALERT
            const messageDiv = document.getElementById('issueStatusMessage');
            if (messageDiv) {
                messageDiv.innerHTML = `
                    <div style="display: flex; gap: 12px; align-items: flex-start;">
                        <i class="fas fa-check-circle" style="color: var(--success); font-size: 20px; flex-shrink: 0; margin-top: 2px;"></i>
                        <div style="flex: 1;">
                            <div style="font-weight: 600; color: var(--success); margin-bottom: 4px;">${title}</div>
                            <div style="font-size: 13px; color: var(--gray-600); white-space: pre-wrap;">${details}</div>
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
        
            // ✅ SHOW NETWORK DISCOVERY BUTTON ONLY FOR CUSTOMERS
            if (showDiscovery) {
                nextStepContainer.style.display = 'block';
                nextStepContainer.classList.add("show");
                console.log("✅ Device Discovery shown for CUSTOMER");
            } else {
                nextStepContainer.style.display = 'none';
                nextStepContainer.classList.remove("show");
                console.log("❌ Device Discovery hidden for DEALER/DISTRIBUTOR");
            }
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


        // Helper function to show validation message
        function showValidationMessage(icon, text, type) {
            validationIcon.textContent = icon;
            validationText.textContent = text;
            validationMessage.className = `validation-message show ${type}`;
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
