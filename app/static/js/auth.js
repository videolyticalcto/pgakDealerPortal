    // ==================== GLOBAL VARIABLES ====================
    let currentVerificationType = null;  // 'phone' or 'email'
    let currentInputId = null;
    let currentButton = null;
    let otpTimerInterval = null;
    let otpTimerSeconds = 300;
    let canResendOTP = false;
    let verifiedData = {};  // { 'phone': bool, 'email': bool }
    let pincodeDebounceTimer = null;
    let selectedPincodeData = null;
    let previousUserType = null;  // Track previous user type

    // ==================== HANDLE USER TYPE CHANGE ====================
    // function handleUserTypeChange() {
    //     const userType = document.getElementById('user_type_select').value;
        
    //     // If user type actually changed, clear all form data
    //     if (userType !== previousUserType && userType !== '') {
    //         clearFormData();
    //         previousUserType = userType;
    //     }
        
    //     updatePincodeLabel();
    //     toggleDistributorCode();
    // }

    // ==================== CLEAR ALL FORM DATA ====================
    function clearFormData() {
        // Clear all input fields
        document.getElementById('dealer_full_name').value = '';
        document.getElementById('dealer_address').value = '';
        document.getElementById('dealer_phone').value = '';
        document.getElementById('dealer_email').value = '';
        document.getElementById('dealer_gst').value = '';
        document.getElementById('dealer_company').value = '';
        document.getElementById('dealer_pincode').value = '';
        document.getElementById('dealer_code').value = '';
        document.getElementById('dealer_password').value = '';
        document.getElementById('dealer_confirm_password').value = '';
        
        // Reset verification status
        verifiedData = {};
        selectedPincodeData = null;
        
        // Reset verify buttons
        const verifyBtns = document.querySelectorAll('.verify-btn');
        verifyBtns.forEach(btn => {
            btn.textContent = 'Verify';
            btn.classList.remove('verified');
            btn.disabled = false;
        });
        
        // Hide pincode info
        document.getElementById('pincode-info').classList.remove('show');
        document.getElementById('pincode-dropdown').classList.remove('show');
    }

    // ==================== TOGGLE DISTRIBUTOR CODE ====================
    function toggleDistributorCode() {
        const userType = document.getElementById('user_type_select').value;
        const distributorCodeGroup = document.getElementById('distributor-code-group');
        const distributorCodeInput = document.getElementById('dealer_code');
        
        if (userType === 'dealer') {
            distributorCodeGroup.classList.add('show');
            distributorCodeInput.disabled = false;
            distributorCodeInput.required = false;
            distributorCodeInput.placeholder = 'Enter the distributor code you belong to (optional)';
        } else {
            distributorCodeGroup.classList.remove('show');
            distributorCodeInput.disabled = false;
            distributorCodeInput.required = false;
            distributorCodeInput.value = '';
        }
    }

    // ==================== PINCODE AUTOCOMPLETE ====================
    async function handlePincodeInput(event) {
        const input = event.target.value.trim();
        const dropdown = document.getElementById('pincode-dropdown');
        const infoDiv = document.getElementById('pincode-info');

        const userType = document.getElementById('user_type_select').value;
        if (userType === 'distributor') {
            const lastCommaIndex = input.lastIndexOf(',');
            let currentPincode = '';
            
            if (lastCommaIndex !== -1) {
                currentPincode = input.substring(lastCommaIndex + 1).trim();
            } else {
                currentPincode = input;
            }

            infoDiv.classList.remove('show');

            if (currentPincode.length === 0) {
                dropdown.classList.remove('show');
                return;
            }

            clearTimeout(pincodeDebounceTimer);
            pincodeDebounceTimer = setTimeout(async () => {
                await fetchPincodeData(currentPincode);
            }, 300);
        } else {
            selectedPincodeData = null;
            infoDiv.classList.remove('show');

            if (input.length === 0) {
                dropdown.classList.remove('show');
                return;
            }

            clearTimeout(pincodeDebounceTimer);
            pincodeDebounceTimer = setTimeout(async () => {
                await fetchPincodeData(input);
            }, 300);
        }
    }

    async function fetchPincodeData(pincodeInput) {
        const dropdown = document.getElementById('pincode-dropdown');

        try {
            dropdown.innerHTML = '<div class="dropdown-loading">Searching pincodes...</div>';
            dropdown.classList.add('show');

            const response = await fetch('/api/pincode-lookup', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pincode: pincodeInput })
            });

            const data = await response.json();

            if (data.status === 'success' && data.data.length > 0) {
                displayPincodeDropdown(data.data);
            } else {
                dropdown.innerHTML = '<div class="dropdown-empty">No pincodes found</div>';
            }
        } catch (error) {
            console.error('Pincode lookup error:', error);
            dropdown.innerHTML = '<div class="dropdown-empty">Error loading pincodes</div>';
        }
    }

    function displayPincodeDropdown(pincodes) {
        const dropdown = document.getElementById('pincode-dropdown');
        dropdown.innerHTML = '';

        pincodes.forEach(pincode => {
            const item = document.createElement('div');
            item.className = 'dropdown-item';
            item.innerHTML = `
                <div class="dropdown-item-code">${pincode.pincode}</div>
                <div class="dropdown-item-details">
                    ${pincode.post_office} • ${pincode.districts_name} • ${pincode.state}
                </div>
            `;
            item.onclick = () => selectPincode(pincode);
            dropdown.appendChild(item);
        });

        dropdown.classList.add('show');
    }

    async function selectPincode(pincodeData) {
        const input = document.getElementById('dealer_pincode');
        const dropdown = document.getElementById('pincode-dropdown');
        const infoDiv = document.getElementById('pincode-info');
        const userType = document.getElementById('user_type_select').value;

        if (userType === 'distributor') {
            const currentValue = input.value.trim();
            const lastCommaIndex = currentValue.lastIndexOf(',');
            
            let newValue = '';
            if (lastCommaIndex !== -1) {
                newValue = currentValue.substring(0, lastCommaIndex + 1) + pincodeData.pincode;
            } else {
                newValue = pincodeData.pincode;
            }
            
            input.value = newValue;
            dropdown.classList.remove('show');
            
            displayPincodeInfo(pincodeData);
            setTimeout(() => {
                infoDiv.classList.remove('show');
                input.focus();
            }, 1000);
        } else {
            input.value = pincodeData.pincode;
            selectedPincodeData = pincodeData;
            dropdown.classList.remove('show');

            displayPincodeInfo(pincodeData);
        }
    }

    function displayPincodeInfo(data) {
        const infoDiv = document.getElementById('pincode-info');
        infoDiv.innerHTML = `
            <div class="pincode-info-row">
                <span class="pincode-info-label">Pincode:</span>
                <span>${data.pincode}</span>
            </div>
            <div class="pincode-info-row">
                <span class="pincode-info-label">Post Office:</span>
                <span>${data.post_office}</span>
            </div>
            <div class="pincode-info-row">
                <span class="pincode-info-label">City:</span>
                <span>${data.city || 'N/A'}</span>
            </div>
            <div class="pincode-info-row">
                <span class="pincode-info-label">District:</span>
                <span>${data.districts_name}</span>
            </div>
            <div class="pincode-info-row">
                <span class="pincode-info-label">State:</span>
                <span>${data.state}</span>
            </div>
        `;
        infoDiv.classList.add('show');
    }

    document.addEventListener('click', function(event) {
        const wrapper = document.querySelector('.pincode-input-wrapper');
        if (wrapper && !wrapper.contains(event.target)) {
            document.getElementById('pincode-dropdown').classList.remove('show');
        }
    });

    // ==================== FORM SWITCHING ====================
    function switchForm(formType) {
        const loginSection = document.getElementById('login-section');
        const signupSection = document.getElementById('signup-section');
        const toggleNote = document.getElementById('toggle-note');

        if (formType === 'login') {
            loginSection.classList.add('active');
            signupSection.classList.remove('active');
            toggleNote.innerHTML = "Don't have an account? <a onclick=\"switchForm('signup')\">Sign up here</a>";
        } else {
            loginSection.classList.remove('active');
            signupSection.classList.add('active');
            toggleNote.innerHTML = "Already have an account? <a onclick=\"switchForm('login')\">Login here</a>";
        }
    }

    function updatePincodeLabel() {
        const userType = document.getElementById('user_type_select').value;
        const label = document.getElementById('pincode_label');
        const input = document.getElementById('dealer_pincode');
        
        if (userType === 'distributor') {
            label.innerHTML = 'Pincodes (Separate with commas) <span class="required-star">*</span>';
            input.placeholder = 'Enter pincodes (comma separated)';
        } else {
            label.innerHTML = 'Pincode <span class="required-star">*</span>';
            input.placeholder = 'Enter your pincode';
        }
    }

    // ==================== LOGIN FORM ====================
    async function handleLoginSubmit(event) {
        event.preventDefault();

        const form = document.getElementById('login-form');
        const loginBtn = document.getElementById('login-btn');
        const contact = document.getElementById('login_contact').value.trim();
        const password = document.getElementById('login_password').value;

        if (!contact || !password) {
            alert('Please enter email/phone and password.');
            return;
        }

        loginBtn.disabled = true;
        loginBtn.textContent = 'Logging in...';

        try {
            const formData = new FormData(form);

            const response = await fetch('/login', {
                method: 'POST',
                body: formData
            });

            const data = await response.json();

            if (data.status === 'success') {
                
                window.location.href = data.redirect;
            } else {
                alert(data.message || 'Login failed. Please try again.');
            }
        } catch (error) {
            console.error('Error:', error);
            alert('Network error. Please try again.');
        } finally {
            loginBtn.disabled = false;
            loginBtn.textContent = 'Login';
        }
    }

    // ==================== SIGNUP FORM (UNIFIED - DEALER & DISTRIBUTOR) ====================
    async function handleSignupSubmit(event) {
        event.preventDefault();

        const userType = document.getElementById('user_type_select').value;

        if (!userType) {
            alert('Please select an account type (Dealer or Distributor)');
            return;
        }

        // Check both phone and email verification
        if (!verifiedData['phone'] || !verifiedData['email']) {
            alert('Please verify both phone number and email address.');
            return;
        }

        if (userType === 'distributor') {
            const pincodeInput = document.getElementById('dealer_pincode').value.trim();
            if (!pincodeInput) {
                alert('Please enter pincodes.');
                return;
            }
        } else {
            if (!selectedPincodeData) {
                alert('Please select a pincode from the dropdown.');
                return;
            }
        }

        const form = document.getElementById('dealer-signup-form');
        const pwd = (document.getElementById('dealer_password') || {}).value || '';
        const confirmPwd = (document.getElementById('dealer_confirm_password') || {}).value || '';

        if (pwd !== confirmPwd) { alert('Passwords do not match'); return; }
        if (pwd.length < 8) { alert('Password must be at least 8 characters long'); return; }
        if (!/[A-Z]/.test(pwd)) { alert('Password must contain at least one uppercase letter'); return; }
        if (!/[a-z]/.test(pwd)) { alert('Password must contain at least one lowercase letter'); return; }
        if (!/[0-9]/.test(pwd)) { alert('Password must contain at least one number'); return; }
        if (!/[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?`~]/.test(pwd)) { alert('Password must contain at least one special character'); return; }

        const submitBtn = form.querySelector('button[type="submit"]');

        submitBtn.disabled = true;
        submitBtn.textContent = 'Signing up...';

        try {
            const formData = new FormData(form);

            const response = await fetch('/signup', {
                method: 'POST',
                body: formData
            });

            const data = await response.json();

            if (response.ok && data.status === 'success') {
                alert(data.message || 'Sign up successful! Your account is awaiting admin approval.');
                form.reset();
                selectedPincodeData = null;
                verifiedData = {};
                previousUserType = null;
                
                const verifyBtns = form.querySelectorAll('.verify-btn');
                verifyBtns.forEach(btn => {
                    btn.textContent = 'Verify';
                    btn.classList.remove('verified');
                    btn.disabled = false;
                });

                // Safe null-check before accessing classList — element may be hidden/absent
                const pincodeInfo = document.getElementById('pincode-info');
                const distributorCodeGroup = document.getElementById('distributor-code-group');
                if (pincodeInfo) pincodeInfo.classList.remove('show');
                if (distributorCodeGroup) distributorCodeGroup.classList.remove('show');
                
                switchForm('login');
            } else {
                alert(data.message || 'Sign up failed. Please try again.');
            }
        } catch (error) {
            console.error('Error:', error);
            alert('Network error. Please try again.');
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Sign Up';
        }
    }

    // ==================== ENHANCED OTP FUNCTIONALITY ====================
    async function sendOTP(type, inputId, button) {
        const input = document.getElementById(inputId);
        const value = input.value.trim();

        if (!value) {
            alert(`Please enter a ${type === 'phone' ? 'phone number' : 'email'}`);
            return;
        }

        // Validation
        if (type === 'phone') {
            const digitsOnly = value.replace(/\D/g, '');
            if (digitsOnly.length < 10) {
                alert('Please enter a valid 10-digit phone number');
                return;
            }
        } else if (type === 'email') {
            const emailRegex = /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/;
            if (!emailRegex.test(value)) {
                alert('Please enter a valid email address');
                return;
            }
        }

        button.classList.add('loading');
        button.disabled = true;
        button.textContent = 'Sending...';

        try {
            const endpoint = type === 'phone' ? '/send-otp' : '/send-otp-email';
            const payload = type === 'phone' 
                ? { phone: value }
                : { email: value };

            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const data = await response.json();

            if (response.ok && data.status === 'success') {
                currentVerificationType = type;
                currentInputId = inputId;
                currentButton = button;
                openOTPModal(type, value);
                resetOTPTimer();
            } else {
                alert(data.message || `Failed to send OTP. Please try again.`);
                button.classList.remove('loading');
                button.disabled = false;
                button.textContent = 'Verify';
            }
        } catch (error) {
            console.error('Error:', error);
            alert('Network error. Please try again.');
            button.classList.remove('loading');
            button.disabled = false;
            button.textContent = 'Verify';
        }
    }

    function openOTPModal(type, value) {
        const modal = document.getElementById('otpModal');
        const title = document.getElementById('otpModalTitle');
        const message = document.getElementById('otpMessage');
        const typeLabel = type === 'phone' ? 'phone number' : 'email address';
        
        title.textContent = `Verify Your ${typeLabel.split(' ')[1].charAt(0).toUpperCase() + typeLabel.split(' ')[1].slice(1)}`;
        message.textContent = `Enter the 4-digit OTP sent to your ${typeLabel}`;
        
        modal.classList.add('show');
        
        const inputs = document.querySelectorAll('.otp-input');
        inputs[0].focus();

        document.getElementById('errorMessage').textContent = '';
        document.getElementById('successMessage').textContent = '';

        inputs.forEach(input => {
            input.value = '';
            input.classList.remove('error');
        });

        setupOTPInputs();
    }

    function closeOTPModal() {
        const modal = document.getElementById('otpModal');
        modal.classList.remove('show');
        clearInterval(otpTimerInterval);

        if (currentButton) {
            currentButton.classList.remove('loading');
            currentButton.disabled = false;
            currentButton.textContent = 'Verify';
        }
    }

    function setupOTPInputs() {
        const inputs = document.querySelectorAll('.otp-input');
        
        inputs.forEach((input, index) => {
            input.addEventListener('input', (e) => {
                e.target.value = e.target.value.replace(/[^0-9]/g, '');
                
                if (e.target.value && index < inputs.length - 1) {
                    inputs[index + 1].focus();
                }
            });

            input.addEventListener('keydown', (e) => {
                if (e.key === 'Backspace' && !input.value && index > 0) {
                    inputs[index - 1].focus();
                }
            });

            input.addEventListener('paste', (e) => {
                e.preventDefault();
                const pasteData = e.clipboardData.getData('text');
                const digits = pasteData.replace(/[^0-9]/g, '').split('');
                
                digits.forEach((digit, i) => {
                    if (i + index < inputs.length) {
                        inputs[i + index].value = digit;
                    }
                });
                
                if (digits.length > 0) {
                    const nextIndex = Math.min(index + digits.length - 1, inputs.length - 1);
                    inputs[nextIndex].focus();
                }
            });
        });
    }

    async function verifyOTP() {
        const inputs = document.querySelectorAll('.otp-input');
        const otp = Array.from(inputs).map(input => input.value).join('');
        const errorMsg = document.getElementById('errorMessage');

        if (otp.length !== 4) {
            errorMsg.textContent = 'Please enter a 4-digit OTP';
            inputs.forEach(input => input.classList.add('error'));
            return;
        }

        const submitBtn = document.querySelector('.modal-btn-submit');
        submitBtn.disabled = true;
        submitBtn.textContent = 'Verifying...';
        errorMsg.textContent = '';

        try {
            const endpoint = currentVerificationType === 'phone' ? '/verify-otp' : '/verify-otp-email';
            const fieldName = currentVerificationType === 'phone' ? 'phone' : 'email';
            const inputElement = document.getElementById(currentInputId);
            const fieldValue = inputElement.value.trim();

            const payload = new URLSearchParams();
            payload.append(fieldName, fieldValue);
            payload.append('otp', otp);

            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: payload
            });

            const data = await response.json();

            if (response.ok && data.status === 'success') {
                const successMsg = document.getElementById('successMessage');
                successMsg.textContent = '✓ Verified successfully!';
                inputs.forEach(input => input.classList.remove('error'));
                
                // Mark this verification type as verified
                verifiedData[currentVerificationType] = true;

                if (currentButton) {
                    currentButton.textContent = 'Verified ✓';
                    currentButton.classList.add('verified');
                    currentButton.disabled = true;
                }

                setTimeout(() => {
                    closeOTPModal();
                }, 1500);
            } else {
                errorMsg.textContent = data.message || 'Invalid OTP. Please try again.';
                inputs.forEach(input => {
                    input.classList.add('error');
                    input.value = '';
                });
                inputs[0].focus();
            }
        } catch (error) {
            console.error('Error:', error);
            errorMsg.textContent = 'Network error. Please try again.';
            inputs.forEach(input => input.classList.add('error'));
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Verify OTP';
        }
    }

    function resetOTPTimer() {
        otpTimerSeconds = 300;
        canResendOTP = false;
        
        const resendLink = document.getElementById('resendLink');
        resendLink.classList.add('disabled');

        if (otpTimerInterval) {
            clearInterval(otpTimerInterval);
        }

        otpTimerInterval = setInterval(() => {
            otpTimerSeconds--;
            document.getElementById('timerValue').textContent = otpTimerSeconds;

            const timerDiv = document.getElementById('otpTimer');
            
            if (otpTimerSeconds <= 60) {
                timerDiv.classList.add('warning');
            }
            
            if (otpTimerSeconds <= 0) {
                clearInterval(otpTimerInterval);
                timerDiv.classList.remove('warning');
                timerDiv.classList.add('expired');
                timerDiv.innerHTML = '<span class="expired">OTP expired</span>';
                canResendOTP = true;
                resendLink.classList.remove('disabled');
            }
        }, 1000);
    }

    async function resendOTP() {
        if (!canResendOTP || !currentInputId) return;

        const resendLink = document.getElementById('resendLink');
        const button = currentButton;
        resendLink.textContent = 'Sending...';
        resendLink.classList.add('disabled');

        try {
            const inputElement = document.getElementById(currentInputId);
            const value = inputElement.value.trim();
            const endpoint = currentVerificationType === 'phone' ? '/send-otp' : '/send-otp-email';
            const payload = currentVerificationType === 'phone'
                ? { phone: value }
                : { email: value };

            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const data = await response.json();

            if (response.ok && data.status === 'success') {
                resetOTPTimer();
                
                const inputs = document.querySelectorAll('.otp-input');
                inputs.forEach(input => {
                    input.value = '';
                    input.classList.remove('error');
                });
                
                document.getElementById('errorMessage').textContent = '';
                document.getElementById('successMessage').textContent = 'OTP resent successfully!';
                inputs[0].focus();

                setTimeout(() => {
                    document.getElementById('successMessage').textContent = '';
                }, 2000);
            } else {
                resendLink.textContent = 'Resend OTP';
                alert(data.message || 'Failed to resend OTP');
            }
        } catch (error) {
            console.error('Error:', error);
            resendLink.textContent = 'Resend OTP';
            alert('Network error. Please try again.');
        }
    }

    // ==================== PASSWORD EYE TOGGLE ====================
    function togglePassword(inputId, btn) {
        const input = document.getElementById(inputId);
        if (!input) return;

        if (input.type === 'password') {
            input.type = 'text';
            btn.textContent = '👁';
            btn.title = 'Hide Password';
        } else {
            input.type = 'password';
            btn.textContent = '🔒';
            btn.title = 'Show Password';
        }
        // Keep focus on input after toggle
        input.focus();
    }

