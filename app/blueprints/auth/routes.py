"""
Auth blueprint routes: index, login, logout, signup, OTP, pincode, current-user.
"""

import json
import logging
import os
import random
import re
import traceback

import psycopg2
import requests
from datetime import datetime, timedelta, timezone
from flask import request, jsonify, render_template, redirect, url_for, session
from psycopg2.extras import RealDictCursor

from app.blueprints.auth import auth_bp
from app.config import Config
from app.extensions import pwd_context
from app.utils.db import get_db_conn
from app.utils.email import send_email, send_email_otp
from app.utils.helpers import (
    generate_unique_code,
    code_exists_in_db,
    get_unique_code,
    verify_password,
    validate_password,
    is_valid_email,
)

logger = logging.getLogger(__name__)

OTP_VALID_MINUTES = 5


# ── Helper: OTP generation ───────────────────────────────────────────────

def generate_otp(length: int = 4) -> str:
    return "".join(random.choice("0123456789") for _ in range(length))


# ── Helper: admin email notification for signups ─────────────────────────

def get_admin_emails_from_db() -> list:
    """Fetch admin emails from database."""
    emails = []
    conn = psycopg2.connect(**Config.DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT email
                FROM user_signups
                WHERE LOWER(user_type) = 'admin'
                  AND (status IS NULL OR status = 'Approved')
                  AND email IS NOT NULL AND TRIM(email) <> ''
            """)
            rows = cur.fetchall()
            for (em,) in rows:
                em = (em or "").strip()
                if em:
                    emails.append(em)
    finally:
        conn.close()
    return emails


def get_admin_emails() -> list:
    """Prefer DB admin emails. Fallback to ENV."""
    try:
        db_admins = get_admin_emails_from_db()
        if db_admins:
            return db_admins
    except Exception as e:
        logger.exception("DB admin email fetch failed: %s", e)

    bulk = (os.getenv("ADMIN_EMAILS", "") or "").strip()
    single = (os.getenv("ADMIN_EMAIL", "") or "").strip()
    emails = []
    if bulk:
        emails.extend([e.strip() for e in bulk.split(",") if e.strip()])
    if single and single not in emails:
        emails.append(single)
    return emails


def notify_admin_new_signup(user_type: str, full_name: str, address: str, email: str, phone: str,
                            gst_no: str, company_name: str, pincode: str,
                            distributor_code: str | None, dealer_code: str | None,
                            user_id: int) -> None:
    admins = get_admin_emails()

    if not admins:
        logger.warning("No admin emails found in DB or ENV. Skipping notify.")
        return

    user_type_clean = (user_type or "").strip().lower()
    subject = f"New {user_type_clean.title()} Signup Request (Pending Approval) - UserID {user_id}"

    body = (
        f"Hello Admin,\n\n"
        f"A new signup request has been received and is awaiting approval.\n\n"
        f"User Type     : {user_type_clean}\n"
        f"User ID       : {user_id}\n"
        f"Full Name     : {full_name}\n"
        f"Address       : {address}\n"
        f"Email         : {email}\n"
        f"Phone         : {phone}\n"
        f"Company Name  : {company_name}\n"
        f"GST No        : {gst_no}\n"
        f"Pincode       : {pincode}\n"
    )

    if user_type_clean == "dealer":
        body += f"Distributor Code Entered : {distributor_code or '(not provided)'}\n"
        body += f"Internal Dealer Code     : {dealer_code or '(not generated)'}\n"

    if user_type_clean == "distributor":
        body += f"Generated Distributor Code : {distributor_code or '(not generated)'}\n"

    body += (
        f"\nPlease login to Admin Dashboard to Approve/Reject.\n\n"
        f"Regards,\n"
        f"PGAK System\n"
    )

    for admin_email in admins:
        send_email(admin_email, subject, body)


# =========================================================================
# ROUTES
# =========================================================================

@auth_bp.route("/")
def index():
    return render_template("auth/sign_up.html", server_ip=request.host.split(":")[0])


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("auth/sign_up.html")

    if request.method == "POST":
        contact = (request.form.get("contact") or "").strip()
        password = request.form.get("password") or ""

        if not contact or not password:
            return jsonify({"status": "error", "message": "Please enter email/phone and password"}), 400

        try:
            with psycopg2.connect(**Config.DB_CONFIG) as conn:
                with conn.cursor() as cur:
                    if "@" in contact:
                        cur.execute("""
                            SELECT user_id, user_type, full_name, address, email, password, status, dealer_code
                            FROM user_signups
                            WHERE email = %s
                            LIMIT 1
                        """, (contact,))
                    else:
                        cur.execute("""
                            SELECT user_id, user_type, full_name, address, email, password, status, dealer_code
                            FROM user_signups
                            WHERE phone_number = %s
                            LIMIT 1
                        """, (contact,))

                    user = cur.fetchone()

                    if not user:
                        return jsonify({"status": "error", "message": "Invalid email/phone or password"}), 401

                    user_id, user_type, full_name, address, email, db_hashed_password, status, dealer_code = user

                    try:
                        if not verify_password(password, db_hashed_password):
                            return jsonify({"status": "error", "message": "Invalid email/phone or password"}), 401
                    except ValueError:
                        return jsonify({"status": "error", "message": "Invalid email/phone or password"}), 401

                    if (status or "").lower() == "pending":
                        return jsonify({"status": "error", "message": "Your account is pending admin approval"}), 403
                    elif (status or "").lower() == "rejected":
                        return jsonify({"status": "error", "message": "Your account has been rejected"}), 403
                    elif (status or "").lower() != "approved":
                        return jsonify({"status": "error", "message": "Your account is not approved yet"}), 403

                    # Store basic session data
                    session["user_type"] = user_type
                    session["user_id"] = user_id
                    session["full_name"] = full_name
                    session["email"] = email
                    session["dealer_code"] = dealer_code

                    # Session permanent (24 hrs globally set)
                    session.permanent = True

                    # For dealers, store credentials for re-authentication
                    external_token_success = False
                    if user_type.lower() == "dealer" and dealer_code:
                        session["dealer_email"] = email
                        session["dealer_password"] = password
                        session["dealer_code"] = dealer_code

                        try:
                            print(f"Getting initial access token for dealer: {email}")

                            external_login_url = Config.EXTERNAL_AUTH_LOGIN
                            external_login_payload = {
                                "email": email,
                                "password": password,
                                "dealer_code": dealer_code
                            }

                            ext_response = requests.post(
                                external_login_url,
                                json=external_login_payload,
                                timeout=15,
                                verify=True
                            )

                            if ext_response.status_code == 200:
                                ext_data = ext_response.json()
                                access_token = ext_data.get("access_token")

                                if access_token:
                                    session["external_access_token"] = access_token
                                    external_token_success = True
                                    print(f"External API authentication successful for dealer {email}")
                                else:
                                    print(f"No access_token in response: {ext_data}")
                            else:
                                print(f"External API login failed: {ext_response.status_code}")

                        except requests.exceptions.Timeout:
                            print(f"External API timeout for dealer {email}")
                        except requests.exceptions.ConnectionError as e:
                            print(f"External API connection error: {e}")
                        except Exception as e:
                            print(f"External API error: {e}")
                            traceback.print_exc()

                    # Mark session as modified
                    session.modified = True

                    # Update login status in DB
                    cur.execute("""
                        UPDATE user_signups
                        SET is_login = TRUE
                        WHERE user_id = %s
                    """, (session["user_id"],))
                    conn.commit()

                    # Determine redirect URL
                    redirect_url = url_for("admin.dashboard")
                    if user_type.lower() == "dealer":
                        redirect_url = url_for("dealer.dashboard")
                    elif user_type.lower() == "distributor":
                        redirect_url = url_for("distributor.dashboard")

                    # Return success response
                    response_data = {
                        "status": "success",
                        "redirect": redirect_url,
                        "user_type": user_type
                    }

                    # Add warning if external token failed for dealer
                    if user_type.lower() == "dealer" and not external_token_success:
                        response_data["warning"] = "External API authentication failed. Customer data may not be available."
                        print("Warning: Dealer logged in but external API authentication failed")

                    print(f"Login successful for {user_type}: {email}")
                    return jsonify(response_data), 200

        except Exception as e:
            print("---- /login ERROR ----")
            traceback.print_exc()
            print("----------------------")
            return jsonify({"status": "error", "message": "Login failed. Please try again later"}), 500


@auth_bp.route("/logout", methods=["GET"])
def logout():
    conn = psycopg2.connect(**Config.DB_CONFIG)
    conn.close()
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/signup", methods=["POST"])
def signup():
    """
    Unified signup endpoint with admin email notification for dealer/distributor requests.
    Modified: Dealer signup now calls external API to get dealer_code
    """

    # Helper: external API response me se "message" nikalna
    def _extract_external_message(resp):
        try:
            data = resp.json()
            if isinstance(data, dict):
                return (
                    data.get("message")
                    or data.get("msg")
                    or data.get("error")
                    or data.get("detail")
                )
        except Exception:
            pass

        try:
            data = json.loads(resp.text or "")
            if isinstance(data, dict):
                return (
                    data.get("message")
                    or data.get("msg")
                    or data.get("error")
                    or data.get("detail")
                )
        except Exception:
            pass

        return (resp.text or "").strip() or None

    # Extract form data
    user_type = request.form.get("user_type", "").lower()
    full_name = request.form.get("full_name", "").strip()
    address = request.form.get("address", "").strip()
    phone_number = request.form.get("phone_number", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    # ===== VALIDATION =====
    if password != confirm_password:
        return jsonify({"status": "error", "message": "Passwords do not match."}), 400

    pwd_error = validate_password(password)
    if pwd_error:
        return jsonify({"status": "error", "message": pwd_error}), 400

    hashed_password = pwd_context.hash(password)

    # Extract dealer/distributor specific fields
    if user_type in ["dealer", "distributor"]:
        gst_no = request.form.get("gst_no", "").strip()
        company_name = request.form.get("company_name", "").strip()
        pincode = request.form.get("pincode", "").strip()

        if not gst_no or not company_name or not pincode:
            return jsonify({
                "status": "error",
                "message": "GST Number, Company Name, and Pincode are required for dealers and distributors."
            }), 400
    else:
        gst_no = ""
        company_name = ""
        pincode = ""

    try:
        with psycopg2.connect(**Config.DB_CONFIG) as conn:
            with conn.cursor() as cur:
                # ===== DUPLICATE CHECK =====
                cur.execute("""
                    SELECT 1 FROM user_signups
                    WHERE email = %s OR phone_number = %s
                    LIMIT 1
                """, (email, phone_number))

                if cur.fetchone():
                    return jsonify({"status": "error", "message": "Email or phone number already registered."}), 400

                # ===== ADMIN SIGNUP (Direct Approval) =====
                if user_type == "admin":
                    cur.execute("""
                        INSERT INTO user_signups
                        (full_name, address, phone_number, email, password, user_type, status, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                        RETURNING user_id
                    """, (full_name, address, phone_number, email, hashed_password, user_type, "Approved"))

                    new_user_id = cur.fetchone()[0]
                    conn.commit()

                    # Send email to new admin user
                    try:
                        subject = f"Admin Account Created Successfully - PGAK System"
                        body = (
                            f"Hello {full_name},\n\n"
                            f"Your admin account has been successfully created and approved.\n\n"
                            f"Account Details:\n"
                            f"Email: {email}\n"
                            f"User ID: {new_user_id}\n"
                            f"User Type: Admin\n\n"
                            f"You can now login to the Admin Dashboard with your credentials.\n\n"
                            f"Regards,\n"
                            f"PGAK System\n"
                        )
                        send_email(email, subject, body)
                        logger.info("Admin account confirmation email sent to %s (user_id=%s)", email, new_user_id)
                    except Exception as e:
                        logger.exception("Failed sending admin confirmation email to %s (user_id=%s): %s", email, new_user_id, e)

                    session["user_type"] = "admin"
                    session["user_id"] = new_user_id
                    session["full_name"] = full_name
                    session["email"] = email

                    return jsonify({
                        "status": "success",
                        "message": "Signup successful. Redirecting to dashboard...",
                        "redirect": url_for("admin.dashboard")
                    })

                # ===== DEALER SIGNUP =====
                elif user_type == "dealer":
                    distributor_code = request.form.get("distributor_code", "").strip()

                    if distributor_code:
                        cur.execute("""
                            SELECT user_id FROM user_signups
                            WHERE distributor_code = %s AND user_type = 'distributor'
                            LIMIT 1
                        """, (distributor_code,))
                        distributor = cur.fetchone()
                        if not distributor:
                            return jsonify({
                                "status": "error",
                                "message": "Invalid distributor code. Please verify and try again."
                            }), 400

                    # Call external API to get dealer_code
                    address = request.form.get("address", company_name).strip()

                    external_api_url = Config.EXTERNAL_DEALER_SIGNUP
                    external_api_payload = {
                        "username": full_name,
                        "email": email,
                        "password": password,
                        "confirm_password": password,
                        "phone_no": phone_number,
                        "address": address,
                        "gst_no": gst_no,
                        "pin_no": pincode,
                        "terms_accepted": True
                    }

                    try:
                        logger.info("Calling external API for dealer signup: %s", external_api_url)

                        api_response = requests.post(
                            external_api_url,
                            json=external_api_payload,
                            timeout=30,
                            headers={"Content-Type": "application/json"}
                        )

                        if api_response.status_code not in (200, 201):
                            api_msg = _extract_external_message(api_response) or f"External system error ({api_response.status_code})"
                            logger.error("External API error (%s): %s", api_response.status_code, api_msg)

                            pass_through_codes = {400, 401, 403, 404, 409, 422}
                            http_code = api_response.status_code if api_response.status_code in pass_through_codes else 502

                            return jsonify({
                                "status": "error",
                                "message": api_msg
                            }), http_code

                        # Parse success response
                        try:
                            api_data = api_response.json()
                        except Exception:
                            logger.error("External API success response not JSON: %s", api_response.text)
                            return jsonify({
                                "status": "error",
                                "message": "External system returned invalid response. Please contact support."
                            }), 502

                        dealer_code = api_data.get("dealer_code") or api_data.get("dealerCode") or api_data.get("code")

                        if not dealer_code:
                            logger.error("External API did not return dealer_code: %s", api_data)
                            return jsonify({
                                "status": "error",
                                "message": "External system did not return dealer code. Please contact support."
                            }), 502

                        logger.info("Received dealer_code from external API: %s", dealer_code)

                    except requests.exceptions.Timeout:
                        logger.error("External API timeout for dealer signup")
                        return jsonify({
                            "status": "error",
                            "message": "External system timeout. Please try again later."
                        }), 504

                    except requests.exceptions.RequestException as e:
                        logger.exception("External API request failed: %s", str(e))
                        return jsonify({
                            "status": "error",
                            "message": "Failed to connect to external system. Please try again later."
                        }), 503

                    except Exception as e:
                        logger.exception("Unexpected error calling external API: %s", str(e))
                        return jsonify({
                            "status": "error",
                            "message": "An unexpected error occurred. Please contact support."
                        }), 500

                    # Save to database with dealer_code from external API
                    cur.execute("""
                        INSERT INTO user_signups
                        (full_name, address, phone_number, email, password, gst_no, company_name,
                         pincode, distributor_code, dealer_code, user_type, status, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        RETURNING user_id
                    """, (
                        full_name, address, phone_number, email, hashed_password,
                        gst_no, company_name, pincode,
                        distributor_code,
                        dealer_code,
                        user_type, "Pending"
                    ))

                    new_user_id = cur.fetchone()[0]
                    conn.commit()

                    # Email admin: New Dealer Request
                    try:
                        notify_admin_new_signup(
                            user_type="dealer",
                            full_name=full_name,
                            address=address,
                            email=email,
                            phone=phone_number,
                            gst_no=gst_no,
                            company_name=company_name,
                            pincode=pincode,
                            distributor_code=distributor_code,
                            dealer_code=dealer_code,
                            user_id=new_user_id
                        )
                    except Exception as e:
                        logger.exception("Admin notify failed (dealer signup) user_id=%s: %s", new_user_id, e)

                    return jsonify({
                        "status": "success",
                        "message": "Signup successful! Your account is awaiting admin approval.",
                        "user_id": new_user_id,
                        "dealer_code": dealer_code,
                        "user_type": user_type
                    })

                # ===== DISTRIBUTOR SIGNUP (UNCHANGED) =====
                elif user_type == "distributor":
                    distributor_code = get_unique_code("distributor", cur)

                    if distributor_code is None:
                        return jsonify({
                            "status": "error",
                            "message": "Failed to generate distributor code. Please try again."
                        }), 500

                    cur.execute("""
                        INSERT INTO user_signups
                        (full_name, address, phone_number, email, password, gst_no, company_name,
                         pincode, distributor_code, user_type, status, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        RETURNING user_id
                    """, (
                        full_name, address, phone_number, email, hashed_password,
                        gst_no, company_name, pincode, distributor_code,
                        user_type, "Pending"
                    ))

                    new_user_id = cur.fetchone()[0]
                    conn.commit()

                    # Email admin: New Distributor Request
                    try:
                        notify_admin_new_signup(
                            user_type="distributor",
                            full_name=full_name,
                            address=address,
                            email=email,
                            phone=phone_number,
                            gst_no=gst_no,
                            company_name=company_name,
                            pincode=pincode,
                            distributor_code=distributor_code,
                            dealer_code=None,
                            user_id=new_user_id
                        )
                    except Exception as e:
                        logger.exception("Admin notify failed (distributor signup) user_id=%s: %s", new_user_id, e)

                    return jsonify({
                        "status": "success",
                        "message": "Signup successful. Awaiting admin approval.",
                        "user_id": new_user_id,
                        "generated_code": distributor_code,
                        "code_type": "distributor",
                        "user_type": user_type
                    })

                else:
                    return jsonify({
                        "status": "error",
                        "message": "Invalid user type."
                    }), 400

    except psycopg2.errors.UniqueViolation:
        return jsonify({"status": "error", "message": "Email or phone number already registered."}), 400
    except Exception as e:
        logger.exception("Signup failed: %s", str(e))
        return jsonify({"status": "error", "message": f"Signup failed: {str(e)}"}), 500


# =========================================================================
# OTP Routes (Phone)
# =========================================================================

@auth_bp.route("/send-otp", methods=["POST"])
def send_otp():
    conn = None
    try:
        json_data = request.get_json(silent=True) or {}
        phone = (request.form.get("phone") or json_data.get("phone") or "").strip()

        if not phone:
            return jsonify({
                "status": "error",
                "message": "Phone number is required",
                "errors": {"detail": "phone field is missing"}
            }), 400

        digits_only = "".join(ch for ch in phone if ch.isdigit())
        if len(digits_only) < 10:
            return jsonify({
                "status": "error",
                "message": "Please enter a valid phone number",
                "errors": {"detail": "phone is invalid"}
            }), 400

        phone_to_send = digits_only

        # Check if phone number is already registered
        conn = psycopg2.connect(**Config.DB_CONFIG)
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT 1 FROM user_signups WHERE phone_number = %s LIMIT 1
            """, (phone_to_send,))
            if cursor.fetchone():
                return jsonify({
                    "status": "error",
                    "message": "Phone number is already registered.",
                    "errors": {"detail": "This phone number is already in use."}
                }), 400

        # Generate OTP + expiry
        otp = generate_otp(4)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_VALID_MINUTES)

        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO otp_verification (phone_number, otp, expires_at, is_verified, attempts, created_at, updated_at)
                VALUES (%s, %s, %s, FALSE, 0, NOW(), NOW())
                ON CONFLICT (phone_number)
                DO UPDATE SET
                    otp = EXCLUDED.otp,
                    expires_at = EXCLUDED.expires_at,
                    is_verified = FALSE,
                    attempts = 0,
                    updated_at = NOW()
            """, (digits_only, otp, expires_at))

            conn.commit()

        # Sending OTP SMS
        message = (
            f"Dear Customer, Use this One-Time Password {otp} to log in to your account. "
            f"This OTP will be valid for the next {OTP_VALID_MINUTES} mins. Team Videolytical Systems"
        )

        base_url = "https://pgapi.smartping.ai/fe/api/v1/send"
        params = {
            "username": "videolytical.trans",
            "password": "W9gnP",
            "unicode": "false",
            "from": "VIDESY",
            "to": phone_to_send,
            "dltPrincipalEntityId": "1701174340505126708",
            "dltContentId": "1707175170253644044",
            "text": message,
        }

        try:
            response = requests.get(base_url, params=params, timeout=15)
        except requests.RequestException as re:
            return jsonify({
                "status": "error",
                "message": "SMS API request failed.",
                "errors": {"detail": str(re)}
            }), 502

        if response.status_code != 200 or "success" not in (response.text or "").lower():
            return jsonify({
                "status": "error",
                "message": "Failed to send OTP SMS.",
                "errors": {"detail": f"Failed to send OTP SMS: {response.text}"}
            }), 500

        return jsonify({
            "status": "success",
            "message": f"OTP sent successfully to {phone_to_send}",
            "data": {
                "phone_no": phone_to_send,
                "expires_in_minutes": OTP_VALID_MINUTES
            }
        }), 200

    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        return jsonify({
            "status": "error",
            "message": "An internal server error occurred.",
            "errors": {"detail": str(e)}
        }), 500

    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


@auth_bp.route("/verify-otp", methods=["POST"])
def verify_otp():
    conn = None
    cursor = None
    try:
        json_data = request.get_json(silent=True) or {}

        phone = (request.form.get("phone") or json_data.get("phone") or "").strip()
        otp = (request.form.get("otp") or json_data.get("otp") or "").strip()

        if not phone or not otp:
            return jsonify({
                "status": "error",
                "message": "Phone and OTP are required",
                "errors": {"detail": "phone/otp missing"}
            }), 400

        digits_only = "".join(ch for ch in phone if ch.isdigit())
        if len(digits_only) < 10:
            return jsonify({
                "status": "error",
                "message": "Invalid phone number",
                "errors": {"detail": "phone invalid"}
            }), 400

        if not (otp.isdigit() and len(otp) == 4):
            return jsonify({
                "status": "error",
                "message": "OTP must be exactly 4 digits.",
                "errors": {"detail": "otp invalid"}
            }), 400

        conn = psycopg2.connect(**Config.DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                phone_number,
                otp,
                expires_at,
                is_verified,
                COALESCE(attempts, 0) AS attempts,
                (expires_at < NOW()) AS expired
            FROM otp_verification
            WHERE phone_number = %s
        """, (digits_only,))
        row = cursor.fetchone()

        if not row:
            return jsonify({
                "status": "error",
                "message": "OTP record not found for this phone number.",
                "errors": {"detail": "otp record not found"}
            }), 404

        if row["expired"]:
            return jsonify({
                "status": "error",
                "message": "OTP has expired. Please request a new OTP.",
                "errors": {"detail": "otp expired"}
            }), 400

        if row.get("is_verified"):
            return jsonify({
                "status": "success",
                "message": "OTP already verified.",
                "data": {"phone_no": digits_only, "verified": True}
            }), 200

        if str(row["otp"]) != str(otp):
            cursor.execute("""
                UPDATE otp_verification
                SET attempts = COALESCE(attempts, 0) + 1,
                    updated_at = NOW()
                WHERE phone_number = %s
            """, (digits_only,))
            conn.commit()

            return jsonify({
                "status": "error",
                "message": "Invalid OTP.",
                "errors": {"detail": "invalid otp"}
            }), 400

        cursor.execute("""
            UPDATE otp_verification
            SET is_verified = TRUE,
                attempts = 0,
                updated_at = NOW()
            WHERE phone_number = %s
        """, (digits_only,))
        conn.commit()

        return jsonify({
            "status": "success",
            "message": "OTP verified successfully.",
            "data": {"phone_no": digits_only, "verified": True}
        }), 200

    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        print("---- /verify-otp ERROR ----")
        traceback.print_exc()
        print("---------------------------")

        return jsonify({
            "status": "error",
            "message": "An internal server error occurred.",
            "errors": {"detail": str(e)}
        }), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# =========================================================================
# OTP Routes (Email)
# =========================================================================

@auth_bp.route("/send-otp-email", methods=["POST"])
def send_otp_email_route():
    conn = None
    try:
        json_data = request.get_json(silent=True) or {}

        email = (request.form.get("email") or json_data.get("email") or "").strip().lower()
        if not email:
            return jsonify({
                "status": "error",
                "message": "Email is required",
                "errors": {"detail": "email field is missing"}
            }), 400

        if not is_valid_email(email):
            return jsonify({
                "status": "error",
                "message": "Please enter a valid email",
                "errors": {"detail": "email is invalid"}
            }), 400

        # Check if email is already registered
        conn = psycopg2.connect(**Config.DB_CONFIG)
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT 1 FROM user_signups WHERE email = %s LIMIT 1
            """, (email,))
            if cursor.fetchone():
                return jsonify({
                    "status": "error",
                    "message": "Email is already registered.",
                    "errors": {"detail": "This email is already in use."}
                }), 400

        otp = generate_otp(4)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_VALID_MINUTES)

        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO otp_verification_email (email, otp, expires_at, is_verified, attempts, created_at, updated_at)
                VALUES (%s, %s, %s, FALSE, 0, NOW(), NOW())
                ON CONFLICT (email)
                DO UPDATE SET
                    otp = EXCLUDED.otp,
                    expires_at = EXCLUDED.expires_at,
                    is_verified = FALSE,
                    attempts = 0,
                    updated_at = NOW()
            """, (email, otp, expires_at))
            conn.commit()

        ok = send_email_otp(email, otp)
        if not ok:
            return jsonify({
                "status": "error",
                "message": "Failed to send OTP email.",
                "errors": {"detail": "SMTP login/send failed. Check SMTP credentials and server settings."}
            }), 502

        return jsonify({
            "status": "success",
            "message": f"OTP sent successfully to {email}",
            "data": {"email": email, "expires_in_minutes": OTP_VALID_MINUTES}
        }), 200

    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        return jsonify({
            "status": "error",
            "message": "An internal server error occurred.",
            "errors": {"detail": str(e)}
        }), 500

    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


@auth_bp.route("/verify-otp-email", methods=["POST"])
def verify_otp_email():
    conn = None
    cursor = None
    try:
        json_data = request.get_json(silent=True) or {}

        email = (request.form.get("email") or json_data.get("email") or "").strip().lower()
        otp = (request.form.get("otp") or json_data.get("otp") or "").strip()

        if not email or not otp:
            return jsonify({
                "status": "error",
                "message": "Email and OTP are required",
                "errors": {"detail": "email/otp missing"}
            }), 400

        if not is_valid_email(email):
            return jsonify({
                "status": "error",
                "message": "Invalid email",
                "errors": {"detail": "email invalid"}
            }), 400

        if not (otp.isdigit() and len(otp) == 4):
            return jsonify({
                "status": "error",
                "message": "OTP must be exactly 4 digits.",
                "errors": {"detail": "otp invalid"}
            }), 400

        conn = psycopg2.connect(**Config.DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                email,
                otp,
                expires_at,
                is_verified,
                attempts,
                (expires_at < NOW()) AS expired
            FROM otp_verification_email
            WHERE email = %s
        """, (email,))
        row = cursor.fetchone()

        if not row:
            return jsonify({
                "status": "error",
                "message": "OTP record not found for this email.",
                "errors": {"detail": "otp record not found"}
            }), 404

        if row["expired"]:
            return jsonify({
                "status": "error",
                "message": "OTP has expired. Please request a new OTP.",
                "errors": {"detail": "otp expired"}
            }), 400

        if row.get("is_verified"):
            return jsonify({
                "status": "success",
                "message": "OTP already verified.",
                "data": {"email": email, "verified": True}
            }), 200

        if str(row["otp"]) != str(otp):
            cursor.execute("""
                UPDATE otp_verification_email
                SET attempts = COALESCE(attempts, 0) + 1,
                    updated_at = NOW()
                WHERE email = %s
            """, (email,))
            conn.commit()

            return jsonify({
                "status": "error",
                "message": "Invalid OTP.",
                "errors": {"detail": "invalid otp"}
            }), 400

        cursor.execute("""
            UPDATE otp_verification_email
            SET is_verified = TRUE,
                attempts = 0,
                updated_at = NOW()
            WHERE email = %s
        """, (email,))
        conn.commit()

        return jsonify({
            "status": "success",
            "message": "OTP verified successfully.",
            "data": {"email": email, "verified": True}
        }), 200

    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        print("---- /verify-otp-email ERROR ----")
        traceback.print_exc()
        print("--------------------------------")

        return jsonify({
            "status": "error",
            "message": "An internal server error occurred.",
            "errors": {"detail": str(e)}
        }), 500

    finally:
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass


# =========================================================================
# Current User API
# =========================================================================

@auth_bp.route("/api/current-user", methods=["GET"])
def api_current_user():
    """
    Returns the currently logged-in user's profile for frontend sidebar.
    """
    try:
        user_id = session.get("user_id")
        user_type = session.get("user_type")

        if not user_id or not user_type:
            return jsonify({
                "status": "error",
                "message": "Not logged in"
            }), 401

        # Fast path (if email was stored in session)
        sess_full_name = session.get("full_name")
        sess_email = session.get("email")

        if sess_full_name and sess_email and user_type:
            return jsonify({
                "user_id": user_id,
                "full_name": sess_full_name,
                "email": sess_email,
                "user_type": user_type
            }), 200

        # Otherwise fetch from DB
        conn = psycopg2.connect(**Config.DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT user_id, full_name, email, user_type, status
            FROM user_signups
            WHERE user_id = %s
            LIMIT 1
        """, (user_id,))

        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if not user:
            session.clear()
            return jsonify({
                "status": "error",
                "message": "Session invalid"
            }), 401

        # Block non-approved users
        status = (user.get("status") or "").lower()
        if status and status != "approved":
            return jsonify({
                "status": "error",
                "message": "Account not approved"
            }), 403

        # Store into session for next time
        session["full_name"] = user.get("full_name")
        session["email"] = user.get("email")
        session["user_type"] = user.get("user_type")
        session["user_id"] = user.get("user_id")

        return jsonify({
            "user_id": user.get("user_id"),
            "full_name": user.get("full_name"),
            "email": user.get("email"),
            "user_type": user.get("user_type")
        }), 200

    except Exception as e:
        print("---- /api/current-user ERROR ----")
        traceback.print_exc()
        print("---------------------------------")
        return jsonify({
            "status": "error",
            "message": "Server error",
            "error": str(e)
        }), 500


# =========================================================================
# Pincode Lookup API
# =========================================================================

@auth_bp.route("/api/pincode-lookup", methods=["POST"])
def pincode_lookup():
    """
    Fetch pincode data including post office, districts_name, city, state.
    Accepts both single pincode and multiple pincodes (for distributor).
    Returns matching records for autocomplete/dropdown.
    """
    try:
        data = request.get_json(silent=True) or {}
        pincode_input = (data.get("pincode") or "").strip()

        if not pincode_input:
            return jsonify({
                "status": "error",
                "message": "Pincode is required",
                "data": []
            }), 400

        # Remove spaces and special characters, keep only digits
        digits_only = "".join(ch for ch in pincode_input if ch.isdigit())

        if len(digits_only) == 0:
            return jsonify({
                "status": "error",
                "message": "Please enter valid pincode digits",
                "data": []
            }), 400

        conn = psycopg2.connect(**Config.DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT DISTINCT
                pincode,
                post_office,
                districts_name,
                city,
                state
            FROM pincodes
            WHERE pincode LIKE %s
            ORDER BY pincode ASC
            LIMIT 20
        """, (digits_only + '%',))

        results = cursor.fetchall()
        cursor.close()
        conn.close()

        if results:
            return jsonify({
                "status": "success",
                "message": f"Found {len(results)} pincode(s)",
                "data": results
            }), 200
        else:
            return jsonify({
                "status": "success",
                "message": "No pincodes found",
                "data": []
            }), 200

    except Exception as e:
        print("---- /api/pincode-lookup ERROR ----")
        traceback.print_exc()
        print("-----------------------------------")

        return jsonify({
            "status": "error",
            "message": "Server error during pincode lookup",
            "data": [],
            "error": str(e)
        }), 500


@auth_bp.route("/api/pincode-details", methods=["POST"])
def pincode_details():
    """
    Get full details for a specific pincode.
    Used when user selects a pincode from dropdown.
    """
    try:
        data = request.get_json(silent=True) or {}
        pincode = (data.get("pincode") or "").strip()

        if not pincode:
            return jsonify({
                "status": "error",
                "message": "Pincode is required"
            }), 400

        conn = psycopg2.connect(**Config.DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                pincode,
                post_office,
                districts_name,
                city,
                state
            FROM pincodes
            WHERE pincode = %s
            LIMIT 1
        """, (pincode,))

        result = cursor.fetchone()
        cursor.close()
        conn.close()

        if result:
            return jsonify({
                "status": "success",
                "data": result
            }), 200
        else:
            return jsonify({
                "status": "error",
                "message": "Pincode not found"
            }), 404

    except Exception as e:
        print("---- /api/pincode-details ERROR ----")
        traceback.print_exc()
        print("------------------------------------")

        return jsonify({
            "status": "error",
            "message": "Server error",
            "error": str(e)
        }), 500
