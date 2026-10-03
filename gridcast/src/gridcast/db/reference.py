"""Idempotent load of reference data (stations, zones, vendors) from the packaged catalog."""

from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert

from gridcast.catalog import catalog
from gridcast.db.schema import weather_providers, weather_stations, zones


def sync_reference(engine: Engine) -> dict[str, int]:
    cat = catalog()
    with engine.begin() as conn:
        for table, key, rows in (
            (weather_stations, "station_id", [
                {"station_id": s.id, "name": s.name, "latitude": s.latitude,
                 "longitude": s.longitude, "elevation_m": s.elevation_m} for s in cat.stations]),
            (zones, "zone_id", [
                {"zone_id": z.id, "name": z.name, "station_id": z.station_id,
                 "base_load_mw": z.base_load_mw, "cooling_mw_per_degc": z.cooling_mw_per_degc,
                 "rooftop_solar_mw": z.rooftop_solar_mw} for z in cat.zones]),
            (weather_providers, "provider_id", [
                {"provider_id": p.id, "name": p.name, "priority": p.priority}
                for p in cat.providers]),
        ):
            stmt = insert(table).values(rows)
            conn.execute(stmt.on_conflict_do_update(
                index_elements=[key],
                set_={c: stmt.excluded[c] for c in rows[0] if c != key},
            ))
    return {"stations": len(cat.stations), "zones": len(cat.zones), "providers": len(cat.providers)}
