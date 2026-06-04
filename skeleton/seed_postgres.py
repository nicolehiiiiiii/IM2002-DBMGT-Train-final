"""
Seed PostgreSQL with all TransitFlow mock data from train-mock-data/.

Usage:
    python skeleton/seed_postgres.py

Run AFTER docker-compose up -d.
You must first design and create your tables in databases/relational/schema.sql.
Safe to re-run: implement your inserts with ON CONFLICT DO NOTHING.
"""

import json
import os
import sys

import psycopg2
from psycopg2.extras import execute_values

# ── resolve paths ────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR    = os.path.join(PROJECT_DIR, "train-mock-data")

sys.path.insert(0, PROJECT_DIR)
from skeleton import config as cfg


def load(filename):
    with open(os.path.join(DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def connect():
    return psycopg2.connect(
        host=cfg.PG_HOST,
        port=cfg.PG_PORT,
        dbname=cfg.PG_DB,
        user=cfg.PG_USER,
        password=cfg.PG_PASSWORD,
    )


def insert_many(cur, table, columns, rows):
    """Bulk insert with ON CONFLICT DO NOTHING. Returns row count inserted."""
    if not rows:
        return 0
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s "
        f"ON CONFLICT DO NOTHING"
    )
    execute_values(cur, sql, rows)
    return cur.rowcount


# ── seeders ──────────────────────────────────────────────────────────────────

def seed_metro_stations(cur):
    data = load("metro_stations.json")
    rows = []
    for s in data:
        # 如果 JSON 裡有 zone 就用 zone，沒有就用 fare_zone，再沒有就預設為 1
        zone_val = s.get("zone") or s.get("fare_zone") or s.get("zone_id") or 1
        rows.append((s["station_id"], s["name"], zone_val))
        
    n = insert_many(cur, "metro_stations", ["station_id", "name", "zone"], rows)
    print(f"  metro_stations: {n} rows")


def seed_national_rail_stations(cur):
    # 1. 讀取台鐵車站的 JSON 檔案
    data = load("national_rail_stations.json")
    
    # 2. 轉換成 tuple 格式
    rows = [
        (s["station_id"], s["name"])
        for s in data
    ]
    
    # 3. 塞進台鐵車站表
    n = insert_many(cur, "national_rail_stations", ["station_id", "name"], rows)
    print(f"  national_rail_stations: {n} rows")


def seed_metro_schedules(cur):
    metro_data = load("metro_schedules.json")
    metro_schedules = []
    metro_stops = []
    
    for s in metro_data:
        t_id = s["schedule_id"]
        dep_time = s.get("first_train_time", "05:30")
        arr_time = s.get("last_train_time", "23:30")
        metro_schedules.append((t_id, s.get("line", "M1"), dep_time, arr_time))
        
        for idx, station in enumerate(s.get("stops_in_order", []), start=1):
            travel_times = s.get("travel_time_from_origin_min", {})
            offset = travel_times.get(station, 0)
            metro_stops.append((t_id, station, None, None, idx))  

    insert_many(cur, "schedules",
                ["train_id", "route_id", "departure_time", "arrival_time"],
                metro_schedules)
    n = insert_many(cur, "schedule_stops",
                    ["train_id", "station_id", "arrival_time", "departure_time", "stop_order"],
                    metro_stops)
    print(f"  metro_schedules: {len(metro_schedules)} rows, stops: {n} rows")


def seed_national_rail_schedules(cur):
    rail_data = load("national_rail_schedules.json")
    rail_schedules = []
    rail_stops = []

    for s in rail_data:
        t_id = s["schedule_id"]
        route_id = s.get("line", "UNKNOWN")
        dep_time = s.get("first_train_time", "00:00")
        arr_time = s.get("last_train_time", "00:00")
        rail_schedules.append((t_id, route_id, dep_time, arr_time))

        for idx, station_id in enumerate(s.get("stops_in_order", []), start=1):
            rail_stops.append((t_id, station_id, None, None, idx))  

    insert_many(cur, "schedules",
                ["train_id", "route_id", "departure_time", "arrival_time"],
                rail_schedules)
    n = insert_many(cur, "schedule_stops",
                    ["train_id", "station_id", "arrival_time", "departure_time", "stop_order"],
                    rail_stops)
    print(f"  national_rail_schedules: {len(rail_schedules)} rows, stops: {n} rows")


def seed_seat_layouts(cur):
    data = load("national_rail_seat_layouts.json")
    rows = []
    for train in data:
        # ⭐ 關鍵修正：多種可能一次抓取，如果都抓不到就拿第一個欄位的值
        t_id = train.get("train_id") or train.get("train_no") or train.get("schedule_id") or train.get("id") or (list(train.values())[0] if train.values() else None)
        
        if not t_id:
            continue
            
        coaches_data = train.get("coaches", [])
        
        # 1. 如果 coaches 是 List
        if isinstance(coaches_data, list):
            for c in coaches_data:
                coach_name = c.get("coach_id") or c.get("coach") or "A"
                seats = c.get("seats") or c.get("seat_ids") or []
                
                for seat in seats:
                    if isinstance(seat, dict):
                        s_id = seat.get("seat_id") or seat.get("seat_number")
                    else:
                        s_id = seat
                    rows.append((t_id, coach_name, s_id))
                    
        # 2. 如果 coaches 是 Dict
        elif isinstance(coaches_data, dict):
            for coach, seats in coaches_data.items():
                for seat in seats:
                    if isinstance(seat, dict):
                        s_id = seat.get("seat_id") or seat.get("seat_number")
                    else:
                        s_id = seat
                    rows.append((t_id, coach, s_id))
                    
    n = insert_many(cur, "seat_layouts", ["train_id", "coach", "seat_id"], rows)
    print(f"  seat_layouts: {n} rows")


def seed_users(cur):
    import bcrypt
    data = load("registered_users.json")
    rows = []
    for u in data:
        password_plain = u.get("password", "password123")
        hashed = bcrypt.hashpw(password_plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        rows.append((
            u["user_id"],
            u["full_name"],
            u["email"],
            u.get("phone"),
            hashed,
            u.get("secret_question"),
            u.get("secret_answer"),
        ))
    n = insert_many(cur, "users",
                    ["user_id", "name", "email", "phone", "password", "secret_question", "secret_answer"],
                    rows)
    print(f"  users: {n} rows")


def seed_national_rail_bookings(cur):
    data = load("bookings.json")
    rows = [
        (
            b["booking_id"],
            b["user_id"],
            b["schedule_id"],
            b["seat_id"],
            b["booked_at"],      
            b.get("travel_date"),
            b["status"]
        )
        for b in data
    ]
    n = insert_many(cur, "bookings",
                    ["booking_id", "user_id", "train_id", "seat_number",
                     "booking_time", "travel_date", "status"],
                    rows)
    print(f"  bookings: {n} rows")


def seed_metro_travels(cur):
    data = load("metro_travel_history.json")
    rows = [
        (
            t["trip_id"],
            t["user_id"],
            t["origin_station_id"],
            t.get("travelled_at") or "2026-05-29 00:00:00+00",
            t.get("amount_usd") or 0
        )
        for t in data
    ]
    n = insert_many(cur, "metro_travels",
                    ["travel_id", "user_id", "station_id", "travel_time", "fare"],
                    rows)
    print(f"  metro_travels: {n} rows")


def seed_payments(cur):
    data = load("payments.json")
    rows = [
        (
            p.get("payment_id") or list(p.values())[0], 
            p.get("booking_id"), 
            p.get("amount") or p.get("fare") or 0, 
            p.get("payment_method") or "Credit Card", 
            p.get("status") or "completed"
        ) 
        for p in data
    ]
    n = insert_many(cur, "payments", ["payment_id", "booking_id", "amount", "payment_method", "status"], rows)
    print(f"  payments: {n} rows")


def seed_feedback(cur):
    data = load("feedback.json")
    rows = [
        (
            f.get("feedback_id") or list(f.values())[0], 
            f.get("user_id"), 
            f.get("rating") or f.get("score") or 5, 
            f.get("comment") or f.get("message") or "", 
            f.get("created_at") or f.get("timestamp") or "2026-05-29 00:00:00"
        ) 
        for f in data
    ]
    n = insert_many(cur, "feedback", ["feedback_id", "user_id", "rating", "comment", "created_at"], rows)
    print(f"  feedback: {n} rows")

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("Connecting to PostgreSQL...")
    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("Seeding tables (dependency order):")
        seed_metro_stations(cur)
        seed_national_rail_stations(cur)
        seed_metro_schedules(cur)
        seed_national_rail_schedules(cur)
        seed_seat_layouts(cur)
        seed_users(cur)
        seed_national_rail_bookings(cur)
        seed_metro_travels(cur)
        seed_payments(cur)
        seed_feedback(cur)
        conn.commit()
        print("\nAll done. Database seeded successfully.")
    except Exception as e:
        conn.rollback()
        print(f"\nError: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
