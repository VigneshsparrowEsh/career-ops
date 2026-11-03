import argparse
import json
import logging
import sys
import time
from pathlib import Path
from .collector import AlreadyRunning, collect
from .config import load, next_schedule
from .database import Database
from .server import csv_text, serve


def main():
    parser = argparse.ArgumentParser(description="JobHarvest daily job collector")
    parser.add_argument("--config", help="Configuration JSON path")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create the database")
    commands.add_parser("collect", help="Collect now, respecting source polling intervals")
    commands.add_parser("due", help="Collect once for the current daily schedule slot")
    commands.add_parser("schedule", help="Keep daily collection running in this process")
    web = commands.add_parser("serve", help="Start the local dashboard")
    web.add_argument("--port", type=int, default=8766)
    web.add_argument("--scheduler", action="store_true", help="Also run the daily scheduler")
    query = commands.add_parser("search", help="Search stored jobs")
    query.add_argument("--q", default="")
    query.add_argument("--location", default="")
    query.add_argument("--skill", default="")
    query.add_argument("--limit", type=int, default=20)
    commands.add_parser("status", help="Show counts and recent runs")
    sites = commands.add_parser("add-sites", help="Discover and save company career boards")
    sites.add_argument("--file", help="Text file: Company name | https://career-url, one per line")
    sites.add_argument("--starter", action="store_true", help="Add the included starter companies")
    export = commands.add_parser("export", help="Export all stored jobs to CSV")
    export.add_argument("--output", default="data/jobs.csv")
    backup = commands.add_parser("backup", help="Create a consistent SQLite backup")
    backup.add_argument("--output", default="data/jobs-backup.sqlite3")
    args = parser.parse_args()
    try:
        config = load(args.config)
        db = Database(config["_database"])
        log_path = Path(db.path).parent / "jobharvest.log"
        from logging.handlers import RotatingFileHandler
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.StreamHandler(), RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")])
        command = args.command
        result = None
        if command == "init":
            result = {"database": db.path, "status": "ready"}
        elif command in {"collect", "due"}:
            result = collect(config, due=command == "due")
        elif command == "serve":
            serve(config, port=args.port, scheduler=args.scheduler)
        elif command == "schedule":
            print("Daily collector running. Next collection: " + next_schedule(config), flush=True)
            while True:
                config = load(args.config)
                try:
                    result = collect(config, due=True)
                    if result["status"] != "not_due":
                        print(json.dumps(result), flush=True)
                except AlreadyRunning:
                    pass
                time.sleep(30)
        elif command == "search":
            result = db.search({"q": args.q, "location": args.location, "skill": args.skill}, max(1, min(100, args.limit)))
        elif command == "status":
            result = {**db.stats(), "schedule": config["schedule"], "next_collection": next_schedule(config), "runs": db.runs()}
        elif command == "add-sites":
            from .company_sites import add_company_sites, parse_sites, starter_sites
            if bool(args.file) == args.starter:
                raise ValueError("Choose exactly one of --file or --starter")
            entries = starter_sites() if args.starter else parse_sites(Path(args.file).read_text(encoding="utf-8-sig"))
            result = add_company_sites(config["_path"], entries)
        elif command == "export":
            output = (Path(config["_path"]).parent / args.output).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(csv_text(db), encoding="utf-8-sig")
            result = {"export": str(output)}
        elif command == "backup":
            output = (Path(config["_path"]).parent / args.output).resolve()
            db.backup(output)
            result = {"backup": str(output)}
        if result:
            print(json.dumps(result, indent=2, ensure_ascii=False))
            if result.get("status") in {"failed", "partial"}:
                return 1
        return 0
    except AlreadyRunning as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
