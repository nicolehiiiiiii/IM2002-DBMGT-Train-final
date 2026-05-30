"""
TransitFlow — Neo4j Graph Database Layer
=========================================
This module handles all queries to Neo4j.

GRAPH ROLE:
  - Model the dual transit network (city metro M1–M4 + national rail NR1–NR2)
  - Find fastest routes (Dijkstra by travel_time_min via APOC)
  - Find cheapest routes (Dijkstra by fare via APOC)
  - Find alternative routes avoiding a given station
  - Find cross-network interchange paths (metro → rail or rail → metro)
  - Show delay ripple: which stations are affected within N hops

STUDENT TASK
------------
Design your graph schema (node labels, relationship types, properties)
based on the data in train-mock-data/, seed it with skeleton/seed_neo4j.py,
then implement the query_ functions below.

Functions prefixed with `query_` are called by the agent (skeleton/agent.py).
"""

from __future__ import annotations

from typing import Optional

from neo4j import GraphDatabase

from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD


def _driver():
    """Return a Neo4j driver. Caller is responsible for closing."""
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


# ── Example ───────────────────────────────────────────────────────────────────
# The block below shows the query pattern: open a session, run Cypher, return data.

def example_count_nodes() -> int:
    """Example: count all nodes currently in the graph."""
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("MATCH (n) RETURN count(n) AS total")
            return result.single()["total"]

# TODO: Implement the query_ functions below.
# ─────────────────────────────────────────────────────────────────────────────


# ── FASTEST ROUTE (Dijkstra by travel_time_min) ───────────────────────────────

def query_shortest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
) -> dict:
    """
    Find the fastest path between two stations, minimising total travel time.
    Uses apoc.algo.dijkstra (APOC required; enabled in docker-compose.yml).

    Args:
        origin_id:       e.g. "MS01" or "NR01"
        destination_id:  e.g. "MS09" or "NR05"
        network:         "metro", "rail", or "auto" (inferred from IDs)

    Returns:
        dict with keys: found, origin_id, destination_id,
                        total_time_min, path (list of station dicts), legs
    """
    if network == "auto":
        network = "metro" if origin_id.upper().startswith("MS") else "rail"

    label = "MetroStation" if network == "metro" else "NationalRailStation"
    rel   = "METRO_LINK"   if network == "metro" else "RAIL_LINK"

    cypher = f"""
        MATCH (start:{label} {{station_id: $origin}})
        MATCH (end:{label}   {{station_id: $destination}})
        CALL apoc.algo.dijkstra(start, end, '{rel}', 'travel_time_min')
        YIELD path, weight
        RETURN
            [n IN nodes(path) | n.station_id] AS ids,
            [n IN nodes(path) | n.name]       AS names,
            weight AS total_time_min
    """

    with _driver() as driver:
        with driver.session() as session:
            row = session.run(cypher, origin=origin_id, destination=destination_id).single()

    if not row:
        return {"found": False, "origin_id": origin_id, "destination_id": destination_id}

    stations = [{"station_id": i, "name": n} for i, n in zip(row["ids"], row["names"])]
    legs = [
        {"from": stations[i], "to": stations[i+1]}
        for i in range(len(stations) - 1)
    ]
    return {
        "found":          True,
        "origin_id":      origin_id,
        "destination_id": destination_id,
        "total_time_min": row["total_time_min"],
        "path":           stations,
        "legs":           legs,
    }


# ── CHEAPEST ROUTE (Dijkstra by fare) ────────────────────────────────────────

def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    """
    Find the cheapest path between two stations, minimising total estimated fare.

    Args:
        origin_id:       e.g. "NR01"
        destination_id:  e.g. "NR05"
        network:         "metro", "rail", or "auto"
        fare_class:      "standard" or "first" (national rail only)

    Returns:
        dict with found, total_fare_usd (approximate), stations, legs
    """
    if network == "auto":
        network = "metro" if origin_id.upper().startswith("MS") else "rail"

    label    = "MetroStation" if network == "metro" else "NationalRailStation"
    rel      = "METRO_LINK"   if network == "metro" else "RAIL_LINK"
    fare_key = "base_fare_usd" if network == "metro" else (
        "first_fare_usd" if fare_class == "first" else "standard_fare_usd"
    )

    cypher = f"""
        MATCH (start:{label} {{station_id: $origin}})
        MATCH (end:{label}   {{station_id: $destination}})
        CALL apoc.algo.dijkstra(start, end, '{rel}', '{fare_key}')
        YIELD path, weight
        RETURN
            [n IN nodes(path) | n.station_id] AS ids,
            [n IN nodes(path) | n.name]       AS names,
            weight AS total_fare_usd
    """

    with _driver() as driver:
        with driver.session() as session:
            row = session.run(cypher, origin=origin_id, destination=destination_id).single()

    if not row:
        return {"found": False, "origin_id": origin_id, "destination_id": destination_id}

    stations = [{"station_id": i, "name": n} for i, n in zip(row["ids"], row["names"])]
    return {
        "found":          True,
        "origin_id":      origin_id,
        "destination_id": destination_id,
        "total_fare_usd": row["total_fare_usd"],
        "stations":       stations,
    }


# ── ALTERNATIVE ROUTES (avoiding a station) ───────────────────────────────────

def query_alternative_routes(
    origin_id: str,
    destination_id: str,
    avoid_station_id: str,
    network: str = "auto",
    max_routes: int = 3,
) -> list[list[dict]]:
    """
    Find paths between two stations that avoid a specific intermediate station.
    Useful for routing around a delayed or closed station.

    Args:
        origin_id:         e.g. "NR01"
        destination_id:    e.g. "NR05"
        avoid_station_id:  e.g. "NR03"
        network:           "metro", "rail", or "auto"
        max_routes:        max number of alternatives to return

    Returns:
        List of routes, each route is a list of leg dicts
    """
    if network == "auto":
        network = "metro" if origin_id.upper().startswith("MS") else "rail"

    label = "MetroStation" if network == "metro" else "NationalRailStation"
    rel   = "METRO_LINK"   if network == "metro" else "RAIL_LINK"

    cypher = f"""
        MATCH path = (start:{label} {{station_id: $origin}})
                     -[:{rel}*]->
                     (end:{label}   {{station_id: $destination}})
        WHERE NONE(n IN nodes(path) WHERE n.station_id = $avoid)
        RETURN [n IN nodes(path) | n.station_id] AS ids,
               [n IN nodes(path) | n.name]       AS names,
               reduce(t = 0, r IN relationships(path) | t + r.travel_time_min) AS total_time
        ORDER BY total_time
        LIMIT $max_routes
    """

    with _driver() as driver:
        with driver.session() as session:
            rows = session.run(
                cypher,
                origin=origin_id,
                destination=destination_id,
                avoid=avoid_station_id,
                max_routes=max_routes,
            ).data()

    routes = []
    for row in rows:
        stations = [{"station_id": i, "name": n} for i, n in zip(row["ids"], row["names"])]
        legs = [
            {"from": stations[i], "to": stations[i+1], "total_time_min": row["total_time"]}
            for i in range(len(stations) - 1)
        ]
        routes.append(legs)
    return routes


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """
    Find a path between a metro station and a national rail station (or vice versa)
    crossing the network boundary via interchange relationships.

    Args:
        origin_id:       e.g. "MS03" (metro) or "NR05" (national rail)
        destination_id:  e.g. "NR05" (national rail) or "MS09" (metro)

    Returns:
        dict with found, stations list, interchange points, total_time_min
    """
    cypher = """
        MATCH (start {station_id: $origin})
        MATCH (end   {station_id: $destination})
        MATCH path = shortestPath(
            (start)-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*]-(end)
        )
        RETURN [n IN nodes(path) | n.station_id] AS ids,
               [n IN nodes(path) | n.name]       AS names,
               reduce(t = 0, r IN relationships(path) |
                   t + coalesce(r.travel_time_min, r.transfer_time_min, 5)
               ) AS total_time_min
    """

    with _driver() as driver:
        with driver.session() as session:
            row = session.run(cypher, origin=origin_id, destination=destination_id).single()

    if not row:
        return {"found": False, "origin_id": origin_id, "destination_id": destination_id}

    stations = [{"station_id": i, "name": n} for i, n in zip(row["ids"], row["names"])]
    return {
        "found":          True,
        "origin_id":      origin_id,
        "destination_id": destination_id,
        "total_time_min": row["total_time_min"],
        "stations":       stations,
    }


# ── DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────────

def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    """
    Find all stations within N hops of a delayed or disrupted station.
    Works on both metro and national rail networks.

    Args:
        delayed_station_id: e.g. "NR03" or "MS01"
        hops:               how many connections out to search (default 2)

    Returns:
        List of dicts: {station_id, name, hops_away, lines_affected}
    """
    cypher = f"""
        MATCH (disrupted {{station_id: $station_id}})
        MATCH path = (disrupted)-[:METRO_LINK|RAIL_LINK*1..{hops}]-(affected)
        WHERE affected.station_id <> $station_id
        RETURN affected.station_id AS station_id,
               affected.name       AS name,
               affected.lines      AS lines,
               min(length(path))   AS hops_away
        ORDER BY hops_away
    """

    with _driver() as driver:
        with driver.session() as session:
            rows = session.run(cypher, station_id=delayed_station_id, hops=hops).data()

    return [
        {
            "station_id":     r["station_id"],
            "name":           r["name"],
            "hops_away":      r["hops_away"],
            "lines_affected": r["lines"],
        }
        for r in rows
    ]


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    """
    List all direct connections from a given station.

    Args:
        station_id: e.g. "MS01" or "NR01"
    """
    cypher = """
        MATCH (s {station_id: $station_id})-[r:METRO_LINK|RAIL_LINK|INTERCHANGE_TO]->(n)
        RETURN n.station_id AS station_id,
               n.name       AS name,
               type(r)      AS relationship_type,
               r.line       AS line,
               coalesce(r.travel_time_min, r.transfer_time_min) AS travel_time_min
        ORDER BY travel_time_min
    """

    with _driver() as driver:
        with driver.session() as session:
            rows = session.run(cypher, station_id=station_id).data()

    return [dict(r) for r in rows]
