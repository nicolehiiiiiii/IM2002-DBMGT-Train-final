"""
TransitFlow — Neo4j Seeder
Run once after starting Docker:
    python skeleton/seed_neo4j.py

Loads station and network data from train-mock-data/:
  - metro_stations.json         — city metro stations and adjacencies
  - national_rail_stations.json — national rail stations and adjacencies

Design your graph schema (node labels, relationship types, properties)
based on the data in these files, then implement the seed() function below.
"""

import json
import os
import sys

sys.path.insert(0, ".")

from neo4j import GraphDatabase
from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "train-mock-data")
)


def _load(filename):
    with open(os.path.join(_DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def seed():
    metro_stations = _load("metro_stations.json")
    rail_stations  = _load("national_rail_stations.json")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:

        # 清空舊資料（skeleton 已有這行，保留）
        session.run("MATCH (n) DETACH DELETE n")
        print("  Cleared existing graph data")

        # ── TODO 1：建立捷運站節點 ──────────────────────────────────────
        for s in metro_stations:
            session.run(
                "MERGE (n:MetroStation {station_id: $id}) "
                "SET n.name = $name, n.lines = $lines",
                id=s["station_id"],
                name=s["name"],
                lines=s["lines"],
            )
        print(f"  Created {len(metro_stations)} MetroStation nodes")

        # ── TODO 2：建立台鐵站節點 ──────────────────────────────────────
        for s in rail_stations:
            session.run(
                "MERGE (n:NationalRailStation {station_id: $id}) "
                "SET n.name = $name, n.lines = $lines",
                id=s["station_id"],
                name=s["name"],
                lines=s["lines"],
            )
        print(f"  Created {len(rail_stations)} NationalRailStation nodes")

        # ── TODO 3：建立捷運相鄰關係 METRO_LINK ─────────────────────────
        for s in metro_stations:
            for adj in s.get("adjacent_stations", []):
                session.run(
                    "MATCH (a:MetroStation {station_id: $from_id}) "
                    "MATCH (b:MetroStation {station_id: $to_id}) "
                    "MERGE (a)-[r:METRO_LINK {line: $line}]->(b) "
                    "SET r.travel_time_min = $time",
                    from_id=s["station_id"],
                    to_id=adj["station_id"],
                    line=adj["line"],
                    time=adj["travel_time_min"],
                )
        print("  Created MetroStation METRO_LINK relationships")

        # ── TODO 4：建立台鐵相鄰關係 RAIL_LINK ──────────────────────────
        for s in rail_stations:
            for adj in s.get("adjacent_stations", []):
                session.run(
                    "MATCH (a:NationalRailStation {station_id: $from_id}) "
                    "MATCH (b:NationalRailStation {station_id: $to_id}) "
                    "MERGE (a)-[r:RAIL_LINK {line: $line}]->(b) "
                    "SET r.travel_time_min = $time",
                    from_id=s["station_id"],
                    to_id=adj["station_id"],
                    line=adj["line"],
                    time=adj["travel_time_min"],
                )
        print("  Created NationalRailStation RAIL_LINK relationships")

        # ── TODO 5：建立換乘關係 INTERCHANGE_TO ─────────────────────────
        # 從捷運站的 JSON 讀取哪些站可以換乘台鐵
        for s in metro_stations:
            rail_id = s.get("interchange_national_rail_station_id")
            if rail_id:
                session.run(
                    "MATCH (m:MetroStation {station_id: $metro_id}) "
                    "MATCH (r:NationalRailStation {station_id: $rail_id}) "
                    "MERGE (m)-[:INTERCHANGE_TO {transfer_time_min: 5}]->(r) "
                    "MERGE (r)-[:INTERCHANGE_TO {transfer_time_min: 5}]->(m)",
                    metro_id=s["station_id"],
                    rail_id=rail_id,
                )
        print("  Created INTERCHANGE_TO relationships")

    driver.close()
    print("\nNeo4j graph seeded successfully.")
    print("   Open http://localhost:7475 to explore the graph.")


if __name__ == "__main__":
    print("Connecting to Neo4j...")
    seed()
