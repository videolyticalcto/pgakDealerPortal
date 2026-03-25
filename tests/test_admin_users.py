"""
Tests for admin create user (POST /admin/users) and edit user (PUT /admin/users/<id>).
"""

import pytest
from unittest.mock import patch, MagicMock


# ═══════════════════════════════════════════════════════════════════════════
# CREATE USER  –  POST /admin/users
# ═══════════════════════════════════════════════════════════════════════════

class TestCreateUser:
    """Tests for the POST /admin/users endpoint."""

    VALID_DEALER_DATA = {
        "full_name": "John Dealer",
        "address": "123 Main St",
        "email": "john@dealer.com",
        "phone_number": "9876543210",
        "user_type": "dealer",
        "company_name": "Dealer Corp",
        "gst_no": "GST12345",
        "pincode": "110001",
        "password": "Secure@123",
        "confirm_password": "Secure@123",
    }

    VALID_DISTRIBUTOR_DATA = {
        "full_name": "Jane Distributor",
        "address": "456 Oak Ave",
        "email": "jane@distributor.com",
        "phone_number": "9123456789",
        "user_type": "distributor",
        "company_name": "Dist Inc",
        "gst_no": "GST67890",
        "pincode": "400001",
        "password": "Secure@456",
        "confirm_password": "Secure@456",
    }

    # ── Auth ──────────────────────────────────────────────────────────────

    def test_create_user_unauthorized(self, client, mock_db):
        """Non-admin users should get 401."""
        resp = client.post("/admin/users", json=self.VALID_DEALER_DATA)
        assert resp.status_code == 401

    # ── Validation ────────────────────────────────────────────────────────

    def test_create_user_missing_full_name(self, admin_session, mock_db):
        data = {**self.VALID_DEALER_DATA, "full_name": ""}
        resp = admin_session.post("/admin/users", json=data)
        assert resp.status_code == 400
        assert "Full Name" in resp.get_json()["message"]

    def test_create_user_missing_email(self, admin_session, mock_db):
        data = {**self.VALID_DEALER_DATA, "email": ""}
        resp = admin_session.post("/admin/users", json=data)
        assert resp.status_code == 400
        assert "Email" in resp.get_json()["message"]

    def test_create_user_missing_password(self, admin_session, mock_db):
        data = {**self.VALID_DEALER_DATA, "password": ""}
        resp = admin_session.post("/admin/users", json=data)
        assert resp.status_code == 400
        assert "Password" in resp.get_json()["message"]

    def test_create_user_missing_company_name(self, admin_session, mock_db):
        data = {**self.VALID_DEALER_DATA, "company_name": ""}
        resp = admin_session.post("/admin/users", json=data)
        assert resp.status_code == 400
        assert "Company Name" in resp.get_json()["message"]

    def test_create_user_invalid_user_type(self, admin_session, mock_db):
        data = {**self.VALID_DEALER_DATA, "user_type": "superuser"}
        resp = admin_session.post("/admin/users", json=data)
        assert resp.status_code == 400
        assert "user_type" in resp.get_json()["message"]

    def test_create_user_password_mismatch(self, admin_session, mock_db):
        data = {**self.VALID_DEALER_DATA, "confirm_password": "different"}
        resp = admin_session.post("/admin/users", json=data)
        assert resp.status_code == 400
        assert "match" in resp.get_json()["message"].lower()

    def test_create_user_password_too_short(self, admin_session, mock_db):
        data = {**self.VALID_DEALER_DATA, "password": "short", "confirm_password": "short"}
        resp = admin_session.post("/admin/users", json=data)
        assert resp.status_code == 400
        assert "8 characters" in resp.get_json()["message"]

    def test_create_user_invalid_email_format(self, admin_session, mock_db):
        data = {**self.VALID_DEALER_DATA, "email": "not-an-email"}
        resp = admin_session.post("/admin/users", json=data)
        assert resp.status_code == 400
        assert "email" in resp.get_json()["message"].lower()

    def test_create_user_invalid_phone_format(self, admin_session, mock_db):
        data = {**self.VALID_DEALER_DATA, "phone_number": "123"}
        resp = admin_session.post("/admin/users", json=data)
        assert resp.status_code == 400
        assert "Phone" in resp.get_json()["message"] or "digits" in resp.get_json()["message"]

    def test_create_user_missing_gst_no(self, admin_session, mock_db):
        data = {**self.VALID_DEALER_DATA, "gst_no": ""}
        resp = admin_session.post("/admin/users", json=data)
        assert resp.status_code == 400
        assert "GST" in resp.get_json()["message"]

    def test_create_user_missing_pincode(self, admin_session, mock_db):
        data = {**self.VALID_DEALER_DATA, "pincode": ""}
        resp = admin_session.post("/admin/users", json=data)
        assert resp.status_code == 400
        assert "Pincode" in resp.get_json()["message"]

    def test_create_user_invalid_pincode_format(self, admin_session, mock_db):
        data = {**self.VALID_DEALER_DATA, "pincode": "abc"}
        resp = admin_session.post("/admin/users", json=data)
        assert resp.status_code == 400
        assert "Pincode" in resp.get_json()["message"] or "digits" in resp.get_json()["message"]

    # ── Duplicate checks ──────────────────────────────────────────────────

    def test_create_user_duplicate_email(self, admin_session, mock_db):
        mock_conn, mock_cursor = mock_db
        # Combined duplicate check returns (email_match=True, phone_match=False)
        mock_cursor.fetchone.side_effect = [(True, False)]

        resp = admin_session.post("/admin/users", json=self.VALID_DEALER_DATA)
        assert resp.status_code == 400
        assert "Email already registered" in resp.get_json()["message"]

    def test_create_user_duplicate_phone(self, admin_session, mock_db):
        mock_conn, mock_cursor = mock_db
        # Combined duplicate check returns (email_match=False, phone_match=True)
        mock_cursor.fetchone.side_effect = [(False, True)]

        resp = admin_session.post("/admin/users", json=self.VALID_DEALER_DATA)
        assert resp.status_code == 400
        assert "Phone number already registered" in resp.get_json()["message"]

    # ── Successful creation ───────────────────────────────────────────────

    @patch("app.blueprints.admin.routes.get_unique_code", return_value="12345")
    def test_create_dealer_success(self, mock_code, admin_session, mock_db):
        mock_conn, mock_cursor = mock_db
        # combined dup check → no match, INSERT RETURNING → row
        mock_cursor.fetchone.side_effect = [
            None,   # no duplicate found
            (99, "John Dealer", "123 Main St", "john@dealer.com", "dealer", "12345", ""),
        ]

        resp = admin_session.post("/admin/users", json=self.VALID_DEALER_DATA)
        assert resp.status_code == 201

        body = resp.get_json()
        assert body["status"] == "success"
        assert body["user_id"] == 99
        assert body["user"]["user_type"] == "dealer"
        assert body["user"]["dealer_code"] == "12345"
        mock_conn.commit.assert_called_once()

    @patch("app.blueprints.admin.routes.get_unique_code", return_value="67890")
    def test_create_distributor_success(self, mock_code, admin_session, mock_db):
        mock_conn, mock_cursor = mock_db
        mock_cursor.fetchone.side_effect = [
            None,   # no duplicate
            (100, "Jane Distributor", "456 Oak Ave", "jane@distributor.com", "distributor", "", "67890"),
        ]

        resp = admin_session.post("/admin/users", json=self.VALID_DISTRIBUTOR_DATA)
        assert resp.status_code == 201

        body = resp.get_json()
        assert body["status"] == "success"
        assert body["user"]["user_type"] == "distributor"
        assert body["user"]["distributor_code"] == "67890"

    @patch("app.blueprints.admin.routes.get_unique_code", return_value="11111")
    def test_create_user_is_auto_approved(self, mock_code, admin_session, mock_db):
        """Admin-created users should have status 'Approved'."""
        mock_conn, mock_cursor = mock_db
        mock_cursor.fetchone.side_effect = [
            None,   # no duplicate
            (101, "Auto User", "addr", "auto@test.com", "dealer", "11111", ""),
        ]

        resp = admin_session.post("/admin/users", json=self.VALID_DEALER_DATA)
        assert resp.status_code == 201

        # Check the INSERT SQL was called with 'Approved'
        insert_call = mock_cursor.execute.call_args_list[-1]
        insert_args = insert_call[0][1]  # positional params tuple
        assert "Approved" in insert_args


# ═══════════════════════════════════════════════════════════════════════════
# EDIT USER  –  PUT /admin/users/<user_id>
# ═══════════════════════════════════════════════════════════════════════════

class TestEditUser:
    """Tests for the PUT /admin/users/<user_id> endpoint."""

    VALID_EDIT_DATA = {
        "full_name": "Updated Name",
        "email": "updated@test.com",
        "address": "New Address",
        "phone_number": "9999999999",
        "user_type": "dealer",
        "status": "Approved",
        "company_name": "Updated Corp",
        "gst_no": "GST99999",
        "pincode": "560001",
        "distributor_code": "",
        "dealer_code": "12345",
    }

    # ── Auth ──────────────────────────────────────────────────────────────

    def test_edit_user_unauthorized(self, client, mock_db):
        resp = client.put("/admin/users/1", json=self.VALID_EDIT_DATA)
        assert resp.status_code == 401

    # ── Validation ────────────────────────────────────────────────────────

    @pytest.mark.parametrize("field", [
        "full_name", "email", "address", "phone_number",
        "user_type", "status", "company_name",
    ])
    def test_edit_user_missing_required_field(self, field, admin_session, mock_db):
        data = {**self.VALID_EDIT_DATA, field: ""}
        resp = admin_session.put("/admin/users/10", json=data)
        assert resp.status_code == 400
        assert field in resp.get_json()["message"]

    # ── Duplicate email ───────────────────────────────────────────────────

    def test_edit_user_duplicate_email(self, admin_session, mock_db):
        mock_conn, mock_cursor = mock_db
        # Combined query returns (user_exists=True, email_taken=True)
        mock_cursor.fetchone.return_value = (True, True)

        resp = admin_session.put("/admin/users/10", json=self.VALID_EDIT_DATA)
        assert resp.status_code == 400
        assert "Email already exists" in resp.get_json()["message"]

    # ── Successful edit ───────────────────────────────────────────────────

    def test_edit_user_success(self, admin_session, mock_db):
        mock_conn, mock_cursor = mock_db
        # Combined query: user exists, email not taken
        mock_cursor.fetchone.return_value = (True, False)

        resp = admin_session.put("/admin/users/10", json=self.VALID_EDIT_DATA)
        assert resp.status_code == 200

        body = resp.get_json()
        assert body["status"] == "success"
        assert body["message"] == "User updated successfully"
        mock_conn.commit.assert_called_once()

    def test_edit_user_not_found(self, admin_session, mock_db):
        """Editing a non-existent user should return 404."""
        mock_conn, mock_cursor = mock_db
        # Combined query: user does not exist
        mock_cursor.fetchone.return_value = (False, False)

        resp = admin_session.put("/admin/users/999", json=self.VALID_EDIT_DATA)
        assert resp.status_code == 404
        assert "User not found" in resp.get_json()["message"]

    def test_edit_user_updates_correct_fields(self, admin_session, mock_db):
        """Verify the UPDATE query receives the right values."""
        mock_conn, mock_cursor = mock_db
        mock_cursor.fetchone.return_value = (True, False)

        admin_session.put("/admin/users/10", json=self.VALID_EDIT_DATA)

        # The second execute call is the UPDATE (1: combined check, 2: update)
        update_call = mock_cursor.execute.call_args_list[1]
        sql = update_call[0][0]
        params = update_call[0][1]

        assert "UPDATE user_signups" in sql
        assert params[0] == "Updated Name"       # full_name
        assert params[2] == "updated@test.com"    # email
        assert params[-1] == 10                   # WHERE user_id

    def test_edit_user_with_optional_fields_empty(self, admin_session, mock_db):
        """gst_no, pincode, distributor_code, dealer_code can be empty."""
        mock_conn, mock_cursor = mock_db
        mock_cursor.fetchone.return_value = (True, False)

        data = {**self.VALID_EDIT_DATA, "gst_no": "", "pincode": "", "distributor_code": "", "dealer_code": ""}
        resp = admin_session.put("/admin/users/10", json=data)
        assert resp.status_code == 200

    def test_edit_user_preserves_dealer_code(self, admin_session, mock_db):
        """Ensure dealer_code is passed through to the UPDATE."""
        mock_conn, mock_cursor = mock_db
        mock_cursor.fetchone.return_value = (True, False)

        data = {**self.VALID_EDIT_DATA, "dealer_code": "55555"}
        admin_session.put("/admin/users/10", json=data)

        update_call = mock_cursor.execute.call_args_list[1]
        params = update_call[0][1]
        assert "55555" in params

    def test_edit_user_db_error_returns_500(self, admin_session, mock_db):
        """Database errors should return 500."""
        mock_conn, mock_cursor = mock_db
        mock_cursor.fetchone.side_effect = Exception("Connection lost")

        resp = admin_session.put("/admin/users/10", json=self.VALID_EDIT_DATA)
        assert resp.status_code == 500
        assert "Failed to update user" in resp.get_json()["message"]
