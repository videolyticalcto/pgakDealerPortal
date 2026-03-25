        // ── Approve / Reject with disable-on-click ──
        async function handleApproveReject(btn, userId, action) {
            if (btn.disabled) return;
            btn.disabled = true;
            const origText = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ' + (action === 'approve' ? 'Approving...' : 'Rejecting...');

            // Disable the sibling button too
            const siblingBtn = btn.closest('div').querySelector('button:not([disabled])');
            if (siblingBtn && siblingBtn !== btn) siblingBtn.disabled = true;

            try {
                const resp = await fetch(`/${action}/${userId}`, { method: 'POST', credentials: 'include', headers: { 'Accept': 'application/json' } });
                const data = await resp.json().catch(() => ({}));
                if (resp.ok && data.status === 'success') {
                    showNotification('success', data.message || (action === 'approve' ? '✓ User approved' : '✕ User rejected'));
                    setTimeout(() => {
                        loadUsersData().then(() => { renderUsers(currentFilter || 'pending'); });
                        loadDashboardData();
                    }, 500);
                } else {
                    showNotification('error', data.message || 'Action failed');
                    btn.disabled = false;
                    btn.innerHTML = origText;
                    if (siblingBtn && siblingBtn !== btn) siblingBtn.disabled = false;
                }
            } catch (e) {
                showNotification('error', 'Network error: ' + e.message);
                btn.disabled = false;
                btn.innerHTML = origText;
                if (siblingBtn && siblingBtn !== btn) siblingBtn.disabled = false;
            }
        }

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
        const nextStepContainer = document.getElementById("nextStepContainer"); // Will be null since removed
        let stream = null;
        let scanning = false;
        let rafId = null;
        let jsQRReady = false;
        let qrScanInProgress = false;
        let scanSuccessful = false;
        let discoveredDevices = [];
        let selectedOnvifSerial = null;
        let allUsersData = {};
        let currentFilter = 'pending';
        
        let currentEditingUserId = null;
        let currentDeletingUserId = null;
        let devicesRefreshInterval = null;
        
        let currentPrintingDevice = null; // Store device for printing
        let showPrintQR = true;


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

            var currentPath = window.location.pathname;
            if (currentPath.indexOf('/users-page') !== -1) {
                loadUsersData().then(() => {
                    currentFilter = 'all';
                    renderUsers('all');
                    updateFilterButtonStates();
                    updateCreateButtonVisibility('all');
                });
            } else if (currentPath.indexOf('/devices-page') !== -1) {
                loadDevicesData();
            } else if (currentPath.indexOf('/discovery-page') !== -1) {
                // discovery page init - no data loader needed
            } else {
                loadDashboardData();
            }

            setupFormHandlers();
            setupMobileDetailBackButton();
            setupResponsiveTable();
        });
        
        // ✅ SETUP RESPONSIVE TABLE BEHAVIOR
        function setupResponsiveTable() {
            window.addEventListener('resize', handleResponsiveTables);
            handleResponsiveTables();
        }
        
        function handleResponsiveTables() {
            if (window.innerWidth <= 768) {
                setupMobileTables();
            } else {
                setupDesktopTables();
            }
        }
        
        function setupMobileTables() {
            // Device rows already handled by CSS
        }
        
        function setupDesktopTables() {
            // Reset to normal table layout
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
        // ===========================
        // MERGED NETWORK SCAN HANDLER
        // ===========================

        // Keep your existing globals (these are assumed already in your codebase)
        // let discoveredDevices = []; // stores discovered devices globally

        // If you have these elements in your UI, keep them; otherwise it's fine
        // (they exist in your old code)
        const scanBtn = document.getElementById('scanBtn'); // modal scan button OR main button

        // OPTIONAL: if your UI also has a status line / count line (current code)
        // you can keep these, but code checks safely if they exist.
        const statusLine = document.getElementById("statusLine");
        const countLine  = document.getElementById("countLine");

        function setStatus(msg) {
        if (statusLine) statusLine.textContent = "Status: " + msg;
        }

        // ==================================================
        // MAIN: Call this function when user clicks Scan
        // ==================================================
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
            scanBtnEl.innerHTML = '<span class="spinner"></span> Scanning Network...';
        }
        setStatus("scanning...");

        if (countLine) {
            countLine.textContent = "Found Devices: 0";
            countLine.classList.remove("ok", "bad");
        }

        try {
            console.log('🔍 Starting network scan with credentials...');

            const res = await fetch(`/api/scan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
            });

            const out = await res.json();
            console.log('Network scan result:', out);

            // ✅ Server response (new): { ok: true, merged_count, merged_devices, devices }
            // Fallback logic:
            const devices = out.merged_devices || out.devices || [];
            const foundCount = Number(out.merged_count ?? out.count ?? devices.length ?? 0);

            if (out.ok === true && foundCount > 0) {
            setStatus("scan completed");
            if (countLine) {
                countLine.textContent = "Found Devices: " + foundCount;
                countLine.classList.add("ok");
            }

            // ✅ keep your original behavior:
            // closeCredentialsModal();
            if (typeof closeCredentialsModal === "function") {
                closeCredentialsModal();
            }

            discoveredDevices = devices;

            // ✅ keep your saveDevicesToAnalyticsAPI call exactly as before
            // (only pass devices array from new response)
            if (typeof saveDevicesToAnalyticsAPI === "function") {
                console.log('📤 Calling saveDevicesToAnalyticsAPI...');
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
                // If analytics API not present, still show modal list
                showDiscoveredDevicesInModal(devices);
            }

            } else {
            // No devices / scan failed
            const msg = out.message || "No devices found";
            setStatus("scan failed");
            if (countLine) {
                countLine.textContent = "Found Devices: " + foundCount;
                countLine.classList.add("bad");
            }
            alert(msg);
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

        // ==================================================
        // SHOW FOUND COUNT LINE (your old modal UI logic)
        // ==================================================
        function showDiscoveredDevicesInModal(devices) {
        
            // Clear existing UI hooks if any
            if (typeof macHighlight !== "undefined") macHighlight.classList.remove("show");
            if (typeof validationMessage !== "undefined") validationMessage.classList.remove("show");
            if (typeof nextStepContainer !== "undefined") nextStepContainer.classList.remove("show");

            // Create or fetch the device list container
            let deviceListDiv = document.getElementById('discovered-devices-list');
            
            if (!deviceListDiv) {
                deviceListDiv = document.createElement('div');
                deviceListDiv.id = 'discovered-devices-list';

                const header = document.querySelector('.qr-modal-header');
                if (header && header.parentNode) {
                    header.parentNode.insertBefore(deviceListDiv, header.nextSibling);
                } else {
                    document.body.appendChild(deviceListDiv); // fallback
                }
            }

            // Clear the previous content
            deviceListDiv.innerHTML = `
                <div style="margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--gray-200);">
                    <p style="font-size: 13px; color: var(--gray-600); margin-bottom: 0; font-weight: 500;">
                        Found ${devices.length} device(s). Click to select:
                    </p>
                </div>
            `;

            // Iterate through the devices and create an entry for each device
            devices.forEach(device => {
                const deviceItem = document.createElement('div');
                deviceItem.classList.add('device-item');
                
                // Create HTML structure for each device
                deviceItem.innerHTML = `
                    <div class="device-info" style="margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--gray-100);">
                        <p><strong>Device IP:</strong> ${device.device_ip}</p>
                        <div>
                            <strong>Snapshot:</strong>
                            <img src="${device.screenshot_path}" alt="Snapshot" style="width: 100px; height: 100px; object-fit: cover; border-radius: 8px;">
                        </div>
                    </div>
                `;

                // Append the device item to the list
                deviceListDiv.appendChild(deviceItem);
            });

            // Your old behavior: open QR scanner after scan (if required)
            if (typeof openQRScanner === "function") {
                openQRScanner();
            }

            // Optionally, add this to the device table if you want to show in the table as well:
            const devicesTableBody = document.getElementById('devicesRows');
            devices.forEach(device => {
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td>${device.device_ip}</td>
                    <td><img src="${device.screenshot_path}" alt="Snapshot" style="width: 100px; height: 100px; object-fit: cover; border-radius: 8px;"></td>
                    <td><button class="details-btn" onclick="viewDeviceDetails(${device.device_ip})">Details</button></td>
                `;
                devicesTableBody.appendChild(row);
            });
        }



            


        // ==================================================
        // HOOK BUTTON CLICK (works for both modal + main UI)
        // ==================================================
        // document.addEventListener("DOMContentLoaded", () => {
        // const btn = document.getElementById("scanBtn");
        // if (btn) {
        //     btn.addEventListener("click", (e) => {
        //     e.preventDefault();
        //     startNetworkScanjjjjjjj();
        //     });
        // }
        // });

        function selectDiscoveredDevice(index) {
            const device = discoveredDevices[index];
            if (!device || !device.device_info?.SerialNumber) {
                alert("Device does not have a valid serial number.");
                return;
            }
            selectedOnvifSerial = device.device_info.SerialNumber.trim();
            macValue.textContent = selectedOnvifSerial;
            macHighlight.classList.add("show");
            if (nextStepContainer) nextStepContainer.classList.add("show");
            showValidationMessage('✅', 'Device selected! Click "Proceed with Device Issuance" to continue.', 'success');
           
            stopScanning();
            qrVideo.style.display = 'none';
           
            const deviceListDiv = document.getElementById('discovered-devices-list');
            if (deviceListDiv) deviceListDiv.remove();
        }
        // ===== MOBILE MENU =====
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
        // ===== MENU ITEMS =====
        function setupMenuItems() {
            // Menu items are now real <a href> links - let them navigate naturally.
        }
        function setActiveMenu(element) {
            document.querySelectorAll('.menu-item').forEach(item => {
                item.classList.remove('active');
            });
            element.classList.add('active');
        }
        // ===== VIEW MANAGEMENT =====
        function showDashboardView() {
            document.getElementById('dashboardView').style.display = 'block';
            document.getElementById('usersView').style.display = 'none';
            document.getElementById('devicesView').style.display = 'none';
            document.getElementById('issuesView').style.display = 'none';
            document.getElementById('pageTitle').textContent = '📊 Dashboard';
            document.getElementById('pageSubtitle').textContent = 'System Overview';
           
            if (devicesRefreshInterval) {
                clearInterval(devicesRefreshInterval);
                devicesRefreshInterval = null;
            }
        }
        function showUsersView() {
            document.getElementById('dashboardView').style.display = 'none';
            document.getElementById('usersView').style.display = 'block';
            document.getElementById('devicesView').style.display = 'none';
            document.getElementById('issuesView').style.display = 'none';
            document.getElementById('pageTitle').textContent = '👥 Users Management';
            document.getElementById('pageSubtitle').textContent = 'All dealers and distributors';
           
            loadUsersData().then(() => {
                currentFilter = 'pending';
                renderUsers('pending');
                updateFilterButtonStates();
                updateCreateButtonVisibility('all');
            });
           
            if (devicesRefreshInterval) {
                clearInterval(devicesRefreshInterval);
                devicesRefreshInterval = null;
            }
        }
        function showDevicesView() {
            document.getElementById('dashboardView').style.display = 'none';
            document.getElementById('usersView').style.display = 'none';
            document.getElementById('devicesView').style.display = 'block';
            document.getElementById('issuesView').style.display = 'none';
            document.getElementById('pageTitle').textContent = '🖥 Device Management';
            document.getElementById('pageSubtitle').textContent = 'All registered devices';
           
            loadDevicesData();
           
            if (devicesRefreshInterval) {
                clearInterval(devicesRefreshInterval);
            }
            devicesRefreshInterval = setInterval(loadDevicesData, 5000);
        }
        function showIssuesView() {
            var _dv = document.getElementById('dashboardView');
            var _uv = document.getElementById('usersView');
            var _devv = document.getElementById('devicesView');
            var _iv = document.getElementById('issuesView');
            if (_dv) _dv.style.display = 'none';
            if (_uv) _uv.style.display = 'none';
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
        
        // ✅ Cache of online device count fetched from API (works on both mobile & desktop)
        let _cachedOnlineDeviceCount = null;

        async function fetchAndCacheOnlineDeviceCount() {
            try {
                const resp = await fetch('/devices_status?filter=online');
                const data = await resp.json();
                const entries = normalizeDevicesPayload(data);
                let count = 0;
                for (const [, d] of entries) {
                    const st = (d && d.status) ? String(d.status).toUpperCase() : '';
                    if (st === 'ACTIVE' || st === 'ONLINE') count++;
                }
                _cachedOnlineDeviceCount = count;
                console.log('Cached online device count:', count);
            } catch (e) {
                console.error('Error fetching online device count:', e);
                _cachedOnlineDeviceCount = null;
            }
        }

        // ✅ FIXED: Works on both mobile and desktop
        // Old version scraped cells[1] from the DOM table, but on mobile each row
        // only has 1 <td> (the whole card), so cells[1] was always undefined → always false.
        // New version uses a text search on the full row OR the API cache.
        function hasOnlineDevices() {
            // Use API cache if available (most reliable)
            if (_cachedOnlineDeviceCount !== null) {
                console.log('Using cached online count:', _cachedOnlineDeviceCount);
                return _cachedOnlineDeviceCount > 0;
            }
            // Fallback: scan all text in devicesRows (works for both 1-td mobile rows and multi-td desktop rows)
            try {
                const devicesRows = document.getElementById('devicesRows');
                if (!devicesRows) { console.warn('devicesRows not found'); return false; }
                const rows = devicesRows.querySelectorAll('tr');
                if (rows.length === 0) { console.warn('No device rows'); return false; }
                for (let row of rows) {
                    if (row.textContent.toUpperCase().includes('ONLINE')) {
                        console.log('Found ONLINE device via DOM fallback');
                        return true;
                    }
                }
                console.warn('No online devices found');
                return false;
            } catch (error) {
                console.error('Error in hasOnlineDevices DOM fallback:', error);
                return false;
            }
        }

        // New function to open Issue Devices from Devices View
        function openIssueDevicesFromDevicesView() {
            // NEW: Check if there are any online devices
            if (!hasOnlineDevices()) {
                showNotification('warning', '⚠️ No online devices available. Device issue is only available when at least one device is online. Please ensure devices are powered on and connected to the network.');
                return;
            }
            
            // Switch to Issues menu (but since it's hidden, we'll show the Issues view directly)
            showIssuesView();
        }

        function goBackToDevicesView() {
            // Hide Issues View
            var _iv = document.getElementById('issuesView');
            if (_iv) _iv.style.display = 'none';

            // Show Devices View
            var _devv = document.getElementById('devicesView');
            if (_devv) _devv.style.display = 'block';

            // Hide other views
            var _dv = document.getElementById('dashboardView');
            var _uv = document.getElementById('usersView');
            if (_dv) _dv.style.display = 'none';
            if (_uv) _uv.style.display = 'none';

            // Update page title
            var _pt = document.getElementById('pageTitle');
            var _ps = document.getElementById('pageSubtitle');
            if (_pt) _pt.textContent = '🖥 Device Management';
            if (_ps) _ps.textContent = 'All registered devices';
            
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
            const typeLabel = (type || '').toLowerCase() === 'dealer' ? 'Dealer' : 'Distributor';
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
        async function loadUsersByType(userType) {
            try {
                const issueUserTable = document.getElementById('issueUserTable');
                const response = await fetch('/admin/users');
                const allUsers = await response.json();
                let filteredUsers = Object.entries(allUsers).filter(([_, user]) => user.user_type === userType);
                issueUserTable.innerHTML = '';
                if (filteredUsers.length === 0) {
                    issueUserTable.innerHTML = `
                        <tr>
                            <td colspan="7" style="text-align: center; padding: 40px; color: var(--gray-500);">
                                No ${userType}s found
                            </td>
                        </tr>
                    `;
                    return;
                }
                filteredUsers.forEach(([userId, user]) => {
                    const row = document.createElement('tr');
                    const codeField = (userType || '').toLowerCase() === 'dealer' ? user.distributor_code : user[userType + '_code'];
                    const statusColor = (user.status || '').toLowerCase() === 'approved' ? '#10B981' : (user.status || '').toLowerCase() === 'pending' ? '#F59E0B' : '#EF4444';
                    const statusBg = (user.status || '').toLowerCase() === 'approved' ? 'rgba(16, 185, 129, 0.1)' : (user.status || '').toLowerCase() === 'pending' ? 'rgba(245, 158, 11, 0.1)' : 'rgba(239, 68, 68, 0.1)';
                    const buttonLabel = (userType || '').toLowerCase() === 'dealer' ? 'Select Dealer' : 'Select Distributor';
                    
                    // Check if mobile view
                    const isMobile = window.innerWidth <= 768;
                    
                    if (isMobile) {
                        // Mobile view - card layout
                        row.setAttribute('onclick', `selectUserForIssue('${userId}', '${escapeHtml(user.full_name)}', '${userType}', '${user.status}')`)
                        row.style.cursor = 'pointer';
                        row.innerHTML = `
                            <td data-label="Full Name" style="font-weight: 600;">${escapeHtml(user.full_name)}</td>
                            <td data-label="Type"><span class="type-badge">${(user.user_type || '').toLowerCase() === 'dealer' ? 'Dealer' : 'Distributor'}</span></td>
                            <td data-label="Status">
                                <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                                    <span class="status-dot" style="background: ${statusColor};"></span>
                                    ${user.status}
                                </span>
                            </td>
                        `;
                    } else {
                        // Desktop view - full table
                        row.innerHTML = `
                            <td data-label="Full Name" style="font-weight: 600;">${escapeHtml(user.full_name)}</td>
                            <td data-label="Address">${escapeHtml(user.address)}</td>
                            <td data-label="Email">${escapeHtml(user.email)}</td>
                            <td data-label="Phone">${escapeHtml(user.phone_number)}</td>
                            <td data-label="Company">${escapeHtml(user.company_name || 'N/A')}</td>
                            <td data-label="Code">${escapeHtml(codeField || 'N/A')}</td>
                            <td data-label="Status">
                                <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                                    <span class="status-dot" style="background: ${statusColor};"></span>
                                    ${user.status}
                                </span>
                            </td>
                            <td data-label="Action">
                                <button class="details-btn" onclick="selectUserForIssue('${userId}', '${escapeHtml(user.full_name)}', '${userType}', '${user.status}')">
                                    ${buttonLabel}
                                </button>
                            </td>
                        `;
                    }
                    issueUserTable.appendChild(row);
                });
            } catch (error) {
                console.error('Error loading users:', error);
                document.getElementById('issueUserTable').innerHTML = `
                    <tr>
                        <td colspan="7" style="text-align: center; padding: 40px; color: var(--danger);">
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
            issueState.currentUserData = { full_name: userName, user_type: userType };
        
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
                const [users, devices] = await Promise.all([
                    fetch('/admin/users').then(r => r.json()).catch(() => ({})),
                    fetch('/devices_status?filter=all').then(r => r.json()).catch(() => ({devices: []}))
                ]);
                let dealers = 0, distributors = 0, pending = 0;
                for (const [key, val] of Object.entries(users)) {
                    if ((val.status || '').toLowerCase() === 'pending') pending++;
                    if ((val.user_type || '').toLowerCase() === 'dealer') dealers++;
                    if ((val.user_type || '').toLowerCase() === 'distributor') distributors++;
                }
                const deviceEntries = normalizeDevicesPayload(devices);
                let onlineCount = 0, offlineCount = 0;
                for (const [key, val] of deviceEntries) {
                    const st = (val && val.status) ? String(val.status).toUpperCase() : '';
                    if (st === 'ACTIVE' || st === 'ONLINE') onlineCount++;
                    else offlineCount++;
                }
                var el;
                el = document.getElementById('totalDealers');
                if (el) el.textContent = dealers;
                el = document.getElementById('totalDistributors');
                if (el) el.textContent = distributors;
                el = document.getElementById('pendingRequests');
                if (el) el.textContent = pending;
                el = document.getElementById('totalDevices');
                if (el) el.textContent = deviceEntries.length;
                el = document.getElementById('onlineDevices');
                if (el) el.textContent = onlineCount;
                el = document.getElementById('offlineDevices');
                if (el) el.textContent = offlineCount;
                el = document.getElementById('usersBadge');
                if (el) el.textContent = pending;
            } catch (error) {
                console.error('Error loading dashboard data:', error);
            }
        }
        async function loadUsersData() {
            try {
                console.log("📥 Loading users data...");
                const response = await fetch('/admin/users');
                allUsersData = await response.json();
                console.log("✅ Users data loaded:", Object.keys(allUsersData).length + " users");
                updateUsersBadge();
                return allUsersData; // ✅ Return data so we can chain .then()
            } catch (error) {
                console.error('❌ Error loading users data:', error);
                allUsersData = {};
                return {};
            }
        }
        // ===== LOAD DATA FUNCTIONS =====
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
                    full_name: 'Administrator',
                    email: 'admin@system.com',
                    user_type: 'admin'
                });
            }
        }
        function displayUserProfile(user) {
            const getInitials = (name) => {
                if (!name) return 'A';
                return name.split(' ')
                    .map(n => n[0])
                    .join('')
                    .toUpperCase()
                    .substring(0, 2);
            };
            const initials = getInitials(user.full_name);
            const fullName = user.full_name || 'Administrator';
            const email = user.email || 'admin@system.com';
            const avatarEl = document.getElementById('userAvatar');
            if (avatarEl) avatarEl.textContent = initials;
            const nameEl = document.getElementById('userName');
            if (nameEl) nameEl.textContent = fullName;
            const emailEl = document.getElementById('userEmail');
            if (emailEl) emailEl.textContent = email;
        }
        async function loadDashboardData() {
            try {
                const [users, devices] = await Promise.all([
                    fetch('/admin/users').then(r => r.json()).catch(() => ({})),
                    fetch('/devices_status?filter=all').then(r => r.json()).catch(() => ({devices: []}))
                ]);
                let dealers = 0, distributors = 0, pending = 0;
                for (const [key, val] of Object.entries(users)) {
                    if ((val.status || '').toLowerCase() === 'pending') pending++;
                    if ((val.user_type || '').toLowerCase() === 'dealer') dealers++;
                    if ((val.user_type || '').toLowerCase() === 'distributor') distributors++;
                }
                const deviceEntries = normalizeDevicesPayload(devices);
                let onlineCount = 0, offlineCount = 0;
                for (const [key, val] of deviceEntries) {
                    const st = (val && val.status) ? String(val.status).toUpperCase() : '';
                    if (st === 'ACTIVE' || st === 'ONLINE') onlineCount++;
                    else offlineCount++;
                }
                var el;
                el = document.getElementById('totalDealers');
                if (el) el.textContent = dealers;
                el = document.getElementById('totalDistributors');
                if (el) el.textContent = distributors;
                el = document.getElementById('pendingRequests');
                if (el) el.textContent = pending;
                el = document.getElementById('totalDevices');
                if (el) el.textContent = deviceEntries.length;
                el = document.getElementById('onlineDevices');
                if (el) el.textContent = onlineCount;
                el = document.getElementById('offlineDevices');
                if (el) el.textContent = offlineCount;
                el = document.getElementById('usersBadge');
                if (el) el.textContent = pending;
            } catch (error) {
                console.error('Error loading dashboard data:', error);
            }
        }

        // ✅ Device cache for mobile detail page
        const deviceCache = {};
        
        async function loadDevicesData() {
            try {
                // ✅ GET view parameter from backend based on current filter
                const filterSel = document.getElementById('deviceStatusFilter');
                const filter = filterSel ? filterSel.value : (localStorage.getItem('deviceStatusFilter') || 'online');

                let data = null;
                try {
                    const resp = await fetch(`/devices_status?filter=${encodeURIComponent(filter)}`);
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
                    const serial = info['Serial Number'] || info['Serial'] || info['serial_number'] || 'N/A';
                    
                    // Check if mobile view
                    const isMobile = window.innerWidth <= 768;
                    
                    if (isMobile) {
                        // Mobile view - simple list with printer on right
                        const row = document.createElement('tr');
                        row.style.display = 'block';
                        row.style.padding = '12px 16px';
                        row.style.marginBottom = '12px';
                        row.style.borderRadius = '8px';
                        row.style.border = '1px solid var(--gray-200)';
                        row.style.backgroundColor = '#fff';
                        row.style.cursor = 'pointer';
                        row.style.transition = 'all 0.2s ease';
                        
                        // ✅ Cache the device data
                        deviceCache[host] = d;
                        
                        row.innerHTML = `
                            <td style="display: flex; justify-content: space-between; align-items: center; gap: 12px; width: 100%;">
                                <!-- Left Section: Device Info with Arrow -->
                                <div style="flex: 1; display: flex; align-items: center; gap: 12px; min-width: 0;">
                                    <!-- Arrow Icon -->
                                    <div class="device-arrow" style="display: flex; align-items: center; justify-content: center; flex-shrink: 0; color: var(--gray-400); font-size: 20px; font-weight: 300; transition: color 0.2s ease;">→</div>
                                    
                                    <!-- Device Info -->
                                    <div style="flex: 1; display: flex; flex-direction: column; gap: 6px; min-width: 0;">
                                        <div style="font-weight: 600; color: var(--gray-900); font-size: 15px; word-break: break-word;">${escapeHtml(host)}</div>
                                        <div style="display: flex; align-items: center; gap: 12px;">
                                            <span class="status-badge ${statusBadgeClass}" style="display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 6px; font-size: 13px; font-weight: 500;">
                                                <span class="status-dot" style="background: ${statusColor}; width: 8px; height: 8px; border-radius: 50%; display: inline-block;"></span>
                                                ${displayStatus}
                                            </span>
                                            <span style="color: var(--gray-600); font-size: 13px;">${d.last_seen || 'Never'}</span>
                                        </div>
                                    </div>
                                </div>
                                
                                <!-- Right Section: Print Button -->
                                <div style="display: flex; gap: 8px; flex-shrink: 0;">
                                    <button class="btn-print" onclick="event.stopPropagation(); printSerialBackend('${escapeHtml(host)}', '${escapeHtml(serial)}')" title="Print QR Code" style="background: var(--primary); color: white; border: none; padding: 8px 12px; border-radius: 6px; cursor: pointer; display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 500;">
                                        <i class="fas fa-print" style="font-size: 14px;"></i>
                                        <span>Print</span>
                                    </button>
                                </div>
                            </td>
                        `;
                        
                        // ✅ Proper click handler using closure
                        row.onclick = (function(deviceHost) {
                            return function(e) {
                                // Don't open detail page if clicking print button
                                if (e.target.closest('.btn-print')) {
                                    return;
                                }
                                // Open device details page using cached data
                                openDeviceDetailPageFromCache(deviceHost);
                            };
                        })(host);

                        // Add hover effect
                        row.onmouseenter = function() {
                            this.style.backgroundColor = 'var(--gray-50)';
                            this.style.boxShadow = 'var(--shadow-sm)';
                            const arrow = this.querySelector('.device-arrow');
                            if (arrow) arrow.style.color = 'var(--primary)';
                        };

                        row.onmouseleave = function() {
                            this.style.backgroundColor = '#fff';
                            this.style.boxShadow = 'none';
                            const arrow = this.querySelector('.device-arrow');
                            if (arrow) arrow.style.color = 'var(--gray-400)';
                        };
                        
                        devicesRows.appendChild(row);
                    } else {
                        // Desktop view - full table with toggle button
                        const serial = info['Serial Number'] || info['Serial'] || info['serial_number'] || 'N/A';
                        const row = document.createElement('tr');
                        row.setAttribute('onclick', `toggleDeviceDetails('${did}', this)`);
                        row.style.cursor = 'pointer';
                        const printQRButton = showPrintQR ? 
                        `<td data-label="Print QR">
                            <button class="btn-print" onclick="event.stopPropagation(); printSerialBackend('${host}', '${info['Serial Number'] || ''}')" title="Print QR Code">
                                <i class="fas fa-print"></i> Print
                            </button>
                        </td>` : 
                        `<td data-label="Print QR"></td>`;
                    
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
                            <td data-label="Print">
                                <button class="details-btn" onclick="event.stopPropagation(); printSerialBackend('${escapeHtml(host)}','${escapeHtml(serial)}')">
                                    Print
                                </button>
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
                // ✅ Refresh API-based online device count cache after loading
                fetchAndCacheOnlineDeviceCount();
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

        // Function to open Print QR for specific device
        // function openPrintForDevice(hostname, serialNumber) {
        //     // Store the device info for printing
        //     currentPrintingDevice = {
        //         hostname: hostname,
        //         serial: serialNumber
        //     };
            
        //     // Open the print modal
        //     openPrintQRModal();
            
        //     // If we have a serial number, try to select it automatically
        //     if (serialNumber) {
        //         setTimeout(() => {
        //             const select = document.getElementById('printDeviceSelect');
        //             if (select) {
        //                 // Find and select the device by serial
        //                 for (let i = 0; i < select.options.length; i++) {
        //                     if (select.options[i].value === serialNumber) {
        //                         select.selectedIndex = i;
        //                         onPrintDeviceSelected();
        //                         break;
        //                     }
        //                 }
        //             }
        //         }, 500);
        //     }
        // }

        async function printSerialBackend(hostname, serialNumber) {
        try {
            const res = await fetch("/api/print-serial", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                hostname: hostname,
                serial_number: serialNumber
            })
            });

            const data = await res.json();
            if (!res.ok || !data.ok) {
            alert("Print failed: " + (data.error || "Unknown error"));
            return;
            }

            alert("Print sent successfully to printer: " + (data.printer || "default"));
        } catch (e) {
            alert("Print error: " + e.message);
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
        
        // ✅ NEW FUNCTION: Open device detail page (mobile)
        function openDeviceDetailPageFromCache(hostname) {
            try {
                const device = deviceCache[hostname];
                if (!device) {
                    console.error('Device not found in cache:', hostname);
                    alert('Error: Device data not found');
                    return;
                }
                openDeviceDetailPage(hostname, JSON.stringify(device));
            } catch (error) {
                console.error('Error opening device detail page from cache:', error);
                alert('Error loading device details');
            }
        }

        function openDeviceDetailPage(hostname, deviceData) {
            try {
                const device = JSON.parse(deviceData);
                const info = device.info || {};
                const detailPage = document.getElementById('deviceMobileDetailPage');
                document.getElementById('deviceMobileDetailTitle').textContent = hostname;
                
                const detailContent = document.getElementById('deviceMobileDetailContent');
                const isOnline = ((device.status || '').toLowerCase() === 'active' || (device.status || '').toLowerCase() === 'online');
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
        
        // ✅ NEW FUNCTION: Close device detail page
        function closeDeviceDetailPage() {
            const detailPage = document.getElementById('deviceMobileDetailPage');
            detailPage.classList.remove('active');
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
        // ===== USER MANAGEMENT =====
        function renderUsers(filter) {
            const tbody = document.getElementById('usersTableBody');
           
            if (Object.keys(allUsersData).length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; padding: 40px;">No users found</td></tr>';
                return;
            }
            let filteredUsers = Object.entries(allUsersData);
            if (filter === 'pending') {
                filteredUsers = filteredUsers.filter(([_, u]) => (u.status || '').toLowerCase() === 'pending');
            } else if (filter === 'approved') {
                filteredUsers = filteredUsers.filter(([_, u]) => u.status === 'approved' || u.status === 'Approved');
            }
            tbody.innerHTML = filteredUsers.map(([userId, user]) => {
                // ===== DETERMINE WHICH ACTIONS TO SHOW =====
                let actionsHTML = '';
               
                if (filter === 'all') {
                    // ALL USERS: Show Edit & Delete buttons
                    actionsHTML = `
                        <div style="display: flex; gap: 8px;">
                            <button class="btn-edit" onclick="event.stopPropagation(); openEditUserModal('${userId}')" title="Edit user">
                                <i class="fas fa-edit"></i>
                            </button>
                            <button class="btn-delete" onclick="event.stopPropagation(); openDeleteConfirmModal('${userId}')" title="Delete user">
                                <i class="fas fa-trash"></i>
                            </button>
                        </div>
                    `;
                } else {
                    // PENDING & APPROVED: Show View Details button
                    actionsHTML = `
                    <button class="details-btn" onclick="event.stopPropagation(); toggleUserDetails('${userId}')" title="View details">
                        View Details
                    </button>
                    <div id="user_details_${userId}" class="details-container" style="display: none;">
                        <div class="details-grid">
                            <div class="details-item"><strong>Full Name:</strong> <code>${escapeHtml(user.full_name)}</code></div>
                            <div class="details-item"><strong>Address:</strong> <code>${escapeHtml(user.address)}</code></div>
                            <div class="details-item"><strong>Email:</strong> <code>${escapeHtml(user.email)}</code></div>
                            <div class="details-item"><strong>Phone:</strong> <code>${escapeHtml(user.phone_number || 'N/A')}</code></div>
                            <div class="details-item"><strong>Type:</strong> <code>${escapeHtml(user.user_type)}</code></div>
                            <div class="details-item"><strong>Company:</strong> <code>${escapeHtml(user.company_name || 'N/A')}</code></div>
                            <div class="details-item"><strong>GST:</strong> <code>${escapeHtml(user.gst_no || 'N/A')}</code></div>
                            <div class="details-item"><strong>Pincode:</strong> <code>${escapeHtml(user.pincode || 'N/A')}</code></div>
                            <div class="details-item"><strong>Distributor:</strong>
                                <code>${
                                    escapeHtml(
                                    user.distributor_full_name
                                        ? `${user.distributor_full_name} (${user.distributor_code || 'N/A'})`
                                        : (user.distributor_code || 'N/A')
                                    )
                                }</code>
                            </div>
                            ${(user.user_type || '').toLowerCase() === 'dealer' ? `
                                <div class="details-item"><strong>Dealer Code:</strong> <code>${escapeHtml(user.dealer_code || 'N/A')}</code></div>
                                
                            ` : ''}

                        </div>
                        ${(user.status || '').toLowerCase() === 'pending' ? `
                            <div style="display: flex; gap: 10px; margin-top: 20px;">
                                <button onclick="handleApproveReject(this, ${user.user_id}, 'approve')" class="details-btn" style="flex: 1; background: var(--success); justify-content: center;">✓ Approve</button>
                                <button onclick="handleApproveReject(this, ${user.user_id}, 'reject')" class="details-btn" style="flex: 1; background: var(--danger); justify-content: center;">✕ Reject</button>
                            </div>
                        ` : ''}
                    </div>
                `;
                }
                
                // ✅ CHECK SCREEN SIZE FOR RESPONSIVE DISPLAY
                const isMobile = window.innerWidth <= 768;
                
                if (isMobile) {
                    if (filter === 'all') {
                        // ===== MOBILE VIEW FOR ALL USERS: 3 COLUMNS (Name, Type, Actions) =====
                        return `
                            <tr onclick="openUserDetailPage('${userId}')">
                                <td data-label="Full Name">${escapeHtml(user.full_name)}</td>
                                <td data-label="Type"><span class="type-badge">${(user.user_type || '').toLowerCase() === 'dealer' ? 'Dealer' : 'Distributor'}</span></td>
                                <td data-label="Actions">${actionsHTML}</td>
                            </tr>
                        `;
                    } else {
                        // ===== MOBILE VIEW FOR PENDING/APPROVED: CLICKABLE ROWS =====
                        return `
                            <tr onclick="openUserDetailPage('${userId}')">
                                <td data-label="Full Name">${escapeHtml(user.full_name)}</td>
                                <td data-label="Type"><span class="type-badge">${(user.user_type || '').toLowerCase() === 'dealer' ? 'Dealer' : 'Distributor'}</span></td>
                                <td data-label="Status" style="display: block;">
                                    <span class="status-badge" style="background: ${(user.status === 'approved' || (user.status || '').toLowerCase() === 'approved') ? 'rgba(16, 185, 129, 0.1)' : 'rgba(245, 158, 11, 0.1)'}; color: ${(user.status === 'approved' || (user.status || '').toLowerCase() === 'approved') ? '#10B981' : '#F59E0B'};">${user.status}</span>
                                </td>
                            </tr>
                        `;
                    }
                } else {
                    // ===== DESKTOP VIEW: FULL TABLE (7 COLUMNS) - MAKE ENTIRE ROW CLICKABLE =====
                    return `
                        <tr onclick="toggleUserDetails('${userId}')" style="cursor: pointer;">
                            <td data-label="Full Name">${escapeHtml(user.full_name)}</td>
                            <td data-label="Address">${escapeHtml(user.address)}</td>
                            <td data-label="Email">${escapeHtml(user.email)}</td>
                            <td data-label="Phone No">${escapeHtml(user.phone_number || 'N/A')}</td>
                            <td data-label="Company">${escapeHtml(user.company_name)}</td>
                            <td data-label="Type"><span class="type-badge">${(user.user_type || '').toLowerCase() === 'dealer' ? 'Dealer' : 'Distributor'}</span></td>
                            <td data-label="Status"><span class="status-badge" style="background: ${(user.status === 'approved' || (user.status || '').toLowerCase() === 'approved') ? 'rgba(16, 185, 129, 0.1)' : 'rgba(245, 158, 11, 0.1)'}; color: ${(user.status === 'approved' || (user.status || '').toLowerCase() === 'approved') ? '#10B981' : '#F59E0B'};">${user.status}</span></td>
                            <td data-label="Actions" onclick="event.stopPropagation();">${actionsHTML}</td>
                        </tr>
                    `;
                }
            }).join('');
        }
        // ===== USERS MANAGEMENT - COLUMN FILTERS =====
        function applyColumnFilters() {
            const fullNameFilter = document.getElementById('searchFullName').value.toLowerCase().trim();
            const addressFilter = document.getElementById('searchAddress').value.toLowerCase().trim();
            const emailFilter = document.getElementById('searchEmail').value.toLowerCase().trim();
            const phoneFilter = document.getElementById('searchPhoneNo').value.toLowerCase().trim();
            const companyFilter = document.getElementById('searchCompany').value.toLowerCase().trim();
            const typeFilter = document.getElementById('searchType').value.toLowerCase().trim();
            const statusFilter = document.getElementById('searchStatus').value.toLowerCase().trim();
            const tbody = document.getElementById('usersTableBody');
           
            if (Object.keys(allUsersData).length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; padding: 40px;">No users found</td></tr>';
                return;
            }
            let filteredUsers = Object.entries(allUsersData);
            // Apply status filter (tab filter)
            if (currentFilter === 'pending') {
                filteredUsers = filteredUsers.filter(([_, u]) => (u.status || '').toLowerCase() === 'pending');
            } else if (currentFilter === 'approved') {
                filteredUsers = filteredUsers.filter(([_, u]) => (u.status || '').toLowerCase() === 'approved');
            } else if (currentFilter === 'rejected') {
                filteredUsers = filteredUsers.filter(([_, u]) => (u.status || '').toLowerCase() === 'rejected');
            }
            // ✅ Apply column filters
            filteredUsers = filteredUsers.filter(([userId, user]) => {
                const matchFullName = !fullNameFilter || (user.full_name || '').toLowerCase().includes(fullNameFilter);
                const matchAddress = !addressFilter || (user.address || '').toLowerCase().includes(addressFilter);
                const matchEmail = !emailFilter || (user.email || '').toLowerCase().includes(emailFilter);
                const matchPhone = !phoneFilter || (user.phone_number || '').toLowerCase().includes(phoneFilter);
                const matchCompany = !companyFilter || (user.company_name || '').toLowerCase().includes(companyFilter);
                const matchType = !typeFilter || (user.user_type || '').toLowerCase().includes(typeFilter);
                const matchStatus = !statusFilter || (user.status || '').toLowerCase().includes(statusFilter);
                return matchFullName && matchAddress && matchEmail && matchPhone && matchCompany && matchType && matchStatus;
            });
            tbody.innerHTML = filteredUsers.map(([userId, user]) => {
                let actionsHTML = '';
               
                if (currentFilter === 'all') {
                    actionsHTML = `
                        <div style="display: flex; gap: 8px;">
                            <button class="btn-edit" onclick="event.stopPropagation(); openEditUserModal('${userId}')" title="Edit user">
                                <i class="fas fa-edit"></i>
                            </button>
                            <button class="btn-delete" onclick="event.stopPropagation(); openDeleteConfirmModal('${userId}')" title="Delete user">
                                <i class="fas fa-trash"></i>
                            </button>
                        </div>
                    `;
                } else {
                    actionsHTML = `
                    <button class="details-btn" onclick="event.stopPropagation(); toggleUserDetails('${userId}')" title="View details">
                        View Details
                    </button>
                    <div id="user_details_${userId}" class="details-container" style="display: none;">
                        <div class="details-grid">
                            <div class="details-item"><strong>Full Name:</strong> <code>${escapeHtml(user.full_name)}</code></div>
                            <div class="details-item"><strong>Address:</strong> <code>${escapeHtml(user.address)}</code></div>
                            <div class="details-item"><strong>Email:</strong> <code>${escapeHtml(user.email)}</code></div>
                            <div class="details-item"><strong>Phone:</strong> <code>${escapeHtml(user.phone_number || 'N/A')}</code></div>
                            <div class="details-item"><strong>Type:</strong> <code>${escapeHtml(user.user_type)}</code></div>
                            <div class="details-item"><strong>Company:</strong> <code>${escapeHtml(user.company_name || 'N/A')}</code></div>
                            <div class="details-item"><strong>GST:</strong> <code>${escapeHtml(user.gst_no || 'N/A')}</code></div>
                            <div class="details-item"><strong>Pincode:</strong> <code>${escapeHtml(user.pincode || 'N/A')}</code></div>
                            <div class="details-item"><strong>Distributor:</strong>
                                <code>${
                                    escapeHtml(
                                    user.distributor_full_name
                                        ? `${user.distributor_full_name} (${user.distributor_code || 'N/A'})`
                                        : (user.distributor_code || 'N/A')
                                    )
                                }</code>
                            </div>
                            ${(user.user_type || '').toLowerCase() === 'dealer' ? `
                                <div class="details-item"><strong>Dealer Code:</strong> <code>${escapeHtml(user.dealer_code || 'N/A')}</code></div>
                            ` : ''}
                        </div>
                        ${(user.status || '').toLowerCase() === 'pending' ? `
                            <div style="display: flex; gap: 10px; margin-top: 20px;">
                                <button onclick="handleApproveReject(this, ${user.user_id}, 'approve')" class="details-btn" style="flex: 1; background: var(--success); justify-content: center;">✓ Approve</button>
                                <button onclick="handleApproveReject(this, ${user.user_id}, 'reject')" class="details-btn" style="flex: 1; background: var(--danger); justify-content: center;">✕ Reject</button>
                            </div>
                        ` : ''}
                    </div>
                `;
                }
                
                // ✅ CHECK SCREEN SIZE FOR RESPONSIVE DISPLAY
                const isMobile = window.innerWidth <= 768;
                
                if (isMobile) {
                    if (currentFilter === 'all') {
                        // ===== MOBILE VIEW FOR ALL USERS =====
                        return `
                            <tr onclick="openUserDetailPage('${userId}')">
                                <td data-label="Full Name">${escapeHtml(user.full_name)}</td>
                                <td data-label="Type"><span class="type-badge">${(user.user_type || '').toLowerCase() === 'dealer' ? 'Dealer' : 'Distributor'}</span></td>
                                <td data-label="Actions">${actionsHTML}</td>
                            </tr>
                        `;
                    } else {
                        // ===== MOBILE VIEW FOR PENDING/APPROVED =====
                        return `
                            <tr onclick="openUserDetailPage('${userId}')">
                                <td data-label="Full Name">${escapeHtml(user.full_name)}</td>
                                <td data-label="Type"><span class="type-badge">${(user.user_type || '').toLowerCase() === 'dealer' ? 'Dealer' : 'Distributor'}</span></td>
                                <td data-label="Status" style="display: block;">
                                    <span class="status-badge" style="background: ${(user.status === 'approved' || (user.status || '').toLowerCase() === 'approved') ? 'rgba(16, 185, 129, 0.1)' : 'rgba(245, 158, 11, 0.1)'}; color: ${(user.status === 'approved' || (user.status || '').toLowerCase() === 'approved') ? '#10B981' : '#F59E0B'};">${user.status}</span>
                                </td>
                            </tr>
                        `;
                    }
                } else {
                    // ===== DESKTOP VIEW =====
                    return `
                        <tr onclick="toggleUserDetails('${userId}')" style="cursor: pointer;">
                            <td data-label="Full Name">${escapeHtml(user.full_name)}</td>
                            <td data-label="Address">${escapeHtml(user.address)}</td>
                            <td data-label="Email">${escapeHtml(user.email)}</td>
                            <td data-label="Phone No">${escapeHtml(user.phone_number || 'N/A')}</td>
                            <td data-label="Company">${escapeHtml(user.company_name)}</td>
                            <td data-label="Type"><span class="type-badge">${(user.user_type || '').toLowerCase() === 'dealer' ? 'Dealer' : 'Distributor'}</span></td>
                            <td data-label="Status"><span class="status-badge" style="background: ${(user.status === 'approved' || (user.status || '').toLowerCase() === 'approved') ? 'rgba(16, 185, 129, 0.1)' : 'rgba(245, 158, 11, 0.1)'}; color: ${(user.status === 'approved' || (user.status || '').toLowerCase() === 'approved') ? '#10B981' : '#F59E0B'};">${user.status}</span></td>
                            <td data-label="Actions" onclick="event.stopPropagation();">${actionsHTML}</td>
                        </tr>
                    `;
                }
            }).join('');
            if (filteredUsers.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; padding: 40px; color: var(--gray-500);">No matching users found</td></tr>';
            }
        }
        function clearColumnFilters() {
            document.getElementById('searchFullName').value = '';
            document.getElementById('searchAddress').value = '';
            document.getElementById('searchEmail').value = '';
            document.getElementById('searchPhoneNo').value = '';
            document.getElementById('searchCompany').value = '';
            document.getElementById('searchType').value = '';
            document.getElementById('searchStatus').value = '';
           
            applyColumnFilters();
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
           
            let filteredUsers = [];
           
            for (const [userId, user] of Object.entries(allUsersData)) {
                if (user.user_type === issueState.currentUserType) {
                    filteredUsers.push([userId, user]);
                }
            }
            // ✅ Apply column filters
            filteredUsers = filteredUsers.filter(([userId, user]) => {
                const codeField = issueState.currentUserType === 'dealer' ? user.distributor_code : user.distributor_code;
               
                const matchFullName = !fullNameFilter || (user.full_name || '').toLowerCase().includes(fullNameFilter);
                const addressEmail = !addressFilter || (user.address || '').toLowerCase().includes(addressFilter);
                const matchEmail = !emailFilter || (user.email || '').toLowerCase().includes(emailFilter);
                const matchPhone = !phoneFilter || (user.phone_number || '').toLowerCase().includes(phoneFilter);
                const matchCompany = !companyFilter || (user.company_name || '').toLowerCase().includes(companyFilter);
                const matchCode = !codeFilter || (codeField || '').toLowerCase().includes(codeFilter);
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
            filteredUsers.forEach(([userId, user]) => {
                const row = document.createElement('tr');
                const codeField = issueState.currentUserType === 'dealer' ? user.distributor_code : user.distributor_code;
                const statusColor = (user.status || '').toLowerCase() === 'approved' ? '#10B981' : (user.status || '').toLowerCase() === 'pending' ? '#F59E0B' : '#EF4444';
                const statusBg = (user.status || '').toLowerCase() === 'approved' ? 'rgba(16, 185, 129, 0.1)' : (user.status || '').toLowerCase() === 'pending' ? 'rgba(245, 158, 11, 0.1)' : 'rgba(239, 68, 68, 0.1)';
                const buttonLabel = issueState.currentUserType === 'dealer' ? 'Select Dealer' : 'Select Distributor';
                
                // Check if mobile view
                const isMobile = window.innerWidth <= 768;
                
                if (isMobile) {
                    // Mobile view - card layout
                    row.setAttribute('onclick', `selectUserForIssue('${userId}', '${escapeHtml(user.full_name)}', '${issueState.currentUserType}')`);
                    row.style.cursor = 'pointer';
                    row.innerHTML = `
                        <td data-label="Full Name" style="font-weight: 600;">${escapeHtml(user.full_name)}</td>
                        <td data-label="Type"><span class="type-badge">${(user.user_type || '').toLowerCase() === 'dealer' ? 'Dealer' : 'Distributor'}</span></td>
                        <td data-label="Status">
                            <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                                <span class="status-dot" style="background: ${statusColor};"></span>
                                ${user.status}
                            </span>
                        </td>
                    `;
                } else {
                    // Desktop view - full table
                    row.innerHTML = `
                        <td data-label="Full Name" style="font-weight: 600;">${escapeHtml(user.full_name)}</td>
                        <td data-label="Address">${escapeHtml(user.address)}</td>
                        <td data-label="Email">${escapeHtml(user.email)}</td>
                        <td data-label="Phone No">${escapeHtml(user.phone_number || 'N/A')}</td>
                        <td data-label="Company">${escapeHtml(user.company_name || 'N/A')}</td>
                        <td data-label="Code">${escapeHtml(codeField || 'N/A')}</td>
                        <td data-label="Status">
                            <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                                <span class="status-dot" style="background: ${statusColor};"></span>
                                ${user.status}
                            </span>
                        </td>
                        <td data-label="Action">
                            <button class="details-btn" onclick="selectUserForIssue('${userId}', '${escapeHtml(user.full_name)}', '${issueState.currentUserType}')">
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
        function filterUsers(filter) {
            currentFilter = filter;
           
            // ✅ Clear column filters
            clearColumnFilters();
           
            // Update active button state
            document.querySelectorAll('.filter-btn').forEach(btn => btn.classList.remove('active'));
           
            const filterMap = {
                'all': 'filterAllBtn',
                'pending': 'filterPendingBtn',
                'approved': 'filterApprovedBtn',
                'rejected': 'filterRejectedBtn'
            };
           
            const buttonId = filterMap[filter];
            if (buttonId) {
                document.getElementById(buttonId).classList.add('active');
            }
           
            updateCreateButtonVisibility(filter);
            applyColumnFilters();
        }
        function updateCreateButtonVisibility(filter) {
            const createBtn = document.querySelector('.btn-primary');
           
            if (createBtn) {
                if (filter === 'all') {
                    // SHOW Create button on All Users tab
                    createBtn.style.display = 'inline-flex';
                    createBtn.style.opacity = '1';
                    createBtn.style.pointerEvents = 'auto';
                } else {
                    // HIDE Create button on Pending/Approved tabs
                    createBtn.style.display = 'none';
                    createBtn.style.opacity = '0';
                    createBtn.style.pointerEvents = 'none';
                }
            }
        }
        // ===== UPDATE FILTER BUTTON STATES =====
        function updateFilterButtonStates() {
            document.getElementById('filterAllBtn').classList.toggle('active', currentFilter === 'all');
            document.getElementById('filterPendingBtn').classList.toggle('active', currentFilter === 'pending');
            document.getElementById('filterApprovedBtn').classList.toggle('active', currentFilter === 'approved');
            const rejBtn = document.getElementById('filterRejectedBtn');
            if (rejBtn) rejBtn.classList.toggle('active', currentFilter === 'rejected');
        }
        function displayFilteredUsers() {
            const table = document.getElementById('usersTable');
            table.innerHTML = '';
            let filteredUsers = {};
           
            if (currentUserFilter === 'all') {
                filteredUsers = allUsersData;
            } else if (currentUserFilter === 'pending') {
                for (const [id, user] of Object.entries(allUsersData)) {
                    if ((user.status || '').toLowerCase() === 'pending') {
                        filteredUsers[id] = user;
                    }
                }
            } else if (currentUserFilter === 'approved') {
                for (const [id, user] of Object.entries(allUsersData)) {
                    if ((user.status || '').toLowerCase() === 'approved') {
                        filteredUsers[id] = user;
                    }
                }
            }
            if (Object.keys(filteredUsers).length === 0) {
                let message = '✨ No users found';
                if (currentUserFilter === 'pending') message = '✨ No pending requests';
                if (currentUserFilter === 'approved') message = '✨ No approved users';
               
                table.innerHTML = `
                    <tr>
                        <td colspan="6" style="text-align: center; padding: 40px; color: var(--gray-500);">
                            ${message}
                        </td>
                    </tr>
                `;
                return;
            }
            for (const [id, user] of Object.entries(filteredUsers)) {
                const statusColor = (user.status || '').toLowerCase() === 'approved' ? '#10B981' : (user.status || '').toLowerCase() === 'pending' ? '#F59E0B' : '#EF4444';
                const statusBg = (user.status || '').toLowerCase() === 'approved' ? 'rgba(16, 185, 129, 0.1)' : (user.status || '').toLowerCase() === 'pending' ? 'rgba(245, 158, 11, 0.1)' : 'rgba(239, 68, 68, 0.1)';
               
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td data-label="Full Name" style="font-weight: 600;">${escapeHtml(user.full_name)}</td>
                    <td data-label="Email">${escapeHtml(user.email)}</td>
                    <td data-label="Address">${escapeHtml(user.address)}</td>
                    <td data-label="Phone">${escapeHtml(user.phone_number)}</td>
                    <td data-label="Company">${escapeHtml(user.company_name || 'N/A')}</td>
                    <td data-label="Type">
                        <span class="status-badge type-badge">${user.user_type}</span>
                    </td>
                    <td data-label="Status">
                        <span class="status-badge" style="background: ${statusBg}; color: ${statusColor};">
                            <span class="status-dot" style="background: ${statusColor};"></span>
                            ${user.status}
                        </span>
                    </td>
                    <td data-label="Action">
                        <button class="details-btn" onclick="toggleUserDetails('${id}')">
                            View Details
                        </button>
                        <div id="user_details_${id}" class="details-container" style="display: none;">
                            <div class="details-grid">
                                <div class="details-item"><strong>Full Name:</strong> <code>${escapeHtml(user.full_name)}</code></div>
                                <div class="details-item"><strong>Address:</strong> <code>${escapeHtml(user.address)}</code></div>
                                <div class="details-item"><strong>Email:</strong> <code>${escapeHtml(user.email)}</code></div>
                                <div class="details-item"><strong>Phone:</strong> <code>${escapeHtml(user.phone_number || 'N/A')}</code></div>
                                <div class="details-item"><strong>Type:</strong> <code>${escapeHtml(user.user_type)}</code></div>
                                <div class="details-item"><strong>Company:</strong> <code>${escapeHtml(user.company_name || 'N/A')}</code></div>
                                <div class("details-item"><strong>GST:</strong> <code>${escapeHtml(user.gst_no || 'N/A')}</code></div>
                                <div class="details-item"><strong>Pincode:</strong> <code>${escapeHtml(user.pincode || 'N/A')}</code></div>
                                <div class="details-item"><strong>Dealer Code:</strong> <code>${escapeHtml(user.dealer_code || 'N/A')}</code></div>
                                <div class="details-item"><strong>Distributor:</strong>
                                <code>${
                                    escapeHtml(
                                    user.distributor_full_name
                                        ? `${user.distributor_full_name} (${user.distributor_code || 'N/A'})`
                                        : (user.distributor_code || 'N/A')
                                    )
                                }</code>
                                </div>
                            </div>
                            ${(user.status || '').toLowerCase() === 'pending' ? `
                                <div style="display: flex; gap: 10px; margin-top: 20px;">
                                    <button onclick="handleApproveReject(this, ${user.user_id}, 'approve')" class="details-btn" style="flex: 1; background: var(--success); justify-content: center;">✓ Approve</button>
                                    <button onclick="handleApproveReject(this, ${user.user_id}, 'reject')" class="details-btn" style="flex: 1; background: var(--danger); justify-content: center;">✕ Reject</button>
                                </div>
                            ` : ''}
                        </div>
                    </td>
                `;
                table.appendChild(row);
            }
        }
        function toggleUserDetails(userId) {
            const detailsId = 'user_details_' + userId;
            const el = document.getElementById(detailsId);
            if (!el) return;
           
            const isVisible = el.style.display !== 'none';
            el.style.display = isVisible ? 'none' : 'block';
        }
        function updateUsersBadge() {
            let pending = 0;
            for (const [id, user] of Object.entries(allUsersData)) {
                if ((user.status || '').toLowerCase() === 'pending') pending++;
            }
            document.getElementById('usersBadge').textContent = pending;
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
        // Make sure to call this when users view is first shown:
        function initializeUsersView() {
            // ✅ REMOVED - This was causing conflicts
            // Data is now loaded in showUsersView() with proper .then() chain
        }
        function openEditUserModal(userId) {
            currentEditingUserId = userId;
            const user = allUsersData[userId];
           
            document.getElementById('editFullName').value = user.full_name;
            document.getElementById('editAddress').value = user.address;
            document.getElementById('editEmail').value = user.email;
            document.getElementById('editPhoneNumber').value = user.phone_number || '';
            document.getElementById('editCompanyName').value = user.company_name || '';
            document.getElementById('editUserType').value = user.user_type;
            document.getElementById('editStatus').value = (user.status || '').charAt(0).toUpperCase() + (user.status || '').slice(1).toLowerCase();
            document.getElementById('editGstNo').value = user.gst_no || '';
            document.getElementById('editPincode').value = user.pincode || '';
            document.getElementById('editDealerCode').value = user.dealer_code || '';
            document.getElementById('editDistributorCode').value = user.distributor_code || '';
            document.getElementById('editUserModal').classList.add('show');
        }
        function closeEditUserModal() {
            document.getElementById('editUserModal').classList.remove('show');
            document.getElementById('editUserForm').reset();
            currentEditingUserId = null;
        }
        function openCreateUserModal() {
            document.getElementById('createUserForm').reset();
            document.getElementById('createUserModal').classList.add('show');
        }
        function closeCreateUserModal() {
            document.getElementById('createUserModal').classList.remove('show');
            document.getElementById('createUserForm').reset();
        }
        function toggleCreatePassword() {
            const input = document.getElementById('createPassword');
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
        function toggleCreateConfirmPassword() {
            const input = document.getElementById('createConfirmPassword');
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
        function openDeleteConfirmModal(userId) {
            currentDeletingUserId = userId;
            document.getElementById('deleteUserName').textContent = allUsersData[userId].full_name;
            document.getElementById('deleteConfirmModal').classList.add('show');
        }
        function closeDeleteConfirmModal() {
            document.getElementById('deleteConfirmModal').classList.remove('show');
            currentDeletingUserId = null;
        }
        // Form submissions
        document.addEventListener('DOMContentLoaded', function() {
            const editForm = document.getElementById('editUserForm');
            const createForm = document.getElementById('createUserForm');
           
            if (editForm) {
                editForm.addEventListener('submit', async (e) => {
                    e.preventDefault();
                    await submitEditUser();
                });
            }
            if (createForm) {
                createForm.addEventListener('submit', async (e) => {
                    e.preventDefault();
                    await submitCreateUser();
                });
            }
        });
        async function submitEditUser() {
            const formData = {
                full_name: document.getElementById('editFullName').value,
                address: document.getElementById('addressEmail').value,
                email: document.getElementById('editEmail').value,
                phone_number: document.getElementById('editPhoneNumber').value,
                company_name: document.getElementById('editCompanyName').value,
                user_type: document.getElementById('editUserType').value,
                status: document.getElementById('editStatus').value,
                gst_no: document.getElementById('editGstNo').value,
                pincode: document.getElementById('editPincode').value
            };
            try {
                const response = await fetch(`/admin/users/${currentEditingUserId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(formData)
                });
                if (!response.ok) {
                    const data = await response.json();
                    showNotification('error', data.message || 'Failed to update user');
                    return;
                }
                await loadUsersData();
                renderUsers(currentFilter || 'all');
                closeEditUserModal();
                showNotification('success', '✓ User updated successfully');
            } catch (error) {
                console.error('Error:', error);
                showNotification('error', 'Failed to update user');
            }
        }
        async function submitCreateUser() {
            const formData = {
                full_name: document.getElementById('createFullName').value.trim(),
                address: document.getElementById('createAddress').value.trim(),
                email: document.getElementById('createEmail').value.trim(),
                phone_number: document.getElementById('createPhoneNumber').value.trim(),
                company_name: document.getElementById('createCompanyName').value.trim(),
                user_type: document.getElementById('createUserType').value,
                password: document.getElementById('createPassword').value,
                confirm_password: document.getElementById('createConfirmPassword').value,
                gst_no: document.getElementById('createGstNo').value.trim(),
                pincode: document.getElementById('createPincode').value.trim()
            };
            try {
                const response = await fetch('/admin/post-users', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(formData)
                });
                if (!response.ok) {
                    const data = await response.json();
                    showNotification('error', data.message || 'Failed to create user');
                    return;
                }
                await loadUsersData();
                renderUsers(currentFilter || 'all');
                closeCreateUserModal();
                showNotification('success', '✓ User created successfully');
            } catch (error) {
                console.error('Error:', error);
                showNotification('error', 'Failed to create user');
            }
        }
        async function confirmDelete() {
            try {
                const response = await fetch(`/admin/delete-users/${currentDeletingUserId}`, {
                    method: 'DELETE'
                });
                if (!response.ok) {
                    const data = await response.json();
                    showNotification('error', data.message || 'Failed to delete user');
                    return;
                }
                await loadUsersData();
                renderUsers(currentFilter || 'all');
                closeDeleteConfirmModal();
                showNotification('success', '✓ User deleted successfully');
            } catch (error) {
                console.error('Error:', error);
                showNotification('error', 'Failed to delete user');
            }
        }
        // // NEW FUNCTION: Update button visibility based on filter
        // function updateButtonVisibility() {
        // const createBtn = document.getElementById('createUserBtn');
        // if (createBtn) {
        // // Only show Create button on "All Users" tab
        // createBtn.style.display = currentFilter === 'all' ? 'inline-flex' : 'none';
        // }
        // }
        // // Show different buttons based on current filter
        // if (filter === 'all') {
        // actionsHTML = `Edit & Delete buttons`; // Full User tab
        // } else {
        // actionsHTML = `<button>Show Details</button>`; // Pending/Approved tabs
        // }
        // ===== NOTIFICATION SYSTEM =====
        // Add this section right after the DOMContentLoaded listener setup
        // function showNotification(type, message) {
        //     // Create notification container if it doesn't exist
        //     let notificationContainer = document.getElementById('notificationContainer');
        //     if (!notificationContainer) {
        //         notificationContainer = document.createElement('div');
        //         notificationContainer.id = 'notificationContainer';
        //         notificationContainer.style.cssText = `
        //             position: fixed;
        //             top: 20px;
        //             right: 20px;
        //             z-index: 9999;
        //             max-width: 400px;
        //             display: flex;
        //             flex-direction: column;
        //             gap: 10px;
        //         `;
        //         document.body.appendChild(notificationContainer);
        //     }
        //     // Create notification element
        //     const notification = document.createElement('div');
        //     const isError = type === 'error';
        //     const bgColor = isError ? '#FEE2E2' : '#DCFCE7';
        //     const borderColor = isError ? '#EF4444' : '#10B981';
        //     const textColor = isError ? '#991B1B' : '#166534';
        //     const icon = isError ? '❌' : '✅';
        //     notification.style.cssText = `
        //         background: ${bgColor};
        //         border: 2px solid ${borderColor};
        //         border-radius: 8px;
        //         padding: 16px;
        //         color: ${textColor};
        //         font-weight: 600;
        //         font-size: 14px;
        //         box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
        //         animation: slideIn 0.3s ease;
        //     `;
        //     notification.innerHTML = `
        //         <div style="display: flex; align-items: flex-start; gap: 12px;">
        //             <span style="font-size: 18px; flex-shrink: 0;">${icon}</span>
        //             <span style="flex: 1; word-break: break-word;">${escapeHtml(message)}</span>
        //             <button onclick="this.parentElement.parentElement.remove()"
        //                     style="background: none; border: none; color: ${textColor}; cursor: pointer; font-size: 18px; padding: 0; margin-left: 8px; width: 20px; height: 20px;">
        //                 ✕
        //             </button>
        //         </div>
        //     `;
        //     notificationContainer.appendChild(notification);
        //     // Auto-remove after 5 seconds (longer for errors)
        //     const duration = isError ? 7000 : 5000;
        //     setTimeout(() => {
        //         if (notification && notification.parentElement) {
        //             notification.style.animation = 'slideOut 0.3s ease';
        //             setTimeout(() => {
        //                 if (notification && notification.parentElement) {
        //                     notification.remove();
        //                 }
        //             }, 300);
        //         }
        //     }, duration);
        // }
        // // Add CSS animations (add to existing <style> tag or create new one)
        // const notificationStyles = document.createElement('style');
        // notificationStyles.textContent = `
        //     @keyframes slideIn {
        //         from {
        //             opacity: 0;
        //             transform: translateX(400px);
        //         }
        //         to {
        //             opacity: 1;
        //             transform: translateX(0);
        //         }
        //     }
        //     @keyframes slideOut {
        //         from {
        //             opacity: 1;
        //             transform: translateX(0);
        //         }
        //         to {
        //             opacity: 0;
        //             transform: translateX(400px);
        //         }
        //     }
        // `;
        // document.head.appendChild(notificationStyles);
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

        // ===== IMPROVED USER CREATION FUNCTION =====
        let _createUserInProgress = false;
        async function submitCreateUser() {
            if (_createUserInProgress) return;
            _createUserInProgress = true;

            const submitBtn = document.querySelector('#createUserForm button[type="submit"]');
            const formData = {
                full_name: document.getElementById('createFullName').value.trim(),
                address: document.getElementById('createAddress').value.trim(),
                email: document.getElementById('createEmail').value.trim(),
                phone_number: document.getElementById('createPhoneNumber').value.trim(),
                company_name: document.getElementById('createCompanyName').value.trim(),
                user_type: document.getElementById('createUserType').value,
                password: document.getElementById('createPassword').value,
                confirm_password: document.getElementById('createConfirmPassword').value,
                gst_no: document.getElementById('createGstNo').value.trim(),
                pincode: document.getElementById('createPincode').value.trim()
            };

            function resetBtn() {
                _createUserInProgress = false;
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = '<i class="fas fa-check"></i> Create User';
                }
            }

            try {
                // ===== CLIENT-SIDE VALIDATION =====
                if (!formData.full_name) { showNotification('error', 'Full Name is required'); resetBtn(); return; }
                if (!formData.address) { showNotification('error', 'Address is required'); resetBtn(); return; }
                if (!formData.email) { showNotification('error', 'Email is required'); resetBtn(); return; }
                const emailRegex = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;
                if (!emailRegex.test(formData.email)) { showNotification('error', 'Please enter a valid email address'); resetBtn(); return; }
                if (!formData.phone_number) { showNotification('error', 'Phone Number is required'); resetBtn(); return; }
                if (!formData.company_name) { showNotification('error', 'Company Name is required'); resetBtn(); return; }
                if (!formData.user_type) { showNotification('error', 'User Type is required'); resetBtn(); return; }
                if (!formData.gst_no) { showNotification('error', 'GST Number is required'); resetBtn(); return; }
                if (!formData.pincode) { showNotification('error', 'Pincode is required'); resetBtn(); return; }
                if (!formData.password) { showNotification('error', 'Password is required'); resetBtn(); return; }
                if (formData.password.length < 8) { showNotification('error', 'Password must be at least 8 characters long'); resetBtn(); return; }
                if (!/[A-Z]/.test(formData.password)) { showNotification('error', 'Password must contain at least one uppercase letter'); resetBtn(); return; }
                if (!/[a-z]/.test(formData.password)) { showNotification('error', 'Password must contain at least one lowercase letter'); resetBtn(); return; }
                if (!/[0-9]/.test(formData.password)) { showNotification('error', 'Password must contain at least one number'); resetBtn(); return; }
                if (!/[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?`~]/.test(formData.password)) { showNotification('error', 'Password must contain at least one special character'); resetBtn(); return; }
                if (formData.password !== formData.confirm_password) { showNotification('error', 'Passwords do not match'); resetBtn(); return; }

                // Disable submit button to prevent duplicate submissions
                if (submitBtn) {
                    submitBtn.disabled = true;
                    submitBtn.innerHTML = '<span class="spinner"></span> Creating...';
                }
                // ===== SEND TO API =====
                const response = await fetch('/admin/post-users', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(formData)
                });
                const responseData = await response.json();
                // ===== HANDLE ERROR RESPONSE =====
                if (!response.ok) {
                    resetBtn();
                    const errorMessage = responseData.message || 'Failed to create user';
                    showNotification('error', errorMessage);
                    console.error('User creation error:', {
                        status: response.status,
                        message: errorMessage,
                        fullResponse: responseData
                    });
                    return;
                }
                // ===== SUCCESS RESPONSE =====
                showNotification('success', `User "${formData.full_name}" created successfully!`);
                console.log('✅ User created:', responseData);
                resetBtn();

                // Close modal and reload data after a short delay
                setTimeout(() => {
                    closeCreateUserModal();
                    loadUsersData().then(() => {
                        renderUsers(currentFilter || 'all');
                    });
                    loadDashboardData();
                }, 800);

            } catch (error) {
                console.error('❌ Exception in submitCreateUser:', error);
                resetBtn();
                showNotification('error', `An unexpected error occurred: ${error.message}`);
            }
        }
        // ===== IMPROVED EDIT USER FUNCTION =====
        async function submitEditUser() {
            const formData = {
                full_name: document.getElementById('editFullName').value,
                address: document.getElementById('editAddress').value,
                email: document.getElementById('editEmail').value,
                phone_number: document.getElementById('editPhoneNumber').value,
                company_name: document.getElementById('editCompanyName').value,
                user_type: document.getElementById('editUserType').value,
                status: document.getElementById('editStatus').value,
                gst_no: document.getElementById('editGstNo').value,
                pincode: document.getElementById('editPincode').value
            };
            try {
                // Disable submit button
                const submitBtn = event.target.querySelector('button[type="submit"]');
                if (submitBtn) {
                    submitBtn.disabled = true;
                    submitBtn.innerHTML = '<span class="spinner"></span> Saving...';
                }
                const response = await fetch(`/admin/users/${currentEditingUserId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(formData)
                });
                const responseData = await response.json();
                if (!response.ok) {
                    // Re-enable button on error
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.innerHTML = '<i class="fas fa-save"></i> Save Changes';
                    }
                    const errorMessage = responseData.message || 'Failed to update user';
                    showNotification('error', errorMessage);
                    console.error('Edit error:', responseData);
                    return;
                }
                showNotification('success', `User updated successfully!`);
                console.log('✅ User updated:', responseData);
               
                // Re-enable button
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = '<i class="fas fa-save"></i> Save Changes';
                }
                setTimeout(() => {
                    closeEditUserModal();
                    loadUsersData().then(() => {
                        renderUsers(currentFilter || 'all');
                    });
                    loadDashboardData();
                }, 800);
            } catch (error) {
                console.error('❌ Exception in submitEditUser:', error);
               
                // Re-enable button on error
                if (event.target && event.target.querySelector('button[type="submit"]')) {
                    const submitBtn = event.target.querySelector('button[type="submit"]');
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = '<i class="fas fa-save"></i> Save Changes';
                }
                showNotification('error', `An unexpected error occurred: ${error.message}`);
            }
        }
        // ===== IMPROVED DELETE CONFIRMATION =====
        async function confirmDelete() {
            try {
                // Disable delete button
                const deleteBtn = event.target;
                if (deleteBtn) {
                    deleteBtn.disabled = true;
                    deleteBtn.innerHTML = '<span class="spinner"></span> Deleting...';
                }
                const response = await fetch(`/admin/delete-users/${currentDeletingUserId}`, {
                    method: 'DELETE'
                });
                const responseData = await response.json();
                if (!response.ok) {
                    // Re-enable button on error
                    if (deleteBtn) {
                        deleteBtn.disabled = false;
                        deleteBtn.innerHTML = '<i class="fas fa-check"></i> Delete User';
                    }
                    const errorMessage = responseData.message || 'Failed to delete user';
                    showNotification('error', errorMessage);
                    console.error('Delete error:', responseData);
                    return;
                }
                showNotification('success', 'User deleted successfully!');
                console.log('✅ User deleted:', responseData);
                setTimeout(() => {
                    closeDeleteConfirmModal();
                    loadUsersData().then(() => {
                        renderUsers(currentFilter || 'all');
                    });
                    loadDashboardData();
                }, 800);
            } catch (error) {
                console.error('❌ Exception in confirmDelete:', error);
               
                // Re-enable button on error
                if (event.target) {
                    event.target.disabled = false;
                    event.target.innerHTML = '<i class="fas fa-check"></i> Delete User';
                }
                showNotification('error', `An unexpected error occurred: ${error.message}`);
            }
        }
        // ===== ENSURE FORM SUBMISSION HANDLERS ARE SET UP =====
        // This should run after DOMContentLoaded
        function setupFormHandlers() {
            const editForm = document.getElementById('editUserForm');
            const createForm = document.getElementById('createUserForm');
           
            if (editForm) {
                editForm.removeEventListener('submit', submitEditUser); // Remove old listener
                editForm.addEventListener('submit', async (e) => {
                    e.preventDefault();
                    await submitEditUser.call(e.target);
                });
            }
            if (createForm) {
                createForm.removeEventListener('submit', submitCreateUser); // Remove old listener
                createForm.addEventListener('submit', async (e) => {
                    e.preventDefault();
                    await submitCreateUser.call(e.target);
                });
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
        
            if (nextStepContainer) nextStepContainer.classList.remove('show');
        
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
        
            if (nextStepContainer) nextStepContainer.classList.remove('show');
        
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
           
            // ✅ SHOW NETWORK DISCOVERY BUTTON ONLY AFTER SUCCESSFUL SCAN
            // NOTE: Network Discovery button removed - not needed
            if (nextStepContainer) {
                nextStepContainer.style.display = 'none';
                nextStepContainer.classList.remove("show");
            }
           
            validateAndSaveDevice(displaySerial);
        }
        // async function proceedAfterScan() {
        // let serialToUse = selectedOnvifSerial || issueState.lastScannedSerial;
        // if (!serialToUse) {
        // showErrorMessage(
        // "❌ No Serial",
        // "No serial number was captured. Please scan a QR code or select a device."
        // );
        // return;
        // }
        // validateAndSaveDevice(serialToUse);
        // }
        // ✅ COMPLETE FIXED FUNCTION
        // Function to dynamically fetch IP address from the backend
        

        // Function to trigger device discovery for the fetched IP
        // function runDeviceDiscovery(ipAddress) {
        //     fetch("/api/device_discovery", {
        //         method: "POST",
        //         headers: {
        //             "Content-Type": "application/json"
        //         },
        //         body: JSON.stringify({ ip_address: ipAddress })
        //     })
        //     .then(response => response.json())
        //     .then(data => {
        //         console.log("Device discovery result:", data);
        //     })
        //     .catch(error => {
        //         console.error("Error during device discovery:", error);
        //     });
        // }

        // // Fetch the IP address and trigger discovery
        // fetchIPAddress();

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

                // Fetching IP address from validation response
                const ipAddress = validationData.device_info.ip_address;

                // Saving the device after validation
                const saveResponse = await fetch('/api/devices/save-from-qr', {
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
                const userName = issueState.currentUserData?.full_name || 'Unknown User';
                showSuccessMessage(
                    `✅ ISSUED TO ${typeLabel} SUCCESSFULLY`,
                    `User: ${userName}`
                );

                // STEP 3: Start the device scan with the fetched IP address
                console.log("📡 Initiating device scan...");
                startDeviceScan(ipAddress); // Initiating device scan with the IP address
            } catch (error) {
                console.error('❌ Exception in validateAndSaveDevice:', error);
                showErrorMessage(
                    "❌ Error Processing Device",
                    'An unexpected error occurred. Please try again.'
                );
            }
        }

        // This function will be triggered to start scanning the device using its IP address
        function startDeviceScan(ipAddress) {
            // You can make further API calls or actions here using the IP address
            console.log("Starting scan for device with IP:", ipAddress);

            // Example: Send IP address to the backend for scanning
            const scanData = {
                ip_address: ipAddress // Pass the device IP for scanning
            };

            // Example: Send IP address to the backend to scan the device
            fetch('/api/onvif/scan-with-ip', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(scanData)
            })
            .then(response => response.json())
            .then(scanResult => {
                if (scanResult.status === 'ok') {
                    // If scanning is successful, handle the devices found
                    console.log("Devices found:", scanResult.devices);
                } else {
                    console.error("Error scanning device:", scanResult.message);
                }
            })
            .catch(error => {
                console.error("Error during device scan:", error);
            });
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
            // NOTE: Network Discovery button removed - not needed
            if (nextStepContainer) {
                nextStepContainer.style.display = 'block';
                nextStepContainer.classList.add("show");
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
            // NOTE: Network Discovery button removed - not needed
            if (nextStepContainer) {
                nextStepContainer.style.display = 'none';
                nextStepContainer.classList.remove("show");
            }
        
            qrScanInProgress = false;
        }
       
        var _qrCloseBtn = document.getElementById("qrCloseBtn");
        if (_qrCloseBtn) _qrCloseBtn.addEventListener("click", closeQRScanner);
        var _qrOverlay = document.getElementById("qrModalOverlay");
        if (_qrOverlay) _qrOverlay.addEventListener("click", (e) => {
            if (e.target === _qrOverlay) closeQRScanner();
        });
        // ✅ OPEN DETAIL PAGE
        function openUserDetailPage(userId) {
            const user = allUsersData[userId];
            if (!user) return;
            const detailPage = document.getElementById('mobileDetailPage');
            document.getElementById('mobileDetailTitle').textContent = user.full_name;
            // ✅ POPULATE DETAILS
            const detailContent = document.getElementById('mobileDetailContent');
            detailContent.innerHTML = `
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Full Name</div>
                    <div class="mobile-detail-value">${escapeHtml(user.full_name)}</div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Address</div>
                    <div class="mobile-detail-value">${escapeHtml(user.address)}</div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Email</div>
                    <div class="mobile-detail-value">${escapeHtml(user.email)}</div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Phone Number</div>
                    <div class="mobile-detail-value">${escapeHtml(user.phone_number || 'N/A')}</div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Type</div>
                    <div class="mobile-detail-value">
                        <span class="type-badge">${(user.user_type || '').toLowerCase() === 'dealer' ? 'Dealer' : 'Distributor'}</span>
                    </div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Company</div>
                    <div class="mobile-detail-value">${escapeHtml(user.company_name || 'N/A')}</div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Status</div>
                    <div class="mobile-detail-value">
                        <span class="status-badge" style="background: ${(user.status === 'approved' || (user.status || '').toLowerCase() === 'approved') ? 'rgba(16, 185, 129, 0.1)' : 'rgba(245, 158, 11, 0.1)'}; color: ${(user.status === 'approved' || (user.status || '').toLowerCase() === 'approved') ? '#10B981' : '#F59E0B'};">
                            ${user.status}
                        </span>
                    </div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">GST Number</div>
                    <div class="mobile-detail-value">${escapeHtml(user.gst_no || 'N/A')}</div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Pincode</div>
                    <div class="mobile-detail-value">${escapeHtml(user.pincode || 'N/A')}</div>
                </div>
                <div class="mobile-detail-section">
                    <div class="mobile-detail-label">Distributor</div>
                    <div class="mobile-detail-value">${
                        escapeHtml(
                        user.distributor_full_name
                            ? `${user.distributor_full_name} (${user.distributor_code || 'N/A'})`
                            : (user.distributor_code || 'N/A')
                        )
                    }</div>
                </div>
                ${(user.user_type || '').toLowerCase() === 'dealer' ? `
                    <div class="mobile-detail-section">
                        <div class="mobile-detail-label">Dealer Code</div>
                        <div class="details-item"><strong>Dealer Code:</strong> <code>${escapeHtml(user.dealer_code || 'N/A')}</code></div>
                            ` : ''}
                    </div>
                ${(user.status || '').toLowerCase() === 'pending' ? `
                    <div style="display: flex; gap: 12px; margin-top: 20px;">
                        <button onclick="handleApproveReject(this, ${user.user_id}, 'approve')" class="details-btn" style="flex: 1; background: var(--success); justify-content: center;">✓ Approve</button>
                        <button onclick="handleApproveReject(this, ${user.user_id}, 'reject')" class="details-btn" style="flex: 1; background: var(--danger); justify-content: center;">✕ Reject</button>
                    </div>
                ` : ''}
            `;
            detailPage.classList.add('active');
            document.getElementById('usersView').style.overflow = 'hidden'; // Prevent background scroll
        }
        // ✅ CLOSE DETAIL PAGE
        function closeUserDetailPage() {
            const detailPage = document.getElementById('mobileDetailPage');
            detailPage.classList.remove('active');
            document.getElementById('usersView').style.overflow = 'auto'; // Re-enable scroll
        }
        // ✅ SETUP BACK BUTTON EVENT LISTENER - THIS IS THE FIX!
        function setupMobileDetailBackButton() {
            const backBtn = document.getElementById('mobileDetailBackBtn');
            if (backBtn) {
                backBtn.addEventListener('click', function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    console.log('✅ Back button clicked! Going back...');
                    closeUserDetailPage();
                });
            }
        }
        // ✅ MODIFY RENDERERS FOR MOBILE
        // ===== USER MANAGEMENT - RENDER USERS =====
        function renderUsers(filter) {
            const tbody = document.getElementById('usersTableBody');
           
            if (Object.keys(allUsersData).length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; padding: 40px;">No users found</td></tr>';
                return;
            }
            let filteredUsers = Object.entries(allUsersData);
            if (filter === 'pending') {
                filteredUsers = filteredUsers.filter(([_, u]) => (u.status || '').toLowerCase() === 'pending');
            } else if (filter === 'approved') {
                filteredUsers = filteredUsers.filter(([_, u]) => u.status === 'approved' || u.status === 'Approved');
            }
            tbody.innerHTML = filteredUsers.map(([userId, user]) => {
                // ===== DETERMINE WHICH ACTIONS TO SHOW =====
                let actionsHTML = '';
               
                if (filter === 'all') {
                    // ALL USERS: Show Edit & Delete buttons
                    actionsHTML = `
                        <div style="display: flex; gap: 8px;">
                            <button class="btn-edit" onclick="event.stopPropagation(); openEditUserModal('${userId}')" title="Edit user">
                                <i class="fas fa-edit"></i>
                            </button>
                            <button class="btn-delete" onclick="event.stopPropagation(); openDeleteConfirmModal('${userId}')" title="Delete user">
                                <i class="fas fa-trash"></i>
                            </button>
                        </div>
                    `;
                } else {
                    // PENDING & APPROVED: Show View Details button
                    actionsHTML = `
                        <button class="details-btn" onclick="event.stopPropagation(); toggleUserDetails('${userId}')" title="View details">
                            View Details
                        </button>
                        <div id="user_details_${userId}" class="details-container" style="display: none;">
                            <div class="details-grid">
                                <div class="details-item"><strong>Full Name:</strong> <code>${escapeHtml(user.full_name)}</code></div>
                                <div class("details-item"><strong>Address:</strong> <code>${escapeHtml(user.address)}</code></div>
                                <div class("details-item"><strong>Email:</strong> <code>${escapeHtml(user.email)}</code></div>
                                <div class="details-item"><strong>Phone:</strong> <code>${escapeHtml(user.phone_number || 'N/A')}</code></div>
                                <div class="details-item"><strong>Type:</strong> <code>${escapeHtml(user.user_type)}</code></div>
                                <div class="details-item"><strong>Company:</strong> <code>${escapeHtml(user.company_name || 'N/A')}</code></div>
                                <div class("details-item"><strong>GST:</strong> <code>${escapeHtml(user.gst_no || 'N/A')}</code></div>
                                <div class="details-item"><strong>Pincode:</strong> <code>${escapeHtml(user.pincode || 'N/A')}</code></div>
                                <div class="details-item"><strong>Dealer Code:</strong> <code>${escapeHtml(user.dealer_code || 'N/A')}</code></div>
                                <div class="details-item"><strong>Distributor:</strong>
                                <code>${
                                    escapeHtml(
                                    user.distributor_full_name
                                        ? `${user.distributor_full_name} (${user.distributor_code || 'N/A'})`
                                        : (user.distributor_code || 'N/A')
                                    )
                                }</code>
                                </div>
                            </div>
                            ${(user.status || '').toLowerCase() === 'pending' ? `
                                <div style="display: flex; gap: 10px; margin-top: 20px;">
                                    <button onclick="handleApproveReject(this, ${user.user_id}, 'approve')" class="details-btn" style="flex: 1; background: var(--success); justify-content: center;">✓ Approve</button>
                                    <button onclick="handleApproveReject(this, ${user.user_id}, 'reject')" class="details-btn" style="flex: 1; background: var(--danger); justify-content: center;">✕ Reject</button>
                                </div>
                            ` : ''}
                        </div>
                    `;
                }
                // ✅ CHECK SCREEN SIZE - RETURN APPROPRIATE LAYOUT
                if (window.innerWidth <= 768) {
                    if (filter === 'all') {
                        // ===== MOBILE VIEW FOR ALL USERS: 3 COLUMNS (Name, Type, Actions) =====
                        return `
                            <tr onclick="openUserDetailPage('${userId}')">
                                <td data-label="Full Name">${escapeHtml(user.full_name)}</td>
                                <td data-label="Type"><span class="type-badge">${(user.user_type || '').toLowerCase() === 'dealer' ? 'Dealer' : 'Distributor'}</span></td>
                                <td data-label="Actions">${actionsHTML}</td>
                            </tr>
                        `;
                    } else {
                        // ===== MOBILE VIEW FOR PENDING/APPROVED: 2 COLUMNS (Name, Type) + CLICKABLE =====
                        return `
                            <tr onclick="openUserDetailPage('${userId}')">
                                <td data-label="Full Name">${escapeHtml(user.full_name)}</td>
                                <td data-label="Type"><span class="type-badge">${(user.user_type || '').toLowerCase() === 'dealer' ? 'Dealer' : 'Distributor'}</span></td>
                            </tr>
                        `;
                    }
                } else {
                    // ===== LAPTOP VIEW: FULL TABLE (7 COLUMNS) =====
                    return `
                        <tr onclick="toggleUserDetails('${userId}')" style="cursor: pointer;">
                            <td data-label="Full Name">${escapeHtml(user.full_name)}</td>
                            <td data-label="Address">${escapeHtml(user.address)}</td>
                            <td data-label="Email">${escapeHtml(user.email)}</td>
                            <td data-label="Phone No">${escapeHtml(user.phone_number || 'N/A')}</td>
                            <td data-label="Company">${escapeHtml(user.company_name)}</td>
                            <td data-label="Type"><span class="type-badge">${(user.user_type || '').toLowerCase() === 'dealer' ? 'Dealer' : 'Distributor'}</span></td>
                            <td data-label="Status"><span class="status-badge" style="background: ${(user.status === 'approved' || (user.status || '').toLowerCase() === 'approved') ? 'rgba(16, 185, 129, 0.1)' : 'rgba(245, 158, 11, 0.1)'}; color: ${(user.status === 'approved' || (user.status || '').toLowerCase() === 'approved') ? '#10B981' : '#F59E0B'};">${user.status}</span></td>
                            <td data-label="Actions" onclick="event.stopPropagation();">${actionsHTML}</td>
                        </tr>
                    `;
                }
            }).join('');
        }
        function setupLogout() {
            var btn = document.getElementById('logoutBtn');
            if (btn) btn.addEventListener('click', function() {
                window.location.href = '/logout';
            });
        }

        // ===== PRINT QR CODE FUNCTIONALITY =====
        async function loadOnlineDevices() {
            try {
                console.log('📡 Loading online devices from assets table...');
                const response = await fetch('/api/assets/online-devices');
                const data = await response.json();
                
                if (data.status === 'success') {
                    onlineDevicesData = data.devices || [];
                    console.log(`✅ Loaded ${onlineDevicesData.length} online devices`);
                    return onlineDevicesData;
                } else {
                    console.error('❌ Failed to load online devices:', data.message);
                    onlineDevicesData = [];
                    return [];
                }
            } catch (error) {
                console.error('❌ Error loading online devices:', error);
                onlineDevicesData = [];
                return [];
            }
        }
        
        async function openPrintQRModal() {
            try {
                // Clear previous data
                document.getElementById('printDeviceSelect').innerHTML = '<option value="">-- Select a device --</option><option value="loading" disabled>Loading online devices...</option>';
                document.getElementById('devicePrintInfo').style.display = 'none';
                document.getElementById('printStatusMessage').style.display = 'none';
                
                // Load online devices from assets table
                const devices = await loadOnlineDevices();
                
                const select = document.getElementById('printDeviceSelect');
                select.innerHTML = '<option value="">-- Select a device --</option>';
                
                if (devices.length === 0) {
                    select.innerHTML = '<option value="" disabled>No online devices found in assets table</option>';
                    showNotification('warning', 'No online devices found. Please ensure devices are ACTIVE/ONLINE in system_information.');
                } else {
                    devices.forEach(device => {
                        const option = document.createElement('option');
                        option.value = device.serial;
                        option.textContent = `${device.serial} - ${device.hostname || 'No hostname'} (${device.system_status})`;
                        option.dataset.device = JSON.stringify(device);
                        select.appendChild(option);
                    });
                }
                
                // Load printers if available
                await loadPrinters();
                
                // Show modal
                document.getElementById('printQRModal').classList.add('show');
            } catch (error) {
                console.error('Error opening print QR modal:', error);
                showNotification('error', 'Failed to load online devices. Please try again.');
            }
        }
        
        function closePrintQRModal() {
            document.getElementById('printQRModal').classList.remove('show');
            document.getElementById('devicePrintInfo').style.display = 'none';
            document.getElementById('printStatusMessage').style.display = 'none';
        }
        
        function onPrintDeviceSelected() {
            const select = document.getElementById('printDeviceSelect');
            const deviceInfo = document.getElementById('devicePrintInfo');
            
            if (select.value) {
                try {
                    const device = JSON.parse(select.options[select.selectedIndex].dataset.device);
                    
                    // Update display with assets table data
                    document.getElementById('printSerial').textContent = device.serial || 'N/A';
                    document.getElementById('printHostname').textContent = device.hostname || 'N/A';
                    
                    // Status with color
                    const status = device.system_status || 'UNKNOWN';
                    const isOnline = status === 'ACTIVE' || status === 'ONLINE';
                    const statusColor = isOnline ? 'var(--success)' : 'var(--danger)';
                    const statusBg = isOnline ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)';
                    
                    const statusSpan = document.getElementById('printStatus');
                    statusSpan.innerHTML = `<span class="status-badge" style="background: ${statusBg}; color: ${statusColor}; padding: 4px 8px; border-radius: 4px;">${status}</span>`;
                    
                    // QR Status
                    const qrStatus = device.qr_status || 'NOT_PRINTED';
                    const qrStatusColor = qrStatus === 'PRINTED' ? 'var(--success)' : 
                                          qrStatus === 'GENERATED' ? 'var(--warning)' : 'var(--danger)';
                    const qrStatusBg = qrStatus === 'PRINTED' ? 'rgba(16, 185, 129, 0.1)' : 
                                       qrStatus === 'GENERATED' ? 'rgba(245, 158, 11, 0.1)' : 'rgba(239, 68, 68, 0.1)';
                    
                    const qrStatusSpan = document.getElementById('printQrStatus');
                    qrStatusSpan.innerHTML = `<span class="status-badge" style="background: ${qrStatusBg}; color: ${qrStatusColor}; padding: 4px 8px; border-radius: 4px;">${qrStatus}</span>`;
                    
                    // Show device info
                    deviceInfo.style.display = 'block';
                    
                    // Generate preview if in preview mode
                    if (document.querySelector('input[name="printMode"]:checked').value === 'preview') {
                        generateQRPreview(device.serial);
                    }
                } catch (error) {
                    console.error('Error parsing device data:', error);
                }
            } else {
                deviceInfo.style.display = 'none';
            }
        }
        
        function togglePrintOptions() {
            const mode = document.querySelector('input[name="printMode"]:checked').value;
            
            // Hide all options first
            document.querySelectorAll('.print-options').forEach(el => {
                el.style.display = 'none';
            });
            
            // Show selected option
            document.getElementById(`${mode}PrinterOptions`).style.display = 'block';
            
            // Generate preview if in preview mode and device selected
            if (mode === 'preview') {
                const select = document.getElementById('printDeviceSelect');
                if (select.value) {
                    try {
                        const device = JSON.parse(select.options[select.selectedIndex].dataset.device);
                        generateQRPreview(device.serial);
                    } catch (error) {
                        console.error('Error generating preview:', error);
                    }
                }
            }
        }
        
        async function loadPrinters() {
            try {
                const response = await fetch('/api/assets/list-printers');
                const data = await response.json();
                
                const select = document.getElementById('printPrinterSelect');
                if (data.status === 'success' && data.printers.length > 0) {
                    select.innerHTML = '';
                    data.printers.forEach(printer => {
                        const option = document.createElement('option');
                        option.value = printer;
                        option.textContent = printer;
                        select.appendChild(option);
                    });
                } else {
                    select.innerHTML = '<option value="">No printers found</option>';
                }
            } catch (error) {
                console.error('Error loading printers:', error);
            }
        }
        
        function generateQRPreview(serial) {
            const prefix = document.getElementById('printPrefix').value.trim() || 'DEV';
            const digits = parseInt(document.getElementById('printDigits').value) || 6;
            
            // Generate code based on serial and prefix
            const code = `${prefix}-${serial.substring(0, digits).padEnd(digits, '0')}`;
            
            // Update preview text
            document.getElementById('previewCode').textContent = code;
            
            // Clear previous QR code
            const qrContainer = document.getElementById('qrPreview');
            qrContainer.innerHTML = '';
            
            // Generate new QR code using QRCode.js
            if (typeof QRCode !== 'undefined') {
                new QRCode(qrContainer, {
                    text: serial, // Using serial as QR content
                    width: 150,
                    height: 150,
                    colorDark: "#000000",
                    colorLight: "#ffffff",
                    correctLevel: QRCode.CorrectLevel.H
                });
            } else {
                qrContainer.innerHTML = `<div style="color: var(--gray-400); padding: 20px;">QR Code preview requires QRCode.js library</div>`;
            }
        }
        
        async function printQRCode() {
            const select = document.getElementById('printDeviceSelect');
            if (!select.value) {
                showNotification('error', 'Please select a device first.');
                return;
            }

            try {
                const device = JSON.parse(select.options[select.selectedIndex].dataset.device);
                const serial = device.serial;

                const printModeUI = document.querySelector('input[name="printMode"]:checked').value; // could be preview/windows/network
                const dryRunUI = document.getElementById('printDryRun').checked;

                // FIX 1: backend expects print.mode none/windows/network (not preview)
                const apiPrintMode = (printModeUI === 'preview') ? 'none' : printModeUI;

                // FIX 2: backend expects rows[]
                const payload = {
                    rows: [
                        { serial: serial }
                    ],
                    print: {
                        mode: apiPrintMode,
                        printer: document.getElementById('printPrinterSelect').value,
                        net_ip: document.getElementById('printNetworkIP').value,
                        net_port: parseInt(document.getElementById('printNetworkPort').value) || 9100
                    },
                    // If preview mode, force dry_run
                    dry_run: (printModeUI === 'preview') ? true : dryRunUI,
                    qr_template: "{serial}"
                };

                // Disable print button and show loading
                const printBtn = document.getElementById('printBtn');
                const originalText = printBtn.innerHTML;
                printBtn.disabled = true;
                printBtn.innerHTML = '<span class="spinner"></span> Printing.';

                const statusMessage = document.getElementById('printStatusMessage');
                statusMessage.style.display = 'none';

                const response = await fetch('/api/assets/generate-and-print', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                const result = await response.json();

                // Restore button
                printBtn.disabled = false;
                printBtn.innerHTML = originalText;

                // Show result
                statusMessage.style.display = 'block';

                if (result.status === 'success') {
                    statusMessage.innerHTML = `
                        <div style="display: flex; align-items: center; gap: 8px; color: var(--success);">
                            <i class="fas fa-check-circle"></i>
                            <span>${result.print_message}</span>
                        </div>
                    `;
                    statusMessage.style.background = 'rgba(16, 185, 129, 0.1)';
                    statusMessage.style.borderLeft = '4px solid var(--success)';

                    // Update QR status in UI
                    const qrStatusSpan = document.getElementById('printQrStatus');
                    if (qrStatusSpan) {
                        qrStatusSpan.innerHTML =
                            `<span class="status-badge" style="background: rgba(16, 185, 129, 0.1); color: var(--success); padding: 4px 8px; border-radius: 4px;">PRINTED</span>`;
                    }

                    if (payload.dry_run) {
                        showNotification('info', 'Dry run completed. QR code generated but not printed.');
                    } else {
                        showNotification('success', 'QR code printed successfully!');
                    }
                } else {
                    statusMessage.innerHTML = `
                        <div style="display: flex; align-items: center; gap: 8px; color: var(--danger);">
                            <i class="fas fa-exclamation-circle"></i>
                            <span>${result.message || 'Failed to print QR code'}</span>
                        </div>
                    `;
                    statusMessage.style.background = 'rgba(239, 68, 68, 0.1)';
                    statusMessage.style.borderLeft = '4px solid var(--danger)';

                    if (response.status === 409) {
                        showNotification('error', 'Cannot print: Device is not ACTIVE/ONLINE in system_information.');
                    } else {
                        showNotification('error', result.message || 'Failed to print QR code');
                    }
                }
            } catch (error) {
                console.error('Error printing QR code:', error);

                const printBtn = document.getElementById('printBtn');
                printBtn.disabled = false;
                printBtn.innerHTML = '<i class="fas fa-print"></i> Print QR Code';

                const statusMessage = document.getElementById('printStatusMessage');
                statusMessage.style.display = 'block';
                statusMessage.innerHTML = `
                    <div style="display: flex; align-items: center; gap: 8px; color: var(--danger);">
                        <i class="fas fa-exclamation-circle"></i>
                        <span>Error: ${error.message}</span>
                    </div>
                `;
                statusMessage.style.background = 'rgba(239, 68, 68, 0.1)';
                statusMessage.style.borderLeft = '4px solid var(--danger)';

                showNotification('error', 'Failed to print QR code');
            }
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
