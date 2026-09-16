"""Relational data store for Zey Brow customer ownership system.

Backed entirely by Postgres/Supabase -- there is no local SQLite file. Every
public method opens its own connection and commits/closes around its unit of
work, mirroring the previous sqlite3-per-call pattern so callers didn't need
to change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd

from sms_campaign.db import get_connection


@dataclass
class SyncResult:
    inserted: int = 0
    updated: int = 0
    deactivated: int = 0
    unchanged: int = 0
    errors: list[str] | None = None


class ZeyDataStore:
    """Unified data store for all Zey Brow business data."""

    def __init__(self, db_path=None):
        # db_path is accepted only so older call sites (scripts, tests) that
        # used to point at a SQLite file keep working unchanged. Supabase
        # Postgres (DATABASE_URL) is now the only store; the value is
        # otherwise unused.
        self.db_path = db_path

    def _conn(self):
        return get_connection()

    @staticmethod
    def _rows_to_df(cur) -> pd.DataFrame:
        columns = [col.name for col in cur.description] if cur.description else []
        rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=columns)
        return pd.DataFrame(rows, columns=columns)

    # ── Customers ──────────────────────────────────────────────

    def sync_customer_from_webhook(self, payload: dict, action: str) -> dict:
        """Apply a single Vagaro customer webhook event safely.

        Scoped to exactly the one customer_id the event names -- this can
        never touch any other row, unlike the bulk report import. Never
        writes sms_opt_out/email_opt_out/active on create or update: those
        stay under the business owner's explicit control (Supabase/manual).
        A 'deleted' action deactivates only this one customer.
        """
        enc_id = self._str(payload.get("customerId"))
        if not enc_id:
            return {"status": "skipped", "reason": "missing customerId"}

        conn = self._conn()
        try:
            existing = conn.execute(
                "SELECT customer_id FROM customers WHERE enc_user_id=%s", (enc_id,)
            ).fetchone()

            if action == "deleted":
                if not existing:
                    return {"status": "skipped", "reason": "unknown customer"}
                conn.execute(
                    "UPDATE customers SET active=0, updated_at=now()::text WHERE customer_id=%s",
                    (existing["customer_id"],),
                )
                conn.commit()
                return {"status": "deactivated", "customer_id": existing["customer_id"]}

            fields = {
                "first_name": self._str(payload.get("customerFirstName")),
                "last_name": self._str(payload.get("customerLastName")),
                "email": self._str(payload.get("email")),
                "address": self._str(payload.get("streetAddress")),
                "city": self._str(payload.get("city")),
                "state": self._str(payload.get("regionCode")),
                "zip": self._str(payload.get("postalCode")),
            }
            mobile = self._normalize_phone(
                payload.get("mobilePhone") or payload.get("dayPhone") or payload.get("nightPhone")
            )

            if existing:
                # Only overwrite columns the webhook actually supplied a
                # value for -- a blank field in the payload must not erase
                # data that a fuller import already captured.
                set_fields = {k: v for k, v in fields.items() if v is not None}
                if mobile:
                    set_fields["mobile"] = mobile
                if not set_fields:
                    return {"status": "noop", "customer_id": existing["customer_id"]}
                set_fields["customer_id"] = existing["customer_id"]
                assignments = ", ".join(f"{k}=%({k})s" for k in set_fields if k != "customer_id")
                conn.execute(
                    f"UPDATE customers SET {assignments}, updated_at=now()::text "
                    f"WHERE customer_id=%(customer_id)s",
                    set_fields,
                )
                conn.commit()
                return {"status": "updated", "customer_id": existing["customer_id"]}

            if not mobile:
                return {"status": "skipped", "reason": "no phone number available"}
            fields["enc_user_id"] = enc_id
            fields["mobile"] = mobile
            cols = ", ".join(fields.keys())
            placeholders = ", ".join(f"%({k})s" for k in fields.keys())
            cur = conn.execute(
                f"INSERT INTO customers ({cols}) VALUES ({placeholders}) RETURNING customer_id",
                fields,
            )
            new_id = cur.fetchone()["customer_id"]
            conn.commit()
            return {"status": "created", "customer_id": new_id}
        finally:
            conn.close()

    def sync_customers(self, df: pd.DataFrame) -> SyncResult:
        """Sync Vagaro customer report into the customers table."""
        if df.empty:
            return SyncResult()

        source_count = len(df)
        df = df.copy()
        df["_normalized_phone"] = df.apply(
            lambda r: self._normalize_phone(r.get("Mobile", r.get("CellPhone"))), axis=1
        )
        invalid_count = int(df["_normalized_phone"].isna().sum())

        valid_df = df[df["_normalized_phone"].notna()].copy()
        valid_df["_sort_date"] = valid_df.apply(
            lambda r: self._str(r.get("LastVisited")) or self._str(r.get("CustomerSince")) or "",
            axis=1,
        )
        valid_df = valid_df.sort_values("_sort_date", ascending=False)
        deduped_df = valid_df.drop_duplicates(subset="_normalized_phone", keep="first")

        duplicate_count = len(valid_df) - len(deduped_df)
        unique_count = len(deduped_df)

        if source_count != invalid_count + duplicate_count + unique_count:
            raise ValueError(
                f"Reconciliation mismatch: source={source_count} != "
                f"invalid={invalid_count} + duplicate={duplicate_count} + unique={unique_count}"
            )
        print(
            f"[sync_customers] reconciliation OK: source={source_count} "
            f"= invalid={invalid_count} + duplicate={duplicate_count} + unique={unique_count}"
        )
        if duplicate_count:
            print(f"[sync_customers] collapsed {duplicate_count} duplicate-phone rows")

        df = deduped_df.drop(columns=["_normalized_phone", "_sort_date"])

        result = SyncResult()
        conn = self._conn()
        try:
            existing = {
                row["mobile"]: dict(row)
                for row in conn.execute("SELECT * FROM customers").fetchall()
            }

            for _, row in df.iterrows():
                mobile = self._normalize_phone(
                    row.get("Mobile", row.get("CellPhone", row.get("CustomerCell")))
                )
                if not mobile:
                    result.errors = (result.errors or []) + [f"Invalid phone: {row.get('Mobile')}"]
                    continue

                vagaro_id = str(row.get("UserID", ""))
                data = {
                    "vagaro_user_id": vagaro_id,
                    "mobile": mobile,
                    "first_name": self._str(row.get("FirstName")),
                    "last_name": self._str(row.get("LastName")),
                    "email": self._str(row.get("EmailAddress")),
                    "birthdate": self._str(row.get("BirthDate")),
                    "gender": self._str(row.get("Gender")),
                    "address": self._str(row.get("StreetAddress")),
                    "city": self._str(row.get("City")),
                    "state": self._str(row.get("State")),
                    "zip": self._str(row.get("Zip")),
                    "apt_suite": self._str(row.get("StreetNo")),
                    "customer_since": self._str(row.get("CustomerSince")),
                    "last_visit": self._str(row.get("LastVisited")),
                    "membership": self._str(row.get("MembershipName")),
                    "referred_by": self._str(row.get("ReferredBy")),
                    "online_booking": self._str(row.get("OnlineBooking")),
                    "tags": self._str(row.get("GeneralTag")),
                # Raw Vagaro fields
                "acquisition": self._str(row.get("Acquisition")),
                "bank_name_number": self._str(row.get("BankNameNumber")),
                "cdn_url": self._str(row.get("CDNUrl")),
                "country_id": self._str(row.get("CountryID")),
                "custom_fields_groups": self._str(row.get("CustomFieldsGroups")),
                "day_phone": self._str(row.get("DayPhone")),
                "email_failed_reason": self._str(row.get("EmailFailedReason")),
                "email_format": self._str(row.get("EmailFormat")),
                "general_tag": self._str(row.get("GeneralTag")),
                "is_valid_email": self._int(row.get("IsValidEmail")),
                "is_valid_text": self._int(row.get("IsValidText")),
                "night_phone": self._str(row.get("NightPhone")),
                "no_of_booking": self._int(row.get("NoOfBooking")),
                "no_of_class_booked": self._int(row.get("NoOfClassBooked")),
                "no_of_class_check_ins": self._int(row.get("NoOfClassCheckIns")),
                "no_show_cancel": self._int(row.get("NoShowCancel")),
                "photo": self._str(row.get("Photo")),
                "service_providers": self._str(row.get("ServiceProviders")),
                "street_address": self._str(row.get("StreetAddress")),
                "street_no": self._str(row.get("StreetNo")),
                "text_failed_reason": self._str(row.get("TextFailedReason")),
                "total_amount_paid": self._float(row.get("TotalAmountPaid")),
                "total_points_accumulated": self._float(row.get("TotalPointsAccumlated")),
                "ucc_no": self._str(row.get("UccNo")),
                "ucc_type": self._str(row.get("UccType")),
                "enc_user_id": self._str(row.get("encUserId")),
                "raw_json": json.dumps(row.to_dict(), default=str),
                }

                # Check if this vagaro_user_id already exists (different mobile)
                existing_by_vagaro = conn.execute(
                    "SELECT customer_id, mobile FROM customers WHERE vagaro_user_id=%s AND mobile!=%s",
                    (vagaro_id, mobile),
                ).fetchone() if vagaro_id else None

                if existing_by_vagaro:
                    # Merge: keep the old mobile's record, update vagaro_id on the new one
                    # Delete the duplicate vagaro_id entry
                    conn.execute(
                        "UPDATE customers SET vagaro_user_id=NULL WHERE customer_id=%s",
                        (existing_by_vagaro["customer_id"],),
                    )

                if mobile in existing:
                    # Preserve opt-out status, communication preference, and tracking
                    old = existing[mobile]
                    data["sms_opt_out"] = old["sms_opt_out"]
                    data["opt_out_date"] = old["opt_out_date"]
                    data["email_opt_out"] = old["email_opt_out"]
                    data["communication_preference"] = old["communication_preference"]
                    data["active"] = 1
                    data["updated_at"] = datetime.utcnow().isoformat()

                    conn.execute(
                        """UPDATE customers SET
                            vagaro_user_id=%(vagaro_user_id)s, first_name=%(first_name)s,
                            last_name=%(last_name)s, email=%(email)s, birthdate=%(birthdate)s,
                            gender=%(gender)s, address=%(address)s, city=%(city)s, state=%(state)s,
                            zip=%(zip)s, apt_suite=%(apt_suite)s, customer_since=%(customer_since)s,
                            last_visit=%(last_visit)s, membership=%(membership)s,
                            referred_by=%(referred_by)s, online_booking=%(online_booking)s,
                            tags=%(tags)s, sms_opt_out=%(sms_opt_out)s, opt_out_date=%(opt_out_date)s,
                            email_opt_out=%(email_opt_out)s, communication_preference=%(communication_preference)s,
                            active=%(active)s, updated_at=%(updated_at)s,
                            acquisition=%(acquisition)s, bank_name_number=%(bank_name_number)s,
                            cdn_url=%(cdn_url)s, country_id=%(country_id)s,
                            custom_fields_groups=%(custom_fields_groups)s, day_phone=%(day_phone)s,
                            email_failed_reason=%(email_failed_reason)s, email_format=%(email_format)s,
                            general_tag=%(general_tag)s, is_valid_email=%(is_valid_email)s,
                            is_valid_text=%(is_valid_text)s, night_phone=%(night_phone)s,
                            no_of_booking=%(no_of_booking)s, no_of_class_booked=%(no_of_class_booked)s,
                            no_of_class_check_ins=%(no_of_class_check_ins)s, no_show_cancel=%(no_show_cancel)s,
                            photo=%(photo)s, service_providers=%(service_providers)s,
                            street_address=%(street_address)s, street_no=%(street_no)s,
                            text_failed_reason=%(text_failed_reason)s, total_amount_paid=%(total_amount_paid)s,
                            total_points_accumulated=%(total_points_accumulated)s, ucc_no=%(ucc_no)s,
                            ucc_type=%(ucc_type)s, enc_user_id=%(enc_user_id)s, raw_json=%(raw_json)s
                        WHERE mobile=%(mobile)s""",
                        data,
                    )
                    result.updated += 1
                else:
                    data["active"] = 1
                    conn.execute(
                        """INSERT INTO customers
                            (vagaro_user_id, mobile, first_name, last_name, email,
                             birthdate, gender, address, city, state, zip, apt_suite,
                             customer_since, last_visit, membership, referred_by,
                             online_booking, tags, active,
                             acquisition, bank_name_number, cdn_url, country_id,
                             custom_fields_groups, day_phone, email_failed_reason, email_format,
                             general_tag, is_valid_email, is_valid_text, night_phone,
                             no_of_booking, no_of_class_booked, no_of_class_check_ins, no_show_cancel,
                             photo, service_providers, street_address, street_no,
                             text_failed_reason, total_amount_paid, total_points_accumulated,
                             ucc_no, ucc_type, enc_user_id, raw_json)
                        VALUES
                            (%(vagaro_user_id)s, %(mobile)s, %(first_name)s, %(last_name)s, %(email)s,
                             %(birthdate)s, %(gender)s, %(address)s, %(city)s, %(state)s, %(zip)s, %(apt_suite)s,
                             %(customer_since)s, %(last_visit)s, %(membership)s, %(referred_by)s,
                             %(online_booking)s, %(tags)s, %(active)s,
                             %(acquisition)s, %(bank_name_number)s, %(cdn_url)s, %(country_id)s,
                             %(custom_fields_groups)s, %(day_phone)s, %(email_failed_reason)s, %(email_format)s,
                             %(general_tag)s, %(is_valid_email)s, %(is_valid_text)s, %(night_phone)s,
                             %(no_of_booking)s, %(no_of_class_booked)s, %(no_of_class_check_ins)s, %(no_show_cancel)s,
                             %(photo)s, %(service_providers)s, %(street_address)s, %(street_no)s,
                             %(text_failed_reason)s, %(total_amount_paid)s, %(total_points_accumulated)s,
                             %(ucc_no)s, %(ucc_type)s, %(enc_user_id)s, %(raw_json)s)""",
                        data,
                    )
                    result.inserted += 1

            # Deactivate customers no longer in Vagaro export
            exported_mobiles = set()
            for _, row in df.iterrows():
                mobile = self._normalize_phone(row.get("Mobile", row.get("CellPhone")))
                if mobile:
                    exported_mobiles.add(mobile)

            for mobile, old in existing.items():
                if mobile not in exported_mobiles and old["active"]:
                    conn.execute(
                        "UPDATE customers SET active=0, updated_at=%s WHERE mobile=%s",
                        (datetime.utcnow().isoformat(), mobile),
                    )
                    result.deactivated += 1

            conn.commit()
        finally:
            conn.close()

        return result

    def set_opt_out(self, mobile: str, opt_out: bool = True) -> None:
        """Set SMS opt-out status for a customer."""
        conn = self._conn()
        try:
            value = 1 if opt_out else 0
            date = datetime.utcnow().isoformat() if opt_out else None
            conn.execute(
                "UPDATE customers SET sms_opt_out=%s, opt_out_date=%s WHERE mobile=%s",
                (value, date, mobile),
            )
            conn.commit()
        finally:
            conn.close()

    def get_active_customers(self) -> pd.DataFrame:
        """Get all active customers as DataFrame."""
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT * FROM customers WHERE active=1 ORDER BY last_name, first_name"
            )
            return self._rows_to_df(cur)
        finally:
            conn.close()

    def get_opted_out_mobiles(self) -> set[str]:
        """Get set of opted-out mobile numbers."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT mobile FROM customers WHERE sms_opt_out=1"
            ).fetchall()
            return {row["mobile"] for row in rows}
        finally:
            conn.close()

    # ── Services ───────────────────────────────────────────────

    def sync_services(self, df: pd.DataFrame) -> SyncResult:
        """Sync Vagaro appointment/service data."""
        if df.empty:
            return SyncResult()

        result = SyncResult()
        conn = self._conn()
        try:
            for _, row in df.iterrows():
                mobile = self._normalize_phone(row.get("Mobile"))
                customer_id = None
                if mobile:
                    cust = conn.execute(
                        "SELECT customer_id FROM customers WHERE mobile=%s", (mobile,)
                    ).fetchone()
                    if cust:
                        customer_id = cust["customer_id"]
                if customer_id is None:
                    vagaro_customer_id = self._str(
                        row.get("CustomerID", row.get("CustomerId", row.get("UserID")))
                    )
                    if vagaro_customer_id:
                        # Webhook payloads send Vagaro's encrypted customer id
                        # (matches enc_user_id); browser-scraped imports send
                        # the plain numeric id (matches vagaro_user_id). Try
                        # both so a customer already on file always resolves
                        # regardless of which source this row came from.
                        cust = conn.execute(
                            "SELECT customer_id FROM customers WHERE vagaro_user_id=%s OR enc_user_id=%s",
                            (vagaro_customer_id, vagaro_customer_id),
                        ).fetchone()
                        if cust:
                            customer_id = cust["customer_id"]

                vagaro_appt_id = str(row.get("AppointmentID", row.get("ID", "")))
                data = {
                    "customer_id": customer_id,
                    "employee_name": self._resolve_employee_name(
                        conn, row.get("Employee", row.get("Staff"))
                    ),
                    "service_name": self._str(row.get("Service", row.get("ServiceName"))),
                    "service_date": self._str(row.get("Date", row.get("AppointmentDate"))),
                    "duration_min": self._int(row.get("Duration")),
                    "amount_paid": self._float(row.get("Amount", row.get("Total"))),
                    "notes": self._str(row.get("Notes")),
                    "vagaro_appt_id": vagaro_appt_id,
                }

                existing = conn.execute(
                    "SELECT service_id FROM services WHERE vagaro_appt_id=%s",
                    (vagaro_appt_id,),
                ).fetchone()

                if existing:
                    conn.execute(
                        """UPDATE services SET customer_id=%(customer_id)s,
                            employee_name=%(employee_name)s, service_name=%(service_name)s,
                            service_date=%(service_date)s, duration_min=%(duration_min)s,
                            amount_paid=%(amount_paid)s, notes=%(notes)s
                        WHERE vagaro_appt_id=%(vagaro_appt_id)s""",
                        data,
                    )
                    result.updated += 1
                else:
                    conn.execute(
                        """INSERT INTO services
                            (customer_id, employee_name, service_name, service_date,
                             duration_min, amount_paid, notes, vagaro_appt_id)
                        VALUES
                            (%(customer_id)s, %(employee_name)s, %(service_name)s, %(service_date)s,
                             %(duration_min)s, %(amount_paid)s, %(notes)s, %(vagaro_appt_id)s)""",
                        data,
                    )
                    result.inserted += 1

            conn.commit()
        finally:
            conn.close()

        return result

    # ── Transactions ───────────────────────────────────────────

    def sync_transactions(self, df: pd.DataFrame) -> SyncResult:
        """Sync Vagaro Reports > Sales > Transaction List data."""
        if df.empty:
            return SyncResult()

        result = SyncResult()
        conn = self._conn()
        try:
            for _, row in df.iterrows():
                mobile = self._normalize_phone(row.get("Mobile", row.get("CellPhone")))
                customer_id = None
                if mobile:
                    cust = conn.execute(
                        "SELECT customer_id FROM customers WHERE mobile=%s", (mobile,)
                    ).fetchone()
                    if cust:
                        customer_id = cust["customer_id"]
                if customer_id is None:
                    vagaro_customer_id = self._str(
                        row.get("CustomerID", row.get("CustomerId", row.get("UserID")))
                    )
                    if vagaro_customer_id:
                        # Webhook payloads send Vagaro's encrypted customer id
                        # (matches enc_user_id); browser-scraped imports send
                        # the plain numeric id (matches vagaro_user_id). Try
                        # both so a customer already on file always resolves
                        # regardless of which source this row came from.
                        cust = conn.execute(
                            "SELECT customer_id FROM customers WHERE vagaro_user_id=%s OR enc_user_id=%s",
                            (vagaro_customer_id, vagaro_customer_id),
                        ).fetchone()
                        if cust:
                            customer_id = cust["customer_id"]

                # ID identifies each line item in Vagaro's report. TransactionID
                # identifies the checkout and repeats when it has multiple items.
                vagaro_transaction_id = str(
                    row.get("ID", row.get("TransactionID", row.get("TransactionId", "")))
                )
                if not vagaro_transaction_id:
                    result.errors = (result.errors or []) + ["Missing transaction ID"]
                    continue

                customer_name = self._str(row.get("CustomerName", row.get("Customer")))
                if customer_name is None and customer_id is not None:
                    # Vagaro's real transaction webhook never sends a name,
                    # only the encrypted customerId -- fill it in from the
                    # customer record we already resolved instead of leaving
                    # a linked transaction with a blank name forever.
                    known = conn.execute(
                        "SELECT first_name, last_name FROM customers WHERE customer_id=%s",
                        (customer_id,),
                    ).fetchone()
                    if known:
                        customer_name = self._str(
                            " ".join(part for part in (known["first_name"], known["last_name"]) if part)
                        )

                data = {
                    "vagaro_transaction_id": vagaro_transaction_id,
                    "customer_id": customer_id,
                    "customer_name": customer_name,
                    "employee_name": self._resolve_employee_name(
                        conn,
                        row.get("Employee", row.get("Staff", row.get("ServiceProviderName", row.get("CheckedOutBy")))),
                    ),
                    "transaction_date": self._str(row.get("TransactionDate", row.get("Date"))),
                    "transaction_type": self._str(row.get("TransactionType", row.get("Type", row.get("TranType")))),
                    "payment_method": self._str(row.get("PaymentMethod", row.get("PaymentType", row.get("CCType")))),
                    "subtotal": self._float(row.get("SubTotal", row.get("Subtotal", row.get("Price")))),
                    "tax": self._float(row.get("Tax", row.get("TaxAmount"))),
                    "tip": self._float(row.get("Tip", row.get("TipAmount"))),
                    "discount": self._float(row.get("Discount", row.get("DiscountAmount"))),
                    "total_amount": self._float(
                        row.get("Total", row.get("TotalAmount", row.get("GrandTotal", row.get("AmountPaid"))))
                    ),
                    "status": self._str(row.get("Status")),
                    "notes": self._str(row.get("Notes")),
                    "raw_json": json.dumps(row.to_dict(), default=str),
                }

                existing = conn.execute(
                    "SELECT transaction_id FROM transactions WHERE vagaro_transaction_id=%s",
                    (vagaro_transaction_id,),
                ).fetchone()

                if existing:
                    conn.execute(
                        """UPDATE transactions SET customer_id=%(customer_id)s,
                            customer_name=%(customer_name)s, employee_name=%(employee_name)s,
                            transaction_date=%(transaction_date)s, transaction_type=%(transaction_type)s,
                            payment_method=%(payment_method)s, subtotal=%(subtotal)s, tax=%(tax)s,
                            tip=%(tip)s, discount=%(discount)s, total_amount=%(total_amount)s,
                            status=%(status)s, notes=%(notes)s, raw_json=%(raw_json)s
                        WHERE vagaro_transaction_id=%(vagaro_transaction_id)s""",
                        data,
                    )
                    result.updated += 1
                else:
                    conn.execute(
                        """INSERT INTO transactions
                            (vagaro_transaction_id, customer_id, customer_name, employee_name,
                             transaction_date, transaction_type, payment_method, subtotal, tax,
                             tip, discount, total_amount, status, notes, raw_json)
                        VALUES
                            (%(vagaro_transaction_id)s, %(customer_id)s, %(customer_name)s, %(employee_name)s,
                             %(transaction_date)s, %(transaction_type)s, %(payment_method)s, %(subtotal)s, %(tax)s,
                             %(tip)s, %(discount)s, %(total_amount)s, %(status)s, %(notes)s, %(raw_json)s)""",
                        data,
                    )
                    result.inserted += 1

                # A completed transaction is the strongest signal that the
                # customer actually showed up -- move last_visit forward to
                # it (never backward, so an out-of-order/backfilled webhook
                # can't clobber a truer, more recent value already on file).
                if customer_id is not None and data["transaction_date"]:
                    visit_date = self._parse_visit_date(data["transaction_date"])
                    if visit_date is not None:
                        current = conn.execute(
                            "SELECT last_visit FROM customers WHERE customer_id=%s", (customer_id,)
                        ).fetchone()
                        current_visit = self._parse_visit_date(current["last_visit"]) if current else None
                        if current_visit is None or current_visit < visit_date:
                            conn.execute(
                                "UPDATE customers SET last_visit=%s, updated_at=now()::text WHERE customer_id=%s",
                                (visit_date.isoformat(), customer_id),
                            )

            conn.commit()
        finally:
            conn.close()

        return result

    # ── Employees ──────────────────────────────────────────────

    def sync_employees(self, df: pd.DataFrame) -> SyncResult:
        """Sync Vagaro employee data."""
        if df.empty:
            return SyncResult()

        result = SyncResult()
        conn = self._conn()
        try:
            for _, row in df.iterrows():
                vagaro_id = str(row.get("EmployeeID", row.get("ID", "")))
                data = {
                    "vagaro_emp_id": vagaro_id,
                    "name": self._str(row.get("Name", row.get("EmployeeName"))),
                    "role": self._str(row.get("Role", row.get("Title"))),
                    "phone": self._str(row.get("Phone")),
                    "email": self._str(row.get("EmailAddress")),
                    "active": 1,
                }

                existing = conn.execute(
                    "SELECT employee_id FROM employees WHERE vagaro_emp_id=%s",
                    (vagaro_id,),
                ).fetchone()

                if existing:
                    conn.execute(
                        """UPDATE employees SET name=%(name)s, role=%(role)s,
                            phone=%(phone)s, email=%(email)s, active=%(active)s,
                            updated_at=now()::text
                        WHERE vagaro_emp_id=%(vagaro_emp_id)s""",
                        data,
                    )
                    result.updated += 1
                else:
                    conn.execute(
                        """INSERT INTO employees
                            (vagaro_emp_id, name, role, phone, email, active)
                        VALUES
                            (%(vagaro_emp_id)s, %(name)s, %(role)s, %(phone)s, %(email)s, %(active)s)""",
                        data,
                    )
                    result.inserted += 1

            conn.commit()
        finally:
            conn.close()

        return result

    # ── SMS History ────────────────────────────────────────────

    def log_sms(
        self,
        customer_id: int,
        campaign_type: str,
        message_text: str,
        status: str,
        twilio_sid: str | None = None,
        error_message: str | None = None,
        campaign_row: int | None = None,
    ) -> None:
        """Log an SMS send event."""
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO sms_history
                    (customer_id, campaign_type, message_text, status,
                     twilio_sid, error_message, campaign_row)
                VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (customer_id, campaign_type, message_text, status,
                 twilio_sid, error_message, campaign_row),
            )
            # Also update customer's last SMS tracking
            conn.execute(
                """UPDATE customers SET
                    updated_at=now()::text
                WHERE customer_id=%s""",
                (customer_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def get_last_sms_date(self, customer_id: int) -> Optional[str]:
        """Get the last SMS date for a customer."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT sent_at FROM sms_history WHERE customer_id=%s ORDER BY sent_at DESC LIMIT 1",
                (customer_id,),
            ).fetchone()
            return row["sent_at"] if row else None
        finally:
            conn.close()

    def was_review_sent(self, customer_id: int) -> bool:
        """Check if a review request was already sent to this customer."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM sms_history WHERE customer_id=%s AND campaign_type='Review'",
                (customer_id,),
            ).fetchone()
            return row["cnt"] > 0
        finally:
            conn.close()

    def log_email(
        self,
        customer_id: int,
        campaign_type: str,
        subject: str,
        body: str,
        status: str,
        error_message: str | None = None,
        campaign_row: int | None = None,
    ) -> None:
        """Log an email attempt without mixing it into SMS history."""
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO email_history
                    (customer_id, campaign_type, subject, body, status,
                     error_message, campaign_row)
                VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (customer_id, campaign_type, subject, body, status, error_message, campaign_row),
            )
            conn.execute(
                "UPDATE customers SET updated_at=now()::text WHERE customer_id=%s",
                (customer_id,),
            )
            conn.commit()
        finally:
            conn.close()

    # ── Campaigns ──────────────────────────────────────────────

    def load_campaigns(self) -> pd.DataFrame:
        """Load active campaigns."""
        conn = self._conn()
        try:
            cur = conn.execute("SELECT * FROM campaigns WHERE active=1 ORDER BY rank")
            return self._rows_to_df(cur)
        finally:
            conn.close()

    def update_campaign_status(self, campaign_id: int, status: str) -> None:
        """Update campaign processing status."""
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE campaigns SET process_date=now()::text, process_status=%s WHERE campaign_id=%s",
                (status, campaign_id),
            )
            conn.commit()
        finally:
            conn.close()

    def import_campaigns_from_dataframe(self, df: pd.DataFrame) -> SyncResult:
        """Import campaign definitions from a DataFrame (e.g., from campaigns.xlsx)."""
        result = SyncResult()
        conn = self._conn()
        try:
            for _, row in df.iterrows():
                data = {
                    "text_prompt": self._str(row.get("Text/Prompt")),
                    "character_limit": self._int(row.get("SMS Text Character Limit", 160)),
                    "campaign_type": self._str(row.get("Type (Campaing / Reminder)", "Campaign")),
                    "filter_last_visit_days": self._int(row.get("Filter-Last Visit Days")),
                    "filter_last_sms_days": self._int(row.get("Filter-Last SMS Day")),
                    "rank": self._int(row.get("Rank", 999)),
                    "process_date": self._str(row.get("Campaign Process Date")),
                    "process_status": self._str(row.get("Campaign Process Status")),
                    "channels": self._str(row.get("Channels", row.get("Channel", "sms"))) or "sms",
                    "email_subject": self._str(row.get("Email Subject")),
                    "email_html": self._str(row.get("Email HTML")),
                    "approved": self._int(row.get("Approved", 0)) or 0,
                    "test_recipients": self._str(row.get("Test Recipients")),
                }

                # Check if similar campaign exists (by text_prompt + type)
                existing = conn.execute(
                    "SELECT campaign_id FROM campaigns WHERE text_prompt=%s AND campaign_type=%s",
                    (data["text_prompt"], data["campaign_type"]),
                ).fetchone()

                if existing:
                    conn.execute(
                        """UPDATE campaigns SET
                            character_limit=%(character_limit)s,
                            filter_last_visit_days=%(filter_last_visit_days)s,
                            filter_last_sms_days=%(filter_last_sms_days)s,
                            rank=%(rank)s, process_date=%(process_date)s,
                            process_status=%(process_status)s,
                            channels=%(channels)s, email_subject=%(email_subject)s,
                            email_html=%(email_html)s,
                            approved=%(approved)s, test_recipients=%(test_recipients)s,
                            updated_at=now()::text
                        WHERE campaign_id=%(campaign_id)s""",
                        {**data, "campaign_id": existing["campaign_id"]},
                    )
                    result.updated += 1
                else:
                    conn.execute(
                        """INSERT INTO campaigns
                            (text_prompt, character_limit, campaign_type,
                             filter_last_visit_days, filter_last_sms_days,
                             rank, process_date, process_status,
                             channels, email_subject, email_html,
                             approved, test_recipients)
                        VALUES
                            (%(text_prompt)s, %(character_limit)s, %(campaign_type)s,
                             %(filter_last_visit_days)s, %(filter_last_sms_days)s,
                            %(rank)s, %(process_date)s, %(process_status)s,
                            %(channels)s, %(email_subject)s, %(email_html)s,
                            %(approved)s, %(test_recipients)s)""",
                        data,
                    )
                    result.inserted += 1

            conn.commit()
        finally:
            conn.close()

        return result

    # ── Sync Log ───────────────────────────────────────────────

    def log_sync(
        self,
        source: str,
        fetched: int,
        inserted: int,
        updated: int,
        deactivated: int,
        errors: str | None = None,
        duration: float | None = None,
    ) -> None:
        """Log a sync run."""
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO sync_log
                    (source, records_fetched, records_inserted, records_updated,
                     records_deactivated, errors, duration_sec)
                VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (source, fetched, inserted, updated, deactivated, errors, duration),
            )
            conn.commit()
        finally:
            conn.close()

    # ── Export to Sheets ───────────────────────────────────────

    def export_table(self, table: str) -> pd.DataFrame:
        """Export any table as a DataFrame for Google Sheets mirroring."""
        conn = self._conn()
        try:
            cur = conn.execute(f"SELECT * FROM {table}")
            frame = self._rows_to_df(cur)
        finally:
            conn.close()

        if table != "transactions" or "raw_json" not in frame.columns:
            return frame

        # Keep the normalized transaction fields for joins and reporting, but
        # also expose every field from Vagaro's original line-item payload.
        # Previously those fields were only visible inside raw_json, which
        # made the Sheet appear to lose values such as ServiceName, Price,
        # CheckedOutByID, and payment breakdowns.
        source_rows: list[dict] = []
        source_columns: list[str] = []
        for raw in frame["raw_json"]:
            try:
                payload = json.loads(raw) if raw else {}
            except (TypeError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            source_rows.append(payload)
            for key in payload:
                if key not in source_columns and key not in frame.columns:
                    source_columns.append(key)

        frame = frame.assign(**{
            key: [
                json.dumps(payload[key], ensure_ascii=False)
                if isinstance(payload.get(key), (dict, list))
                else payload.get(key, "")
                for payload in source_rows
            ]
            for key in source_columns
        })

        if source_columns:
            base_columns = [column for column in frame.columns if column not in source_columns]
            raw_index = base_columns.index("raw_json")
            frame = frame[
                base_columns[:raw_index] + source_columns + base_columns[raw_index:]
            ]
        return frame

    def get_stats(self) -> dict:
        """Get summary statistics."""
        conn = self._conn()
        try:
            def scalar(sql: str):
                return conn.execute(sql).fetchone()["c"]

            return {
                "customers_total": scalar("SELECT COUNT(*) AS c FROM customers"),
                "customers_active": scalar("SELECT COUNT(*) AS c FROM customers WHERE active=1"),
                "customers_opted_out": scalar("SELECT COUNT(*) AS c FROM customers WHERE sms_opt_out=1"),
                "services_total": scalar("SELECT COUNT(*) AS c FROM services"),
                "transactions_total": scalar("SELECT COUNT(*) AS c FROM transactions"),
                "employees_active": scalar("SELECT COUNT(*) AS c FROM employees WHERE active=1"),
                "sms_sent_total": scalar("SELECT COUNT(*) AS c FROM sms_history"),
                "email_sent_total": scalar("SELECT COUNT(*) AS c FROM email_history"),
                "campaigns_active": scalar("SELECT COUNT(*) AS c FROM campaigns WHERE active=1"),
                "last_sync": scalar("SELECT MAX(sync_date) AS c FROM sync_log"),
            }
        finally:
            conn.close()

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _normalize_phone(phone) -> Optional[str]:
        """Normalize phone to E.164 format."""
        if phone is None or (isinstance(phone, float) and pd.isna(phone)):
            return None
        phone_str = str(phone).strip()
        if phone_str.endswith(".0"):
            phone_str = phone_str[:-2]
        clean = "".join(c for c in phone_str if c.isalnum() or c == "+")
        if not clean:
            return None
        if not clean.startswith("+"):
            if len(clean) == 10:
                clean = f"+1{clean}"
            elif len(clean) == 11 and clean.startswith("1"):
                clean = f"+{clean}"
            else:
                clean = f"+{clean}"
        return clean

    def _resolve_employee_name(self, conn, raw_value) -> Optional[str]:
        """Resolve a webhook/import employee reference to a real name.

        Webhook payloads send Vagaro's encrypted staff id (matches
        enc_emp_id); browser-scraped imports already send a plain name or
        the plain numeric id (matches vagaro_emp_id). If neither column
        matches, the raw value is kept as-is so nothing is silently lost.
        """
        value = self._str(raw_value)
        if not value:
            return None
        row = conn.execute(
            "SELECT name FROM employees WHERE vagaro_emp_id=%s OR enc_emp_id=%s",
            (value, value),
        ).fetchone()
        if row:
            return row["name"]
        # Solo-practice shortcut: Zey Brow & Wax has exactly one active
        # staff member as of 2026-09-15. Vagaro sends more than one
        # distinct encrypted id for the same person across event types, so
        # an unmatched id is still safely them rather than an unknown
        # employee -- as long as there truly is only one on file.
        solo = conn.execute("SELECT name FROM employees WHERE active=1").fetchall()
        if len(solo) == 1:
            return solo[0]["name"]
        return value

    @staticmethod
    def _str(val) -> Optional[str]:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        s = str(val).strip()
        return s if s and s != "---" and s.lower() != "nan" else None

    @staticmethod
    def _int(val) -> Optional[int]:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        try:
            return int(float(str(val).strip()))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_visit_date(value):
        """Best-effort parse of a date/timestamp string to a plain date,
        tolerating whatever format is already on file (legacy bulk imports
        and webhook-derived ISO timestamps look nothing alike)."""
        if not value:
            return None
        parsed = pd.to_datetime(value, errors="coerce", utc=True)
        if parsed is None or pd.isna(parsed):
            return None
        return parsed.date()

    @staticmethod
    def _float(val) -> Optional[float]:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        try:
            s = str(val).strip().replace("$", "").replace(",", "")
            return float(s)
        except (ValueError, TypeError):
            return None
