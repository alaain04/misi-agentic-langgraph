#!/usr/bin/env python3
"""Debug main graph nodes independently with canned inputs.

Usage (run from backend/):
    uv run python scripts/debug_subgraphs.py discovery
    uv run python scripts/debug_subgraphs.py planner
    uv run python scripts/debug_subgraphs.py dispatch
    uv run python scripts/debug_subgraphs.py skill <skill_id>
    uv run python scripts/debug_subgraphs.py correlate

Nodes NOT covered here:
    - evidence_collector: no-op fan-in node, nothing to debug.
    - skill_executor: thin wrapper around skills; use 'skill' mode instead.

Requires API keys in .env.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

from src.main_graph.skills.base import SkillContext
from src.models.evidence import Evidence
from src.models.hypothesis import Hypothesis
from src.models.investigation_plan import InvestigationPlan, SkillAssignment

# ── Canned fixtures ────────────────────────────────────────────────────────────

REPO_URL = "https://github.com/jsynowiec/node-typescript-boilerplate"
JOB_ID = str(int(time.time()))
CONCERN = "security"

# CANNED_SBOM = {
#     "metadata": {"component": {"bom-ref": "root", "name": "my-app"}},
#     "components": [
#         {"name": "express", "bom-ref": "express@4.18.0", "version": "4.18.0"},
#         {"name": "lodash", "bom-ref": "lodash@4.17.21", "version": "4.17.21"},
#     ],
#     "dependencies": [
#         {"ref": "root", "dependsOn": ["express@4.18.0"]},
#     ],
# }
CANNED_SBOM = {
     "metadata": {
      "timestamp": "2026-06-28T07:58:08.019Z",
      "lifecycles": [
        {
          "phase": "pre-build"
        }
      ],
      "tools": [
        {
          "vendor": "npm",
          "name": "cli",
          "version": "11.16.0"
        }
      ],
      "component": {
        "bom-ref": "node-typescript-boilerplate@0.0.0",
        "type": "library",
        "name": "workspace",
        "version": "0.0.0",
        "scope": "required",
        "author": "Jakub Synowiec <jsynowiec@users.noreply.github.com>",
        "description": "Minimalistic boilerplate to quick-start Node.js development in TypeScript.",
        "purl": "pkg:npm/node-typescript-boilerplate@0.0.0",
        "properties": [],
        "externalReferences": [],
        "licenses": [
          {
            "license": {
              "id": "Apache-2.0"
            }
          }
        ]
      }
    },
    "components": [
      {
        "bom-ref": "@babel/helper-string-parser@7.27.1",
        "type": "library",
        "name": "@babel/helper-string-parser",
        "version": "7.27.1",
        "scope": "optional",
        "purl": "pkg:npm/%40babel/helper-string-parser@7.27.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@babel/helper-string-parser/-/helper-string-parser-7.27.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "a8c952c4a6e946502b89d0c4c64f769d2a1bc837693e28d4ab60d6ea80e752a77488e1b19908f2aa13088a123dfb3bf82cfc997518ded9c6af58f6c26d69b778"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@babel/helper-validator-identifier@7.28.5",
        "type": "library",
        "name": "@babel/helper-validator-identifier",
        "version": "7.28.5",
        "scope": "optional",
        "purl": "pkg:npm/%40babel/helper-validator-identifier@7.28.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@babel/helper-validator-identifier/-/helper-validator-identifier-7.28.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "a92b3889fc33289495dfdb9c363b2f73a5951ece9bed2d37b0e87639c1c5f541df54fa965802d4b0d515ce1481888b63459a0b1f1ee721aad58ea295bac519d5"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@babel/parser@7.29.2",
        "type": "library",
        "name": "@babel/parser",
        "version": "7.29.2",
        "scope": "optional",
        "purl": "pkg:npm/%40babel/parser@7.29.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@babel/parser/-/parser-7.29.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "e06811cf2ffe7ec05aef6fd165526618a3e666ef41ca7f28e0ca0ba6635ed66f197d89f3e5e9872d0cf753880bb9de99c25d1164872088b0fb52aef2485b832c"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@babel/types@7.29.0",
        "type": "library",
        "name": "@babel/types",
        "version": "7.29.0",
        "scope": "optional",
        "purl": "pkg:npm/%40babel/types@7.29.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@babel/types/-/types-7.29.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "2f07591e949c338433f17c3688a4b34be71f825673246be87d0202cbb5bbbf871aaeee046809b252e3ba046adbc90da6615d755b453c8f998185dd7875ddc1d0"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@bcoe/v8-coverage@1.0.2",
        "type": "library",
        "name": "@bcoe/v8-coverage",
        "version": "1.0.2",
        "scope": "optional",
        "purl": "pkg:npm/%40bcoe/v8-coverage@1.0.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@bcoe/v8-coverage/-/v8-coverage-1.0.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "eb300193f10203f418482435346895c306d07ab50267e4d06e9eb843702099f36fbab1c7d23f13b576b5a9b4a15c0eaaaa4a408f85795bca4fea62ded6670ca8"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@emnapi/core@1.9.2",
        "type": "library",
        "name": "@emnapi/core",
        "version": "1.9.2",
        "scope": "optional",
        "purl": "pkg:npm/%40emnapi/core@1.9.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@emnapi/core/-/core-1.9.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "502f99847dd7b5ccd061f3a5bb794d124756fe9e1db09d6bfdb3fb1fcfab85aa374d34cc3b5013abfe037488b6dd7b86a0563e0b3d099826dd565d21cfd8d970"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@emnapi/runtime@1.9.2",
        "type": "library",
        "name": "@emnapi/runtime",
        "version": "1.9.2",
        "scope": "optional",
        "purl": "pkg:npm/%40emnapi/runtime@1.9.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@emnapi/runtime/-/runtime-1.9.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "dd4e3e3085872267b2bb5c27995ca08795a581f603b727f493c01b2e1305c4e8a98a17fa9eb582e2cc889bf4b011e79cd2635269f8a2367309c1bcdafc8b3a2f"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@emnapi/wasi-threads@1.2.1",
        "type": "library",
        "name": "@emnapi/wasi-threads",
        "version": "1.2.1",
        "scope": "optional",
        "purl": "pkg:npm/%40emnapi/wasi-threads@1.2.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@emnapi/wasi-threads/-/wasi-threads-1.2.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "b93208ece605fbf31eb3f32b708398a79c8eb5230b056488a0b3e9720c22a6888a6e58ba937db6b5ca05b31a082313c3a96d35490180824a38133662d8136fdb"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@eslint-community/eslint-utils@4.9.1",
        "type": "library",
        "name": "@eslint-community/eslint-utils",
        "version": "4.9.1",
        "scope": "optional",
        "purl": "pkg:npm/%40eslint-community/eslint-utils@4.9.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@eslint-community/eslint-utils/-/eslint-utils-4.9.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "a61ad898d898a6947bce714476a81f5875d1e8d0a46442bb8705831d95238adff6fd4d2be97be40e5d12627a0ce751eaec584219d2c34facf1082398d617b1b1"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "eslint-visitor-keys@3.4.3",
        "type": "library",
        "name": "eslint-visitor-keys",
        "version": "3.4.3",
        "scope": "optional",
        "purl": "pkg:npm/eslint-visitor-keys@3.4.3",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/eslint-visitor-keys/-/eslint-visitor-keys-3.4.3.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "c2973e2d77a2ca28acc4f944914cd4eacbf24b57eb20edcc8318f57ddcbb3e6f1883382e6b1d8ddc56bf0ff6a0d56a9b3a9add23eb98eb031497cfdad86fa26a"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "Apache-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "@eslint-community/regexpp@4.12.2",
        "type": "library",
        "name": "@eslint-community/regexpp",
        "version": "4.12.2",
        "scope": "optional",
        "purl": "pkg:npm/%40eslint-community/regexpp@4.12.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@eslint-community/regexpp/-/regexpp-4.12.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "12b8924e5b79382f7fed25e445208085f4b1c684948019b7dce1fe224c1b769828aac4ac520ef2dee87e208088fd08380415abdd4da2dfd4699b271bc4cab87b"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@eslint/config-array@0.23.5",
        "type": "library",
        "name": "@eslint/config-array",
        "version": "0.23.5",
        "scope": "optional",
        "purl": "pkg:npm/%40eslint/config-array@0.23.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@eslint/config-array/-/config-array-0.23.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "63790a2ef0b576f4ce4fea0696a350d572ea2ba0f51d4d985cf739d8d98094965b30c583cc66173223d127c4d80f7f66b83fce4e395998d27889beddbd2acc04"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "Apache-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "@eslint/config-helpers@0.5.5",
        "type": "library",
        "name": "@eslint/config-helpers",
        "version": "0.5.5",
        "scope": "optional",
        "purl": "pkg:npm/%40eslint/config-helpers@0.5.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@eslint/config-helpers/-/config-helpers-0.5.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "78825829308409b3ff9ec29a6abb85e8b5bdebb9ad6d06ecc38253b5256451073d32779291bae03c901b2a5f675abd197a8c15f017ec6ab0663e907319e940db"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "Apache-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "@eslint/core@1.2.1",
        "type": "library",
        "name": "@eslint/core",
        "version": "1.2.1",
        "scope": "optional",
        "purl": "pkg:npm/%40eslint/core@1.2.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@eslint/core/-/core-1.2.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "330704d4ff806780ba0d69698a7fce98e039e2698867ffb166e26241de12c81dbda00263377d145bdc2428da6d5b672da78704b2f8652d8fc2b10d6ea070e5a1"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "Apache-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "@eslint/js@10.0.1",
        "type": "library",
        "name": "@eslint/js",
        "version": "10.0.1",
        "scope": "optional",
        "purl": "pkg:npm/%40eslint/js@10.0.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@eslint/js/-/js-10.0.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "cde47d939a5de20c6367469b46821ac5d73b2379c392da17664daa3aff6008d5b1de6570127df65518722da46c0e22634ecd31abf4fc99f3edcae5eeec658170"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@eslint/object-schema@3.0.5",
        "type": "library",
        "name": "@eslint/object-schema",
        "version": "3.0.5",
        "scope": "optional",
        "purl": "pkg:npm/%40eslint/object-schema@3.0.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@eslint/object-schema/-/object-schema-3.0.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "bea4da504831ce6f980d274495a77a3e24685f8b7c5460e30adb74e739f89d4f35d14231fee34457bfe5649e8ac054e12993b33b1cd7cb8f1d6be368ec765a33"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "Apache-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "@eslint/plugin-kit@0.7.1",
        "type": "library",
        "name": "@eslint/plugin-kit",
        "version": "0.7.1",
        "scope": "optional",
        "purl": "pkg:npm/%40eslint/plugin-kit@0.7.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@eslint/plugin-kit/-/plugin-kit-0.7.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "ad900fdda56007d76cf4a39e5122fecd9db584f9a8f1d87a7e7205c11423e4401997d83347bc3161b617632b0033c093a869941b2a764b8914755d7b3271ae59"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "Apache-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "@humanfs/core@0.19.2",
        "type": "library",
        "name": "@humanfs/core",
        "version": "0.19.2",
        "scope": "optional",
        "purl": "pkg:npm/%40humanfs/core@0.19.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@humanfs/core/-/core-0.19.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "5215cd9be08531671b0a15f2c05c249a1aa3b373d10a67126bf85f0602c86fba10e4735bd704b489c5ac1ad48050d81e7c7788f9e06b03c2357f199b1ec19dbc"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "Apache-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "@humanfs/node@0.16.8",
        "type": "library",
        "name": "@humanfs/node",
        "version": "0.16.8",
        "scope": "optional",
        "purl": "pkg:npm/%40humanfs/node@0.16.8",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@humanfs/node/-/node-0.16.8.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "804d5e40d67747efa44f3154a5d1a5a66cbc903643fcc2f21ea0f0aa3915408d0931d2350f9d6ccb51fde7c3cd5d890cdab01a73b7b9fc29c8299ac7b4f87705"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "Apache-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "@humanfs/types@0.15.0",
        "type": "library",
        "name": "@humanfs/types",
        "version": "0.15.0",
        "scope": "optional",
        "purl": "pkg:npm/%40humanfs/types@0.15.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@humanfs/types/-/types-0.15.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "659d70d1aa10930b94b82ed87feeec75e68d7ea42288b71245b7c8d3ca00c6a2eda5742bf402155fb032ec72c3ba22d801a14fbbca0160dabf4088bd5111c9dd"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "Apache-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "@humanwhocodes/module-importer@1.0.1",
        "type": "library",
        "name": "@humanwhocodes/module-importer",
        "version": "1.0.1",
        "scope": "optional",
        "purl": "pkg:npm/%40humanwhocodes/module-importer@1.0.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@humanwhocodes/module-importer/-/module-importer-1.0.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "6f1bde57857cbf961be277054d3deb3d281904ea429237cad32e28555549c08b8354144c0d7acfc9744bf7cf22e5aa7d9bd6e7c8412359f9b95a4066b5f7cb7c"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "Apache-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "@humanwhocodes/retry@0.4.3",
        "type": "library",
        "name": "@humanwhocodes/retry",
        "version": "0.4.3",
        "scope": "optional",
        "purl": "pkg:npm/%40humanwhocodes/retry@0.4.3",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@humanwhocodes/retry/-/retry-0.4.3.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "6d5d13828f4ae217cf09e93e68c027f35469a452afdb248341e328499baf4c04b2c0d4e7549080ac2644d855aaa6f21ab4abbb54c44b5a547511acef5610f285"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "Apache-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "@jridgewell/resolve-uri@3.1.2",
        "type": "library",
        "name": "@jridgewell/resolve-uri",
        "version": "3.1.2",
        "scope": "optional",
        "purl": "pkg:npm/%40jridgewell/resolve-uri@3.1.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@jridgewell/resolve-uri/-/resolve-uri-3.1.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "6d12128022233f6d3fb5b5923d63048b9e1054f45913192e0fd9492fe508c542adc15240f305b54eb6f58ccb354455e8d42053359ff98690bd42f98a59da292b"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@jridgewell/sourcemap-codec@1.5.5",
        "type": "library",
        "name": "@jridgewell/sourcemap-codec",
        "version": "1.5.5",
        "scope": "optional",
        "purl": "pkg:npm/%40jridgewell/sourcemap-codec@1.5.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@jridgewell/sourcemap-codec/-/sourcemap-codec-1.5.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "71843ddf5d20aeac6e7966e5f96b885086a251a0dc8fb58eab97d58449633558117ce52163d7f2db34ef7e8a96b2779b87c4a5ef45527056c80af2672ca0743a"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@jridgewell/trace-mapping@0.3.31",
        "type": "library",
        "name": "@jridgewell/trace-mapping",
        "version": "0.3.31",
        "scope": "optional",
        "purl": "pkg:npm/%40jridgewell/trace-mapping@0.3.31",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@jridgewell/trace-mapping/-/trace-mapping-0.3.31.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "cf3351f9275048327373c8e869e3fc410a0242bf0db98c76748232b65d507811191c9f6e5ba85e6ecad881bcfc849c1441aa374d608cb667d5f0dbb5b7038b03"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@napi-rs/wasm-runtime@1.1.4",
        "type": "library",
        "name": "@napi-rs/wasm-runtime",
        "version": "1.1.4",
        "scope": "optional",
        "purl": "pkg:npm/%40napi-rs/wasm-runtime@1.1.4",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@napi-rs/wasm-runtime/-/wasm-runtime-1.1.4.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "dcd40d3600356129496ff90c1f58a574048ff475bbffb9189d1236b335896a87da4b585699b188e07f9ddfedb6686cd75cdf4827e9fe1a21557068a924fd7ca3"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@oxc-project/types@0.126.0",
        "type": "library",
        "name": "@oxc-project/types",
        "version": "0.126.0",
        "scope": "optional",
        "purl": "pkg:npm/%40oxc-project/types@0.126.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@oxc-project/types/-/types-0.126.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "a067d5b63020c10555a5f06b6ed9387b55c3c961d116d6ba052dc6595ceb17cc58053d95190024dfdc894bfc0548cad9aa8882538a20853dc28741da1c620771"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@rolldown/binding-android-arm64@1.0.0-rc.16",
        "type": "library",
        "name": "@rolldown/binding-android-arm64",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/%40rolldown/binding-android-arm64@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@rolldown/binding-android-arm64/-/binding-android-arm64-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "ae163793b06c69ef6a41f3ada61d8f9b68d9100facf069a3a33e21866c7bd0af62310fdd75e69efb185141c33922e571e6bcb5f9b19f92f327ec3e8c26081548"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@rolldown/binding-darwin-arm64@1.0.0-rc.16",
        "type": "library",
        "name": "@rolldown/binding-darwin-arm64",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/%40rolldown/binding-darwin-arm64@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@rolldown/binding-darwin-arm64/-/binding-darwin-arm64-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "acdcf4c8ad3bf32acd9f70eb76037e3ca88c396f077d0f768d0897c705fcc96f3df5ac95d3430b55d68235e541846fd36c7de8b9854e6e8f3fcab92279cb6471"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@rolldown/binding-darwin-x64@1.0.0-rc.16",
        "type": "library",
        "name": "@rolldown/binding-darwin-x64",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/%40rolldown/binding-darwin-x64@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@rolldown/binding-darwin-x64/-/binding-darwin-x64-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "aff3a6751d341e60f88bbf59fffc4ed3ab843ceab98515dd870ee7ce4c50c7049abecdcf4876b58a39ed7693a26769b3390ddf555bbc0b5335f45a0d33e74c51"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@rolldown/binding-freebsd-x64@1.0.0-rc.16",
        "type": "library",
        "name": "@rolldown/binding-freebsd-x64",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/%40rolldown/binding-freebsd-x64@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@rolldown/binding-freebsd-x64/-/binding-freebsd-x64-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "29c444e70f21d0e9e351ab46f2995dc83d78fc24393e1b35a317d1fb7a4a0e36e81d1a3df8c92a41a888665651a6cc42d79a5a79799efc8d76eed51af535c9ea"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@rolldown/binding-linux-arm-gnueabihf@1.0.0-rc.16",
        "type": "library",
        "name": "@rolldown/binding-linux-arm-gnueabihf",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/%40rolldown/binding-linux-arm-gnueabihf@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@rolldown/binding-linux-arm-gnueabihf/-/binding-linux-arm-gnueabihf-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "6d3d20b80d5ba71109fd98534678907fbacd17cc9bbd73ae59b34878b001695e4d1a3c7812d396053491196154f5959590f399f873453fc44c701a240626742a"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@rolldown/binding-linux-arm64-gnu@1.0.0-rc.16",
        "type": "library",
        "name": "@rolldown/binding-linux-arm64-gnu",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/%40rolldown/binding-linux-arm64-gnu@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@rolldown/binding-linux-arm64-gnu/-/binding-linux-arm64-gnu-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "fad1e4b421d657c0434128de994aa6fc997f4cf93740e6c24c88e6743cbf9e5ba972e8d982198a2b6f7ad8b62ba85a56bbe6a2d3500dfd110e1f7344a6abd842"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@rolldown/binding-linux-arm64-musl@1.0.0-rc.16",
        "type": "library",
        "name": "@rolldown/binding-linux-arm64-musl",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/%40rolldown/binding-linux-arm64-musl@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@rolldown/binding-linux-arm64-musl/-/binding-linux-arm64-musl-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "ddf3f3751107f34ea844bc694d65b51ade2d407b344e2b5914e102076c730852cf2a748ecbdd20c00ecfdbd724b188a514ee9755163593381ad1c2f69d18ca3e"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@rolldown/binding-linux-ppc64-gnu@1.0.0-rc.16",
        "type": "library",
        "name": "@rolldown/binding-linux-ppc64-gnu",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/%40rolldown/binding-linux-ppc64-gnu@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@rolldown/binding-linux-ppc64-gnu/-/binding-linux-ppc64-gnu-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "10ac08d6d4ab2eced8570f893c94ff1b67494358e5f6a9534d3106d15d8e93f45d39e9d17c1c363d074b3f28e122ee7ca1c7417cfeef21137fa6f32c3f1b3f01"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@rolldown/binding-linux-s390x-gnu@1.0.0-rc.16",
        "type": "library",
        "name": "@rolldown/binding-linux-s390x-gnu",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/%40rolldown/binding-linux-s390x-gnu@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@rolldown/binding-linux-s390x-gnu/-/binding-linux-s390x-gnu-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "5249e569d9dbdd2c6abba48472a065750c8952993c3657a8a1911cd0c6d1049e229c4851616657d0d26ed76bcd7f69aa02aee0b287c0c47ac682189462385a2d"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@rolldown/binding-linux-x64-gnu@1.0.0-rc.16",
        "type": "library",
        "name": "@rolldown/binding-linux-x64-gnu",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/%40rolldown/binding-linux-x64-gnu@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@rolldown/binding-linux-x64-gnu/-/binding-linux-x64-gnu-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "1486fcfae1b8f6c641b4b4e7fb3b75009db44ea55ca96792232a15b74a2beee0167ac80a6876e2061e8ea40fe4f6fd0b4edf8f4eb6f52daa35df790fe2e57192"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@rolldown/binding-linux-x64-musl@1.0.0-rc.16",
        "type": "library",
        "name": "@rolldown/binding-linux-x64-musl",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/%40rolldown/binding-linux-x64-musl@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@rolldown/binding-linux-x64-musl/-/binding-linux-x64-musl-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "46e111845f7f1205b16445d858239a5625161c86e871e2b8fe2bddb50dd1d13e383632e4208946200540b8275d171b19eef9d11ed35052bb76bd1da7dac941db"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@rolldown/binding-openharmony-arm64@1.0.0-rc.16",
        "type": "library",
        "name": "@rolldown/binding-openharmony-arm64",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/%40rolldown/binding-openharmony-arm64@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@rolldown/binding-openharmony-arm64/-/binding-openharmony-arm64-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "9977179ef77d1a96b30b17940829d9dbe605ee7badf993846c4e06b5a88fb7263a02485959b2bbd32d4a2b78fe44386356ae7e53c1724a42916fffb0d04794c0"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@rolldown/binding-wasm32-wasi@1.0.0-rc.16",
        "type": "library",
        "name": "@rolldown/binding-wasm32-wasi",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/%40rolldown/binding-wasm32-wasi@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@rolldown/binding-wasm32-wasi/-/binding-wasm32-wasi-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "dd0d8a4319c2f0824e2ea5e650ca18c3220f654f61cd16e71daa15dc4bb3f955678d929c63c92d9cd3fc4fd478fc6190b5bdbb0bf5182800717ac2966fc96cbd"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@rolldown/binding-win32-arm64-msvc@1.0.0-rc.16",
        "type": "library",
        "name": "@rolldown/binding-win32-arm64-msvc",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/%40rolldown/binding-win32-arm64-msvc@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@rolldown/binding-win32-arm64-msvc/-/binding-win32-arm64-msvc-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "b63ed745e99070e705c2feea86953131305b2399963251387353a6860e7e87c1ae2d7cf28fc1ef898811f9b0760cc0e046a504fa388395ea920918f1e1a624fd"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@rolldown/binding-win32-x64-msvc@1.0.0-rc.16",
        "type": "library",
        "name": "@rolldown/binding-win32-x64-msvc",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/%40rolldown/binding-win32-x64-msvc@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@rolldown/binding-win32-x64-msvc/-/binding-win32-x64-msvc-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "3c7e434594fe1787f63d35d15d1f2e2719c1ab6a68ff116d75dc9a6d325526cfd96151ea5cf120348af7e481d311ae9ba5ad10f00c20fb29a44da1a72884f0e2"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@rolldown/pluginutils@1.0.0-rc.16",
        "type": "library",
        "name": "@rolldown/pluginutils",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/%40rolldown/pluginutils@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@rolldown/pluginutils/-/pluginutils-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "e39f98b6ac4b60a0d6428b8b282ae92198647be9d7c61b30faa0075731c3570b6dc8194734156cd8adb9ac35eb6738694e9f70d4594096fc1e5751fef9f759a0"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@standard-schema/spec@1.1.0",
        "type": "library",
        "name": "@standard-schema/spec",
        "version": "1.1.0",
        "scope": "optional",
        "purl": "pkg:npm/%40standard-schema/spec@1.1.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@standard-schema/spec/-/spec-1.1.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "976685cb98c02e19e21b91e0aab0fa8d72e2feb516acabea37fa89c7aca826c80a85b95577e8aaa94e110976af9bf8cf8adc83a394c2bca327a632a73ab8b2d3"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@tybys/wasm-util@0.10.1",
        "type": "library",
        "name": "@tybys/wasm-util",
        "version": "0.10.1",
        "scope": "optional",
        "purl": "pkg:npm/%40tybys/wasm-util@0.10.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@tybys/wasm-util/-/wasm-util-0.10.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "f6d4da3c92d289e8d92b1f819a8838b92b9bb5ea93bc5ad5ad44709261e2c41a341b8b1e0f4cd4c69f7c1350f35012712d0dcd3f05eb18a0e2563c31fc3a4fb2"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@types/chai@5.2.3",
        "type": "library",
        "name": "@types/chai",
        "version": "5.2.3",
        "scope": "optional",
        "purl": "pkg:npm/%40types/chai@5.2.3",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@types/chai/-/chai-5.2.3.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "330e79f28780f5f15bbfae7fcb8987b570ecf5b3e714c6402ff8f174f154a4e1c72175fdd667201076d2e4b6a1afea7064547c03b19095e456788e9c1850b650"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@types/deep-eql@4.0.2",
        "type": "library",
        "name": "@types/deep-eql",
        "version": "4.0.2",
        "scope": "optional",
        "purl": "pkg:npm/%40types/deep-eql@4.0.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@types/deep-eql/-/deep-eql-4.0.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "73d87d75554c8a030f7386f04ef0b9771aada8967040f78fb168cf96948e9e88dba2bea91aa764e78d657c0ec0a8542be6907505176ad23b98f5d6fcd41c3217"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@types/esrecurse@4.3.1",
        "type": "library",
        "name": "@types/esrecurse",
        "version": "4.3.1",
        "scope": "optional",
        "purl": "pkg:npm/%40types/esrecurse@4.3.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@types/esrecurse/-/esrecurse-4.3.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "c490406c389fa398697df0c1b8797463ccb0b306e2029fd68bb63f1ad0204a5672200069a72babc55b9e38f13c2d440ec5d9608ba66a71eeeea04a6a3537a253"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@types/estree@1.0.8",
        "type": "library",
        "name": "@types/estree",
        "version": "1.0.8",
        "scope": "optional",
        "purl": "pkg:npm/%40types/estree@1.0.8",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@types/estree/-/estree-1.0.8.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "7561f31dad96a845c8fced44f4e8eba1c313289976992ac4a258752289abbfa53e26e3706875ec5f1f5b2eee601bb05458520dd2c90840943f2f5ac87b1e17eb"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@types/json-schema@7.0.15",
        "type": "library",
        "name": "@types/json-schema",
        "version": "7.0.15",
        "scope": "optional",
        "purl": "pkg:npm/%40types/json-schema@7.0.15",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@types/json-schema/-/json-schema-7.0.15.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "e7e7cff0ff0c14d0be0326420f1ac1da991914f1b3a90594ce949ebae54bbe6f1531ca2b3586af06aa057312bc6d0cf842c6e7e2850411e9b8c032df732b061c"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@types/node@24.12.2",
        "type": "library",
        "name": "@types/node",
        "version": "24.12.2",
        "scope": "optional",
        "purl": "pkg:npm/%40types/node@24.12.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@types/node/-/node-24.12.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "035b2b7b6ea47bb1c322e63f336de777d81f07e9eb9a1b58c8c20d6e3235cc727162d78a47aa92317e7a16c9a331c0dbdd231c8c983906245180e082ff2043d2"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@typescript-eslint/eslint-plugin@8.59.0",
        "type": "library",
        "name": "@typescript-eslint/eslint-plugin",
        "version": "8.59.0",
        "scope": "optional",
        "purl": "pkg:npm/%40typescript-eslint/eslint-plugin@8.59.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@typescript-eslint/eslint-plugin/-/eslint-plugin-8.59.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "1f2019b69764819c29abc4b3dc5494bc247873e49c6ee59af4092c2b62707ae6fbc38337c93cf83b5d40a952732d88f2fc1f5958fc9cf3523e98e74953f6c343"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "ignore@7.0.5",
        "type": "library",
        "name": "ignore",
        "version": "7.0.5",
        "scope": "optional",
        "purl": "pkg:npm/ignore@7.0.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/ignore/-/ignore-7.0.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "1ece7dc4135f508ba730581601b197e5cabaf3ddc86d68382a7ae36d8c17dedc74ceda2b5604c303a076b317fc7a31c9e30cfc06a194318967ccd05eaf936f1a"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@typescript-eslint/parser@8.59.0",
        "type": "library",
        "name": "@typescript-eslint/parser",
        "version": "8.59.0",
        "scope": "optional",
        "purl": "pkg:npm/%40typescript-eslint/parser@8.59.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@typescript-eslint/parser/-/parser-8.59.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "4c8d571b029b0e9a3db515bc50321708e78b939e6a7bd6451acf0c4ca53afccd3c1d64f0e760c3fc86217d0b4e12111d3e12cc4f6e8a6bfc7ba7bd277777730e"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@typescript-eslint/project-service@8.59.0",
        "type": "library",
        "name": "@typescript-eslint/project-service",
        "version": "8.59.0",
        "scope": "optional",
        "purl": "pkg:npm/%40typescript-eslint/project-service@8.59.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@typescript-eslint/project-service/-/project-service-8.59.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "2f0e484eb479b394db0b5f584af96beb765f2da26853abed2931f20741903a95f45bb779fc8afabd46a15a2ffc4a9b3f9ceba4650d080774a69716678da2959f"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@typescript-eslint/scope-manager@8.59.0",
        "type": "library",
        "name": "@typescript-eslint/scope-manager",
        "version": "8.59.0",
        "scope": "optional",
        "purl": "pkg:npm/%40typescript-eslint/scope-manager@8.59.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@typescript-eslint/scope-manager/-/scope-manager-8.59.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "533475e94b7c22903731ce036e00128653cf9159bcc5731669f5f107406871a5511ecf1919a900c4646c905ec521ddec764f658060fbdc648569699614313876"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@typescript-eslint/tsconfig-utils@8.59.0",
        "type": "library",
        "name": "@typescript-eslint/tsconfig-utils",
        "version": "8.59.0",
        "scope": "optional",
        "purl": "pkg:npm/%40typescript-eslint/tsconfig-utils@8.59.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@typescript-eslint/tsconfig-utils/-/tsconfig-utils-8.59.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "f7549b977b3829bdd2c9b962218ea6b850661d5bfea585dfc9b0b83a8969dddbe4f01bc8137c0e3dcfb8d37096213ee624d91f4111ad76a821cecdbe67dd53aa"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@typescript-eslint/type-utils@8.59.0",
        "type": "library",
        "name": "@typescript-eslint/type-utils",
        "version": "8.59.0",
        "scope": "optional",
        "purl": "pkg:npm/%40typescript-eslint/type-utils@8.59.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@typescript-eslint/type-utils/-/type-utils-8.59.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "dd346265a41296d1aa19e36b273cebd7ef18704a1b287f6b1e7a88a7fd69b1f2859a14500cd3063f9841b9f6a76131b39f04a1cd52ecdcccfe80337b1e459f5e"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@typescript-eslint/types@8.59.0",
        "type": "library",
        "name": "@typescript-eslint/types",
        "version": "8.59.0",
        "scope": "optional",
        "purl": "pkg:npm/%40typescript-eslint/types@8.59.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@typescript-eslint/types/-/types-8.59.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "9cbcddb13d6074e805c71c70ae5355501cd23411041c9f3a6db9669384004bab2d7e283badc27358aa82cb1172dd84511d70d61246f636b6a50359ce624926fc"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@typescript-eslint/typescript-estree@8.59.0",
        "type": "library",
        "name": "@typescript-eslint/typescript-estree",
        "version": "8.59.0",
        "scope": "optional",
        "purl": "pkg:npm/%40typescript-eslint/typescript-estree@8.59.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@typescript-eslint/typescript-estree/-/typescript-estree-8.59.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "3bd45ef4fd419812c52728a445b4292e4bbf400dff02e79934ef5678f2c1c10aef922c539837bcbbbe81e826140084d197fac76b082012a5069f940891544b33"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@typescript-eslint/utils@8.59.0",
        "type": "library",
        "name": "@typescript-eslint/utils",
        "version": "8.59.0",
        "scope": "optional",
        "purl": "pkg:npm/%40typescript-eslint/utils@8.59.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@typescript-eslint/utils/-/utils-8.59.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "23547f2bb574ed7b0c275d8e6b183f3bd19faf2b064e609186f64906fd1113435c50b3338ea5694799114508c7b33dc9fdb12553b1f008eef392a2fe312533fa"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@typescript-eslint/visitor-keys@8.59.0",
        "type": "library",
        "name": "@typescript-eslint/visitor-keys",
        "version": "8.59.0",
        "scope": "optional",
        "purl": "pkg:npm/%40typescript-eslint/visitor-keys@8.59.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@typescript-eslint/visitor-keys/-/visitor-keys-8.59.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "fee7a366de1d49eaded5bc75d962e53dfbfc1a4b7371a0edb89eece36fc7119e731a3f68c51683e1b8fbab04ae9d791ffa96c0845b76ce3a47614893e651dfd1"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@vitest/coverage-v8@4.1.5",
        "type": "library",
        "name": "@vitest/coverage-v8",
        "version": "4.1.5",
        "scope": "optional",
        "purl": "pkg:npm/%40vitest/coverage-v8@4.1.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@vitest/coverage-v8/-/coverage-v8-4.1.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "dfc0b4fc375bec77111b4678fc351e9bcc79eddda9f63620a75f2691a62cc04390046b08d421b87ff8639b464279a25f5a1499e24ee382cdbd5756689bb2a2f4"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@vitest/eslint-plugin@1.6.16",
        "type": "library",
        "name": "@vitest/eslint-plugin",
        "version": "1.6.16",
        "scope": "optional",
        "purl": "pkg:npm/%40vitest/eslint-plugin@1.6.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@vitest/eslint-plugin/-/eslint-plugin-1.6.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "da904dd45d495eaeb34d26980b9f023096bba46c57211b0b7ce8a879933870f137a51752875c924d2a073d094e4c41795a0a15cd6650c61190df2813efc84e56"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@vitest/expect@4.1.5",
        "type": "library",
        "name": "@vitest/expect",
        "version": "4.1.5",
        "scope": "optional",
        "purl": "pkg:npm/%40vitest/expect@4.1.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@vitest/expect/-/expect-4.1.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "3d605a458e49a0ab919c79541dfa55fcaa2117295a0d94eea5c5cdd47f6f62bc8d2ce9e2b52c3ad0cc3d200136afaecd6f0c33070fc273ff2af412b79085fc2b"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@vitest/mocker@4.1.5",
        "type": "library",
        "name": "@vitest/mocker",
        "version": "4.1.5",
        "scope": "optional",
        "purl": "pkg:npm/%40vitest/mocker@4.1.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@vitest/mocker/-/mocker-4.1.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "ff1d849850b8993e0d373aaf0b77e67acb95f7bc39142f74dca3e67b2e20b27262310dc17b52250ca55a0da1bc8aa68b147a89d85544931664e5599e2e3208b7"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@vitest/pretty-format@4.1.5",
        "type": "library",
        "name": "@vitest/pretty-format",
        "version": "4.1.5",
        "scope": "optional",
        "purl": "pkg:npm/%40vitest/pretty-format@4.1.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@vitest/pretty-format/-/pretty-format-4.1.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "ec8deaea5e6aaf4ddd55f317db00a8f45c704896cf7702a3cb6baefd83e9537c1f1ef20be101f0551a79ece7c6ac315e509f3ff1075f04a215d76153b4b9f4d2"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@vitest/runner@4.1.5",
        "type": "library",
        "name": "@vitest/runner",
        "version": "4.1.5",
        "scope": "optional",
        "purl": "pkg:npm/%40vitest/runner@4.1.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@vitest/runner/-/runner-4.1.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "d83fa8ecfafcd8810ee3a60fa6803f614d2779ecabe854dead06f9468ec152706ebfa350b53fe49959dcce7822304061ce0ab3d94658979800ade8ecc8444349"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@vitest/snapshot@4.1.5",
        "type": "library",
        "name": "@vitest/snapshot",
        "version": "4.1.5",
        "scope": "optional",
        "purl": "pkg:npm/%40vitest/snapshot@4.1.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@vitest/snapshot/-/snapshot-4.1.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "cf2a5712de0a1ff5e02863d4cf8782d80bc4ad8c74332e617cbf280dbd47cc6169124d4feb66f14a885dc8e9afcfe77d509c1a9c8ebc30ac2bd84aae39a3a031"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@vitest/spy@4.1.5",
        "type": "library",
        "name": "@vitest/spy",
        "version": "4.1.5",
        "scope": "optional",
        "purl": "pkg:npm/%40vitest/spy@4.1.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@vitest/spy/-/spy-4.1.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "da534eb21ebe47621d9dfd53099a92c1894a37613f883943f2c814e7d918565f8e3039af95d3b5543937f6c9917e950dc18a4d4559f7c3861fb82eca7db06791"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "@vitest/utils@4.1.5",
        "type": "library",
        "name": "@vitest/utils",
        "version": "4.1.5",
        "scope": "optional",
        "purl": "pkg:npm/%40vitest/utils@4.1.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/@vitest/utils/-/utils-4.1.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "efac1d92b99f5dfa868ee7868276f8e484cfc948b5c9c678207802d9b84f0d47d61e4958feadcc74b38007e4c5d5eeb17e5f0dc4d63465868f0853564acc3752"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "acorn@8.16.0",
        "type": "library",
        "name": "acorn",
        "version": "8.16.0",
        "scope": "optional",
        "purl": "pkg:npm/acorn@8.16.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/acorn/-/acorn-8.16.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "51527213d32db4eb014080cac35b246fd9c0c10b91e70b860f7fbcd8ae89809966fd8f8a23dda836c30d199098743b15b511d26a4d29715e439e8e7ee2387db3"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "acorn-jsx@5.3.2",
        "type": "library",
        "name": "acorn-jsx",
        "version": "5.3.2",
        "scope": "optional",
        "purl": "pkg:npm/acorn-jsx@5.3.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/acorn-jsx/-/acorn-jsx-5.3.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "aeaf6cf893617f4202863b435f196527b838d68664e52957b69d0b1f0c80e5c7a3c27eef2a62a9e293eb8ba60478fbf63d4eb9b00b1e81b5ed2229e60c50d781"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "ajv@6.14.0",
        "type": "library",
        "name": "ajv",
        "version": "6.14.0",
        "scope": "optional",
        "purl": "pkg:npm/ajv@6.14.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/ajv/-/ajv-6.14.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "216ae8b26ff2ae7e377a22aa91f9078aced08a80e579a5d01dd0d53ca834152c3077f0eebf25fbf5366714e9d8a41edd72c140326b45ced66e5cf0ef49e3e417"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "assertion-error@2.0.1",
        "type": "library",
        "name": "assertion-error",
        "version": "2.0.1",
        "scope": "optional",
        "purl": "pkg:npm/assertion-error@2.0.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/assertion-error/-/assertion-error-2.0.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "2338bc45071f7ea09e3558058a02a58b5b2c92521ba479c261ce809275c662807a82b26ac9e6f2ee3bf5d895108264c09c80e76dc935bb192c4f87733773d604"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "ast-v8-to-istanbul@1.0.0",
        "type": "library",
        "name": "ast-v8-to-istanbul",
        "version": "1.0.0",
        "scope": "optional",
        "purl": "pkg:npm/ast-v8-to-istanbul@1.0.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/ast-v8-to-istanbul/-/ast-v8-to-istanbul-1.0.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "d5f49f230b83202140e0b2a40b344f3bb17487315fd01efe5eaae5dbbca741a6be461d1ed44b34bfa9161cfa2db77954d7403202bee82876bae4ea698cb9f746"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "balanced-match@4.0.4",
        "type": "library",
        "name": "balanced-match",
        "version": "4.0.4",
        "scope": "optional",
        "purl": "pkg:npm/balanced-match@4.0.4",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/balanced-match/-/balanced-match-4.0.4.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "04bae011c453c17da8ea01b118e08dc8cbc64a9df96287ee633c3d87520c4d198aaadb40659554ebb6dd6fd3ebdaf50703cfa3de2dad25f8cee82ebee26c864c"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "brace-expansion@5.0.5",
        "type": "library",
        "name": "brace-expansion",
        "version": "5.0.5",
        "scope": "optional",
        "purl": "pkg:npm/brace-expansion@5.0.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/brace-expansion/-/brace-expansion-5.0.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "559ce72e0b70867f8c69cb7db5f8b0c7ae1f03d7ab1c7fcc0971147c1ff46d7ffa173ea7cb91064d7625b4ca1caa0e31140419b673b70c75965e2f118ae37b71"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "chai@6.2.2",
        "type": "library",
        "name": "chai",
        "version": "6.2.2",
        "scope": "optional",
        "purl": "pkg:npm/chai@6.2.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/chai/-/chai-6.2.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "3543d196e39f3a24ca04abd63ed483e0f845bd60aa3a2d01192b4d5ace7b5fd8eced7193a6b4a6168cf9174b56851e163e335e47d8d7a9d0bbfd4a522539e546"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "convert-source-map@2.0.0",
        "type": "library",
        "name": "convert-source-map",
        "version": "2.0.0",
        "scope": "optional",
        "purl": "pkg:npm/convert-source-map@2.0.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/convert-source-map/-/convert-source-map-2.0.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "2afa78e7d1eb576144275080b22d4abbe318de46ac1f5f53172913cf6c5698c7aae9b936354dd75ef7c9f90eb59b4c64b56c2dfb51d261fdc966c4e6b3769126"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "cross-spawn@7.0.6",
        "type": "library",
        "name": "cross-spawn",
        "version": "7.0.6",
        "scope": "optional",
        "purl": "pkg:npm/cross-spawn@7.0.6",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/cross-spawn/-/cross-spawn-7.0.6.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "b95d903963f69d6ceccb668ca7c69189b862f5d9731791e0879487681f4e893184c834e2249cb1d2ecb9d505ddc966ed00736e6b85c9cd429c6b73b3294777bc"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "debug@4.4.3",
        "type": "library",
        "name": "debug",
        "version": "4.4.3",
        "scope": "optional",
        "purl": "pkg:npm/debug@4.4.3",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/debug/-/debug-4.4.3.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "446c305a7c10be455f6af295b76d8518bc3ec5849dcc04709b4aeee83853540dee994e6165cdbc57790ee2cb6062bcab4e52e9baf808f468a28e5b408cd6dca8"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "deep-is@0.1.4",
        "type": "library",
        "name": "deep-is",
        "version": "0.1.4",
        "scope": "optional",
        "purl": "pkg:npm/deep-is@0.1.4",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/deep-is/-/deep-is-0.1.4.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "a083f392c993838fccae289a6063bea245c34fbced9ffc37129b6fffe81221d31d2ac268d2ee027d834524fcbee1228cb82a86c36c319c0f9444c837b7c6bf6d"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "detect-libc@2.1.2",
        "type": "library",
        "name": "detect-libc",
        "version": "2.1.2",
        "scope": "optional",
        "purl": "pkg:npm/detect-libc@2.1.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/detect-libc/-/detect-libc-2.1.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "06d8f604e38ef37a375b21f9f5ef0c817b3111055c6ab9143a9118aee6c1d2eaf09cdd74c90dfae2bb22072535d67665a966199b4e62fe87fb8a8e26ce2841b5"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "Apache-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "es-module-lexer@2.0.0",
        "type": "library",
        "name": "es-module-lexer",
        "version": "2.0.0",
        "scope": "optional",
        "purl": "pkg:npm/es-module-lexer@2.0.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/es-module-lexer/-/es-module-lexer-2.0.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "e4f384714b99c9b1fb21d986b03f3095fd00239e7031e70cf6b5414c8fea100cb67359133a6dc38c5623ac1748d8adc16898c961f605791b4cd2df6cb2746ec7"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "escape-string-regexp@4.0.0",
        "type": "library",
        "name": "escape-string-regexp",
        "version": "4.0.0",
        "scope": "optional",
        "purl": "pkg:npm/escape-string-regexp@4.0.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/escape-string-regexp/-/escape-string-regexp-4.0.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "4eda5c349dd7033c771aaf2c591cc96956a346cd2e57103660091d6f58e6d9890fcf81ba7a05050320379f9bed10865e7cf93959ae145db2ae4b97ca90959d80"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "eslint@10.2.1",
        "type": "library",
        "name": "eslint",
        "version": "10.2.1",
        "scope": "optional",
        "purl": "pkg:npm/eslint@10.2.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/eslint/-/eslint-10.2.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "c22c8668ab0382a5ef178d0ff260f0894a7f2908c4d4576b20426c33c3d9dd70a29e24cc5d2dce1d65947b9148e5a8280a7afcc78c4fad30d9bb1b0194d1e5f9"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "eslint-config-prettier@10.1.8",
        "type": "library",
        "name": "eslint-config-prettier",
        "version": "10.1.8",
        "scope": "optional",
        "purl": "pkg:npm/eslint-config-prettier@10.1.8",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/eslint-config-prettier/-/eslint-config-prettier-10.1.8.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "f36199523452d29fe381a9dfeaad6b10edb9552a071f484a3c24eb8229653e3748ff76e0061004d50cc7ac74e2ce3a51bf2ea9180bca8c326d936a45f4d0eaf3"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "eslint-scope@9.1.2",
        "type": "library",
        "name": "eslint-scope",
        "version": "9.1.2",
        "scope": "optional",
        "purl": "pkg:npm/eslint-scope@9.1.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/eslint-scope/-/eslint-scope-9.1.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "c52f741f9d5c2b0d2396dc66be61f2d886a2d4b22aadf6f0e7b6fbf70fc9ecc7ef0df90890567e923eb30b7063b54c21d79d07b1249dc5765cb2ebfbda688315"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BSD-2-Clause"
            }
          }
        ]
      },
      {
        "bom-ref": "eslint-visitor-keys@5.0.1",
        "type": "library",
        "name": "eslint-visitor-keys",
        "version": "5.0.1",
        "scope": "optional",
        "purl": "pkg:npm/eslint-visitor-keys@5.0.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/eslint-visitor-keys/-/eslint-visitor-keys-5.0.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "b43e34787c40df98743c421935e223907a034786238c9a77e1b88cd260efa6505efff981f881c2a870c657ba7117eecc9254ef8a085c08f3d90bbca75b28dd4c"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "Apache-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "espree@11.2.0",
        "type": "library",
        "name": "espree",
        "version": "11.2.0",
        "scope": "optional",
        "purl": "pkg:npm/espree@11.2.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/espree/-/espree-11.2.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "ee9dc3ad5108a295b50756af0062ee0928758ee6dcd351f624773c078aaa19b966839808f72ba60600028d6a3826521cd38b9fba0e3127749023c1e44bf460cf"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BSD-2-Clause"
            }
          }
        ]
      },
      {
        "bom-ref": "esquery@1.7.0",
        "type": "library",
        "name": "esquery",
        "version": "1.7.0",
        "scope": "optional",
        "purl": "pkg:npm/esquery@1.7.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/esquery/-/esquery-1.7.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "029e86d16430714fcb1ecbcbc0e3757c0417f59a7403663a63f709065f6bfc96d6f74672838ff36c6eb3cca6b639300b10b6ab60798abb41a1a4ce443beed3d2"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BSD-3-Clause"
            }
          }
        ]
      },
      {
        "bom-ref": "esrecurse@4.3.0",
        "type": "library",
        "name": "esrecurse",
        "version": "4.3.0",
        "scope": "optional",
        "purl": "pkg:npm/esrecurse@4.3.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/esrecurse/-/esrecurse-4.3.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "2a67ca2f76fa1be457bcff0dd6faf74ead642ffa021609f63585c4b6a3fcfcbde929aa540381bc70555aa05dd2537db7083e17ca947f7df8a81e692d8bafd36a"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BSD-2-Clause"
            }
          }
        ]
      },
      {
        "bom-ref": "estraverse@5.3.0",
        "type": "library",
        "name": "estraverse",
        "version": "5.3.0",
        "scope": "optional",
        "purl": "pkg:npm/estraverse@5.3.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/estraverse/-/estraverse-5.3.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "30c74046e54443388d4de243f0380caa6870475d41450fdc04ffa92ed61d4939dfdcc20ef1f15e8883446d7dfa65d3657d4ffb03d7f7814c38f41de842cbf004"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BSD-2-Clause"
            }
          }
        ]
      },
      {
        "bom-ref": "estree-walker@3.0.3",
        "type": "library",
        "name": "estree-walker",
        "version": "3.0.3",
        "scope": "optional",
        "purl": "pkg:npm/estree-walker@3.0.3",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/estree-walker/-/estree-walker-3.0.3.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "ed150a7d781230c933b7a66e5e6a9aa4ebab2c63cf7e08fa97db9167b9511a896f934cb6ca871cdf92dd731282e4f419767d8332a8a8010d8da1672b4ca9a6ea"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "esutils@2.0.3",
        "type": "library",
        "name": "esutils",
        "version": "2.0.3",
        "scope": "optional",
        "purl": "pkg:npm/esutils@2.0.3",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/esutils/-/esutils-2.0.3.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "915b1ca97938382a7af126747648042958baffc8a3df4d0a0564c9ab7d8ffdd61e5934b02b8d56c93c5a94dd5e46603967d514fcb5fd0fb1564a657d480631ea"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BSD-2-Clause"
            }
          }
        ]
      },
      {
        "bom-ref": "expect-type@1.3.0",
        "type": "library",
        "name": "expect-type",
        "version": "1.3.0",
        "scope": "optional",
        "purl": "pkg:npm/expect-type@1.3.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/expect-type/-/expect-type-1.3.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "927bf279ab9886a8ce62f43ae8cce748cb3cdf0987ac2c9c34437a028fb601e6047f150892e895c5d11ad6a94610f2be59ede7d131e20dc8984ac09c816fc3a0"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "Apache-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "fast-deep-equal@3.1.3",
        "type": "library",
        "name": "fast-deep-equal",
        "version": "3.1.3",
        "scope": "optional",
        "purl": "pkg:npm/fast-deep-equal@3.1.3",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/fast-deep-equal/-/fast-deep-equal-3.1.3.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "7f7a90f68432f63d808417bf1fd542f75c0b98a042094fe00ce9ca340606e61b303bb04b2a3d3d1dce4760dcfd70623efb19690c22200da8ad56cd3701347ce1"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "fast-json-stable-stringify@2.1.0",
        "type": "library",
        "name": "fast-json-stable-stringify",
        "version": "2.1.0",
        "scope": "optional",
        "purl": "pkg:npm/fast-json-stable-stringify@2.1.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/fast-json-stable-stringify/-/fast-json-stable-stringify-2.1.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "96177fc05f8b93df076684c2b6556b687b5f8795d88a32236a55dc93bb1a52db9a9d20f22ccc671e149710326a1f10fb9ac47c0f4b829aa964c23095f31bf01f"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "fast-levenshtein@2.0.6",
        "type": "library",
        "name": "fast-levenshtein",
        "version": "2.0.6",
        "scope": "optional",
        "purl": "pkg:npm/fast-levenshtein@2.0.6",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/fast-levenshtein/-/fast-levenshtein-2.0.6.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "0c25eee887e1a9c92ced364a6371f1a77cbaaa9858e522599ab58c0eb29c11148e5d641d32153d220fcf62bcf2c3fba5f63388ca1d0de0cd2d6c2e61a1d83c77"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "fdir@6.5.0",
        "type": "library",
        "name": "fdir",
        "version": "6.5.0",
        "scope": "optional",
        "purl": "pkg:npm/fdir@6.5.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/fdir/-/fdir-6.5.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "b486d8b596ee70eb340511aa3c992c84951874bf920c7edd54cf208f2f84469dd60148cb105244fb4da46a7c87b708d63a7c2b298062c0098cd29e242c90275e"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "file-entry-cache@8.0.0",
        "type": "library",
        "name": "file-entry-cache",
        "version": "8.0.0",
        "scope": "optional",
        "purl": "pkg:npm/file-entry-cache@8.0.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/file-entry-cache/-/file-entry-cache-8.0.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "5d74d4c02be2b1ae6869c34644ff527cdb5804d00c8be44fc011666e564417b37bb301d8412ebf65f93b491c31e03e63dc21f6d7560d45ca350c430d55f6429d"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "find-up@5.0.0",
        "type": "library",
        "name": "find-up",
        "version": "5.0.0",
        "scope": "optional",
        "purl": "pkg:npm/find-up@5.0.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/find-up/-/find-up-5.0.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "efcfcf5d3d7094b2c3813cc3b3bb23abd873cf4bd70fece7fbbc32a447b87d74310a6766a9f1ac10f4319a2092408dda8c557dd5b552b2f36dac94625ba9c69e"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "flat-cache@4.0.1",
        "type": "library",
        "name": "flat-cache",
        "version": "4.0.1",
        "scope": "optional",
        "purl": "pkg:npm/flat-cache@4.0.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/flat-cache/-/flat-cache-4.0.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "7fb71c14f2b7497147a71d795081b2449fc525072db8a674cd5b8dddfac1a381e72b771acbd5445b447ac8f6051c2d0082a86e90fcca8eadb6b790e6032a86cb"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "flatted@3.4.2",
        "type": "library",
        "name": "flatted",
        "version": "3.4.2",
        "scope": "optional",
        "purl": "pkg:npm/flatted@3.4.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/flatted/-/flatted-3.4.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "3e30ec7bb47385c3e4209c32e6deca3d641267d7006f341771a7ec7ad4280fbb0e2514251a290d6f1ef2669d8ea2d0e7272ac371bc91ab74ed6f5f2260eaa4c4"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "ISC"
            }
          }
        ]
      },
      {
        "bom-ref": "fsevents@2.3.3",
        "type": "library",
        "name": "fsevents",
        "version": "2.3.3",
        "scope": "optional",
        "purl": "pkg:npm/fsevents@2.3.3",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/fsevents/-/fsevents-2.3.3.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "e71a037d7f9f2fb7da0139da82658fa5b16dc21fd1efb5a630caaa1c64bae42defbc1d181eb805f81d58999df8e35b4c8f99fade4d36d765cda09c339617df43"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "glob@13.0.6",
        "type": "library",
        "name": "glob",
        "version": "13.0.6",
        "scope": "optional",
        "purl": "pkg:npm/glob@13.0.6",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/glob/-/glob-13.0.6.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "5a3972ae89669bcb83a66fe8806c976576f567e09ad81f0d6c9c2a0558346b12bd19b05ea12ef2195eaf8d79d87469ba5f9de27a112ec716f288aa7c42637857"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BlueOak-1.0.0"
            }
          }
        ]
      },
      {
        "bom-ref": "glob-parent@6.0.2",
        "type": "library",
        "name": "glob-parent",
        "version": "6.0.2",
        "scope": "optional",
        "purl": "pkg:npm/glob-parent@6.0.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/glob-parent/-/glob-parent-6.0.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "5f1c08f043a1550816a7a8832feddbd2bf3a7f877a017eb3494e791df078c9d084b972d773915c61e3aefa79c67ed4b84c48eeff5d6bb782893d33206df9afe0"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "ISC"
            }
          }
        ]
      },
      {
        "bom-ref": "globals@17.5.0",
        "type": "library",
        "name": "globals",
        "version": "17.5.0",
        "scope": "optional",
        "purl": "pkg:npm/globals@17.5.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/globals/-/globals-17.5.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "aa857e1cadb2165ff7ebab76fc26f7fb1c4f528e41b8cca7a26a039a269904875bb3ed2961b8df654fadc0b8462a9e2e099ffe35bb6955ea47e5b182c51cb2da"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "has-flag@4.0.0",
        "type": "library",
        "name": "has-flag",
        "version": "4.0.0",
        "scope": "optional",
        "purl": "pkg:npm/has-flag@4.0.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/has-flag/-/has-flag-4.0.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "1329094ff4352a34d672da698080207d23b4b4a56e6548e180caf5ee4a93ba6325e807efdc421295e53ba99533a170c54c01d30c2e0d3a81bf67153712f94c3d"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "html-escaper@2.0.2",
        "type": "library",
        "name": "html-escaper",
        "version": "2.0.2",
        "scope": "optional",
        "purl": "pkg:npm/html-escaper@2.0.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/html-escaper/-/html-escaper-2.0.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "1f688cb5dd08e0cb7979889aa517480e3a7e5f37a55d0d2d144e094bb605c057af5d73263a9f66c8dad4bc28340fac2cf22aa444f05f28781bc228354a694b7e"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "ignore@5.3.2",
        "type": "library",
        "name": "ignore",
        "version": "5.3.2",
        "scope": "optional",
        "purl": "pkg:npm/ignore@5.3.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/ignore/-/ignore-5.3.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "86c053354a904c3c245ad71d608da2d3a63f9d4044b0d10324a8d676280bbde832f240ee2404bcb91969924710a721172f467fa630f2e4706632344227682afa"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "imurmurhash@0.1.4",
        "type": "library",
        "name": "imurmurhash",
        "version": "0.1.4",
        "scope": "optional",
        "purl": "pkg:npm/imurmurhash@0.1.4",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/imurmurhash/-/imurmurhash-0.1.4.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "2665cc67ac2ebc398b88712697dca4cea3ba97015ba1fd061b822470668435d0910c398c5679f2eece47b0880709b6aad30d8cc8f843aa48535204b62d4d8f1c"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "is-extglob@2.1.1",
        "type": "library",
        "name": "is-extglob",
        "version": "2.1.1",
        "scope": "optional",
        "purl": "pkg:npm/is-extglob@2.1.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/is-extglob/-/is-extglob-2.1.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "49b29b00d90deb4dd58b88c466fe3d2de549327e321b0b1bcd9c28ac4a32122badb0dde725875b3b7eb37e1189e90103a4e6481640ed9eae494719af9778eca1"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "is-glob@4.0.3",
        "type": "library",
        "name": "is-glob",
        "version": "4.0.3",
        "scope": "optional",
        "purl": "pkg:npm/is-glob@4.0.3",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/is-glob/-/is-glob-4.0.3.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "c5e9526b21c7dfa66013b6568658bba56df884d6cd97c3a3bf92959a4243e2105d0f7b61f137e4f6f61ab0b33e99758e6611648197f184b4a7af046be1e9524a"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "isexe@2.0.0",
        "type": "library",
        "name": "isexe",
        "version": "2.0.0",
        "scope": "optional",
        "purl": "pkg:npm/isexe@2.0.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/isexe/-/isexe-2.0.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "447c4c2e9f659ca1c61d19e0f5016144231b600715a67ebdb2648672addfdfac638155564e18f8aaa2db4cb96aed2b23f01f9f210d44b8210623694ab3241e23"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "ISC"
            }
          }
        ]
      },
      {
        "bom-ref": "istanbul-lib-coverage@3.2.2",
        "type": "library",
        "name": "istanbul-lib-coverage",
        "version": "3.2.2",
        "scope": "optional",
        "purl": "pkg:npm/istanbul-lib-coverage@3.2.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/istanbul-lib-coverage/-/istanbul-lib-coverage-3.2.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "3bc769b05fabd1657ff0c35129f9e6aed09686e2a3c6bab6c3e8e9cc12f95192938b62de5569d63a6591c4595eb0938d99cfb02c01af29064439a9e4a342c54e"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BSD-3-Clause"
            }
          }
        ]
      },
      {
        "bom-ref": "istanbul-lib-report@3.0.1",
        "type": "library",
        "name": "istanbul-lib-report",
        "version": "3.0.1",
        "scope": "optional",
        "purl": "pkg:npm/istanbul-lib-report@3.0.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/istanbul-lib-report/-/istanbul-lib-report-3.0.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "1827c4d66b6c1c63842c253c7bf67b616ce99b26ebc7ff9d4937cbaef63ca9199a63acd74ca5a7e964088da005c34ebd89c9ba19530d920bb437323888f65437"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BSD-3-Clause"
            }
          }
        ]
      },
      {
        "bom-ref": "istanbul-reports@3.2.0",
        "type": "library",
        "name": "istanbul-reports",
        "version": "3.2.0",
        "scope": "optional",
        "purl": "pkg:npm/istanbul-reports@3.2.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/istanbul-reports/-/istanbul-reports-3.2.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "1c6616592fde86a4d5df1375d22db7b643e4a47e3a30b08830534269a28d6af0174c5d5192ac5ac043ed9e39c667a5ca4889c12a488e03904a4be699898dc0bc"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BSD-3-Clause"
            }
          }
        ]
      },
      {
        "bom-ref": "js-tokens@10.0.0",
        "type": "library",
        "name": "js-tokens",
        "version": "10.0.0",
        "scope": "optional",
        "purl": "pkg:npm/js-tokens@10.0.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/js-tokens/-/js-tokens-10.0.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "94cfd40734267c9468f400576cf59e9a2bdd096f15d86f051da1ddca941a232e76dec9d48e88345bbd5ac965d38e247e8b178cc951cdd977299d377f9623e0fd"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "json-buffer@3.0.1",
        "type": "library",
        "name": "json-buffer",
        "version": "3.0.1",
        "scope": "optional",
        "purl": "pkg:npm/json-buffer@3.0.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/json-buffer/-/json-buffer-3.0.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "e1b57905f4769aa7d04c99be579b4f3dd7fe669ba1888bd3b8007983c91cad7399a534ff430c15456072c17d68cebea512e3dd6c7c70689966f46ea6236b1f49"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "json-schema-traverse@0.4.1",
        "type": "library",
        "name": "json-schema-traverse",
        "version": "0.4.1",
        "scope": "optional",
        "purl": "pkg:npm/json-schema-traverse@0.4.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/json-schema-traverse/-/json-schema-traverse-0.4.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "c5b6c21f9742614e53f0b704861ba1ec727cf075ee5b7aac237634cce64529f6441dca5688753f271ce4eb6f41aec69bfe63221d0b62f7030ffbce3944f7b756"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "json-stable-stringify-without-jsonify@1.0.1",
        "type": "library",
        "name": "json-stable-stringify-without-jsonify",
        "version": "1.0.1",
        "scope": "optional",
        "purl": "pkg:npm/json-stable-stringify-without-jsonify@1.0.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/json-stable-stringify-without-jsonify/-/json-stable-stringify-without-jsonify-1.0.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "05d6e8cbe97bb40dce196e858f21475a43f92ee0728f54e4df72e3caad1ac72cdd93dfff2528b6bb77cfd504a677528dc2ae9538a606940bbcec28ac562afa3f"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "keyv@4.5.4",
        "type": "library",
        "name": "keyv",
        "version": "4.5.4",
        "scope": "optional",
        "purl": "pkg:npm/keyv@4.5.4",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/keyv/-/keyv-4.5.4.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "a3154790747f1097f608d5e75b144b5ba9a0ec9c82094706d03b441a62f672d528d4f3538a7d4f52297eafffb8af93295600bf7e7d648ecc7b9a34ae8caa88a7"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "levn@0.4.1",
        "type": "library",
        "name": "levn",
        "version": "0.4.1",
        "scope": "optional",
        "purl": "pkg:npm/levn@0.4.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/levn/-/levn-0.4.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "f9b4f6b87e04e4b184ee1fe7ddebdc4bfb109495c2a48a7aca6f0e589e5e57afbaec3b2a97f2da693eea24102ddabcdfa1aff94011818710e2c7574cb7691029"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "lightningcss@1.32.0",
        "type": "library",
        "name": "lightningcss",
        "version": "1.32.0",
        "scope": "optional",
        "purl": "pkg:npm/lightningcss@1.32.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/lightningcss/-/lightningcss-1.32.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "357601ce29cdadb95fada3c6cab6cfa03d7d0b587d95f23fd66ce0598bd75137b8d781b3fd7d450f65c165264ceeb453acc03c24bdceb4068689fac82a1439c9"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MPL-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "lightningcss-android-arm64@1.32.0",
        "type": "library",
        "name": "lightningcss-android-arm64",
        "version": "1.32.0",
        "scope": "optional",
        "purl": "pkg:npm/lightningcss-android-arm64@1.32.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/lightningcss-android-arm64/-/lightningcss-android-arm64-1.32.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "60aeff0a54ede2400ad2fa3ac375fe3e79b40f671fdaf3c76e139776836d8b519ad1a9753f84c1661c23013be33702c40429cabe325cda34201d71f4344c2502"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MPL-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "lightningcss-darwin-arm64@1.32.0",
        "type": "library",
        "name": "lightningcss-darwin-arm64",
        "version": "1.32.0",
        "scope": "optional",
        "purl": "pkg:npm/lightningcss-darwin-arm64@1.32.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/lightningcss-darwin-arm64/-/lightningcss-darwin-arm64-1.32.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "473786f49bb96da83606fd7f97095526f044deae93b57b247592cb0b27e0e69b7e1cbcfd06a94808eecb64ced51cd4d39ffe4f4611c50528e4e65738726b1c3d"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MPL-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "lightningcss-darwin-x64@1.32.0",
        "type": "library",
        "name": "lightningcss-darwin-x64",
        "version": "1.32.0",
        "scope": "optional",
        "purl": "pkg:npm/lightningcss-darwin-x64@1.32.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/lightningcss-darwin-x64/-/lightningcss-darwin-x64-1.32.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "53e42c069da6fecdb0aa95184ffeb09e56a07596ed65d9dd4a6badfcd26a94270c2d35a9e66b82ac80fe2b9509ea3a83d8116c85e8c26179e23c36cd87bdd5f3"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MPL-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "lightningcss-freebsd-x64@1.32.0",
        "type": "library",
        "name": "lightningcss-freebsd-x64",
        "version": "1.32.0",
        "scope": "optional",
        "purl": "pkg:npm/lightningcss-freebsd-x64@1.32.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/lightningcss-freebsd-x64/-/lightningcss-freebsd-x64-1.32.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "2424e281e74492c664ded1d34ed86731d55f19feb5164cbc262d84e188d44c4417d78c62cbf953cd79eed6fc2265eddb61ed2af92a6c487fc24de0d72ba5878a"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MPL-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "lightningcss-linux-arm-gnueabihf@1.32.0",
        "type": "library",
        "name": "lightningcss-linux-arm-gnueabihf",
        "version": "1.32.0",
        "scope": "optional",
        "purl": "pkg:npm/lightningcss-linux-arm-gnueabihf@1.32.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/lightningcss-linux-arm-gnueabihf/-/lightningcss-linux-arm-gnueabihf-1.32.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "c7aae79e945ad862f4cd03a4b7aaedb376033f376e2e95afc0017a10c8571556570f8b4fac190416acc6a30cc2b085ac3e3a922beb723443835015de7951d293"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MPL-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "lightningcss-linux-arm64-gnu@1.32.0",
        "type": "library",
        "name": "lightningcss-linux-arm64-gnu",
        "version": "1.32.0",
        "scope": "optional",
        "purl": "pkg:npm/lightningcss-linux-arm64-gnu@1.32.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/lightningcss-linux-arm64-gnu/-/lightningcss-linux-arm64-gnu-1.32.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "d279ccca8c8e2d12577db30e8a569245c2c7dc9c39cfd1c33467d3fe0c023e06838e7c748bcc3bbc1cc52c54757fa08c2ca17c8156de6e69143777dafe4409a5"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MPL-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "lightningcss-linux-arm64-musl@1.32.0",
        "type": "library",
        "name": "lightningcss-linux-arm64-musl",
        "version": "1.32.0",
        "scope": "optional",
        "purl": "pkg:npm/lightningcss-linux-arm64-musl@1.32.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/lightningcss-linux-arm64-musl/-/lightningcss-linux-arm64-musl-1.32.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "529424a1e9ebe14244ce054862923cd250c5bd198f560ea8a9ba0d1dfa07e024087cd03e1cead9ecca3b2993f4d9d0ba2e38213d025e06cbd7849a1dff09c806"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MPL-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "lightningcss-linux-x64-gnu@1.32.0",
        "type": "library",
        "name": "lightningcss-linux-x64-gnu",
        "version": "1.32.0",
        "scope": "optional",
        "purl": "pkg:npm/lightningcss-linux-x64-gnu@1.32.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/lightningcss-linux-x64-gnu/-/lightningcss-linux-x64-gnu-1.32.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "57b42be7622166674a3d5afe56dc3ca3e58bb1025809377c96821fa4368c45619465f04e60425ec89224a862033193f03f1db8a5431fc12c7123ca61aff31b38"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MPL-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "lightningcss-linux-x64-musl@1.32.0",
        "type": "library",
        "name": "lightningcss-linux-x64-musl",
        "version": "1.32.0",
        "scope": "optional",
        "purl": "pkg:npm/lightningcss-linux-x64-musl@1.32.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/lightningcss-linux-x64-musl/-/lightningcss-linux-x64-musl-1.32.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "6d870ba7e55bd1ac2c89783ff34b8245ecc2607360d7f9779add20cc79d657d5cfd56e6c29ae7f4c274659a47fcc13363de17f1dbb10bff8f651134e895bb15a"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MPL-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "lightningcss-win32-arm64-msvc@1.32.0",
        "type": "library",
        "name": "lightningcss-win32-arm64-msvc",
        "version": "1.32.0",
        "scope": "optional",
        "purl": "pkg:npm/lightningcss-win32-arm64-msvc@1.32.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/lightningcss-win32-arm64-msvc/-/lightningcss-win32-arm64-msvc-1.32.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "f126c2f01478d294ba6da08cf2c6ed6034b011541de099454ce95a0f78161877d385370006734305d6ba79365ea9ba1f6a5209845c74a8ace01c999c3d39c677"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MPL-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "lightningcss-win32-x64-msvc@1.32.0",
        "type": "library",
        "name": "lightningcss-win32-x64-msvc",
        "version": "1.32.0",
        "scope": "optional",
        "purl": "pkg:npm/lightningcss-win32-x64-msvc@1.32.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/lightningcss-win32-x64-msvc/-/lightningcss-win32-x64-msvc-1.32.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "026abd07f4a86587438b5905ae88e7a2a3cbc58850e16a395e22fc11526b56c07c011a02d4f596e951ad4f458a09e9a3cbc682fa5a2e2678d2ed4d7cc776f4e9"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MPL-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "locate-path@6.0.0",
        "type": "library",
        "name": "locate-path",
        "version": "6.0.0",
        "scope": "optional",
        "purl": "pkg:npm/locate-path@6.0.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/locate-path/-/locate-path-6.0.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "88f64ae9e6236f146edee078fd667712c10830914ca80a28a65dd1fb3baad148dc026fcc3ba282c1e0e03df3f77a54f3b6828fdcab67547c539f63470520d553"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "lru-cache@11.3.5",
        "type": "library",
        "name": "lru-cache",
        "version": "11.3.5",
        "scope": "optional",
        "purl": "pkg:npm/lru-cache@11.3.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/lru-cache/-/lru-cache-11.3.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "371545c0b027addf62eca501c42e03ad4866823cceb3ed509b9d03de8175fe82feaf5369678800ef1bc6d3fcc9f1ebd1ef320a9f8bcb7fba9335db9616d0ab47"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BlueOak-1.0.0"
            }
          }
        ]
      },
      {
        "bom-ref": "magic-string@0.30.21",
        "type": "library",
        "name": "magic-string",
        "version": "0.30.21",
        "scope": "optional",
        "purl": "pkg:npm/magic-string@0.30.21",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/magic-string/-/magic-string-0.30.21.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "bddd85e1853211728670b1e8abe4c4c828f1b9e49e1e7171cb28cda7cd328345d5e2f5219c37abfe5bef96a33f6ab0796d740de4adbfde88a7c82472c7c4f609"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "magicast@0.5.2",
        "type": "library",
        "name": "magicast",
        "version": "0.5.2",
        "scope": "optional",
        "purl": "pkg:npm/magicast@0.5.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/magicast/-/magicast-0.5.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "1376498782774bd29fc1d8d985ed9a7e3e91f651883793e17abd69177f541ab5d1aaafd50da195206375dc18c7776bbc07ad6102b0063a7b28ee704ea2e5b74d"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "make-dir@4.0.0",
        "type": "library",
        "name": "make-dir",
        "version": "4.0.0",
        "scope": "optional",
        "purl": "pkg:npm/make-dir@4.0.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/make-dir/-/make-dir-4.0.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "8577544d960854eb75131fff8c0422fb04d9669529c018ffd10b0ecea7a06f7ac630c78989212ee712c79d87c1ad1578447dbe38248e3bde48b3fef1d562786f"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "minimatch@10.2.5",
        "type": "library",
        "name": "minimatch",
        "version": "10.2.5",
        "scope": "optional",
        "purl": "pkg:npm/minimatch@10.2.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/minimatch/-/minimatch-10.2.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "3142e454b7ca1980c561e8cfd3b40ebab0cb2d0a5c8e4ec5c3eee35d2d91d9ccd1433479eb21d1bde5393432443af887fa111364a4a4224e5cf93db71a315432"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BlueOak-1.0.0"
            }
          }
        ]
      },
      {
        "bom-ref": "minipass@7.1.3",
        "type": "library",
        "name": "minipass",
        "version": "7.1.3",
        "scope": "optional",
        "purl": "pkg:npm/minipass@7.1.3",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/minipass/-/minipass-7.1.3.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "b44047a839c8a0cff5ad7304d738246bd83a43695ca029311cbb9cece0c9e41c5b3f977873667970682d5c092ee7ddb4d02c7b762b874ce61f4810fc9fa9a6f0"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BlueOak-1.0.0"
            }
          }
        ]
      },
      {
        "bom-ref": "ms@2.1.3",
        "type": "library",
        "name": "ms",
        "version": "2.1.3",
        "scope": "optional",
        "purl": "pkg:npm/ms@2.1.3",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/ms/-/ms-2.1.3.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "e85973b9b4cb646dc9d9afcd542025784863ceae68c601f268253dc985ef70bb2fa1568726afece715c8ebf5d73fab73ed1f7100eb479d23bfb57b45dd645394"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "nanoid@3.3.11",
        "type": "library",
        "name": "nanoid",
        "version": "3.3.11",
        "scope": "optional",
        "purl": "pkg:npm/nanoid@3.3.11",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/nanoid/-/nanoid-3.3.11.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "37c4a97cf527529d5b2be3cc616f2a496765f54fb0c0d588e102b13980f2f4902ba3758c5fba7639e55117dbfedf8ee99da90d7af3e688784d999d876c503beb"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "natural-compare@1.4.0",
        "type": "library",
        "name": "natural-compare",
        "version": "1.4.0",
        "scope": "optional",
        "purl": "pkg:npm/natural-compare@1.4.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/natural-compare/-/natural-compare-1.4.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "396343f1e8b756d342f61ed5eb4a9f7f7495a1b1ebf7de824f0831b9b832418129836f7487d2746eec8408d3497b19059b9b0e6a38791b5d7a45803573c64c4b"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "obug@2.1.1",
        "type": "library",
        "name": "obug",
        "version": "2.1.1",
        "scope": "optional",
        "purl": "pkg:npm/obug@2.1.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/obug/-/obug-2.1.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "b93a85f4cb8fada010f88b273dfdfae911b870ff51b548bb30b3b537728473ec1bd1aeb22a978bd259a4d880758d8e4a1cf0254dce93fc945d0bf62ac4737091"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "optionator@0.9.4",
        "type": "library",
        "name": "optionator",
        "version": "0.9.4",
        "scope": "optional",
        "purl": "pkg:npm/optionator@0.9.4",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/optionator/-/optionator-0.9.4.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "e88a50ee6294c5171934b20e6d1d21cfb971b1aa5248860d649c173c6785d264d5a862852178f50d070ca13db64b744e70bc98febcf43d669667d6b25a669df6"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "p-limit@3.1.0",
        "type": "library",
        "name": "p-limit",
        "version": "3.1.0",
        "scope": "optional",
        "purl": "pkg:npm/p-limit@3.1.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/p-limit/-/p-limit-3.1.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "4d839a9ccdf01b0346b193767154d83c0af0e39e319d78f9aa6585d5b12801ce3e714fe897b19587ba1d7af8e9d4534776e1dcdca64c70576ec54e5773ab8945"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "p-locate@5.0.0",
        "type": "library",
        "name": "p-locate",
        "version": "5.0.0",
        "scope": "optional",
        "purl": "pkg:npm/p-locate@5.0.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/p-locate/-/p-locate-5.0.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "2da363b51594058fbecc1e6713f37071aa0cca548f93e4be647341d53cdd6cc24c9f2e9dca7a401aded7fed97f418ab74c8784ea7c47a696e8d8b1b29ab1b93f"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "package-json-from-dist@1.0.1",
        "type": "library",
        "name": "package-json-from-dist",
        "version": "1.0.1",
        "scope": "optional",
        "purl": "pkg:npm/package-json-from-dist@1.0.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/package-json-from-dist/-/package-json-from-dist-1.0.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "5046484b7fdbcb8382f2f2f73f67535d1113a5e6cb236362239bc8ae3683ff952dae4157fed35bc234d2440182ffeec2028da921c05a4605a670104772c68223"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BlueOak-1.0.0"
            }
          }
        ]
      },
      {
        "bom-ref": "path-exists@4.0.0",
        "type": "library",
        "name": "path-exists",
        "version": "4.0.0",
        "scope": "optional",
        "purl": "pkg:npm/path-exists@4.0.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/path-exists/-/path-exists-4.0.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "6a4f50cb943b8d86f65b071ecb9169be0d8aa0073f64884b48b392066466ca03ec1b091556dd1f65ad2aaed333fa6ead2530077d943c167981e0c1b82d6cbbff"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "path-key@3.1.1",
        "type": "library",
        "name": "path-key",
        "version": "3.1.1",
        "scope": "optional",
        "purl": "pkg:npm/path-key@3.1.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/path-key/-/path-key-3.1.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "a2399e374a9dfb2d23b3312da18e3caf43deab97703049089423aee90e5fe3595f92cc17b8ab58ae18284e92e7c887079b6e1486ac7ee53aa6d889d2c0b844e9"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "path-scurry@2.0.2",
        "type": "library",
        "name": "path-scurry",
        "version": "2.0.2",
        "scope": "optional",
        "purl": "pkg:npm/path-scurry@2.0.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/path-scurry/-/path-scurry-2.0.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "dcefe2555b0900fb0e9e9c1621e0fe77acffecf9aa029c9078f52d0a77636ad8fff48e4bca51efb79aa5b856814f7239877af57a37d1d39e9cf850aff8d9cd5e"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BlueOak-1.0.0"
            }
          }
        ]
      },
      {
        "bom-ref": "pathe@2.0.3",
        "type": "library",
        "name": "pathe",
        "version": "2.0.3",
        "scope": "optional",
        "purl": "pkg:npm/pathe@2.0.3",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/pathe/-/pathe-2.0.3.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "5948c6700a8fd6041a72841ef8e049b0503b2dde03c97b9422367971cef970b1ef27b76d36c4ee8298712000f0b294be02b68051e3c22ab495b4f2c58ff17cf3"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "picocolors@1.1.1",
        "type": "library",
        "name": "picocolors",
        "version": "1.1.1",
        "scope": "optional",
        "purl": "pkg:npm/picocolors@1.1.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/picocolors/-/picocolors-1.1.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "c5c787dac9e1b5be4cf658aa0ec984c39ea57b7efa993664117fe311bfd1c4d1727a036e97b78db250973fd1438ff2dcbb45fc284c8c71e3f69eda5a1eb0c454"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "ISC"
            }
          }
        ]
      },
      {
        "bom-ref": "picomatch@4.0.4",
        "type": "library",
        "name": "picomatch",
        "version": "4.0.4",
        "scope": "optional",
        "purl": "pkg:npm/picomatch@4.0.4",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/picomatch/-/picomatch-4.0.4.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "40ff3c0402af31a9bfdcdc47eaf8f6a36d51e8c8f165401dea7970012fe99c6bcdf4854ba1c2c7c46608cc5860e9f510fb9b61e8fe1dbf8796f635f70d2223ec"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "postcss@8.5.10",
        "type": "library",
        "name": "postcss",
        "version": "8.5.10",
        "scope": "optional",
        "purl": "pkg:npm/postcss@8.5.10",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/postcss/-/postcss-8.5.10.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "a4c307c4139928553a1e0019e1ec869f05c5fc4bcf186a94af4327679fbdf78f39c305b8d645bdd40e0b386c521e182e8199920a12f902512535dc253607272d"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "prelude-ls@1.2.1",
        "type": "library",
        "name": "prelude-ls",
        "version": "1.2.1",
        "scope": "optional",
        "purl": "pkg:npm/prelude-ls@1.2.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/prelude-ls/-/prelude-ls-1.2.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "be47033eb459a354192db9f944b18fa60fd698843ae6aa165a170629ffdbe5ea659246ab5f49bdcfca6909ab789a53aa52c5a9c8db9880edd5472ad81d2cd7e6"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "prettier@3.8.3",
        "type": "library",
        "name": "prettier",
        "version": "3.8.3",
        "scope": "optional",
        "purl": "pkg:npm/prettier@3.8.3",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/prettier/-/prettier-3.8.3.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "ee280f4cce777061cc5bcc56b954f2762d8a3b6df754589337217984b26aa629477e69fc0bc80f7fe3d2edd513eb861c5c56e23066714bda424b12ff0f19bf27"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "punycode@2.3.1",
        "type": "library",
        "name": "punycode",
        "version": "2.3.1",
        "scope": "optional",
        "purl": "pkg:npm/punycode@2.3.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/punycode/-/punycode-2.3.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "bd8b7b503d54f5683ad77f2c84bb4b3af740bbef03b02fe2945b44547707fb0c9d712a4d136d007d239db9fe8c91115a84be4563b5f5a14ee7295645b5fabc16"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "rimraf@6.1.3",
        "type": "library",
        "name": "rimraf",
        "version": "6.1.3",
        "scope": "optional",
        "purl": "pkg:npm/rimraf@6.1.3",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/rimraf/-/rimraf-6.1.3.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "2ca83e0abd9917ad5f91c68ad547641f6c840412a76234f25b34c94fa28d3dc48f6a24fb1d2761b4c5d0b8de709135f45eeef6290d65f12e36ae599ec52da148"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BlueOak-1.0.0"
            }
          }
        ]
      },
      {
        "bom-ref": "rolldown@1.0.0-rc.16",
        "type": "library",
        "name": "rolldown",
        "version": "1.0.0-rc.16",
        "scope": "optional",
        "purl": "pkg:npm/rolldown@1.0.0-rc.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/rolldown/-/rolldown-1.0.0-rc.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "af38b95aa2b3119c374a8a13b7b7209b87aa228ba33c8c8670934614bee23c4b9a8d0c3bbf13075245f296ee3fbe10a42465ec8119b1a8c2975294fa144825d2"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "semver@7.7.4",
        "type": "library",
        "name": "semver",
        "version": "7.7.4",
        "scope": "optional",
        "purl": "pkg:npm/semver@7.7.4",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/semver/-/semver-7.7.4.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "bc5282d8812d427561a53efc875629f30cf0adff0233e33328c1c62597c1b738593727111675ec1e4e84e53c4892432c80d4bb99d5f700607bc7640cd9d8b894"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "ISC"
            }
          }
        ]
      },
      {
        "bom-ref": "shebang-command@2.0.0",
        "type": "library",
        "name": "shebang-command",
        "version": "2.0.0",
        "scope": "optional",
        "purl": "pkg:npm/shebang-command@2.0.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/shebang-command/-/shebang-command-2.0.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "907c6bdb366962d766acdd6a0e3aeb5ff675ad1d641bc0f1fa09292b51b87979af5ecc26704d614d6056614ce5ada630d7fc99a7a62e0d8efb62dbdb3747660c"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "shebang-regex@3.0.0",
        "type": "library",
        "name": "shebang-regex",
        "version": "3.0.0",
        "scope": "optional",
        "purl": "pkg:npm/shebang-regex@3.0.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/shebang-regex/-/shebang-regex-3.0.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "efef9d161b5cc77df9dee05aabc0c347836ec417ad0730bb6503a19934089c711de9b4ab5dd884cb30af1b4ed9e3851874b4a1594c97b7933fca1cfc7a471bd4"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "siginfo@2.0.0",
        "type": "library",
        "name": "siginfo",
        "version": "2.0.0",
        "scope": "optional",
        "purl": "pkg:npm/siginfo@2.0.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/siginfo/-/siginfo-2.0.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "c9bc7458ed7ff1b4812c459766f11dee0316dd29f7245956dd3bd7d674446c32d135035a78d37c58ad26781c0f74068e23b4ed4514499ff12cd7386bac21eeee"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "ISC"
            }
          }
        ]
      },
      {
        "bom-ref": "source-map-js@1.2.1",
        "type": "library",
        "name": "source-map-js",
        "version": "1.2.1",
        "scope": "optional",
        "purl": "pkg:npm/source-map-js@1.2.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/source-map-js/-/source-map-js-1.2.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "51758c2a12cec1529bef6f0852d40f5f17d853ebac7726ed52b2bff2e184f0240cbeb84ea70bf30c1c23d108522fb31073bbc8b084811bc550f3e203431a5f40"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BSD-3-Clause"
            }
          }
        ]
      },
      {
        "bom-ref": "stackback@0.0.2",
        "type": "library",
        "name": "stackback",
        "version": "0.0.2",
        "scope": "optional",
        "purl": "pkg:npm/stackback@0.0.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/stackback/-/stackback-0.0.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "d573091397d0a358c61fa63fede6e7c0f3811242049d3e10177d9de51d7e557757bde334201309b7ccdf6b15f53f7421570ad87bee7bebe8e400db524b69816f"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "std-env@4.1.0",
        "type": "library",
        "name": "std-env",
        "version": "4.1.0",
        "scope": "optional",
        "purl": "pkg:npm/std-env@4.1.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/std-env/-/std-env-4.1.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "46aef26dc5f646e0b9e6bf6868f5445bbff1bb7b63f2ee067816070560b272116dccc22bf3a03b7b73cf1013d3dfbb074ad297dfe4e25ff16bfc00a624b5652d"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "supports-color@7.2.0",
        "type": "library",
        "name": "supports-color",
        "version": "7.2.0",
        "scope": "optional",
        "purl": "pkg:npm/supports-color@7.2.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/supports-color/-/supports-color-7.2.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "aa9080bd197db2db8e1ef78ab27ec79dc251befe74d6a21a70acd094effe2f0c5cf7ed2adb02f2bf80dfbedf34fc33e7da9a8e06c25d0e2a205c647df8ebf047"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "tinybench@2.9.0",
        "type": "library",
        "name": "tinybench",
        "version": "2.9.0",
        "scope": "optional",
        "purl": "pkg:npm/tinybench@2.9.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/tinybench/-/tinybench-2.9.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "d3e0d4bea58c55a94b9a16ba96be240fc88030ad47cd5d3f68a9c2b566fdbfdeb8d539cffcc15becf7366f1a314234d7004aebc9756050e7efd98a8d965a867a"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "tinyexec@1.1.1",
        "type": "library",
        "name": "tinyexec",
        "version": "1.1.1",
        "scope": "optional",
        "purl": "pkg:npm/tinyexec@1.1.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/tinyexec/-/tinyexec-1.1.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "54a4bf65a42186428530036600e8615d5a087c15db950c465f59b2090d9f690adf9a86cc7ed5de24f7191a9d204b4ee872f1895832d91b23990da74306a60826"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "tinyglobby@0.2.16",
        "type": "library",
        "name": "tinyglobby",
        "version": "0.2.16",
        "scope": "optional",
        "purl": "pkg:npm/tinyglobby@0.2.16",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/tinyglobby/-/tinyglobby-0.2.16.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "a67f7d561a0009847c9c51e1c6a8b1faebec6d78a77806ac5a6e688d7a0df311302b929ddff4eb84d9f5c01cae0f9d94c5644bcbca6efa444c9e2122e84abd66"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "tinyrainbow@3.1.0",
        "type": "library",
        "name": "tinyrainbow",
        "version": "3.1.0",
        "scope": "optional",
        "purl": "pkg:npm/tinyrainbow@3.1.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/tinyrainbow/-/tinyrainbow-3.1.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "05ff882e6060adeb54add271cd73344a05cb6775df89a52e3a3fc82901ee4d78a9fb4e579febb21187558349180e2a5305c2eb095c94cc03f3ed0980adbd269b"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "ts-api-utils@2.5.0",
        "type": "library",
        "name": "ts-api-utils",
        "version": "2.5.0",
        "scope": "optional",
        "purl": "pkg:npm/ts-api-utils@2.5.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/ts-api-utils/-/ts-api-utils-2.5.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "389fe26f184f96aacc33452234727fd02290928285db8dff0049a996ddeaa518245bc546ec87ce4b8d61ed5f138c84ea741c87ceb8dc4bfdac8becb894887c34"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "tslib@2.8.1",
        "type": "library",
        "name": "tslib",
        "version": "2.8.1",
        "scope": "required",
        "purl": "pkg:npm/tslib@2.8.1",
        "properties": [],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/tslib/-/tslib-2.8.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "a0916ef781d06fe29576e49440bef09e99aa9df98bb0e03f9c087a6fa107d30084a0ad3f98f79753a737c0a0d5f373243ae1cf447b525ca294f7d2016b34bfdb"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "0BSD"
            }
          }
        ]
      },
      {
        "bom-ref": "type-check@0.4.0",
        "type": "library",
        "name": "type-check",
        "version": "0.4.0",
        "scope": "optional",
        "purl": "pkg:npm/type-check@0.4.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/type-check/-/type-check-0.4.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "5e5794a1cf6ec065ea8d6c176944d9026ccc705679f39f10036befc7552be7121c8b15c83fef0b9c50e0469954df4bacead7aa765b2415fbbe69ee0aefd3a87b"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "typescript@6.0.3",
        "type": "library",
        "name": "typescript",
        "version": "6.0.3",
        "scope": "optional",
        "purl": "pkg:npm/typescript@6.0.3",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/typescript/-/typescript-6.0.3.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "cb64efbb14993c3c906a490544f6472859be28a56a222b1d83dfc26709bd7edbca5cb3fc3515a3dfcfce0e335baf8dd2b285ea36e022b047f519d0b1a9671d07"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "Apache-2.0"
            }
          }
        ]
      },
      {
        "bom-ref": "typescript-eslint@8.59.0",
        "type": "library",
        "name": "typescript-eslint",
        "version": "8.59.0",
        "scope": "optional",
        "purl": "pkg:npm/typescript-eslint@8.59.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/typescript-eslint/-/typescript-eslint-8.59.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "054dce356f57faff7411c087f594ba2cc69c91c56dc512e52375eb632a992305521c893b41fedb170d73d0cf50d0853185331909ff29898f614d868d108012af"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "undici-types@7.16.0",
        "type": "library",
        "name": "undici-types",
        "version": "7.16.0",
        "scope": "optional",
        "purl": "pkg:npm/undici-types@7.16.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/undici-types/-/undici-types-7.16.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "673f9a6564a3f0b13ace8c43fb1ae387855f9081bc61ae8bbd8919aad5101893d98d8979df2a42694c16aa8ede234c7ae8a046791a3e9a504490c49e499dfc37"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "uri-js@4.4.1",
        "type": "library",
        "name": "uri-js",
        "version": "4.4.1",
        "scope": "optional",
        "purl": "pkg:npm/uri-js@4.4.1",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/uri-js/-/uri-js-4.4.1.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "eeb294cb2df7435c9cf7ca50d430262edc17d74f45ed321f5a55b561da3c5a5d628b549e1e279e8741c77cf78bd9f3172bacf4b3c79c2acf5fac2b8b26f9dd06"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "BSD-2-Clause"
            }
          }
        ]
      },
      {
        "bom-ref": "vite@8.0.9",
        "type": "library",
        "name": "vite",
        "version": "8.0.9",
        "scope": "optional",
        "purl": "pkg:npm/vite@8.0.9",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/vite/-/vite-8.0.9.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "b7b83b1954693178cda5aebb1da55623ff015ad7552103c22f65a8a335d703b2c118414ae009242a41f1da10107f9c751994a57261033e454b4866a1c6710467"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "vitest@4.1.5",
        "type": "library",
        "name": "vitest",
        "version": "4.1.5",
        "scope": "optional",
        "purl": "pkg:npm/vitest@4.1.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/vitest/-/vitest-4.1.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "f57c75bf7fe28779bd84df926df914cb2d0902cef66a9debee3a1cf3b5cbea3c05d231a0ea6141bd0d52af0697fa1f019645fa1f3f6c85d775ba8e8017e646a6"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "which@2.0.2",
        "type": "library",
        "name": "which",
        "version": "2.0.2",
        "scope": "optional",
        "purl": "pkg:npm/which@2.0.2",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/which/-/which-2.0.2.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "04b2374e5d535b73ef97bd25df2ab763ae22f9ac29c17aac181616924a8cb676d782b303fb28fbae15b492e103c7325a6171a3116e6881aa4a34c10a34c8e26c"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "ISC"
            }
          }
        ]
      },
      {
        "bom-ref": "why-is-node-running@2.3.0",
        "type": "library",
        "name": "why-is-node-running",
        "version": "2.3.0",
        "scope": "optional",
        "purl": "pkg:npm/why-is-node-running@2.3.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/why-is-node-running/-/why-is-node-running-2.3.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "854ae669605d543731bd8aa7ca1d3dcee9cacd13968db65388dcbc741123912ede8440d089b5c9ed7be59ad6f0b9372552223237e0b25d00f8566928f1f366f3"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "word-wrap@1.2.5",
        "type": "library",
        "name": "word-wrap",
        "version": "1.2.5",
        "scope": "optional",
        "purl": "pkg:npm/word-wrap@1.2.5",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/word-wrap/-/word-wrap-1.2.5.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "04ddb607979a30c23d50cb63ac677983978260fa423c3532d052576d8b1a4f9cd8c6314e7244b9dd2403137a56915a16a475d56f706b61c10de13c1ae7907970"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      },
      {
        "bom-ref": "yocto-queue@0.1.0",
        "type": "library",
        "name": "yocto-queue",
        "version": "0.1.0",
        "scope": "optional",
        "purl": "pkg:npm/yocto-queue@0.1.0",
        "properties": [
          {
            "name": "cdx:npm:package:development",
            "value": "true"
          }
        ],
        "externalReferences": [
          {
            "type": "distribution",
            "url": "https://registry.npmjs.org/yocto-queue/-/yocto-queue-0.1.0.tgz"
          }
        ],
        "hashes": [
          {
            "alg": "SHA-512",
            "content": "ad592cbec9cd09d27fa2119ceb180fc3237c7a1782c6c88b33c9b1b84fedfe6395a897b03ee3b59a22e94c74224604ca08b7b12f831e00555a82db3b1e6359d9"
          }
        ],
        "licenses": [
          {
            "license": {
              "id": "MIT"
            }
          }
        ]
      }
    ],
    "dependencies": [
      {
        "ref": "node-typescript-boilerplate@0.0.0",
        "dependsOn": [
          "tslib@2.8.1",
          "@eslint/js@10.0.1",
          "@types/node@24.12.2",
          "@typescript-eslint/parser@8.59.0",
          "@vitest/coverage-v8@4.1.5",
          "@vitest/eslint-plugin@1.6.16",
          "eslint@10.2.1",
          "eslint-config-prettier@10.1.8",
          "globals@17.5.0",
          "prettier@3.8.3",
          "rimraf@6.1.3",
          "ts-api-utils@2.5.0",
          "typescript@6.0.3",
          "typescript-eslint@8.59.0",
          "vitest@4.1.5"
        ]
      },
      {
        "ref": "@babel/helper-string-parser@7.27.1",
        "dependsOn": []
      },
      {
        "ref": "@babel/helper-validator-identifier@7.28.5",
        "dependsOn": []
      },
      {
        "ref": "@babel/parser@7.29.2",
        "dependsOn": [
          "@babel/types@7.29.0"
        ]
      },
      {
        "ref": "@babel/types@7.29.0",
        "dependsOn": [
          "@babel/helper-string-parser@7.27.1",
          "@babel/helper-validator-identifier@7.28.5"
        ]
      },
      {
        "ref": "@bcoe/v8-coverage@1.0.2",
        "dependsOn": []
      },
      {
        "ref": "@emnapi/core@1.9.2",
        "dependsOn": [
          "@emnapi/wasi-threads@1.2.1",
          "tslib@2.8.1"
        ]
      },
      {
        "ref": "@emnapi/runtime@1.9.2",
        "dependsOn": [
          "tslib@2.8.1"
        ]
      },
      {
        "ref": "@emnapi/wasi-threads@1.2.1",
        "dependsOn": [
          "tslib@2.8.1"
        ]
      },
      {
        "ref": "@eslint-community/eslint-utils@4.9.1",
        "dependsOn": [
          "eslint@10.2.1",
          "eslint-visitor-keys@3.4.3"
        ]
      },
      {
        "ref": "eslint-visitor-keys@3.4.3",
        "dependsOn": []
      },
      {
        "ref": "@eslint-community/regexpp@4.12.2",
        "dependsOn": []
      },
      {
        "ref": "@eslint/config-array@0.23.5",
        "dependsOn": [
          "@eslint/object-schema@3.0.5",
          "debug@4.4.3",
          "minimatch@10.2.5"
        ]
      },
      {
        "ref": "@eslint/config-helpers@0.5.5",
        "dependsOn": [
          "@eslint/core@1.2.1"
        ]
      },
      {
        "ref": "@eslint/core@1.2.1",
        "dependsOn": [
          "@types/json-schema@7.0.15"
        ]
      },
      {
        "ref": "@eslint/js@10.0.1",
        "dependsOn": [
          "eslint@10.2.1"
        ]
      },
      {
        "ref": "@eslint/object-schema@3.0.5",
        "dependsOn": []
      },
      {
        "ref": "@eslint/plugin-kit@0.7.1",
        "dependsOn": [
          "@eslint/core@1.2.1",
          "levn@0.4.1"
        ]
      },
      {
        "ref": "@humanfs/core@0.19.2",
        "dependsOn": [
          "@humanfs/types@0.15.0"
        ]
      },
      {
        "ref": "@humanfs/node@0.16.8",
        "dependsOn": [
          "@humanfs/core@0.19.2",
          "@humanfs/types@0.15.0",
          "@humanwhocodes/retry@0.4.3"
        ]
      },
      {
        "ref": "@humanfs/types@0.15.0",
        "dependsOn": []
      },
      {
        "ref": "@humanwhocodes/module-importer@1.0.1",
        "dependsOn": []
      },
      {
        "ref": "@humanwhocodes/retry@0.4.3",
        "dependsOn": []
      },
      {
        "ref": "@jridgewell/resolve-uri@3.1.2",
        "dependsOn": []
      },
      {
        "ref": "@jridgewell/sourcemap-codec@1.5.5",
        "dependsOn": []
      },
      {
        "ref": "@jridgewell/trace-mapping@0.3.31",
        "dependsOn": [
          "@jridgewell/resolve-uri@3.1.2",
          "@jridgewell/sourcemap-codec@1.5.5"
        ]
      },
      {
        "ref": "@napi-rs/wasm-runtime@1.1.4",
        "dependsOn": [
          "@emnapi/core@1.9.2",
          "@emnapi/runtime@1.9.2",
          "@tybys/wasm-util@0.10.1"
        ]
      },
      {
        "ref": "@oxc-project/types@0.126.0",
        "dependsOn": []
      },
      {
        "ref": "@rolldown/binding-android-arm64@1.0.0-rc.16",
        "dependsOn": []
      },
      {
        "ref": "@rolldown/binding-darwin-arm64@1.0.0-rc.16",
        "dependsOn": []
      },
      {
        "ref": "@rolldown/binding-darwin-x64@1.0.0-rc.16",
        "dependsOn": []
      },
      {
        "ref": "@rolldown/binding-freebsd-x64@1.0.0-rc.16",
        "dependsOn": []
      },
      {
        "ref": "@rolldown/binding-linux-arm-gnueabihf@1.0.0-rc.16",
        "dependsOn": []
      },
      {
        "ref": "@rolldown/binding-linux-arm64-gnu@1.0.0-rc.16",
        "dependsOn": []
      },
      {
        "ref": "@rolldown/binding-linux-arm64-musl@1.0.0-rc.16",
        "dependsOn": []
      },
      {
        "ref": "@rolldown/binding-linux-ppc64-gnu@1.0.0-rc.16",
        "dependsOn": []
      },
      {
        "ref": "@rolldown/binding-linux-s390x-gnu@1.0.0-rc.16",
        "dependsOn": []
      },
      {
        "ref": "@rolldown/binding-linux-x64-gnu@1.0.0-rc.16",
        "dependsOn": []
      },
      {
        "ref": "@rolldown/binding-linux-x64-musl@1.0.0-rc.16",
        "dependsOn": []
      },
      {
        "ref": "@rolldown/binding-openharmony-arm64@1.0.0-rc.16",
        "dependsOn": []
      },
      {
        "ref": "@rolldown/binding-wasm32-wasi@1.0.0-rc.16",
        "dependsOn": [
          "@emnapi/core@1.9.2",
          "@emnapi/runtime@1.9.2",
          "@napi-rs/wasm-runtime@1.1.4"
        ]
      },
      {
        "ref": "@rolldown/binding-win32-arm64-msvc@1.0.0-rc.16",
        "dependsOn": []
      },
      {
        "ref": "@rolldown/binding-win32-x64-msvc@1.0.0-rc.16",
        "dependsOn": []
      },
      {
        "ref": "@rolldown/pluginutils@1.0.0-rc.16",
        "dependsOn": []
      },
      {
        "ref": "@standard-schema/spec@1.1.0",
        "dependsOn": []
      },
      {
        "ref": "@tybys/wasm-util@0.10.1",
        "dependsOn": [
          "tslib@2.8.1"
        ]
      },
      {
        "ref": "@types/chai@5.2.3",
        "dependsOn": [
          "@types/deep-eql@4.0.2",
          "assertion-error@2.0.1"
        ]
      },
      {
        "ref": "@types/deep-eql@4.0.2",
        "dependsOn": []
      },
      {
        "ref": "@types/esrecurse@4.3.1",
        "dependsOn": []
      },
      {
        "ref": "@types/estree@1.0.8",
        "dependsOn": []
      },
      {
        "ref": "@types/json-schema@7.0.15",
        "dependsOn": []
      },
      {
        "ref": "@types/node@24.12.2",
        "dependsOn": [
          "undici-types@7.16.0"
        ]
      },
      {
        "ref": "@typescript-eslint/eslint-plugin@8.59.0",
        "dependsOn": [
          "@typescript-eslint/parser@8.59.0",
          "eslint@10.2.1",
          "typescript@6.0.3",
          "@eslint-community/regexpp@4.12.2",
          "@typescript-eslint/scope-manager@8.59.0",
          "@typescript-eslint/type-utils@8.59.0",
          "@typescript-eslint/utils@8.59.0",
          "@typescript-eslint/visitor-keys@8.59.0",
          "ignore@7.0.5",
          "natural-compare@1.4.0",
          "ts-api-utils@2.5.0"
        ]
      },
      {
        "ref": "ignore@7.0.5",
        "dependsOn": []
      },
      {
        "ref": "@typescript-eslint/parser@8.59.0",
        "dependsOn": [
          "eslint@10.2.1",
          "typescript@6.0.3",
          "@typescript-eslint/scope-manager@8.59.0",
          "@typescript-eslint/types@8.59.0",
          "@typescript-eslint/typescript-estree@8.59.0",
          "@typescript-eslint/visitor-keys@8.59.0",
          "debug@4.4.3"
        ]
      },
      {
        "ref": "@typescript-eslint/project-service@8.59.0",
        "dependsOn": [
          "typescript@6.0.3",
          "@typescript-eslint/tsconfig-utils@8.59.0",
          "@typescript-eslint/types@8.59.0",
          "debug@4.4.3"
        ]
      },
      {
        "ref": "@typescript-eslint/scope-manager@8.59.0",
        "dependsOn": [
          "@typescript-eslint/types@8.59.0",
          "@typescript-eslint/visitor-keys@8.59.0"
        ]
      },
      {
        "ref": "@typescript-eslint/tsconfig-utils@8.59.0",
        "dependsOn": [
          "typescript@6.0.3"
        ]
      },
      {
        "ref": "@typescript-eslint/type-utils@8.59.0",
        "dependsOn": [
          "eslint@10.2.1",
          "typescript@6.0.3",
          "@typescript-eslint/types@8.59.0",
          "@typescript-eslint/typescript-estree@8.59.0",
          "@typescript-eslint/utils@8.59.0",
          "debug@4.4.3",
          "ts-api-utils@2.5.0"
        ]
      },
      {
        "ref": "@typescript-eslint/types@8.59.0",
        "dependsOn": []
      },
      {
        "ref": "@typescript-eslint/typescript-estree@8.59.0",
        "dependsOn": [
          "typescript@6.0.3",
          "@typescript-eslint/project-service@8.59.0",
          "@typescript-eslint/tsconfig-utils@8.59.0",
          "@typescript-eslint/types@8.59.0",
          "@typescript-eslint/visitor-keys@8.59.0",
          "debug@4.4.3",
          "minimatch@10.2.5",
          "semver@7.7.4",
          "tinyglobby@0.2.16",
          "ts-api-utils@2.5.0"
        ]
      },
      {
        "ref": "@typescript-eslint/utils@8.59.0",
        "dependsOn": [
          "eslint@10.2.1",
          "typescript@6.0.3",
          "@eslint-community/eslint-utils@4.9.1",
          "@typescript-eslint/scope-manager@8.59.0",
          "@typescript-eslint/types@8.59.0",
          "@typescript-eslint/typescript-estree@8.59.0"
        ]
      },
      {
        "ref": "@typescript-eslint/visitor-keys@8.59.0",
        "dependsOn": [
          "@typescript-eslint/types@8.59.0",
          "eslint-visitor-keys@5.0.1"
        ]
      },
      {
        "ref": "@vitest/coverage-v8@4.1.5",
        "dependsOn": [
          "vitest@4.1.5",
          "@bcoe/v8-coverage@1.0.2",
          "@vitest/utils@4.1.5",
          "ast-v8-to-istanbul@1.0.0",
          "istanbul-lib-coverage@3.2.2",
          "istanbul-lib-report@3.0.1",
          "istanbul-reports@3.2.0",
          "magicast@0.5.2",
          "obug@2.1.1",
          "std-env@4.1.0",
          "tinyrainbow@3.1.0"
        ]
      },
      {
        "ref": "@vitest/eslint-plugin@1.6.16",
        "dependsOn": [
          "eslint@10.2.1",
          "@typescript-eslint/eslint-plugin@8.59.0",
          "typescript@6.0.3",
          "vitest@4.1.5",
          "@typescript-eslint/scope-manager@8.59.0",
          "@typescript-eslint/utils@8.59.0"
        ]
      },
      {
        "ref": "@vitest/expect@4.1.5",
        "dependsOn": [
          "@standard-schema/spec@1.1.0",
          "@types/chai@5.2.3",
          "@vitest/spy@4.1.5",
          "@vitest/utils@4.1.5",
          "chai@6.2.2",
          "tinyrainbow@3.1.0"
        ]
      },
      {
        "ref": "@vitest/mocker@4.1.5",
        "dependsOn": [
          "vite@8.0.9",
          "@vitest/spy@4.1.5",
          "estree-walker@3.0.3",
          "magic-string@0.30.21"
        ]
      },
      {
        "ref": "@vitest/pretty-format@4.1.5",
        "dependsOn": [
          "tinyrainbow@3.1.0"
        ]
      },
      {
        "ref": "@vitest/runner@4.1.5",
        "dependsOn": [
          "@vitest/utils@4.1.5",
          "pathe@2.0.3"
        ]
      },
      {
        "ref": "@vitest/snapshot@4.1.5",
        "dependsOn": [
          "@vitest/pretty-format@4.1.5",
          "@vitest/utils@4.1.5",
          "magic-string@0.30.21",
          "pathe@2.0.3"
        ]
      },
      {
        "ref": "@vitest/spy@4.1.5",
        "dependsOn": []
      },
      {
        "ref": "@vitest/utils@4.1.5",
        "dependsOn": [
          "@vitest/pretty-format@4.1.5",
          "convert-source-map@2.0.0",
          "tinyrainbow@3.1.0"
        ]
      },
      {
        "ref": "acorn@8.16.0",
        "dependsOn": []
      },
      {
        "ref": "acorn-jsx@5.3.2",
        "dependsOn": [
          "acorn@8.16.0"
        ]
      },
      {
        "ref": "ajv@6.14.0",
        "dependsOn": [
          "fast-deep-equal@3.1.3",
          "fast-json-stable-stringify@2.1.0",
          "json-schema-traverse@0.4.1",
          "uri-js@4.4.1"
        ]
      },
      {
        "ref": "assertion-error@2.0.1",
        "dependsOn": []
      },
      {
        "ref": "ast-v8-to-istanbul@1.0.0",
        "dependsOn": [
          "@jridgewell/trace-mapping@0.3.31",
          "estree-walker@3.0.3",
          "js-tokens@10.0.0"
        ]
      },
      {
        "ref": "balanced-match@4.0.4",
        "dependsOn": []
      },
      {
        "ref": "brace-expansion@5.0.5",
        "dependsOn": [
          "balanced-match@4.0.4"
        ]
      },
      {
        "ref": "chai@6.2.2",
        "dependsOn": []
      },
      {
        "ref": "convert-source-map@2.0.0",
        "dependsOn": []
      },
      {
        "ref": "cross-spawn@7.0.6",
        "dependsOn": [
          "path-key@3.1.1",
          "shebang-command@2.0.0",
          "which@2.0.2"
        ]
      },
      {
        "ref": "debug@4.4.3",
        "dependsOn": [
          "ms@2.1.3"
        ]
      },
      {
        "ref": "deep-is@0.1.4",
        "dependsOn": []
      },
      {
        "ref": "detect-libc@2.1.2",
        "dependsOn": []
      },
      {
        "ref": "es-module-lexer@2.0.0",
        "dependsOn": []
      },
      {
        "ref": "escape-string-regexp@4.0.0",
        "dependsOn": []
      },
      {
        "ref": "eslint@10.2.1",
        "dependsOn": [
          "@eslint-community/eslint-utils@4.9.1",
          "@eslint-community/regexpp@4.12.2",
          "@eslint/config-array@0.23.5",
          "@eslint/config-helpers@0.5.5",
          "@eslint/core@1.2.1",
          "@eslint/plugin-kit@0.7.1",
          "@humanfs/node@0.16.8",
          "@humanwhocodes/module-importer@1.0.1",
          "@humanwhocodes/retry@0.4.3",
          "@types/estree@1.0.8",
          "ajv@6.14.0",
          "cross-spawn@7.0.6",
          "debug@4.4.3",
          "escape-string-regexp@4.0.0",
          "eslint-scope@9.1.2",
          "eslint-visitor-keys@5.0.1",
          "espree@11.2.0",
          "esquery@1.7.0",
          "esutils@2.0.3",
          "fast-deep-equal@3.1.3",
          "file-entry-cache@8.0.0",
          "find-up@5.0.0",
          "glob-parent@6.0.2",
          "ignore@5.3.2",
          "imurmurhash@0.1.4",
          "is-glob@4.0.3",
          "json-stable-stringify-without-jsonify@1.0.1",
          "minimatch@10.2.5",
          "natural-compare@1.4.0",
          "optionator@0.9.4"
        ]
      },
      {
        "ref": "eslint-config-prettier@10.1.8",
        "dependsOn": [
          "eslint@10.2.1"
        ]
      },
      {
        "ref": "eslint-scope@9.1.2",
        "dependsOn": [
          "@types/esrecurse@4.3.1",
          "@types/estree@1.0.8",
          "esrecurse@4.3.0",
          "estraverse@5.3.0"
        ]
      },
      {
        "ref": "eslint-visitor-keys@5.0.1",
        "dependsOn": []
      },
      {
        "ref": "espree@11.2.0",
        "dependsOn": [
          "acorn@8.16.0",
          "acorn-jsx@5.3.2",
          "eslint-visitor-keys@5.0.1"
        ]
      },
      {
        "ref": "esquery@1.7.0",
        "dependsOn": [
          "estraverse@5.3.0"
        ]
      },
      {
        "ref": "esrecurse@4.3.0",
        "dependsOn": [
          "estraverse@5.3.0"
        ]
      },
      {
        "ref": "estraverse@5.3.0",
        "dependsOn": []
      },
      {
        "ref": "estree-walker@3.0.3",
        "dependsOn": [
          "@types/estree@1.0.8"
        ]
      },
      {
        "ref": "esutils@2.0.3",
        "dependsOn": []
      },
      {
        "ref": "expect-type@1.3.0",
        "dependsOn": []
      },
      {
        "ref": "fast-deep-equal@3.1.3",
        "dependsOn": []
      },
      {
        "ref": "fast-json-stable-stringify@2.1.0",
        "dependsOn": []
      },
      {
        "ref": "fast-levenshtein@2.0.6",
        "dependsOn": []
      },
      {
        "ref": "fdir@6.5.0",
        "dependsOn": [
          "picomatch@4.0.4"
        ]
      },
      {
        "ref": "file-entry-cache@8.0.0",
        "dependsOn": [
          "flat-cache@4.0.1"
        ]
      },
      {
        "ref": "find-up@5.0.0",
        "dependsOn": [
          "locate-path@6.0.0",
          "path-exists@4.0.0"
        ]
      },
      {
        "ref": "flat-cache@4.0.1",
        "dependsOn": [
          "flatted@3.4.2",
          "keyv@4.5.4"
        ]
      },
      {
        "ref": "flatted@3.4.2",
        "dependsOn": []
      },
      {
        "ref": "fsevents@2.3.3",
        "dependsOn": []
      },
      {
        "ref": "glob@13.0.6",
        "dependsOn": [
          "minimatch@10.2.5",
          "minipass@7.1.3",
          "path-scurry@2.0.2"
        ]
      },
      {
        "ref": "glob-parent@6.0.2",
        "dependsOn": [
          "is-glob@4.0.3"
        ]
      },
      {
        "ref": "globals@17.5.0",
        "dependsOn": []
      },
      {
        "ref": "has-flag@4.0.0",
        "dependsOn": []
      },
      {
        "ref": "html-escaper@2.0.2",
        "dependsOn": []
      },
      {
        "ref": "ignore@5.3.2",
        "dependsOn": []
      },
      {
        "ref": "imurmurhash@0.1.4",
        "dependsOn": []
      },
      {
        "ref": "is-extglob@2.1.1",
        "dependsOn": []
      },
      {
        "ref": "is-glob@4.0.3",
        "dependsOn": [
          "is-extglob@2.1.1"
        ]
      },
      {
        "ref": "isexe@2.0.0",
        "dependsOn": []
      },
      {
        "ref": "istanbul-lib-coverage@3.2.2",
        "dependsOn": []
      },
      {
        "ref": "istanbul-lib-report@3.0.1",
        "dependsOn": [
          "istanbul-lib-coverage@3.2.2",
          "make-dir@4.0.0",
          "supports-color@7.2.0"
        ]
      },
      {
        "ref": "istanbul-reports@3.2.0",
        "dependsOn": [
          "html-escaper@2.0.2",
          "istanbul-lib-report@3.0.1"
        ]
      },
      {
        "ref": "js-tokens@10.0.0",
        "dependsOn": []
      },
      {
        "ref": "json-buffer@3.0.1",
        "dependsOn": []
      },
      {
        "ref": "json-schema-traverse@0.4.1",
        "dependsOn": []
      },
      {
        "ref": "json-stable-stringify-without-jsonify@1.0.1",
        "dependsOn": []
      },
      {
        "ref": "keyv@4.5.4",
        "dependsOn": [
          "json-buffer@3.0.1"
        ]
      },
      {
        "ref": "levn@0.4.1",
        "dependsOn": [
          "prelude-ls@1.2.1",
          "type-check@0.4.0"
        ]
      },
      {
        "ref": "lightningcss@1.32.0",
        "dependsOn": [
          "detect-libc@2.1.2",
          "lightningcss-android-arm64@1.32.0",
          "lightningcss-darwin-arm64@1.32.0",
          "lightningcss-darwin-x64@1.32.0",
          "lightningcss-freebsd-x64@1.32.0",
          "lightningcss-linux-arm-gnueabihf@1.32.0",
          "lightningcss-linux-arm64-gnu@1.32.0",
          "lightningcss-linux-arm64-musl@1.32.0",
          "lightningcss-linux-x64-gnu@1.32.0",
          "lightningcss-linux-x64-musl@1.32.0",
          "lightningcss-win32-arm64-msvc@1.32.0",
          "lightningcss-win32-x64-msvc@1.32.0"
        ]
      },
      {
        "ref": "lightningcss-android-arm64@1.32.0",
        "dependsOn": []
      },
      {
        "ref": "lightningcss-darwin-arm64@1.32.0",
        "dependsOn": []
      },
      {
        "ref": "lightningcss-darwin-x64@1.32.0",
        "dependsOn": []
      },
      {
        "ref": "lightningcss-freebsd-x64@1.32.0",
        "dependsOn": []
      },
      {
        "ref": "lightningcss-linux-arm-gnueabihf@1.32.0",
        "dependsOn": []
      },
      {
        "ref": "lightningcss-linux-arm64-gnu@1.32.0",
        "dependsOn": []
      },
      {
        "ref": "lightningcss-linux-arm64-musl@1.32.0",
        "dependsOn": []
      },
      {
        "ref": "lightningcss-linux-x64-gnu@1.32.0",
        "dependsOn": []
      },
      {
        "ref": "lightningcss-linux-x64-musl@1.32.0",
        "dependsOn": []
      },
      {
        "ref": "lightningcss-win32-arm64-msvc@1.32.0",
        "dependsOn": []
      },
      {
        "ref": "lightningcss-win32-x64-msvc@1.32.0",
        "dependsOn": []
      },
      {
        "ref": "locate-path@6.0.0",
        "dependsOn": [
          "p-locate@5.0.0"
        ]
      },
      {
        "ref": "lru-cache@11.3.5",
        "dependsOn": []
      },
      {
        "ref": "magic-string@0.30.21",
        "dependsOn": [
          "@jridgewell/sourcemap-codec@1.5.5"
        ]
      },
      {
        "ref": "magicast@0.5.2",
        "dependsOn": [
          "@babel/parser@7.29.2",
          "@babel/types@7.29.0",
          "source-map-js@1.2.1"
        ]
      },
      {
        "ref": "make-dir@4.0.0",
        "dependsOn": [
          "semver@7.7.4"
        ]
      },
      {
        "ref": "minimatch@10.2.5",
        "dependsOn": [
          "brace-expansion@5.0.5"
        ]
      },
      {
        "ref": "minipass@7.1.3",
        "dependsOn": []
      },
      {
        "ref": "ms@2.1.3",
        "dependsOn": []
      },
      {
        "ref": "nanoid@3.3.11",
        "dependsOn": []
      },
      {
        "ref": "natural-compare@1.4.0",
        "dependsOn": []
      },
      {
        "ref": "obug@2.1.1",
        "dependsOn": []
      },
      {
        "ref": "optionator@0.9.4",
        "dependsOn": [
          "deep-is@0.1.4",
          "fast-levenshtein@2.0.6",
          "levn@0.4.1",
          "prelude-ls@1.2.1",
          "type-check@0.4.0",
          "word-wrap@1.2.5"
        ]
      },
      {
        "ref": "p-limit@3.1.0",
        "dependsOn": [
          "yocto-queue@0.1.0"
        ]
      },
      {
        "ref": "p-locate@5.0.0",
        "dependsOn": [
          "p-limit@3.1.0"
        ]
      },
      {
        "ref": "package-json-from-dist@1.0.1",
        "dependsOn": []
      },
      {
        "ref": "path-exists@4.0.0",
        "dependsOn": []
      },
      {
        "ref": "path-key@3.1.1",
        "dependsOn": []
      },
      {
        "ref": "path-scurry@2.0.2",
        "dependsOn": [
          "lru-cache@11.3.5",
          "minipass@7.1.3"
        ]
      },
      {
        "ref": "pathe@2.0.3",
        "dependsOn": []
      },
      {
        "ref": "picocolors@1.1.1",
        "dependsOn": []
      },
      {
        "ref": "picomatch@4.0.4",
        "dependsOn": []
      },
      {
        "ref": "postcss@8.5.10",
        "dependsOn": [
          "nanoid@3.3.11",
          "picocolors@1.1.1",
          "source-map-js@1.2.1"
        ]
      },
      {
        "ref": "prelude-ls@1.2.1",
        "dependsOn": []
      },
      {
        "ref": "prettier@3.8.3",
        "dependsOn": []
      },
      {
        "ref": "punycode@2.3.1",
        "dependsOn": []
      },
      {
        "ref": "rimraf@6.1.3",
        "dependsOn": [
          "glob@13.0.6",
          "package-json-from-dist@1.0.1"
        ]
      },
      {
        "ref": "rolldown@1.0.0-rc.16",
        "dependsOn": [
          "@oxc-project/types@0.126.0",
          "@rolldown/pluginutils@1.0.0-rc.16",
          "@rolldown/binding-android-arm64@1.0.0-rc.16",
          "@rolldown/binding-darwin-arm64@1.0.0-rc.16",
          "@rolldown/binding-darwin-x64@1.0.0-rc.16",
          "@rolldown/binding-freebsd-x64@1.0.0-rc.16",
          "@rolldown/binding-linux-arm-gnueabihf@1.0.0-rc.16",
          "@rolldown/binding-linux-arm64-gnu@1.0.0-rc.16",
          "@rolldown/binding-linux-arm64-musl@1.0.0-rc.16",
          "@rolldown/binding-linux-ppc64-gnu@1.0.0-rc.16",
          "@rolldown/binding-linux-s390x-gnu@1.0.0-rc.16",
          "@rolldown/binding-linux-x64-gnu@1.0.0-rc.16",
          "@rolldown/binding-linux-x64-musl@1.0.0-rc.16",
          "@rolldown/binding-openharmony-arm64@1.0.0-rc.16",
          "@rolldown/binding-wasm32-wasi@1.0.0-rc.16",
          "@rolldown/binding-win32-arm64-msvc@1.0.0-rc.16",
          "@rolldown/binding-win32-x64-msvc@1.0.0-rc.16"
        ]
      },
      {
        "ref": "semver@7.7.4",
        "dependsOn": []
      },
      {
        "ref": "shebang-command@2.0.0",
        "dependsOn": [
          "shebang-regex@3.0.0"
        ]
      },
      {
        "ref": "shebang-regex@3.0.0",
        "dependsOn": []
      },
      {
        "ref": "siginfo@2.0.0",
        "dependsOn": []
      },
      {
        "ref": "source-map-js@1.2.1",
        "dependsOn": []
      },
      {
        "ref": "stackback@0.0.2",
        "dependsOn": []
      },
      {
        "ref": "std-env@4.1.0",
        "dependsOn": []
      },
      {
        "ref": "supports-color@7.2.0",
        "dependsOn": [
          "has-flag@4.0.0"
        ]
      },
      {
        "ref": "tinybench@2.9.0",
        "dependsOn": []
      },
      {
        "ref": "tinyexec@1.1.1",
        "dependsOn": []
      },
      {
        "ref": "tinyglobby@0.2.16",
        "dependsOn": [
          "fdir@6.5.0",
          "picomatch@4.0.4"
        ]
      },
      {
        "ref": "tinyrainbow@3.1.0",
        "dependsOn": []
      },
      {
        "ref": "ts-api-utils@2.5.0",
        "dependsOn": [
          "typescript@6.0.3"
        ]
      },
      {
        "ref": "tslib@2.8.1",
        "dependsOn": []
      },
      {
        "ref": "type-check@0.4.0",
        "dependsOn": [
          "prelude-ls@1.2.1"
        ]
      },
      {
        "ref": "typescript@6.0.3",
        "dependsOn": []
      },
      {
        "ref": "typescript-eslint@8.59.0",
        "dependsOn": [
          "eslint@10.2.1",
          "typescript@6.0.3",
          "@typescript-eslint/eslint-plugin@8.59.0",
          "@typescript-eslint/parser@8.59.0",
          "@typescript-eslint/typescript-estree@8.59.0",
          "@typescript-eslint/utils@8.59.0"
        ]
      },
      {
        "ref": "undici-types@7.16.0",
        "dependsOn": []
      },
      {
        "ref": "uri-js@4.4.1",
        "dependsOn": [
          "punycode@2.3.1"
        ]
      },
      {
        "ref": "vite@8.0.9",
        "dependsOn": [
          "@types/node@24.12.2",
          "lightningcss@1.32.0",
          "picomatch@4.0.4",
          "postcss@8.5.10",
          "rolldown@1.0.0-rc.16",
          "tinyglobby@0.2.16",
          "fsevents@2.3.3"
        ]
      },
      {
        "ref": "vitest@4.1.5",
        "dependsOn": [
          "@types/node@24.12.2",
          "@vitest/coverage-v8@4.1.5",
          "@vitest/expect@4.1.5",
          "@vitest/mocker@4.1.5",
          "@vitest/pretty-format@4.1.5",
          "@vitest/runner@4.1.5",
          "@vitest/snapshot@4.1.5",
          "@vitest/spy@4.1.5",
          "@vitest/utils@4.1.5",
          "es-module-lexer@2.0.0",
          "expect-type@1.3.0",
          "magic-string@0.30.21",
          "obug@2.1.1",
          "pathe@2.0.3",
          "picomatch@4.0.4",
          "std-env@4.1.0",
          "tinybench@2.9.0",
          "tinyexec@1.1.1",
          "tinyglobby@0.2.16",
          "tinyrainbow@3.1.0",
          "vite@8.0.9",
          "why-is-node-running@2.3.0"
        ]
      },
      {
        "ref": "which@2.0.2",
        "dependsOn": [
          "isexe@2.0.0"
        ]
      },
      {
        "ref": "why-is-node-running@2.3.0",
        "dependsOn": [
          "siginfo@2.0.0",
          "stackback@0.0.2"
        ]
      },
      {
        "ref": "word-wrap@1.2.5",
        "dependsOn": []
      },
      {
        "ref": "yocto-queue@0.1.0",
        "dependsOn": []
      }
    ]
}

CANNED_PLAN = InvestigationPlan(
    concern=CONCERN,
    rationale="Canned plan for debug.",
    hypotheses=[
        Hypothesis(
            id="h1",
            dep_name="express",
            statement="express@4.18.0 may expose known CVEs to the application",
            risk_theme="vulnerability",
            rationale="Primary web framework — high attack surface",
            skills=["VulnerabilitySkill", "MaintainerTrustSkill"],
        ),
        Hypothesis(
            id="h2",
            dep_name="lodash",
            statement="lodash may expose prototype pollution vulnerabilities",
            risk_theme="vulnerability",
            rationale="Widely used utility library with known CVE history",
            skills=["VulnerabilitySkill"],
        ),
    ],
    skill_plan=[
        SkillAssignment(dep_name="express", hypothesis_id="h1", skill_id="VulnerabilitySkill"),
        SkillAssignment(dep_name="express", hypothesis_id="h1", skill_id="MaintainerTrustSkill"),
        SkillAssignment(dep_name="lodash", hypothesis_id="h2", skill_id="VulnerabilitySkill"),
    ],
)

# Medium severity keeps finding_reviewer on the auto-approve path (skips HITL).
CANNED_EVIDENCE: list[Evidence] = [
    Evidence(
        kind="vulnerability",
        dep_name="express",
        skill_id="VulnerabilitySkill",
        hypothesis_id="h1",
        signal="CVE-2024-1234 (medium) in express@4.18.0",
        raw_data={"VulnerabilityID": "CVE-2024-1234", "Severity": "MEDIUM"},
        source="trivy",
        reliability=0.95,
        confidence=0.65,
        severity="medium",
        supports_hypothesis=True,
    ),
    Evidence(
        kind="maintainer_signal",
        dep_name="express",
        skill_id="MaintainerTrustSkill",
        hypothesis_id="h1",
        signal="Active: 45 commits/90d, 12 contributors",
        raw_data={"commits_last_90_days": 45, "contributors": 12},
        source="github_mcp",
        reliability=0.8,
        confidence=0.75,
        severity="info",
        supports_hypothesis=False,
    ),
    Evidence(
        kind="vulnerability",
        dep_name="lodash",
        skill_id="VulnerabilitySkill",
        hypothesis_id="h2",
        signal="CVE-2021-23337 (medium) in lodash@4.17.21",
        raw_data={"VulnerabilityID": "CVE-2021-23337", "Severity": "MEDIUM"},
        source="trivy",
        reliability=0.95,
        confidence=0.65,
        severity="medium",
        supports_hypothesis=True,
    ),
]

# ── Helpers ────────────────────────────────────────────────────────────────────


def _print_section(title: str) -> None:
    width = 60
    print(f"\n{'─' * width}")
    print(f"  {title}")
    print(f"{'─' * width}")


def _dump(label: str, data: object) -> None:
    print(f"\n[{label}]")
    try:
        print(json.dumps(data, indent=2, default=str))
    except Exception:
        print(data)


# ── Discovery ──────────────────────────────────────────────────────────────────


async def debug_discovery() -> None:
    """Run the full discovery subgraph.

    Requires Docker running (clone + SBOM generation) and MongoDB (sbom_dao).
    """
    _print_section("DISCOVERY")

    from src.main_graph.adapters.docker_container_adapter import DockerContainerAdapter
    from src.main_graph.subgraphs.discovery.dao import sbom_dao
    from src.main_graph.subgraphs.discovery.graph import discovery_subgraph
    from src.main_graph.subgraphs.discovery.tools.docker import make_docker_tool

    container = DockerContainerAdapter()
    config = {
        "configurable": {
            "thread_id": JOB_ID,
            "container": container,
            "docker_tool": make_docker_tool(container),
            "sbom_dao": sbom_dao,
        }
    }

    state = {"repo_url": REPO_URL, "concern": CONCERN, "job_id": JOB_ID}
    _dump("Input", state)

    print("\n→ invoking discovery subgraph ...")
    result = await discovery_subgraph.ainvoke(state, config)
    _dump("Output", dict(result))


# ── Planner ───────────────────────────────────────────────────────────────────


async def debug_planner() -> None:
    """Test investigation_planner LLM call with interrupt() patched to auto-approve.

    Exercises _run_planner and _classify_intent but skips the HITL pause/resume
    cycle. The mock DAO absorbs push_proposal / update_proposal calls.
    """
    _print_section("INVESTIGATION_PLANNER")

    from unittest.mock import AsyncMock, patch

    from src.main_graph.nodes.investigation_planner_service import investigation_planner_service

    state = {
        "repo_url": REPO_URL,
        "concern": CONCERN,
        "job_id": JOB_ID,
        "sbom_cyclonedx": CANNED_SBOM,
        "discovery_summary": "Express 4 is the primary web framework. Lodash is a transitive utility dependency.",
        "messages": [],
    }
    mock_dao = AsyncMock()

    with patch("src.main_graph.nodes.investigation_planner_service.interrupt", return_value="approve"):
        result = await investigation_planner_service(state, mock_dao)

    plan = result.get("investigation_plan")
    if plan:
        _dump("InvestigationPlan", {
            "rationale": plan.rationale,
            "hypotheses": [h.__dict__ for h in plan.hypotheses],
            "skill_plan": [s.__dict__ for s in plan.skill_plan],
        })
    else:
        _dump("result", result)


# ── Dispatch ───────────────────────────────────────────────────────────────────


async def debug_dispatch() -> None:
    """Test skill_dispatcher with the canned investigation plan.

    Shows which skills would be fanned out for which dependencies,
    without actually executing any skills.
    """
    _print_section("SKILL_DISPATCHER")

    from src.main_graph.nodes.skill_dispatcher import skill_dispatcher

    state = {
        "repo_url": REPO_URL,
        "concern": CONCERN,
        "job_id": JOB_ID,
        "repo_path": "/tmp/debug-repo",  # non-null so repo_path-gated skills pass can_run()
        "investigation_plan": CANNED_PLAN,
        "sbom_cyclonedx": CANNED_SBOM,
        "evidence": [],
        "messages": [],
    }

    sends = skill_dispatcher(state)  # type: ignore[arg-type]
    _dump(
        f"Sends ({len(sends)} tasks)",
        [
            {
                "skill_id": s.arg.get("current_skill_id"),
                "dep_name": s.arg.get("current_dep_name"),
                "hypothesis_id": s.arg.get("current_hypothesis_id"),
            }
            for s in sends
        ],
    )


# ── Skill ──────────────────────────────────────────────────────────────────────


async def debug_skill(skill_id: str) -> None:
    """Call a single skill directly via SkillContext.

    Bypasses skill_executor and RunnableConfig. Skills that need services
    (e.g. VulnerabilitySkill needs 'container') will return empty evidence
    when services={} — can_run() indicates whether the skill would even fire.
    """
    _print_section(f"SKILL: {skill_id}")

    from src.main_graph.skills.registry import SKILL_REGISTRY

    skill = SKILL_REGISTRY.get(skill_id)
    if skill is None:
        available = ", ".join(SKILL_REGISTRY)
        print(f"Unknown skill '{skill_id}'. Available: {available}")
        return

    ctx = SkillContext(
        dep_name="express",
        hypothesis_id="h1",
        hypothesis="express@4.18.0 may expose known CVEs",
        sbom=CANNED_SBOM,
        concern=CONCERN,
        repo_path=None,
        services={},
    )

    _dump("can_run", skill.can_run(ctx))
    evidence = await skill.execute(ctx)
    _dump(f"Evidence ({len(evidence)} items)", [e.__dict__ for e in evidence])


# ── Correlate → Review → Report ────────────────────────────────────────────────


async def debug_correlate() -> None:
    """Chain evidence_correlator → finding_reviewer → report_builder.

    Uses canned medium-severity evidence so finding_reviewer auto-approves
    (no HITL interrupt triggered).
    """
    from src.main_graph.nodes.evidence_correlator import evidence_correlator
    from src.main_graph.nodes.finding_reviewer import finding_reviewer
    from src.main_graph.nodes.report_builder import report_builder

    base_state = {
        "repo_url": REPO_URL,
        "concern": CONCERN,
        "job_id": JOB_ID,
        "investigation_plan": CANNED_PLAN,
        "evidence": CANNED_EVIDENCE,
        "messages": [],
    }

    _print_section("EVIDENCE_CORRELATOR")
    correlator_out = await evidence_correlator(base_state)  # type: ignore[arg-type]
    _dump(
        "Output",
        {
            "findings": [f.__dict__ for f in correlator_out.get("risk_findings", [])],
            "contradictions": [c.__dict__ for c in correlator_out.get("contradictions", [])],
        },
    )

    _print_section("FINDING_REVIEWER")
    reviewer_state = {**base_state, **correlator_out}
    reviewer_out = await finding_reviewer(reviewer_state)  # type: ignore[arg-type]
    _dump("Output", reviewer_out)

    _print_section("REPORT_BUILDER")
    report_state = {**reviewer_state, **reviewer_out}
    report_out = report_builder(report_state)  # type: ignore[arg-type]
    _dump("analysis_report", report_out.get("analysis_report"))


# ── Entrypoint ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "correlate"

    if mode == "discovery":
        asyncio.run(debug_discovery())
    elif mode == "planner":
        asyncio.run(debug_planner())
    elif mode == "dispatch":
        asyncio.run(debug_dispatch())
    elif mode == "skill":
        sid = sys.argv[2] if len(sys.argv) > 2 else "VulnerabilitySkill"
        asyncio.run(debug_skill(sid))
    elif mode == "correlate":
        asyncio.run(debug_correlate())
    else:
        print(f"Unknown mode '{mode}'. Choose from: discovery, planner, dispatch, skill <skill_id>, correlate")
        sys.exit(1)
