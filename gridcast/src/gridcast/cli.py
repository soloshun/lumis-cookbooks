"""`gridcast` — the in-container entrypoint for every GridCast workload.

    gridcast serve <service>              run an HTTP service
    gridcast pipeline worker              run the forecast pipeline on its cadence
    gridcast pipeline run-once            run one forecast pipeline flow
    gridcast db migrate                   apply migrations + reference data (schema owner)
    gridcast ingest backfill --days N     load history through the vendor APIs
    gridcast model train --profile P      train and register a model (optionally promote)
    gridcast model list                   show registry versions and aliases
    gridcast model promote --version V    move an alias to a version

Kept on argparse (no extra dependencies) so every image can run it.
"""

import argparse
import json
import logging
import sys

from gridcast import telemetry
from gridcast.logs import configure_logging

SERVICES = {
    "weather-vendor": "gridcast.services.weather_vendor",
    "grid-telemetry": "gridcast.services.grid_telemetry",
    "ingestion": "gridcast.services.ingestion",
    "feature-service": "gridcast.services.feature_service",
    "forecast-service": "gridcast.services.forecast_service",
    "planning-api": "gridcast.services.planning_api",
    "grid-operator": "gridcast.services.operator",
}


def _job(name: str) -> None:
    from gridcast.config import BaseServiceSettings
    from gridcast.release import load_release

    settings = BaseServiceSettings()
    release = load_release(settings.release_file, name)
    configure_logging(name, release.version, settings.log_level, settings.log_format)
    telemetry.setup_telemetry(name, release.version, settings.environment)


def _print(payload) -> None:  # noqa: ANN001
    print(json.dumps(payload, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gridcast")
    sub = parser.add_subparsers(dest="group", required=True)

    serve = sub.add_parser("serve", help="run an HTTP service")
    serve.add_argument("service", choices=sorted(SERVICES))

    pipe = sub.add_parser("pipeline", help="forecast pipeline")
    pipe.add_argument("action", choices=["worker", "run-once"])

    db = sub.add_parser("db", help="database administration")
    db.add_argument("action", choices=["migrate"])

    ingest = sub.add_parser("ingest", help="ingestion jobs")
    ingest.add_argument("action", choices=["backfill"])
    ingest.add_argument("--days", type=int, default=10)

    model = sub.add_parser("model", help="model registry and training")
    model.add_argument("action", choices=["train", "list", "promote"])
    model.add_argument("--profile", default="standard", choices=["standard", "hifi"])
    model.add_argument("--alias", default="production")
    model.add_argument("--promote", action="store_true", help="point --alias at the new model")
    model.add_argument("--version", type=int)
    model.add_argument("--actor", default="ml-platform")
    model.add_argument("--reason", default="manual promotion")
    model.add_argument("--max-train-rows", type=int)

    args = parser.parse_args(argv)

    if args.group == "serve":
        import importlib

        importlib.import_module(SERVICES[args.service]).main()
        return 0

    if args.group == "pipeline":
        _job("forecast-pipeline")
        from gridcast.pipeline import flows

        if args.action == "worker":
            flows.run_forever()
        else:
            _print(flows.forecast_pipeline())
            telemetry.shutdown()
        return 0

    if args.group == "db":
        _job("db-migrate")
        from gridcast.config import DatabaseSettings
        from gridcast.db.engine import make_engine
        from gridcast.db.migrate import upgrade
        from gridcast.db.reference import sync_reference

        settings = DatabaseSettings()
        upgrade(settings)
        _print({"migrated": True, "reference": sync_reference(
            make_engine(settings, application_name="db-migrate"))})
        return 0

    if args.group == "ingest":
        _job("ingestion-backfill")
        from gridcast.services.ingestion import Settings, build_ingestor

        _print(build_ingestor(Settings()).backfill(args.days))
        telemetry.shutdown()
        return 0

    if args.group == "model":
        _job("model-registry")
        from gridcast.db.engine import make_engine
        from gridcast.ml import registry

        engine = make_engine(application_name="model-registry")
        if args.action == "list":
            with engine.connect() as conn:
                _print(registry.list_versions(conn))
        elif args.action == "promote":
            if args.version is None:
                parser.error("--version is required")
            with engine.begin() as conn:
                previous = registry.set_alias(conn, args.alias, args.version, actor=args.actor,
                                              reason=args.reason)
            _print({"alias": args.alias, "version": args.version, "previous": previous})
        else:
            from gridcast.pipeline.flows import train_model

            _print(train_model(profile=args.profile,
                               promote_alias=args.alias if args.promote else None,
                               max_train_rows=args.max_train_rows, actor=args.actor))
        telemetry.shutdown()
        return 0
    return 1


if __name__ == "__main__":
    logging.captureWarnings(True)
    sys.exit(main())
