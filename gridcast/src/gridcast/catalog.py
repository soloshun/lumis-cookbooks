"""Static reference data: stations, load zones, weather vendors and the holiday calendar."""

from datetime import date, datetime
from functools import lru_cache
from importlib.resources import files

import yaml
from pydantic import BaseModel


class Station(BaseModel):
    id: str
    name: str
    latitude: float
    longitude: float
    elevation_m: float


class Zone(BaseModel):
    id: str
    name: str
    station_id: str
    base_load_mw: float
    cooling_mw_per_degc: float
    rooftop_solar_mw: float
    comfort_temp_c: float


class Provider(BaseModel):
    id: str
    name: str
    priority: int


class Catalog(BaseModel):
    stations: list[Station]
    zones: list[Zone]
    providers: list[Provider]
    holidays: list[str]

    def station(self, station_id: str) -> Station:
        return next(s for s in self.stations if s.id == station_id)

    def zone(self, zone_id: str) -> Zone:
        return next(z for z in self.zones if z.id == zone_id)

    def station_for_zone(self, zone_id: str) -> Station:
        return self.station(self.zone(zone_id).station_id)

    def is_holiday(self, day: date | datetime) -> bool:
        return day.strftime("%m-%d") in self.holidays


@lru_cache
def catalog() -> Catalog:
    raw = yaml.safe_load(files("gridcast.data").joinpath("catalog.yaml").read_text())
    return Catalog.model_validate(raw)
