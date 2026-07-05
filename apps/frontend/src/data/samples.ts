// src/data/samples.ts
export interface Sample {
  id: string
  label: string
  description: string
  repo_url: string
  concern: string
}

export const SAMPLES: Sample[] = [
  {
    id: 'supply-chain',
    label: 'Supply chain',
    description: 'Express API — look for malicious postinstall scripts in transitive deps',
    repo_url: 'https://github.com/expressjs/express',
    concern: 'Supply chain attack via malicious postinstall scripts in transitive dependencies',
  },
  {
    id: 'license-risk',
    label: 'License compliance',
    description: 'React project — surface GPL or AGPL licenses that block commercial use',
    repo_url: 'https://github.com/facebook/react',
    concern:
      'License compliance — identify any GPL or AGPL dependencies that could affect commercial distribution',
  },
  {
    id: 'known-cves',
    label: 'Known CVEs',
    description: 'Legacy API — assess exploitability of pinned CVE-affected versions',
    repo_url: 'https://github.com/nodejs/node',
    concern:
      'Known vulnerabilities (CVEs) in pinned dependency versions — assess exploitability and upgrade urgency',
  },
  {
    id: 'maintainer-trust',
    label: 'Maintainer trust',
    description: 'Check for abandoned or single-maintainer dependencies',
    repo_url: 'https://github.com/vercel/next.js',
    concern:
      'Maintainer trust and bus factor — identify dependencies with low activity or single maintainers',
  },
]
