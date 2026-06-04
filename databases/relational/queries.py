"""
TransitFlow - PostgreSQL / Relational Database Layer
=====================================================
This module handles all queries to PostgreSQL.

TWO ROLES ARE SERVED HERE:
  1. Relational  -> dual-network transit (metro + national rail),
                    availability, fares, bookings, seat selection
  2. Vector      -> policy document similarity search (pgvector)

Functions prefixed with query_  are read-only lookups called by the agent.
Functions prefixed with execute_ are write operations (booking/cancellation).

The vector functions (query_policy_vector_search, store_policy_document)
are already implemented - do not modify them.
"""

from __future__ import annotations

import bcrypt
import random
import string
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

from skeleton.config import PG_DSN, VECTOR_TOP_K, VECTOR_SIMILARITY_THRESHOLD


def _connect():
    """Return a new psycopg2 connection with autocommit enabled."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    return conn


def _gen_booking_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"BK-{suffix}"


def _gen_payment_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"PM-{suffix}"


# -- Example ------------------------------------------------------------------

def example_query() -> dict:
    """Example: returns the name of the connected database."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT current_database() AS db;")
            return dict(cur.fetchone())


# -- NATIONAL RAIL AVAILABILITY -----------------------------------------------

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:
    """
    Return available national rail schedules between origin and destination.
    Filters by travel date if provided. Only returns schedules where origin
    appears before destination in stop order.

    Args:
        origin_id:      e.g. "NR01"
        destination_id: e.g. "NR05"
        travel_date:    e.g. "2026-06-01" (optional)

    Returns:
        List of dicts with schedule_id, line, service_type, departure_time,
        stops_travelled, available_seats.
    """
    sql = """
        SELECT
            s.schedule_id,
            s.line,
            s.service_type,
            s.first_train_time AS departure_time,
            origin_stop.stop_order    AS origin_stop_order,
            dest_stop.stop_order      AS destination_stop_order,
            dest_stop.stop_order - origin_stop.stop_order AS stops_travelled,
            (
                SELECT COUNT(*)
                FROM national_rail_bookings b
                WHERE b.schedule_id = s.schedule_id
                  AND b.status != 'cancelled'
                  AND (%s IS NULL OR b.travel_date = %s::date)
            ) AS booked_seats,
            (
                SELECT COUNT(*)
                FROM seat_layouts sl
                WHERE sl.schedule_id = s.schedule_id
            ) AS total_seats
        FROM national_rail_schedules s
        JOIN national_rail_schedule_stops origin_stop
            ON origin_stop.schedule_id = s.schedule_id
           AND origin_stop.station_id  = %s
        JOIN national_rail_schedule_stops dest_stop
            ON dest_stop.schedule_id = s.schedule_id
           AND dest_stop.station_id  = %s
        WHERE origin_stop.stop_order < dest_stop.stop_order
        ORDER BY s.line, s.first_train_time
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (travel_date, travel_date, origin_id, destination_id))
            rows = cur.fetchall()

    results = []
    for row in rows:
        d = dict(row)
        d["available_seats"] = max(0, d["total_seats"] - d["booked_seats"])
        results.append(d)
    return results


def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """
    Calculate the fare for a national rail journey.
    Reads actual rates from national_rail_schedules table.

    Args:
        schedule_id:     e.g. "NR_SCH01"
        fare_class:      "standard" or "first"
        stops_travelled: number of stops between origin and destination

    Returns:
        Dict with base_fare_usd, per_stop_rate_usd, total_fare_usd.
        None if schedule_id not found.
    """
    sql = """
        SELECT
            standard_base_fare_usd,
            standard_per_stop_rate_usd,
            first_base_fare_usd,
            first_per_stop_rate_usd
        FROM national_rail_schedules
        WHERE schedule_id = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id,))
            row = cur.fetchone()

    if not row:
        return None

    if fare_class == "first":
        base_fare = float(row["first_base_fare_usd"])
        per_stop  = float(row["first_per_stop_rate_usd"])
    else:
        base_fare = float(row["standard_base_fare_usd"])
        per_stop  = float(row["standard_per_stop_rate_usd"])

    total = round(base_fare + per_stop * stops_travelled, 2)

    return {
        "base_fare_usd":     base_fare,
        "per_stop_rate_usd": per_stop,
        "total_fare_usd":    total,
    }


# -- METRO SCHEDULES & FARE ---------------------------------------------------

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """
    Return metro schedules that serve both origin and destination,
    in correct stop order (origin appears before destination).

    Args:
        origin_id:      e.g. "MS01"
        destination_id: e.g. "MS09"

    Returns:
        List of dicts with schedule_id, line, direction, first_train_time,
        last_train_time, frequency_min, stops_travelled, base_fare_usd,
        per_stop_rate_usd.
    """
    sql = """
        SELECT
            s.schedule_id,
            s.line,
            s.direction,
            s.first_train_time,
            s.last_train_time,
            s.frequency_min,
            s.base_fare_usd,
            s.per_stop_rate_usd,
            dest_stop.stop_order - origin_stop.stop_order AS stops_travelled
        FROM metro_schedules s
        JOIN metro_schedule_stops origin_stop
            ON origin_stop.schedule_id = s.schedule_id
           AND origin_stop.station_id  = %s
        JOIN metro_schedule_stops dest_stop
            ON dest_stop.schedule_id = s.schedule_id
           AND dest_stop.station_id  = %s
        WHERE origin_stop.stop_order < dest_stop.stop_order
        ORDER BY s.line, s.first_train_time
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (origin_id, destination_id))
            return [dict(row) for row in cur.fetchall()]


def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """
    Calculate the fare for a metro journey.
    Reads actual rates from metro_schedules table.

    Args:
        schedule_id:     e.g. "MS_SCH01"
        stops_travelled: number of stops between origin and destination

    Returns:
        Dict with base_fare_usd, per_stop_rate_usd, total_fare_usd.
        None if schedule_id not found.
    """
    sql = """
        SELECT base_fare_usd, per_stop_rate_usd
        FROM metro_schedules
        WHERE schedule_id = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id,))
            row = cur.fetchone()

    if not row:
        return None

    base_fare = float(row["base_fare_usd"])
    per_stop  = float(row["per_stop_rate_usd"])
    total     = round(base_fare + per_stop * stops_travelled, 2)

    return {
        "base_fare_usd":     base_fare,
        "per_stop_rate_usd": per_stop,
        "total_fare_usd":    total,
    }


# -- SEAT SELECTION -----------------------------------------------------------

def auto_select_adjacent_seats(available_seats: list[dict], count: int) -> list[str]:
    """
    Auto-select adjacent seats from available seats list.

    Args:
        available_seats: list of seat dicts from query_available_seats
        count:           number of adjacent seats needed

    Returns:
        List of seat_id strings.
    """
    if not available_seats or count <= 0:
        return []
    if count >= len(available_seats):
        return [s["seat_id"] for s in available_seats[:count]]

    from collections import defaultdict
    rows: dict = defaultdict(list)
    for seat in available_seats:
        rows[seat["seat_row"]].append(seat)

    for row_seats in sorted(rows.values(), key=lambda s: s[0]["seat_row"]):
        if len(row_seats) >= count:
            return [s["seat_id"] for s in row_seats[:count]]

    sorted_seats = sorted(available_seats, key=lambda s: (s["seat_row"], s["seat_column"]))
    return [s["seat_id"] for s in sorted_seats[:count]]


def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
) -> list[dict]:
    """
    Return seats in the specified fare class that are not yet booked
    for the given schedule and travel date.

    Args:
        schedule_id: e.g. "NR_SCH01"
        travel_date: e.g. "2026-06-01"
        fare_class:  "standard" or "first"

    Returns:
        List of dicts with seat_id, coach, fare_class, seat_row, seat_column.
    """
    sql = """
        SELECT
            sl.seat_id,
            sl.coach,
            sl.fare_class,
            sl.seat_row,
            sl.seat_column
        FROM seat_layouts sl
        WHERE sl.schedule_id = %s
          AND sl.fare_class  = %s
          AND sl.seat_id NOT IN (
              SELECT b.seat_id
              FROM national_rail_bookings b
              WHERE b.schedule_id  = %s
                AND b.travel_date  = %s::date
                AND b.status      != 'cancelled'
          )
        ORDER BY sl.seat_row, sl.seat_column
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id, fare_class, schedule_id, travel_date))
            return [dict(row) for row in cur.fetchall()]


# -- USER & BOOKING QUERIES ---------------------------------------------------

def query_user_profile(user_email: str) -> Optional[dict]:
    """
    Return a single user profile by email.

    Args:
        user_email: e.g. "alice.tan@email.com"

    Returns:
        Dict with user_id, email, first_name, surname, full_name,
        year_of_birth, phone. None if not found.
    """
    sql = """
        SELECT
            user_id,
            email,
            first_name,
            surname,
            first_name || ' ' || surname AS full_name,
            year_of_birth,
            phone
        FROM users
        WHERE email = %s
          AND is_active = TRUE
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (user_email,))
            row = cur.fetchone()
            return dict(row) if row else None


def query_user_bookings(user_email: str) -> dict:
    """
    Return all bookings and trips for a given user.
    Both keys are always present even if lists are empty.

    Args:
        user_email: e.g. "alice.tan@email.com"

    Returns:
        Dict with keys "national_rail" (list) and "metro" (list).
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            cur.execute("""
                SELECT
                    b.booking_id,
                    b.schedule_id,
                    b.origin_station_id,
                    b.destination_station_id,
                    orig.name AS origin_name,
                    dest.name AS destination_name,
                    b.travel_date,
                    b.departure_time,
                    b.fare_class,
                    b.ticket_type,
                    b.stops_travelled,
                    b.amount_usd,
                    b.status,
                    b.booked_at
                FROM national_rail_bookings b
                JOIN users u
                    ON u.user_id = b.user_id
                JOIN national_rail_stations orig
                    ON orig.station_id = b.origin_station_id
                JOIN national_rail_stations dest
                    ON dest.station_id = b.destination_station_id
                WHERE u.email = %s
                ORDER BY b.travel_date DESC
            """, (user_email,))
            national_rail = [dict(row) for row in cur.fetchall()]

            cur.execute("""
                SELECT
                    t.trip_id,
                    t.schedule_id,
                    t.origin_station_id,
                    t.destination_station_id,
                    orig.name AS origin_name,
                    dest.name AS destination_name,
                    t.travel_date,
                    t.ticket_type,
                    t.stops_travelled,
                    t.amount_usd,
                    t.status,
                    t.purchased_at
                FROM metro_trips t
                JOIN users u
                    ON u.user_id = t.user_id
                LEFT JOIN metro_stations orig
                    ON orig.station_id = t.origin_station_id
                LEFT JOIN metro_stations dest
                    ON dest.station_id = t.destination_station_id
                WHERE u.email = %s
                ORDER BY t.travel_date DESC
            """, (user_email,))
            metro = [dict(row) for row in cur.fetchall()]

    return {
        "national_rail": national_rail,
        "metro":         metro,
    }


def query_payment_info(booking_id: str) -> Optional[dict]:
    """
    Return payment record for a given booking ID or trip ID.

    Args:
        booking_id: e.g. "BK001" (national rail) or "MT001" (metro)

    Returns:
        Dict with payment_id, amount_usd, method, status, paid_at.
        None if not found.
    """
    sql = """
        SELECT
            payment_id,
            booking_id,
            trip_id,
            amount_usd,
            method,
            status,
            paid_at
        FROM payments
        WHERE booking_id = %s
           OR trip_id    = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (booking_id, booking_id))
            row = cur.fetchone()
            return dict(row) if row else None


# -- TRANSACTIONAL OPERATIONS -------------------------------------------------

def execute_booking(
    user_id: str,
    schedule_id: str,
    origin_station_id: str,
    destination_station_id: str,
    travel_date: str,
    fare_class: str,
    seat_id: str,
    ticket_type: str = "single",
) -> tuple[bool, dict | str]:
    """
    Create a booking and payment record atomically.
    Both records are committed together - if either fails, both roll back.

    Args:
        user_id:                e.g. "RU01"
        schedule_id:            e.g. "NR_SCH01"
        origin_station_id:      e.g. "NR01"
        destination_station_id: e.g. "NR05"
        travel_date:            e.g. "2026-06-01"
        fare_class:             "standard" or "first"
        seat_id:                e.g. "B05" or "any" for auto-assign
        ticket_type:            "single" or "return"

    Returns:
        (True, booking_dict) on success.
        (False, error_message) on failure.
    """
    if seat_id == "any":
        available = query_available_seats(schedule_id, travel_date, fare_class)
        if not available:
            return False, "No seats available for this schedule and date."
        seat_id = available[0]["seat_id"]

    fare_info = query_national_rail_fare(schedule_id, fare_class, 1)
    if not fare_info:
        return False, f"Schedule {schedule_id} not found."

    stops_sql = """
        SELECT
            dest.stop_order - orig.stop_order AS stops_travelled
        FROM national_rail_schedule_stops orig
        JOIN national_rail_schedule_stops dest
            ON dest.schedule_id = orig.schedule_id
        WHERE orig.schedule_id = %s
          AND orig.station_id  = %s
          AND dest.station_id  = %s
          AND orig.stop_order  < dest.stop_order
    """

    b_id = _gen_booking_id()
    p_id = _gen_payment_id()
    now  = datetime.now(timezone.utc)

    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            cur.execute("""
                SELECT seat_id FROM national_rail_bookings
                WHERE schedule_id = %s
                  AND travel_date = %s::date
                  AND seat_id     = %s
                  AND status     != 'cancelled'
            """, (schedule_id, travel_date, seat_id))
            if cur.fetchone():
                conn.rollback()
                return False, f"Seat {seat_id} is already booked for this date."

            cur.execute(stops_sql, (schedule_id, origin_station_id, destination_station_id))
            stops_row = cur.fetchone()
            stops_travelled = stops_row["stops_travelled"] if stops_row else 1

            fare_info = query_national_rail_fare(schedule_id, fare_class, stops_travelled)
            amount    = fare_info["total_fare_usd"]

            cur.execute("""
                INSERT INTO national_rail_bookings (
                    booking_id, user_id, schedule_id,
                    origin_station_id, destination_station_id,
                    seat_id, travel_date, ticket_type,
                    fare_class, stops_travelled, amount_usd,
                    status, booked_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::date, %s, %s, %s, %s, 'confirmed', %s)
            """, (
                b_id, user_id, schedule_id,
                origin_station_id, destination_station_id,
                seat_id, travel_date, ticket_type,
                fare_class, stops_travelled, amount, now
            ))

            cur.execute("""
                INSERT INTO payments (
                    payment_id, booking_id, trip_id,
                    amount_usd, method, status, paid_at
                ) VALUES (%s, %s, NULL, %s, 'credit_card', 'paid', %s)
            """, (p_id, b_id, amount, now))

        conn.commit()
        return True, {
            "booking_id":              b_id,
            "user_id":                 user_id,
            "schedule_id":             schedule_id,
            "seat_id":                 seat_id,
            "origin_station_id":       origin_station_id,
            "destination_station_id":  destination_station_id,
            "travel_date":             travel_date,
            "fare_class":              fare_class,
            "stops_travelled":         stops_travelled,
            "amount_usd":              amount,
            "status":                  "confirmed",
        }

    except Exception as e:
        conn.rollback()
        return False, f"Booking failed: {str(e)}"
    finally:
        conn.close()


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """
    Cancel a booking and calculate refund based on cancellation policy.
    Updates booking status and payment status atomically.

    Args:
        booking_id: e.g. "BK001"
        user_id:    e.g. "RU01"

    Returns:
        (True, result_dict) on success.
        (False, error_message) on failure.
    """
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            cur.execute("""
                SELECT
                    b.booking_id,
                    b.user_id,
                    b.amount_usd,
                    b.status,
                    b.travel_date,
                    b.booked_at,
                    s.service_type
                FROM national_rail_bookings b
                JOIN national_rail_schedules s
                    ON s.schedule_id = b.schedule_id
                WHERE b.booking_id = %s
                  AND b.user_id    = %s
            """, (booking_id, user_id))
            booking = cur.fetchone()

            if not booking:
                return False, "Booking not found or does not belong to this user."

            if booking["status"] == "cancelled":
                return False, "Booking is already cancelled."

            import datetime as dt
            travel_date = booking["travel_date"]
            travel_dt = dt.datetime.combine(travel_date, dt.time(0, 0))
            travel_dt = travel_dt.replace(tzinfo=timezone.utc)
            hours_until = (travel_dt - datetime.now(timezone.utc)).total_seconds() / 3600

            amount = float(booking["amount_usd"])
            if hours_until >= 48:
                refund_percent = 100
                admin_fee      = 0.00
            elif hours_until >= 24:
                refund_percent = 75
                admin_fee      = 0.50
            elif hours_until >= 2:
                refund_percent = 50
                admin_fee      = 0.50
            else:
                refund_percent = 0
                admin_fee      = 0.00

            refund_amount = round(amount * refund_percent / 100 - admin_fee, 2)
            refund_amount = max(0.0, refund_amount)

            cur.execute("""
                UPDATE national_rail_bookings
                SET status = 'cancelled'
                WHERE booking_id = %s
            """, (booking_id,))

            cur.execute("""
                UPDATE payments
                SET status = 'refunded'
                WHERE booking_id = %s
            """, (booking_id,))

        conn.commit()
        return True, {
            "booking_id":      booking_id,
            "original_amount": amount,
            "refund_percent":  refund_percent,
            "admin_fee":       admin_fee,
            "refund_amount":   refund_amount,
            "policy_note":     f"Cancelled {round(hours_until)}h before departure. "
                               f"{refund_percent}% refund applied.",
        }

    except Exception as e:
        conn.rollback()
        return False, f"Cancellation failed: {str(e)}"
    finally:
        conn.close()


# -- AUTHENTICATION QUERIES ---------------------------------------------------

def register_user(
    email: str,
    first_name: str,
    surname: str,
    year_of_birth: int,
    password: str,
    secret_question: str,
    secret_answer: str,
) -> tuple[bool, str]:
    """
    Register a new user. Password is hashed with bcrypt before storing.

    Args:
        email:           e.g. "newuser@email.com"
        first_name:      e.g. "Alice"
        surname:         e.g. "Tan"
        year_of_birth:   e.g. 1990
        password:        plain text password
        secret_question: e.g. "What was the name of your first pet?"
        secret_answer:   e.g. "Biscuit"

    Returns:
        (True, user_id) on success.
        (False, error_message) on failure.
    """
    hashed = bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt()
    ).decode("utf-8")

    user_id = f"RU{random.randint(21, 99)}"

    sql = """
        INSERT INTO users (
            user_id, email, first_name, surname,
            year_of_birth, password,
            secret_question, secret_answer
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(sql, (
                    user_id, email.strip().lower(),
                    first_name.strip(), surname.strip(),
                    year_of_birth, hashed,
                    secret_question, secret_answer.strip()
                ))
                return True, user_id
            except psycopg2.errors.UniqueViolation:
                return False, "An account with this email already exists."


def login_user(email: str, password: str) -> Optional[dict]:
    """
    Verify email and password. Uses bcrypt to compare against stored hash.

    Args:
        email:    e.g. "alice.tan@email.com"
        password: plain text password

    Returns:
        User dict on success, None if email not found or password wrong.
    """
    sql = """
        SELECT user_id, email, first_name, surname,
               first_name || ' ' || surname AS full_name,
               password, year_of_birth
        FROM users
        WHERE email = %s AND is_active = TRUE
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (email.strip().lower(),))
            row = cur.fetchone()

    if not row:
        return None

    if not bcrypt.checkpw(password.encode("utf-8"), row["password"].encode("utf-8")):
        return None

    return {
        "user_id":       row["user_id"],
        "email":         row["email"],
        "first_name":    row["first_name"],
        "surname":       row["surname"],
        "full_name":     row["full_name"],
        "year_of_birth": row["year_of_birth"],
    }


def get_user_secret_question(email: str) -> Optional[str]:
    """
    Return the secret question for a given email, or None if not found.

    Args:
        email: e.g. "alice.tan@email.com"

    Returns:
        Secret question string, or None.
    """
    sql = "SELECT secret_question FROM users WHERE email = %s AND is_active = TRUE"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email.strip().lower(),))
            row = cur.fetchone()
            return row[0] if row else None


def verify_secret_answer(email: str, answer: str) -> bool:
    """
    Check if the provided answer matches the stored secret answer.
    Comparison is case-insensitive.

    Args:
        email:  e.g. "alice.tan@email.com"
        answer: the user answer to their secret question

    Returns:
        True if answer matches, False otherwise.
    """
    sql = "SELECT secret_answer FROM users WHERE email = %s AND is_active = TRUE"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email.strip().lower(),))
            row = cur.fetchone()

    if not row:
        return False
    return row[0].strip().lower() == answer.strip().lower()


def update_password(email: str, new_password: str) -> bool:
    """
    Update the user password. New password is hashed with bcrypt.

    Args:
        email:        e.g. "alice.tan@email.com"
        new_password: plain text new password

    Returns:
        True if updated successfully, False if user not found.
    """
    hashed = bcrypt.hashpw(
        new_password.encode("utf-8"),
        bcrypt.gensalt()
    ).decode("utf-8")

    sql = """
        UPDATE users SET password = %s
        WHERE email = %s AND is_active = TRUE
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (hashed, email.strip().lower()))
            return cur.rowcount > 0


# -- VECTOR / RAG QUERIES - do not modify -------------------------------------

def query_policy_vector_search(embedding: list[float], top_k: int = VECTOR_TOP_K) -> list[dict]:
    """
    Find the most relevant policy documents for a given query embedding.

    Args:
        embedding: Query vector from llm.embed(user_question)
        top_k:     Number of results to return

    Returns:
        List of dicts with title, category, content, and similarity score
    """
    sql = """
        SELECT
            title,
            category,
            content,
            1 - (embedding <=> %s::vector) AS similarity
        FROM policy_documents
        WHERE 1 - (embedding <=> %s::vector) > %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (vec_str, vec_str, VECTOR_SIMILARITY_THRESHOLD, vec_str, top_k))
            return [dict(row) for row in cur.fetchall()]


def store_policy_document(
    title: str,
    category: str,
    content: str,
    embedding: list[float],
    source_file: str = "",
) -> int:
    """
    Insert a policy document with its embedding into the database.
    Used by skeleton/seed_vectors.py - students do not need to call this directly.

    Returns:
        The new document id
    """
    sql = """
        INSERT INTO policy_documents (title, category, content, embedding, source_file)
        VALUES (%s, %s, %s, %s::vector, %s)
        RETURNING id
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (title, category, content, vec_str, source_file))
            return cur.fetchone()[0]
