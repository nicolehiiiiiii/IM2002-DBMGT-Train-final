// Deprecated: seeding is now done via skeleton/seed_neo4j.py
// which loads data directly from train-mock-data/ JSON files.
//
// If you prefer Cypher-file seeding, implement your graph schema here.
// Run with: python skeleton/seed_neo4j.py (or via the Neo4j Browser)

// TransitFlow — Graph Schema Definition
// Seeding is handled by skeleton/seed_neo4j.py
// This file documents the graph schema and constraints.

// ── Constraints (ensure unique station IDs) ──────────────────────────────────

CREATE CONSTRAINT metro_station_id_unique
FOR (s:MetroStation)
REQUIRE s.station_id IS UNIQUE;

CREATE CONSTRAINT nr_station_id_unique
FOR (s:NationalRailStation)
REQUIRE s.station_id IS UNIQUE;

// ── Node Schema ───────────────────────────────────────────────────────────────
// (:MetroStation {station_id, name, lines[]})
// (:NationalRailStation {station_id, name, lines[]})

// ── Relationship Schema ───────────────────────────────────────────────────────
// (:MetroStation)-[:METRO_LINK {line, travel_time_min}]->(:MetroStation)
// (:NationalRailStation)-[:RAIL_LINK {line, travel_time_min}]->(:NationalRailStation)
// (:MetroStation)-[:INTERCHANGE_TO {transfer_time_min}]->(:NationalRailStation)
// (:NationalRailStation)-[:INTERCHANGE_TO {transfer_time_min}]->(:MetroStation)