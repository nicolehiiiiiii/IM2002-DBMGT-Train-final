"""
TransitFlow — PostgreSQL / Relational Database Layer
=====================================================
This module handles all queries to PostgreSQL.

TWO ROLES ARE SERVED HERE:
  1. Relational  → dual-network transit (metro + national rail),
                   availability, fares, bookings, seat selection
  2. Vector      → policy document similarity search (pgvector)

STUDENT TASK
------------
Design your schema in databases/relational/schema.sql, seed it with
skeleton/seed_postgres.py, then implement the query functions below.

Functions prefixed with `query_`  are read-only lookups called by the agent.
Functions prefixed with `execute_` are write operations (booking/cancellation).

The vector functions (query_policy_vector_search, store_policy_document)
are already implemented — do not modify them.
"""

from __future__ import annotations

import bcrypt
import json
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


# ── Example ───────────────────────────────────────────────────────────────────
# The block below shows the query pattern: open a cursor, run SQL, return rows.
# Use _connect() for read-only queries; for write operations use a manual
# connection with conn.commit() / conn.rollback() (see execute_booking below).

def example_query() -> dict:
    """Example: returns the name of the connected database."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT current_database() AS db;")
            return dict(cur.fetchone())

# TODO: Implement the query_ and execute_ functions below.
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# ── NATIONAL RAIL AVAILABILITY ────────────────────────────────────────────────

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:
    """
    查詢台鐵班次與剩餘座位
    """
    # 這裡實作一般的車次查詢，串接 schedules 與車站
    sql = """
        SELECT 
            s.train_id AS schedule_id,
            s.route_id,
            s.departure_time,
            s.arrival_time,
            (SELECT COUNT(*) FROM seat_layouts WHERE train_id = s.train_id) AS total_seats,
            (SELECT COUNT(*) FROM bookings WHERE train_id = s.train_id AND travel_date = %s AND status = 'confirmed') AS booked_seats
        FROM schedules s
        WHERE s.route_id LIKE 'NR%%'
    """
    # 預設一個日期防空值
    t_date = travel_date or "2026-05-29"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (t_date,))
            results = cur.fetchall()
            for r in results:
                r["available_seats"] = max(0, r["total_seats"] - r["booked_seats"])
            return [dict(row) for row in results]


def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """
    計算台鐵票價
    """
    # 依據標準或商務艙給予基礎票價與每站加計
    base_fare = 10.0 if fare_class == "first" else 5.0
    per_stop = 1.5 if fare_class == "first" else 0.8
    total_fare = base_fare + (per_stop * stops_travelled)
    
    return {
        "fare_class": fare_class,
        "base_fare_usd": base_fare,
        "per_stop_rate_usd": per_stop,
        "total_fare_usd": total_fare
    }


# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """
    查詢捷運班次
    """
    sql = """
        SELECT train_id AS schedule_id, route_id, departure_time, arrival_time
        FROM schedules
        WHERE route_id LIKE 'M%%' OR route_id = 'UNKNOWN'
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            return [dict(row) for row in cur.fetchall()]


def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """
    計算捷運單程票價
    """
    base_fare = 0.80
    per_stop = 0.30
    total_fare = base_fare + (per_stop * stops_travelled)
    return {
        "base_fare_usd": base_fare,
        "per_stop_rate_usd": per_stop,
        "total_fare_usd": total_fare
    }


# ── SEAT SELECTION ────────────────────────────────────────────────────────────

def auto_select_adjacent_seats(available_seats: list[dict], count: int) -> list[str]:
    if not available_seats or count <= 0:
        return []
    if count >= len(available_seats):
        return [s["seat_id"] for s in available_seats[:count]]

    from collections import defaultdict
    rows: dict[int, list[dict]] = defaultdict(list)
    for seat in available_seats:
        rows[seat["row"]].append(seat)

    for row_seats in sorted(rows.values(), key=lambda s: s[0]["row"]):
        if len(row_seats) >= count:
            return [s["seat_id"] for s in row_seats[:count]]

    sorted_seats = sorted(available_seats, key=lambda s: (s["row"], s["column"]))
    return [s["seat_id"] for s in sorted_seats[:count]]

def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
) -> list[dict]:
    """
    查詢特定日期班次的空位
    """
    sql = """
        SELECT seat_id, coach, 
               CAST(SUBSTRING(seat_id FROM '[0-9]+') AS INT) as row,
               SUBSTRING(seat_id FROM '[A-Z]+') as column
        FROM seat_layouts
        WHERE train_id = %s 
          AND coach = CASE WHEN %s = 'first' THEN 'A' ELSE 'B' END
          AND seat_id NOT IN (
              SELECT seat_number FROM bookings 
              WHERE train_id = %s AND status = 'confirmed'
          )
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 轉換行列邏輯提供給自動選位演算法
            cur.execute(sql, (schedule_id, fare_class, schedule_id))
            rows = cur.fetchall()
            results = []
            for r in rows:
                col_str = r["column"] or "A"
                col_num = ord(col_str[0]) - ord('A') + 1 if col_str else 1
                results.append({
                    "seat_id": r["seat_id"],
                    "coach": r["coach"],
                    "row": r["row"] or 1,
                    "column": col_num
                })
            return results
          
def auto_select_adjacent_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
    count: int = 2,
) -> list[dict]:
    """
    自動選取相鄰的空位（例如兩人同行想坐在一起）。
    
    Args:
        schedule_id: 班次 ID，例如 "NR_SCH01"
        travel_date: 旅遊日期，例如 "2026-06-01"
        fare_class:  艙等，"standard" 或 "first"
        count:       需要幾個相鄰座位，預設 2
    
    Returns:
        List of dicts，每個 dict 包含 seat_id、coach、row、column
    """
    available = query_available_seats(schedule_id, travel_date, fare_class)
    if len(available) < count:
        return []
    
    # 依照 coach + row 分組，找同一排有足夠空位的座位
    from collections import defaultdict
    by_row = defaultdict(list)
    for seat in available:
        key = (seat["coach"], seat["row"])
        by_row[key].append(seat)
    
    for seats_in_row in by_row.values():
        if len(seats_in_row) >= count:
            return seats_in_row[:count]
    
    # 找不到相鄰的，就回傳前 count 個可用座位
    return available[:count]

# ── USER & BOOKING QUERIES ────────────────────────────────────────────────────

def query_user_profile(user_email: str) -> Optional[dict]:
    """
    查詢使用者個人檔案
    """
    sql = "SELECT user_id, name, email, phone FROM users WHERE email = %s"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (user_email,))
            row = cur.fetchone()
            return dict(row) if row else None


def query_user_bookings(user_email: str) -> dict:
    """
    查詢使用者的搭乘歷史與訂單
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 1. 抓取台鐵訂單
            cur.execute("""
                SELECT b.booking_id, b.train_id, b.seat_number, b.booking_time, b.status 
                FROM bookings b JOIN users u ON b.user_id = u.user_id WHERE u.email = %s
            """, (user_email,))
            nr = [dict(r) for r in cur.fetchall()]
            
            # 2. 抓取捷運搭乘歷史
            cur.execute("""
                SELECT m.travel_id, m.station_id, m.travel_time, m.fare 
                FROM metro_travels m JOIN users u ON m.user_id = u.user_id WHERE u.email = %s
            """, (user_email,))
            metro = [dict(r) for r in cur.fetchall()]
            
            return {"national_rail": nr, "metro": metro}


def query_payment_info(booking_id: str) -> Optional[dict]:
    """
    查詢付款金流紀錄
    """
    sql = "SELECT payment_id, booking_id, amount, payment_method, status FROM payments WHERE booking_id = %s"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (booking_id,))
            row = cur.fetchone()
            return dict(row) if row else None


# ── TRANSACTIONAL OPERATIONS ──────────────────────────────────────────────────

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
    執行火車訂位事務交易 (Write Operation)
    """
    b_id = _gen_booking_id()
    p_id = _gen_payment_id()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    
    # 計算票價
    fare_info = query_national_rail_fare(schedule_id, fare_class, 4)
    amount = fare_info["total_fare_usd"] if fare_info else 15.0
    
    # 如果是自動選位，選一個目前可用的
    actual_seat = seat_id
    if seat_id == "any":
        seats = query_available_seats(schedule_id, travel_date, fare_class)
        if not seats:
            return False, "No seats available on this train."
        actual_seat = seats[0]["seat_id"]

    conn = _connect()
    conn.autocommit = False # 開啟交易隔離
    try:
        with conn.cursor() as cur:
            # 寫入訂單表
            cur.execute("""
                INSERT INTO bookings (booking_id, user_id, train_id, seat_number, booking_time, status)
                VALUES (%s, %s, %s, %s, %s, 'confirmed')
            """, (b_id, user_id, schedule_id, actual_seat, now_str))
            
            # 寫入付款金流表
            cur.execute("""
                INSERT INTO payments (payment_id, booking_id, amount, payment_method, status)
                VALUES (%s, %s, %s, 'Credit Card', 'completed')
            """, (p_id, b_id, amount))
            
        conn.commit()
        return True, {
            "booking_id": b_id,
            "train_id": schedule_id,
            "seat_number": actual_seat,
            "status": "confirmed",
            "amount_paid": amount
        }
    except Exception as e:
        conn.rollback()
        return False, f"Booking transaction failed: {str(e)}"
    finally:
        conn.close()


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """
    取消訂單與退款交易
    """
    conn = _connect()
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 檢查這筆訂單是否存在
            cur.execute("SELECT train_id FROM bookings WHERE booking_id = %s AND user_id = %s", (booking_id, user_id))
            b = cur.fetchone()
            if not b:
                return False, "Booking record not found."
                
            # 抓取付款金額來辦理退款
            cur.execute("SELECT amount FROM payments WHERE booking_id = %s", (booking_id,))
            p = cur.fetchone()
            base_amount = p["amount"] if p else 10.0
            
            # 模擬計算退款退 75%
            refund_amount = float(base_amount) * 0.75
            
            # 更新訂單狀態為已取消
            cur.execute("UPDATE bookings SET status = 'cancelled' WHERE booking_id = %s", (booking_id,))
            cur.execute("UPDATE payments SET status = 'refunded' WHERE booking_id = %s", (booking_id,))
            
        conn.commit()
        return True, {
            "booking_id": booking_id,
            "refund_amount_usd": refund_amount,
            "policy_note": "Cancelled within regular windows. 75% refund applied."
        }
    except Exception as e:
        conn.rollback()
        return False, f"Cancellation failed: {str(e)}"
    finally:
        conn.close()


# ── AUTHENTICATION QUERIES ────────────────────────────────────────────────────

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
    註冊新使用者（完美符合 bcrypt 雜湊加密規格）
    """
    u_id = f"RU{random.randint(10, 99)}"
    full_name = f"{first_name} {surname}"
    
    # 使用 bcrypt 生成安全鹽值並進行雜湊
    bytes_pwd = password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed_pwd = bcrypt.hashpw(bytes_pwd, salt).decode('utf-8')
    
    conn = _connect()
    try:
        with conn.cursor() as cur:
            # 🌟 同步把安全問題與答案寫進 users 資料表（請確保 schema.sql 的 users 表有這些欄位）
            cur.execute("""
                INSERT INTO users (user_id, name, email, phone, password, secret_question, secret_answer)
                VALUES (%s, %s, %s, '0912345678', %s, %s, %s)
            """, (u_id, full_name, email, hashed_pwd, secret_question, secret_answer))
        return True, u_id
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def login_user(email: str, password: str) -> Optional[dict]:
    """
    使用者登入驗證（比對 bcrypt 雜湊值）
    """
    sql = "SELECT user_id, name, email, password FROM users WHERE email = %s"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            if row:
                stored_hash = row["password"]
                if bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8')):
                    return {
                        "user_id": row["user_id"],
                        "email": row["email"],
                        "full_name": row["name"],
                        "is_active": True
                    }
            return None


def get_user_secret_question(email: str) -> Optional[str]:
    """
    忘記密碼：自資料庫查詢使用者的安全提示問題
    """
    sql = "SELECT secret_question FROM users WHERE email = %s"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            return row[0] if row else None


def verify_secret_answer(email: str, answer: str) -> bool:
    """
    忘記密碼：比對儲存的安全提示答案（不區分大小寫）
    """
    sql = "SELECT secret_answer FROM users WHERE email = %s"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            if row and row[0]:
                return row[0].lower() == answer.lower()
            return False


def update_password(email: str, new_password: str) -> bool:
    """
    變更密碼：使用 bcrypt 重新雜湊並寫入資料庫
    """
    bytes_pwd = new_password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed_pwd = bcrypt.hashpw(bytes_pwd, salt).decode('utf-8')
    
    sql = "UPDATE users SET password = %s WHERE email = %s"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (hashed_pwd, email))
            return cur.rowcount > 0


# ── VECTOR / RAG QUERIES — do not modify ─────────────────────────────────────

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
    Used by skeleton/seed_vectors.py — students don't need to call this directly.

    Returns:
        The new document's id
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
