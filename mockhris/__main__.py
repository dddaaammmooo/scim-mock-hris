"""MockHRIS command line.

  python -m mockhris serve [--port 8000]       run API + GUI + MCP
  python -m mockhris mcp                       run the MCP server over stdio
  python -m mockhris seed [--seed N]           wipe users/groups and regenerate the company
  python -m mockhris check                     resolve every group and report errors
  python -m mockhris query 'team == "Payments"'
  python -m mockhris export out.json | import in.json [--merge]
  python -m mockhris integrations                   list provisioning integrations
  python -m mockhris plan <integration> [--full]     dry run
  python -m mockhris sync <integration> [--full]     sync (foreground)
  python -m mockhris verify <integration>            reconcile the target against the HRIS

DynamoDB endpoint comes from DYNAMODB_ENDPOINT (default http://localhost:8001).
"""
import argparse
import json
import sys

from . import services
from .hr import ServiceError
from .store import get_store


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mockhris", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve"); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8000)
    sub.add_parser("mcp")
    p = sub.add_parser("seed"); p.add_argument("--seed", type=int, default=20261114)
    sub.add_parser("check")
    p = sub.add_parser("query"); p.add_argument("query")
    p = sub.add_parser("export"); p.add_argument("path")
    p = sub.add_parser("import"); p.add_argument("path"); p.add_argument("--merge", action="store_true")
    sub.add_parser("integrations")
    p = sub.add_parser("plan"); p.add_argument("integration"); p.add_argument("--full", action="store_true")
    p = sub.add_parser("sync"); p.add_argument("integration"); p.add_argument("--full", action="store_true")
    p = sub.add_parser("verify"); p.add_argument("integration")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("mockhris.app:app", host=args.host, port=args.port)
        return 0
    if args.cmd == "mcp":
        from .mcp_server import main as mcp_main
        mcp_main()
        return 0

    get_store().ensure_tables()
    hr = services.hr()
    services.integrations().ensure_defaults()
    try:
        if args.cmd == "seed":
            print(json.dumps(hr.seed(args.seed)))
        elif args.cmd == "check":
            bad = 0
            for r in hr.validate_groups():
                if r["ok"]:
                    print(f"ok    {r['name']:<40} {r['memberCount']:>4}")
                else:
                    bad += 1
                    print(f"ERROR {r['name']:<40} {r['error']}")
            return 1 if bad else 0
        elif args.cmd == "query":
            print("\n".join(hr.evaluate_query(args.query)["emails"]))
        elif args.cmd == "export":
            with open(args.path, "w", encoding="utf-8") as fh:
                json.dump(hr.export(), fh, indent=2, ensure_ascii=False)
            print(f"wrote {args.path}")
        elif args.cmd == "import":
            with open(args.path, encoding="utf-8") as fh:
                print(json.dumps(hr.import_bundle(json.load(fh), replace=not args.merge)))
        elif args.cmd == "integrations":
            for i in services.integrations().list():
                state = "on" if i["enabled"] else "off"
                print(f"{i['id']:<20} {i['type']:<11} {state:<4} {i['connection']['baseUrl']}")
        elif args.cmd == "verify":
            report = services.engine(args.integration).verify()
            print(json.dumps(report, indent=2))
            return 0 if report.get("ok") else 1
        elif args.cmd == "plan":
            plan = services.engine(args.integration).plan(full=args.full)
            for op in plan["operations"]:
                print(f"{op['op']:<16} {op['key']:<45} {op.get('summary', '')}")
            print(json.dumps(plan["counts"]))
        else:  # sync (argparse only allows the commands above)
            run = services.runner().start(args.integration, full=args.full, background=False)
            for r in run["results"]:
                if r["status"] != "ok":
                    print(f"FAILED {r['op']} {r['key']}: {r.get('error')}")
            print(json.dumps({k: run[k] for k in ("id", "status", "total", "succeeded", "failed")}))
            return 0 if run["status"] in ("succeeded",) or run["total"] == 0 else 1
    except ServiceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
