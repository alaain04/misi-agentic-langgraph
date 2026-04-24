import json
import random
import subprocess
import sys
import time

BASE_URL = "http://localhost:8000"

_PACKAGE_JSON = json.dumps(
    {
        "name": "demo-app",
        "version": "1.0.0",
        "dependencies": {
            "express": "^4.18.0",
            "lodash": "^4.17.21",
            "axios": "^1.4.0",
        },
        "devDependencies": {"jest": "^29.0.0", "typescript": "^5.0.0"},
    }
)

# Transitives: express pulls in body-parser, accepts, qs, etc.
# axios pulls in follow-redirects, form-data, proxy-from-env.
_LOCK_FILES = {
    "package-lock.json": json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                # direct — with their own dep edges
                "node_modules/express": {
                    "version": "4.18.2",
                    "dependencies": {
                        "accepts": "~1.3.5",
                        "body-parser": "1.20.2",
                        "qs": "6.11.0",
                        "vary": "~1.1.2",
                    },
                },
                "node_modules/lodash": {"version": "4.17.21"},
                "node_modules/axios": {
                    "version": "1.4.0",
                    "dependencies": {
                        "follow-redirects": "^1.15.2",
                        "form-data": "^4.0.0",
                        "proxy-from-env": "^1.1.0",
                    },
                },
                "node_modules/jest": {
                    "version": "29.5.0",
                    "dependencies": {
                        "jest-circus": "^29.5.0",
                        "jest-cli": "^29.5.0",
                        "@jest/core": "^29.5.0",
                    },
                },
                "node_modules/typescript": {"version": "5.1.6"},
                # transitive — express
                "node_modules/accepts": {
                    "version": "1.3.8",
                    "dependencies": {
                        "mime-types": "~2.1.34",
                        "negotiator": "0.6.3",
                    },
                },
                "node_modules/body-parser": {
                    "version": "1.20.2",
                    "dependencies": {
                        "depd": "2.0.0",
                        "raw-body": "2.5.2",
                    },
                },
                "node_modules/depd": {"version": "2.0.0"},
                "node_modules/finalhandler": {"version": "1.2.0"},
                "node_modules/fresh": {"version": "0.5.2"},
                "node_modules/merge-descriptors": {"version": "1.0.1"},
                "node_modules/methods": {"version": "1.1.2"},
                "node_modules/mime-db": {"version": "1.52.0"},
                "node_modules/mime-types": {
                    "version": "2.1.35",
                    "dependencies": {"mime-db": "1.52.0"},
                },
                "node_modules/negotiator": {"version": "0.6.3"},
                "node_modules/on-finished": {"version": "2.4.1"},
                "node_modules/parseurl": {"version": "1.3.3"},
                "node_modules/path-to-regexp": {"version": "0.1.7"},
                "node_modules/proxy-addr": {"version": "2.0.7"},
                "node_modules/qs": {"version": "6.11.0"},
                "node_modules/range-parser": {"version": "1.2.1"},
                "node_modules/raw-body": {"version": "2.5.2"},
                "node_modules/router": {"version": "1.3.8"},
                "node_modules/safe-buffer": {"version": "5.2.1"},
                "node_modules/send": {"version": "0.18.0"},
                "node_modules/serve-static": {"version": "1.15.0"},
                "node_modules/setprototypeof": {"version": "1.2.0"},
                "node_modules/statuses": {"version": "2.0.1"},
                "node_modules/toidentifier": {"version": "1.0.1"},
                "node_modules/type-is": {"version": "1.6.18"},
                "node_modules/utils-merge": {"version": "1.0.1"},
                "node_modules/vary": {"version": "1.1.2"},
                # transitive — axios
                "node_modules/follow-redirects": {"version": "1.15.2"},
                "node_modules/form-data": {"version": "4.0.0"},
                "node_modules/proxy-from-env": {"version": "1.1.0"},
                # transitive — jest
                "node_modules/jest-circus": {"version": "29.5.0"},
                "node_modules/jest-cli": {"version": "29.5.0"},
                "node_modules/jest-config": {"version": "29.5.0"},
                "node_modules/jest-runtime": {"version": "29.5.0"},
                "node_modules/@jest/core": {"version": "29.5.0"},
                "node_modules/@jest/reporters": {"version": "29.5.0"},
                "node_modules/@jest/test-result": {"version": "29.5.0"},
                "node_modules/@jest/transform": {"version": "29.5.0"},
                "node_modules/babel-jest": {"version": "29.5.0"},
                "node_modules/graceful-fs": {"version": "4.2.11"},
            },
        }
    ),
    "yarn.lock": (
        "# yarn lockfile v1\n\n"
        # direct — with dep edges
        '"express@^4.18.0":\n'
        '  version "4.18.2"\n'
        "  dependencies:\n"
        '    accepts "~1.3.5"\n'
        '    body-parser "1.20.2"\n'
        '    qs "6.11.0"\n'
        '    vary "~1.1.2"\n\n'
        '"lodash@^4.17.21":\n  version "4.17.21"\n\n'
        '"axios@^1.4.0":\n'
        '  version "1.4.0"\n'
        "  dependencies:\n"
        '    follow-redirects "^1.15.2"\n'
        '    form-data "^4.0.0"\n'
        '    proxy-from-env "^1.1.0"\n\n'
        '"jest@^29.0.0":\n'
        '  version "29.5.0"\n'
        "  dependencies:\n"
        '    jest-circus "^29.5.0"\n'
        '    jest-cli "^29.5.0"\n'
        '    "@jest/core" "^29.5.0"\n\n'
        '"typescript@^5.0.0":\n  version "5.1.6"\n\n'
        # transitive — express
        '"accepts@~1.3.5":\n'
        '  version "1.3.8"\n'
        "  dependencies:\n"
        '    mime-types "~2.1.34"\n'
        '    negotiator "0.6.3"\n\n'
        '"body-parser@1.20.2":\n'
        '  version "1.20.2"\n'
        "  dependencies:\n"
        '    depd "2.0.0"\n'
        '    raw-body "2.5.2"\n\n'
        '"depd@2.0.0":\n  version "2.0.0"\n\n'
        '"finalhandler@1.2.0":\n  version "1.2.0"\n\n'
        '"fresh@0.5.2":\n  version "0.5.2"\n\n'
        '"merge-descriptors@1.0.1":\n  version "1.0.1"\n\n'
        '"methods@~1.1.2":\n  version "1.1.2"\n\n'
        '"mime-db@1.52.0":\n  version "1.52.0"\n\n'
        '"mime-types@~2.1.34":\n'
        '  version "2.1.35"\n'
        "  dependencies:\n"
        '    mime-db "1.52.0"\n\n'
        '"negotiator@0.6.3":\n  version "0.6.3"\n\n'
        '"on-finished@2.4.1":\n  version "2.4.1"\n\n'
        '"parseurl@~1.3.3":\n  version "1.3.3"\n\n'
        '"path-to-regexp@0.1.7":\n  version "0.1.7"\n\n'
        '"proxy-addr@~2.0.7":\n  version "2.0.7"\n\n'
        '"qs@6.11.0":\n  version "6.11.0"\n\n'
        '"range-parser@~1.2.1":\n  version "1.2.1"\n\n'
        '"raw-body@2.5.2":\n  version "2.5.2"\n\n'
        '"safe-buffer@5.2.1":\n  version "5.2.1"\n\n'
        '"send@0.18.0":\n  version "0.18.0"\n\n'
        '"serve-static@1.15.0":\n  version "1.15.0"\n\n'
        '"setprototypeof@1.2.0":\n  version "1.2.0"\n\n'
        '"statuses@2.0.1":\n  version "2.0.1"\n\n'
        '"toidentifier@1.0.1":\n  version "1.0.1"\n\n'
        '"type-is@~1.6.18":\n  version "1.6.18"\n\n'
        '"utils-merge@1.0.1":\n  version "1.0.1"\n\n'
        '"vary@~1.1.2":\n  version "1.1.2"\n\n'
        # transitive — axios
        '"follow-redirects@^1.15.2":\n  version "1.15.2"\n\n'
        '"form-data@^4.0.0":\n  version "4.0.0"\n\n'
        '"proxy-from-env@^1.1.0":\n  version "1.1.0"\n\n'
        # transitive — jest
        '"jest-circus@^29.5.0":\n  version "29.5.0"\n\n'
        '"jest-cli@^29.5.0":\n  version "29.5.0"\n\n'
        '"jest-config@^29.5.0":\n  version "29.5.0"\n\n'
        '"jest-runtime@^29.5.0":\n  version "29.5.0"\n\n'
        '"@jest/core@^29.5.0":\n  version "29.5.0"\n\n'
        '"@jest/reporters@^29.5.0":\n  version "29.5.0"\n\n'
        '"@jest/test-result@^29.5.0":\n  version "29.5.0"\n\n'
        '"@jest/transform@^29.5.0":\n  version "29.5.0"\n\n'
        '"babel-jest@^29.5.0":\n  version "29.5.0"\n\n'
        '"graceful-fs@^4.2.0":\n  version "4.2.11"\n'
    ),
    "pnpm-lock.yaml": (
        "lockfileVersion: '6.0'\n\n"
        "dependencies:\n"
        "  express:\n    specifier: ^4.18.0\n    version: 4.18.2\n"
        "  lodash:\n    specifier: ^4.17.21\n    version: 4.17.21\n"
        "  axios:\n    specifier: ^1.4.0\n    version: 1.4.0\n\n"
        "devDependencies:\n"
        "  jest:\n    specifier: ^29.0.0\n    version: 29.5.0\n"
        "  typescript:\n    specifier: ^5.0.0\n    version: 5.1.6\n\n"
        "packages:\n\n"
        # direct — with dep edges
        "  /express/4.18.2:\n"
        "    resolution: {integrity: sha512-abc}\n"
        "    dependencies:\n"
        "      accepts: 1.3.8\n"
        "      body-parser: 1.20.2\n"
        "      qs: 6.11.0\n"
        "      vary: 1.1.2\n"
        "  /lodash/4.17.21:\n    resolution: {integrity: sha512-def}\n"
        "  /axios/1.4.0:\n"
        "    resolution: {integrity: sha512-ghi}\n"
        "    dependencies:\n"
        "      follow-redirects: 1.15.2\n"
        "      form-data: 4.0.0\n"
        "      proxy-from-env: 1.1.0\n"
        "  /jest/29.5.0:\n"
        "    resolution: {integrity: sha512-jkl}\n"
        "    dependencies:\n"
        "      jest-circus: 29.5.0\n"
        "      jest-cli: 29.5.0\n"
        "      /@jest/core/29.5.0: 29.5.0\n"
        "  /typescript/5.1.6:\n    resolution: {integrity: sha512-mno}\n"
        # transitive — express
        "  /accepts/1.3.8:\n"
        "    resolution: {integrity: sha512-pqr}\n"
        "    dependencies:\n"
        "      mime-types: 2.1.35\n"
        "      negotiator: 0.6.3\n"
        "  /body-parser/1.20.2:\n"
        "    resolution: {integrity: sha512-stu}\n"
        "    dependencies:\n"
        "      depd: 2.0.0\n"
        "      raw-body: 2.5.2\n"
        "  /depd/2.0.0:\n    resolution: {integrity: sha512-vwx}\n"
        "  /finalhandler/1.2.0:\n    resolution: {integrity: sha512-yza}\n"
        "  /fresh/0.5.2:\n    resolution: {integrity: sha512-bcd}\n"
        "  /merge-descriptors/1.0.1:\n    resolution: {integrity: sha512-efg}\n"
        "  /methods/1.1.2:\n    resolution: {integrity: sha512-hij}\n"
        "  /mime-db/1.52.0:\n    resolution: {integrity: sha512-klm}\n"
        "  /mime-types/2.1.35:\n"
        "    resolution: {integrity: sha512-nop}\n"
        "    dependencies:\n"
        "      mime-db: 1.52.0\n"
        "  /negotiator/0.6.3:\n    resolution: {integrity: sha512-qrs}\n"
        "  /on-finished/2.4.1:\n    resolution: {integrity: sha512-tuv}\n"
        "  /parseurl/1.3.3:\n    resolution: {integrity: sha512-wxy}\n"
        "  /path-to-regexp/0.1.7:\n    resolution: {integrity: sha512-zab}\n"
        "  /proxy-addr/2.0.7:\n    resolution: {integrity: sha512-cde}\n"
        "  /qs/6.11.0:\n    resolution: {integrity: sha512-fgh}\n"
        "  /range-parser/1.2.1:\n    resolution: {integrity: sha512-ijk}\n"
        "  /raw-body/2.5.2:\n    resolution: {integrity: sha512-lmn}\n"
        "  /safe-buffer/5.2.1:\n    resolution: {integrity: sha512-opq}\n"
        "  /send/0.18.0:\n    resolution: {integrity: sha512-rst}\n"
        "  /serve-static/1.15.0:\n    resolution: {integrity: sha512-uvw}\n"
        "  /setprototypeof/1.2.0:\n    resolution: {integrity: sha512-xyz}\n"
        "  /statuses/2.0.1:\n    resolution: {integrity: sha512-abc2}\n"
        "  /toidentifier/1.0.1:\n    resolution: {integrity: sha512-def2}\n"
        "  /type-is/1.6.18:\n    resolution: {integrity: sha512-ghi2}\n"
        "  /utils-merge/1.0.1:\n    resolution: {integrity: sha512-jkl2}\n"
        "  /vary/1.1.2:\n    resolution: {integrity: sha512-mno2}\n"
        # transitive — axios
        "  /follow-redirects/1.15.2:\n    resolution: {integrity: sha512-pqr2}\n"
        "  /form-data/4.0.0:\n    resolution: {integrity: sha512-stu2}\n"
        "  /proxy-from-env/1.1.0:\n    resolution: {integrity: sha512-vwx2}\n"
        # transitive — jest
        "  /jest-circus/29.5.0:\n    resolution: {integrity: sha512-yza2}\n"
        "  /jest-cli/29.5.0:\n    resolution: {integrity: sha512-bcd2}\n"
        "  /jest-config/29.5.0:\n    resolution: {integrity: sha512-efg2}\n"
        "  /jest-runtime/29.5.0:\n    resolution: {integrity: sha512-hij2}\n"
        "  /@jest/core/29.5.0:\n    resolution: {integrity: sha512-klm2}\n"
        "  /@jest/reporters/29.5.0:\n    resolution: {integrity: sha512-nop2}\n"
        "  /@jest/test-result/29.5.0:\n    resolution: {integrity: sha512-qrs2}\n"
        "  /@jest/transform/29.5.0:\n    resolution: {integrity: sha512-tuv2}\n"
        "  /babel-jest/29.5.0:\n    resolution: {integrity: sha512-wxy2}\n"
        "  /graceful-fs/4.2.11:\n    resolution: {integrity: sha512-zab2}\n"
    ),
}


def _submit(lock_file_name: str, concern: str) -> str:
    """POST /analyze and return the trace_id."""
    payload = json.dumps(
        {
            "concern": concern,
            "lock_file_name": lock_file_name,
            "package_json": _PACKAGE_JSON,
            "lock_file": _LOCK_FILES[lock_file_name],
        }
    )
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", f"{BASE_URL}/analyze",
         "-H", "Content-Type: application/json", "-d", payload],
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout)
    trace_id = data["trace_id"]
    print(f"  submitted → trace_id={trace_id}  status={data['status']}")
    return trace_id


def _poll(trace_id: str, timeout: int = 60) -> dict:
    """GET /analyze/{trace_id} until done or failed (or timeout)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            ["curl", "-s", f"{BASE_URL}/analyze/{trace_id}"],
            capture_output=True,
            text=True,
        )
        data = json.loads(result.stdout)
        status = data.get("status")
        if status in ("done", "failed"):
            return data
        print(f"  polling  → status={status}")
        time.sleep(2)
    raise TimeoutError(f"job {trace_id} did not finish within {timeout}s")


def curl_test():
    """Run a quick end-to-end curl smoke test against the running API."""
    fixed_cases = [
        ("package-lock.json", "security vulnerabilities"),
        ("yarn.lock", "outdated packages"),
        ("pnpm-lock.yaml", "license compliance"),
    ]

    random_lock = random.choice(list(_LOCK_FILES))
    random_case = (random_lock, "dependency risk")

    passed = failed = 0
    for lock_file_name, concern in [*fixed_cases, random_case]:
        label = f"[{lock_file_name}]"
        if (lock_file_name, concern) == random_case:
            label += " (random)"
        print(f"\n{label} concern={concern!r}")
        try:
            trace_id = _submit(lock_file_name, concern)
            data = _poll(trace_id)
            status = data.get("status")
            if status == "done":
                print(f"  PASS     → status={status}")
                passed += 1
            else:
                print(f"  FAIL     → status={status}")
                failed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR    → {exc}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


def dev():
    sys.exit(subprocess.call(["uvicorn", "src.main:app", "--reload"]))


def lint():
    sys.exit(subprocess.call(["ruff", "check", ".", "--fix"]))


def fmt():
    sys.exit(subprocess.call(["ruff", "format", "."]))


def test():
    sys.exit(subprocess.call(["pytest"]))
