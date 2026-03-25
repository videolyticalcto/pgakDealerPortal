"""
Admin blueprint routes: dashboard, user management, pending approvals, codes.
"""

import logging
import re
import traceback

import psycopg2
from flask import request, jsonify, render_template, redirect, url_for, session
from psycopg2.extras import RealDictCursor

from app.blueprints.admin import admin_bp
from app.config import Config
from app.extensions import pwd_context
from app.utils.email import send_email
from app.utils.helpers import get_unique_code, validate_password

logger = logging.getLogger(__name__)


# ── DB helpers (used by approve/reject) ──────────────────────────────────

def get_user_signup_details(user_id: int):
    """Fetch dealer/distributor email, name, type, and code from user_signups."""
    conn = psycopg2.connect(**Config.DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id,
                       COALESCE(full_name, '') as full_name,
                       COALESCE(email, '') as email,
                       COALESCE(user_type, '') as user_type,
                       COALESCE(dealer_code, '') as dealer_code,
                       COALESCE(distributor_code, '') as distributor_code
                FROM user_signups
                WHERE user_id = %s
            """, (user_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {
                "user_id": row[0],
                "full_name": row[1],
                "email": row[2],
                "user_type": row[3],
                "dealer_code": row[4],
                "distributor_code": row[5],
            }
    finally:
        conn.close()


def update_user_status(user_id: int, new_status: str) -> None:
    conn = psycopg2.connect(**Config.DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE user_signups
                SET status = %s
                WHERE user_id = %s
            """, (new_status, user_id))
        conn.commit()
    finally:
        conn.close()


# =========================================================================
# PAGE ROUTES
# =========================================================================

@admin_bp.route("/dashboard", methods=["GET"])
def dashboard():
    if 'user_type' not in session or session['user_type'] != 'admin':
        return redirect(url_for('auth.login'))

    with psycopg2.connect(**Config.DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id, full_name, address, email, status, user_type, gst_no, company_name, pincode, phone_number, distributor_code
                FROM user_signups
                WHERE user_type IN ('dealer', 'distributor') AND status = 'Pending'
            """)
            users = cur.fetchall()

    return render_template("admin/dashboard.html", users=users)


@admin_bp.route("/users-page", methods=["GET"])
def users_page():
    if 'user_type' not in session or session['user_type'] != 'admin':
        return redirect(url_for('auth.login'))
    return render_template("admin/users.html")


@admin_bp.route("/devices-page", methods=["GET"])
def devices_page():
    if 'user_type' not in session or session['user_type'] != 'admin':
        return redirect(url_for('auth.login'))
    return render_template("admin/devices.html")


# =========================================================================
# USER MANAGEMENT API
# =========================================================================

@admin_bp.route("/users", methods=["GET"])
def admin_users():
    """
    Fetch all users with their details (dealers, distributors, admins).
    Returns JSON with user_id as key and user details as value.
    Excludes the currently logged-in admin from the list.
    Also returns distributor's name/details for the distributor_code (if any).
    """
    try:
        if 'user_type' not in session or session['user_type'] != 'admin':
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        current_user_id = session.get('user_id')

        conn = psycopg2.connect(**Config.DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # LATERAL join ensures ONLY ONE distributor row is picked even if duplicates exist
        cursor.execute("""
            SELECT
                u.user_id,
                u.full_name,
                u.address,
                u.email,
                u.phone_number,
                u.user_type,
                u.status,
                u.gst_no,
                u.company_name,
                u.pincode,
                u.distributor_code,
                u.dealer_code,
                u.created_at,

                d.distributor_user_id,
                d.distributor_full_name,
                d.distributor_email,
                d.distributor_phone_number

            FROM user_signups u
            LEFT JOIN LATERAL (
                SELECT
                    us.user_id AS distributor_user_id,
                    us.full_name AS distributor_full_name,
                    us.email AS distributor_email,
                    us.phone_number AS distributor_phone_number
                FROM user_signups us
                WHERE us.user_type = 'distributor'
                  AND us.distributor_code = u.distributor_code
                ORDER BY us.created_at DESC NULLS LAST
                LIMIT 1
            ) d ON TRUE
            WHERE u.user_id != %s
            ORDER BY u.created_at DESC
        """, (current_user_id,))

        users = cursor.fetchall()
        cursor.close()
        conn.close()

        users_dict = {}
        for user in users:
            users_dict[str(user['user_id'])] = {
                "user_id": user.get('user_id'),
                "full_name": user.get('full_name'),
                "address": user.get('address'),
                "email": user.get('email'),
                "phone_number": user.get('phone_number'),
                "user_type": user.get('user_type'),
                "status": user.get('status'),
                "gst_no": user.get('gst_no'),
                "company_name": user.get('company_name'),
                "pincode": user.get('pincode'),
                "distributor_code": user.get('distributor_code'),
                "dealer_code": user.get('dealer_code'),
                "created_at": user['created_at'].isoformat() if user.get('created_at') else None,

                # Distributor details
                "distributor_user_id": user.get('distributor_user_id'),
                "distributor_full_name": user.get('distributor_full_name'),
                "distributor_email": user.get('distributor_email'),
                "distributor_phone_number": user.get('distributor_phone_number'),
            }

        return jsonify(users_dict), 200

    except Exception as e:
        print("---- /admin/users ERROR ----")
        traceback.print_exc()
        print("---------------------------")
        return jsonify({
            "status": "error",
            "message": "Failed to fetch users",
            "error": str(e)
        }), 500


@admin_bp.route("/users/<int:user_id>", methods=["PUT"])
def edit_user(user_id):
    """Edit an existing user's details."""
    conn = None
    try:
        if 'user_type' not in session or session['user_type'] != 'admin':
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        data = request.get_json(silent=True) or {}
        logger.info("edit_user called for user_id=%s with data keys=%s", user_id, list(data.keys()))

        required_fields = ['full_name', 'email', 'address', 'phone_number', 'user_type', 'status', 'company_name']
        for field in required_fields:
            if field not in data or not str(data[field]).strip():
                return jsonify({"status": "error", "message": f"{field} is required"}), 400

        conn = psycopg2.connect(**Config.DB_CONFIG)
        cursor = conn.cursor()

        # Check user exists and email not taken — single query
        cursor.execute("""
            SELECT
                EXISTS(SELECT 1 FROM user_signups WHERE user_id = %s) AS user_exists,
                EXISTS(SELECT 1 FROM user_signups WHERE email = %s AND user_id != %s) AS email_taken
        """, (user_id, data['email'], user_id))
        row = cursor.fetchone()
        if not row[0]:
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": "User not found"}), 404
        if row[1]:
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": "Email already exists"}), 400

        cursor.execute("""
            UPDATE user_signups
            SET
                full_name = %s,
                address = %s,
                email = %s,
                phone_number = %s,
                user_type = %s,
                status = %s,
                gst_no = %s,
                company_name = %s,
                pincode = %s,
                distributor_code = %s,
                dealer_code = %s
            WHERE user_id = %s
        """, (
            data['full_name'],
            data['address'],
            data['email'],
            data['phone_number'],
            data['user_type'],
            data['status'],
            data.get('gst_no', ''),
            data['company_name'],
            data.get('pincode', ''),
            data.get('distributor_code', ''),
            data.get('dealer_code', ''),
            user_id
        ))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "status": "success",
            "message": "User updated successfully"
        }), 200

    except Exception as e:
        logger.error("edit_user ERROR for user_id=%s: %s", user_id, e, exc_info=True)
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
        return jsonify({
            "status": "error",
            "message": "Failed to update user",
            "error": str(e)
        }), 500


@admin_bp.route("/users/<int:user_id>", methods=["DELETE"])
def delete_user(user_id):
    """Delete a user."""
    try:
        if 'user_type' not in session or session['user_type'] != 'admin':
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        if user_id == session.get('user_id'):
            return jsonify({"status": "error", "message": "Cannot delete your own account"}), 400

        conn = psycopg2.connect(**Config.DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute("DELETE FROM user_signups WHERE user_id = %s", (user_id,))

        if cursor.rowcount == 0:
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": "User not found"}), 404

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "status": "success",
            "message": "User deleted successfully"
        }), 200

    except Exception as e:
        print("---- /admin/users/<user_id> DELETE ERROR ----")
        traceback.print_exc()
        print("---------------------------------------------------")
        return jsonify({
            "status": "error",
            "message": "Failed to delete user",
            "error": str(e)
        }), 500


@admin_bp.route("/users", methods=["POST"])
def create_user():
    """
    Create a new user (dealer or distributor).
    Admin-created users are auto-approved.
    """
    try:
        # Authentication check
        if 'user_type' not in session or session['user_type'] != 'admin':
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        data = request.get_json()

        # Extract form data
        user_type = data.get("user_type", "").lower()
        full_name = data.get("full_name", "").strip()
        address = data.get("address", "").strip()
        phone_number = data.get("phone_number", "").strip()
        email = data.get("email", "").strip()
        password = data.get("password", "")
        confirm_password = data.get("confirm_password", "")

        # Validation: Required Fields
        required_fields = {
            'full_name': full_name,
            'address': address,
            'email': email,
            'phone_number': phone_number,
            'user_type': user_type,
            'company_name': data.get('company_name', '').strip(),
            'password': password,
            'confirm_password': confirm_password
        }

        for field_name, field_value in required_fields.items():
            if not field_value:
                return jsonify({
                    "status": "error",
                    "message": f"{field_name.replace('_', ' ').title()} is required"
                }), 400

        # Validation: User Type
        if user_type not in ['dealer', 'distributor']:
            return jsonify({
                "status": "error",
                "message": "user_type must be 'dealer' or 'distributor'"
            }), 400

        # Validation: Password Match
        if password != confirm_password:
            return jsonify({
                "status": "error",
                "message": "Passwords do not match"
            }), 400

        # Validation: Password Strength
        pwd_error = validate_password(password)
        if pwd_error:
            return jsonify({
                "status": "error",
                "message": pwd_error
            }), 400

        # Validation: Email Format
        email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_regex, email):
            return jsonify({
                "status": "error",
                "message": "Invalid email format"
            }), 400

        # Validation: Phone Number Format
        phone_regex = r'^\d{7,15}$'
        if not re.match(phone_regex, phone_number.replace(' ', '').replace('-', '')):
            return jsonify({
                "status": "error",
                "message": "Phone number must be 7-15 digits"
            }), 400

        # Extract dealer/distributor specific fields
        gst_no = data.get("gst_no", "").strip()
        company_name = data.get("company_name", "").strip()
        pincode = data.get("pincode", "").strip()

        # Validation: Dealer/Distributor Required Fields
        if not company_name:
            return jsonify({
                "status": "error",
                "message": "Company Name is required"
            }), 400

        if not gst_no:
            return jsonify({
                "status": "error",
                "message": "GST Number is required"
            }), 400

        if not pincode:
            return jsonify({
                "status": "error",
                "message": "Pincode is required"
            }), 400

        # Validation: Pincode Format
        if not re.match(r'^\d{5,10}$', pincode):
            return jsonify({
                "status": "error",
                "message": "Pincode must be 5-10 digits"
            }), 400

        # Hash Password
        try:
            hashed_password = pwd_context.hash(password)
        except Exception as e:
            print(f"Error hashing password: {e}")
            return jsonify({
                "status": "error",
                "message": "Error processing password"
            }), 500

        conn = None
        try:
            conn = psycopg2.connect(**Config.DB_CONFIG)
            cursor = conn.cursor()

            # Duplicate Check: Email and Phone in single query
            cursor.execute(
                "SELECT LOWER(email) = LOWER(%s) AS email_match, phone_number = %s AS phone_match FROM user_signups WHERE LOWER(email) = LOWER(%s) OR phone_number = %s LIMIT 1",
                (email, phone_number, email, phone_number)
            )
            dup = cursor.fetchone()
            if dup:
                cursor.close()
                conn.close()
                msg = "Email already registered" if dup[0] else "Phone number already registered"
                return jsonify({"status": "error", "message": msg}), 400

            # Generate codes based on user type
            dealer_code = None
            distributor_code = ''

            if user_type == 'dealer':
                dealer_code = get_unique_code('dealer', cursor)

            elif user_type == 'distributor':
                distributor_code = get_unique_code('distributor', cursor)

            # Insert new user
            cursor.execute("""
                INSERT INTO user_signups
                (full_name, address, email, phone_number, password, user_type, status,
                gst_no, company_name, pincode, distributor_code, dealer_code, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                RETURNING user_id, full_name, address, email, user_type, dealer_code, distributor_code
            """, (
                full_name,
                address,
                email,
                phone_number,
                hashed_password,
                user_type,
                'Approved',  # Admin-created users are auto-approved
                gst_no,
                company_name,
                pincode,
                distributor_code,
                dealer_code
            ))

            result = cursor.fetchone()
            new_user_id = result[0]

            conn.commit()
            cursor.close()

            # Success response
            return jsonify({
                "status": "success",
                "message": f"{user_type.capitalize()} created successfully",
                "user_id": new_user_id,
                "user": {
                    "user_id": result[0],
                    "full_name": result[1],
                    "address": result[2],
                    "email": result[3],
                    "user_type": result[4],
                    "dealer_code": result[5],
                    "distributor_code": result[6],
                    "status": "approved"
                }
            }), 201

        except psycopg2.IntegrityError as e:
            if conn:
                try:
                    conn.rollback()
                    conn.close()
                except Exception:
                    pass

            logger.error("create_user IntegrityError: %s", e)
            err_str = str(e).lower()

            if 'email' in err_str:
                msg = "Email already registered"
            elif 'phone' in err_str:
                msg = "Phone number already registered"
            elif 'dealer_code' in err_str:
                msg = "Dealer code conflict. Please try again."
            elif 'distributor_code' in err_str:
                msg = "Distributor code conflict. Please try again."
            else:
                msg = "A user with this information already exists."

            return jsonify({
                "status": "error",
                "message": msg
            }), 400

        except psycopg2.DatabaseError as e:
            if conn:
                try:
                    conn.rollback()
                    conn.close()
                except Exception:
                    pass

            logger.error("create_user DatabaseError: %s", e)

            return jsonify({
                "status": "error",
                "message": "Database error occurred"
            }), 500

        finally:
            if conn and not conn.closed:
                try:
                    conn.close()
                except Exception:
                    pass

    except Exception as e:
        print("---- /admin/users POST ERROR ----")
        traceback.print_exc()
        print("---------------------------------")
        return jsonify({
            "status": "error",
            "message": "Failed to create user",
            "error": str(e)
        }), 500


# =========================================================================
# PENDING REQUESTS
# =========================================================================

@admin_bp.route("/pending", methods=["GET"])
def admin_pending():
    """
    Fetch only pending requests (dealers and distributors awaiting approval).
    Returns JSON with user_id as key and user details as value.
    Excludes the currently logged-in admin from the list.
    """
    try:
        if 'user_type' not in session or session['user_type'] != 'admin':
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        current_user_id = session.get('user_id')

        conn = psycopg2.connect(**Config.DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                user_id,
                full_name,
                address,
                email,
                phone_number,
                user_type,
                status,
                gst_no,
                company_name,
                pincode,
                distributor_code,
                dealer_code,
                created_at
            FROM user_signups
            WHERE status = 'Pending' AND (user_type = 'dealer' OR user_type = 'distributor')
            AND user_id != %s
            ORDER BY created_at ASC
        """, (current_user_id,))

        users = cursor.fetchall()
        cursor.close()
        conn.close()

        # Convert to dictionary format with user_id as key
        users_dict = {}
        for user in users:
            users_dict[str(user['user_id'])] = {
                "user_id": user['user_id'],
                "full_name": user['full_name'],
                "address": user['address'],
                "email": user['email'],
                "phone_number": user['phone_number'],
                "user_type": user['user_type'],
                "status": user['status'],
                "gst_no": user['gst_no'],
                "company_name": user['company_name'],
                "pincode": user['pincode'],
                "distributor_code": user['distributor_code'],
                "dealer_code": user['dealer_code'],
                "created_at": user['created_at'].isoformat() if user['created_at'] else None
            }

        return jsonify(users_dict), 200

    except Exception as e:
        print("---- /admin/pending ERROR ----")
        traceback.print_exc()
        print("-----------------------------")
        return jsonify({
            "status": "error",
            "message": "Failed to fetch pending requests",
            "error": str(e)
        }), 500


# =========================================================================
# APPROVE / REJECT
# =========================================================================

@admin_bp.route("/approve/<int:user_id>", methods=["POST"])
def approve_dealer(user_id):
    is_ajax = request.accept_mimetypes.best == 'application/json' or request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    # 1) Update status
    update_user_status(user_id, "Approved")

    # 2) Fetch email details
    info = get_user_signup_details(user_id)
    if not info:
        logger.warning("approve: user_id %s not found in user_signups", user_id)
        if is_ajax:
            return jsonify({"status": "error", "message": "User not found"}), 404
        return redirect(url_for("admin.dashboard"))

    # 3) Send email
    try:
        name = info["full_name"].strip() or "User"
        utype = (info["user_type"].strip().lower() or "user")
        subject = "PGAK Account Approved"

        if info["user_type"] == "dealer":
            code = info["dealer_code"]
        else:
            code = info["distributor_code"]

        body = (
            f"Hello {name},\n\n"
            f"Your {utype} account has been APPROVED.\n\n"
            f"Your {utype} code: {code}\n\n"
            f"You can now log in and use the portal with your credentials.\n\n"
            f"Please visit the portal and log in with the email you registered with: {info['email']}.\n\n"
            f"Regards,\n"
            f"PGAK Team"
        )
        send_email(info["email"], subject, body)
        logger.info("approve: email sent to %s (user_id=%s)", info["email"], user_id)
    except Exception as e:
        logger.exception("approve: email failed for user_id=%s, err=%s", user_id, e)

    if is_ajax:
        return jsonify({"status": "success", "message": "User approved successfully"}), 200
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/reject/<int:user_id>", methods=["POST"])
def reject_dealer(user_id):
    is_ajax = request.accept_mimetypes.best == 'application/json' or request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    # 1) Update status
    update_user_status(user_id, "Rejected")

    # 2) Fetch email details
    info = get_user_signup_details(user_id)
    if not info:
        logger.warning("reject: user_id %s not found in user_signups", user_id)
        if is_ajax:
            return jsonify({"status": "error", "message": "User not found"}), 404
        return redirect(url_for("admin.dashboard"))

    # 3) Send email
    try:
        name = info["full_name"].strip() or "User"
        utype = (info["user_type"].strip().lower() or "user")
        subject = "PGAK Account Rejected"

        if info["user_type"] == "dealer":
            code = info["dealer_code"]
        else:
            code = info["distributor_code"]

        body = (
            f"Hello {name},\n\n"
            f"Your {utype} account has been REJECTED.\n\n"
            f"Your {utype} code: {code}\n\n"
            f"If you believe this decision is in error, please contact support at support@pgak.com.\n\n"
            f"Regards,\n"
            f"PGAK Team"
        )
        send_email(info["email"], subject, body)
        logger.info("reject: email sent to %s (user_id=%s)", info["email"], user_id)
    except Exception as e:
        logger.exception("reject: email failed for user_id=%s, err=%s", user_id, e)

    if is_ajax:
        return jsonify({"status": "success", "message": "User rejected successfully"}), 200
    return redirect(url_for("admin.dashboard"))


# =========================================================================
# CODE MANAGEMENT
# =========================================================================

@admin_bp.route("/dealer-codes", methods=["GET"])
def admin_dealer_codes():
    """Get all dealer codes."""
    if "user_type" not in session or session["user_type"] != "admin":
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    try:
        with psycopg2.connect(**Config.DB_CONFIG) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT user_id, full_name, address, email, phone_number,
                           dealer_code, status, created_at
                    FROM user_signups
                    WHERE user_type = 'dealer'
                    ORDER BY created_at DESC
                """)

                columns = ['user_id', 'full_name', 'address', 'email', 'phone_number',
                          'dealer_code', 'status', 'created_at']
                dealers = [dict(zip(columns, row)) for row in cur.fetchall()]

                return jsonify({
                    "status": "success",
                    "count": len(dealers),
                    "dealers": dealers
                })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error fetching dealer codes: {str(e)}"
        }), 500


@admin_bp.route("/distributor-codes", methods=["GET"])
def admin_distributor_codes():
    """Get all distributor codes."""
    if "user_type" not in session or session["user_type"] != "admin":
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    try:
        with psycopg2.connect(**Config.DB_CONFIG) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT user_id, full_name, address, email, phone_number,
                           distributor_code, status, created_at
                    FROM user_signups
                    WHERE user_type = 'distributor'
                    ORDER BY created_at DESC
                """)

                columns = ['user_id', 'full_name', 'address', 'email', 'phone_number',
                          'distributor_code', 'status', 'created_at']
                distributors = [dict(zip(columns, row)) for row in cur.fetchall()]

                return jsonify({
                    "status": "success",
                    "count": len(distributors),
                    "distributors": distributors
                })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error fetching distributor codes: {str(e)}"
        }), 500


@admin_bp.route("/regenerate-code/<user_type>/<int:user_id>", methods=["POST"])
def regenerate_code(user_type, user_id):
    """
    Regenerate code for a dealer or distributor.

    Args:
        user_type: 'dealer' or 'distributor'
        user_id: User ID
    """
    if "user_type" not in session or session["user_type"] != "admin":
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    if user_type not in ["dealer", "distributor"]:
        return jsonify({
            "status": "error",
            "message": "Invalid user type"
        }), 400

    try:
        with psycopg2.connect(**Config.DB_CONFIG) as conn:
            with conn.cursor() as cur:
                # Verify user exists and is correct type
                cur.execute("""
                    SELECT user_type FROM user_signups WHERE user_id = %s
                """, (user_id,))

                result = cur.fetchone()
                if not result or result[0] != user_type:
                    return jsonify({
                        "status": "error",
                        "message": f"User not found or not a {user_type}"
                    }), 404

                # Generate new unique code
                new_code = get_unique_code(user_type, cur)
                if new_code is None:
                    return jsonify({
                        "status": "error",
                        "message": "Failed to generate new code"
                    }), 500

                # Update database
                if user_type == 'dealer':
                    cur.execute("UPDATE user_signups SET dealer_code = %s, code_rotated_at = NOW() WHERE user_id = %s", (new_code, user_id))
                else:
                    cur.execute("UPDATE user_signups SET distributor_code = %s, code_rotated_at = NOW() WHERE user_id = %s", (new_code, user_id))
                conn.commit()

                return jsonify({
                    "status": "success",
                    "message": f"{user_type.capitalize()} code regenerated successfully",
                    "new_code": new_code,
                    "user_id": user_id
                })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error regenerating code: {str(e)}"
        }), 500
